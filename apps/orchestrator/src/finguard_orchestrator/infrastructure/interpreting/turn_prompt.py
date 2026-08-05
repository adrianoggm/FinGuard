"""What the model is told in order to classify one turn.

Short on purpose. This is not the planning prompt: nothing here needs a category glossary, a period
vocabulary or a worked example of a merge, because the answer is one word from a list of nine. A
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

from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    TransferInProgress,
    UnsupportedCapability,
)

# One line per intent, in the order the deterministic router checks them — because the order is the
# design and a model that reads them in it inherits the same tie-breaks. The wording is about what
# the *user* is doing, never about which function answers: an intent named after its handler invites
# a model to reason about our architecture.
_INTENTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "LIST_CONTACTS",
        "asking who they can send money to. A question about the list, even when it says «bizum».",
    ),
    (
        "PAYMENT",
        "telling you to send money to another person, or answering a question about one you are "
        "already building. Never a question about money that has already arrived.",
    ),
    ("IDENTITY_OR_HELP", "asking what you are or what you can do. Not about their account at all."),
    ("LIST_CATEGORIES", "asking which categories exist. Product vocabulary, not their money."),
    ("CORRECTION", "saying the last answer was wrong."),
    ("EXPLAIN_PREVIOUS", "asking how the last answer was reached, or what something was filed as."),
    (
        "OWN_TRANSFER",
        "telling you to move money between two of their OWN accounts. An instruction, never a "
        "question about how much they usually move.",
    ),
    (
        "ACCOUNT_BALANCE",
        "asking how much money there is right now. A state, not a total over a period — «cuántos "
        "movimientos tengo» is not this.",
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

    lines.append(
        "There is a previous answered question they can follow up on."
        if conversation.last_plan is not None
        else "There is no previous answered question."
    )
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
    the schema constrains the answer to nine values and why a model may not start a payment
    whatever it picks.
    """
    intents = "\n".join(f"- {name}: {about}" for name, about in _INTENTS)
    return _TEMPLATE.format(intents=intents, state=_state_of(conversation), sentence=sentence)
