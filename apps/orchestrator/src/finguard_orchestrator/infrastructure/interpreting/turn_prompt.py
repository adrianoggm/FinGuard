"""What the model is told in order to classify one turn.

Short on purpose. This is not the planning prompt: nothing here needs a category glossary, a period
vocabulary or a worked example of a merge, because the answer is one word from a list of ten. A
long prompt for a small decision buys latency and invites the model to reason about things it is not
being asked.

**The state is described, not the history.** A model given a transcript would start inferring
intent from tone; what decides these turns is mechanical — whether a payment is open, which slot was
asked for, whether there is an answered question behind this one. Those are facts the conversation
already holds, and stating them as facts is what keeps the classification reproducible.

Everything below is sent to the model. Notes about the prompt belong out here, not in the string: a
paragraph of engineering rationale inside the template is another instruction as far as a decoder is
concerned.
"""

from typing import Final

from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    PlannedQuery,
    TransferInProgress,
    UnsupportedCapability,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

# One line per intent, in the order the deterministic router checks them — because the order is the
# design and a model that reads them in it inherits the same tie-breaks. The wording is about what
# the *user* is doing, never about which function answers: an intent named after its handler invites
# a model to reason about our architecture.
_INTENTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "COURTESY",
        "greeting you, thanking you or saying goodbye — a whole turn of manners with nothing "
        "else in it. «gracias, ¿y en la hucha?» is not this: it carries a question. A bare "
        "«vale», «perfecto» or «eso es» is also this, but only when nothing above is waiting "
        "for a reply — while something is, the same word may be the reply.",
    ),
    (
        "LIST_CARDS",
        "asking about the cards in their wallet — which they have, of what kind, or what each one "
        "has been charged. «¿cuánto gasté con la tarjeta en Mercadona?» is not this: that is one "
        "purchase, not the wallet.",
    ),
    (
        "LIST_CONTACTS",
        "asking who they can send money to. A question about the list, even when it says «bizum».",
    ),
    (
        "PAYMENT",
        "telling you to send money to another person, or answering a question about one you are "
        "already building — and only then: with nothing under way above, a bare name or a bare "
        "amount is not a payment. Never a question about money that has already arrived.",
    ),
    ("IDENTITY_OR_HELP", "asking what you are or what you can do. Not about their account at all."),
    (
        "LIST_CATEGORIES",
        "asking for the catalogue itself — «dime las categorías», «¿qué categorías hay?». "
        "*Naming* a category is not this: «¿y en gasolina?» narrows the previous question and "
        "belongs to FINANCIAL_QUERY.",
    ),
    (
        "CORRECTION",
        "saying the last answer was wrong — «no es eso», «no me has entendido», «te has "
        "equivocado». **Also when it carries the fix**: «me refería a gasolina» and «quería "
        "decir el año pasado» are corrections, not new questions, and that is the half that "
        "gets missed.",
    ),
    (
        "EXPLAIN_PREVIOUS",
        "asking how the last answer was reached — «¿cómo lo has calculado?», «¿de dónde sale?» "
        "— or what something was filed as. Asking for more of it is not this: «¿y el segundo?» "
        "wants another row, not an explanation.",
    ),
    (
        "OWN_TRANSFER",
        "telling you to move money between two of their OWN accounts. An instruction, never a "
        "question about how much they usually move.",
    ),
    (
        "ACCOUNT_BALANCE",
        "asking how much money there is right now — including «¿qué cuentas tengo?» and «¿cómo "
        "está mi hucha?», because the answer to both is the total with every account under it. "
        "A state, not a total over a period: «cuánto gasté» and «cuántos movimientos tengo» are "
        "never this.",
    ),
    (
        "FINANCIAL_QUERY",
        "everything else, and the answer whenever you are unsure. It is the long way round and it "
        "is always safe.",
    ),
)

_TEMPLATE: Final = """\
You classify one turn of a Spanish banking conversation. Answer with one intent and nothing else.

## The intents, in priority order

{intents}

## Where the conversation is

{state}

## The turn

{sentence}

## Rules

Pick the FIRST intent in the list above that fits. When two fit, the earlier one wins.
When you are unsure, answer FINANCIAL_QUERY. It is never wrong to be unsure.
Do not answer PAYMENT for a question about money the user received.
"""


def _the_question_behind_this_one(plan: PlannedQuery | None) -> str:
    """The previous question in its own terms, because an ellipsis has nothing else to lean on.

    This line used to be a boolean — *"there is a previous answered question they can follow up
    on"* — and that is the single largest thing wrong with this prompt. 157 of the corpus's turns
    arrive with a plan behind them and most of the remaining disagreements are elliptical:

        "¿y en gasolina?"   narrows the previous plan by category
        "¿y el mes pasado?" moves its window
        "¿y el segundo?"    asks for the next row of its ranking
        "¿y en la hucha?"   narrows it by account — or asks a balance, if that is what came before

    Resolving any of those without the antecedent is not a hard problem, it is an unanswerable
    one, and that is the honest explanation for a 120B model scoring no better than a 9B on this
    corpus: neither of them had the information. A bigger model cannot infer a period nobody told
    it.

    Stated as fields rather than as the sentence the user typed. The sentence is theirs and has
    already been through a prompt once; the plan is ours, validated, and cannot carry an
    instruction. That distinction is the same one `SpokenPlan` exists for on the wire.
    """
    if plan is None:
        return "There is no previous answered question."
    if not isinstance(plan, FinancialPlan):
        # A ranking. It has no window or category to narrow, and what a follow-up does to one is
        # ask for another row — which is a fact about it worth stating rather than describing
        # fields it does not have.
        return "The last answer was a ranking of movements; «¿y el segundo?» asks for another row."

    said = [
        "about money the user spent"
        if plan.direction is Direction.EXPENSE
        else "about money the user received"
    ]
    if plan.period is not None:
        said.append(f"over {plan.period.period.value}")
    if plan.categories:
        said.append(f"filtered to {', '.join(plan.categories)}")
    if plan.merchants:
        said.append(f"at {', '.join(plan.merchants)}")
    if plan.merchant_terms:
        said.append(f"at merchants containing {', '.join(plan.merchant_terms)}")
    if plan.group_by is not GroupBy.NONE:
        said.append(f"grouped by {plan.group_by.value}")
    return (
        f"The previous answered question was {', '.join(said)}. A turn that only names a period, "
        "a category, a merchant or an account is narrowing THAT question, not asking a new one."
    )


def _state_of(conversation: Conversation) -> str:
    """The conversation as facts, in the fewest lines that decide anything."""
    lines: list[str] = []
    match conversation.active:
        case PaymentInProgress(awaiting=slot, suspended=suspended):
            paused = " It was set aside for another subject." if suspended else ""
            lines.append(
                f"A payment to another person is half-built, waiting for: {slot.value}.{paused}"
            )
        case PaymentConfirmation():
            lines.append("A payment is previewed and waiting for the user to confirm or refuse it.")
        case TransferInProgress(awaiting=slot):
            lines.append(
                "A transfer between the user's own accounts is half-read, "
                f"waiting for: {slot.value}."
            )
        case FinancialClarification(awaiting=slot):
            lines.append(f"A question was asked back about: {slot.value}.")
        case UnsupportedCapability(capability=named):
            lines.append(f"The last turn was refused because it needed: {named}.")
        case _:
            lines.append("Nothing is under way.")

    lines.append(_the_question_behind_this_one(conversation.last_plan))
    if conversation.answered_the_balance:
        lines.append("The last answer was a balance, so a bare account name is about the balance.")
    if conversation.retry_plan is not None:
        lines.append("A query the bank could not run is saved and can be retried.")
    return "\n".join(f"- {line}" for line in lines)


def build_prompt(sentence: str, conversation: Conversation) -> str:
    """The whole prompt, with the rules *after* the sentence.

    The sentence is the untrusted part, and somebody typing directions at a bank assistant is not
    hypothetical. Ordering is the cheapest defence there is: whatever the sentence claims, the last
    thing the model reads is the instruction to pick from a closed list and to answer
    FINANCIAL_QUERY when unsure. It is not a guarantee — nothing about a prompt is — which is why
    the schema constrains the answer to ten values and why a model may not start a payment
    whatever it picks.
    """
    intents = "\n".join(f"- {name}: {about}" for name, about in _INTENTS)
    return _TEMPLATE.format(intents=intents, state=_state_of(conversation), sentence=sentence)
