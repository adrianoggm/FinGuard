"""Where the conversation is, as one value.

The client used to hold five independent fields:

    plan        the last financial question that was answered
    unfinished  a financial question missing its window
    pending     a payment waiting for a yes
    payment     a payment still being built
    refusal     the capability the last turn was refused for

Every conversational defect added another one, and the client decided what each combination of
nulls meant. That is the fragility the review named: five nullable fields describe thirty-two
states, most of which are nonsense — a payment awaiting confirmation *and* a half-built payment, a
clarification *and* a refusal — and nothing prevented them.

So: **at most one workflow is active**, and it is a tagged union. The states that cannot coexist
cannot be represented, and the client stops inferring — it stores what the service sends and sends
it back.

`last_plan` sits beside the active workflow rather than inside it, because the plan behind the last
answer is not a workflow: it is memory. A payment can be under way while "¿y en gasolina?" still
means last month's window.

**Every waiting state names what it waits for.** That is the second half of the review's point: the
window vocabulary grew "del año" and "el año" because nothing recorded that the question had been
about a year. With `awaiting` on the state, "de este" becomes answerable — the slot says which field
the sentence is filling, so the reply no longer has to be a self-contained phrase.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from finguard_orchestrator.domain.enums import Rail
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# What a sentence can turn into and a follow-up can inherit from. Declared here as well as on
# the planning result because both need the pair named, and importing one from the other would
# tie a conversation to a planner.
type PlannedQuery = FinancialPlan | TopMovements

# MAX_RANK was declared here as well as in `top_movements`, at 100 in both, with the DTO importing
# this one and the value object enforcing the other. It lives in `domain/constants` now, with every
# other limit the wire has to agree with banking about.


class Workflow(StrEnum):
    """The tag of the active state.

    The union above is the type; this is its name, for the two places that need one — the wire, and
    a client switching on what to render. Deriving it from `isinstance` at the edge would put the
    same four-way decision in every layer that touches a conversation.
    """

    FINANCIAL_CLARIFICATION = "FINANCIAL_CLARIFICATION"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    PAYMENT_IN_PROGRESS = "PAYMENT_IN_PROGRESS"
    # Between the holder's own accounts. Its own state and not a payment: there is nobody to name,
    # and the two questions it can ask are which account and how much.
    TRANSFER_IN_PROGRESS = "TRANSFER_IN_PROGRESS"
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION"


class Slot(StrEnum):
    """What a turn is waiting for the user to supply.

    One vocabulary across both workflows, because a state that says "waiting" without saying what
    is the state the five nullable fields already were.
    """

    WINDOW = "WINDOW"
    YEAR = "YEAR"
    CATEGORY = "CATEGORY"
    # The user rejected the last answer without yet saying which field should change.
    CORRECTION = "CORRECTION"
    AMOUNT = "AMOUNT"
    RECIPIENT = "RECIPIENT"
    # Not a field to type: a choice between candidates the answer carries. Named separately so a
    # client can tell "write something" from "pick one of these".
    RECIPIENT_CHOICE = "RECIPIENT_CHOICE"
    CONFIRMATION = "CONFIRMATION"
    # Which of the holder's accounts. Asked rarely on purpose: "¿cuánto tengo?" names none and is
    # answered with the total and a breakdown, because a breakdown is a better answer than a
    # question. This is for the sentences where the accounts would not add up into one figure.
    ACCOUNT = "ACCOUNT"


@dataclass(frozen=True)
class FinancialClarification:
    """A question the assistant asked about a query it had half read."""

    draft: FinancialPlanDraft
    awaiting: Slot = Slot.WINDOW


@dataclass(frozen=True)
class UnsupportedCapability:
    """A capability this build does not have, by the name of its family.

    It carried a draft and a rank for one release: the largest expense was refused, "¿y el segundo?"
    asked for the next one, and remembering only the family name meant re-reading a period and a
    category out of a sentence containing neither. RANK_MOVEMENTS answers both of those questions
    now, so the fields went with the refusal that needed them — a state carrying data no branch
    reads is the fragility the review named, one field at a time.
    """

    capability: str


@dataclass(frozen=True)
class PaymentInProgress:
    """A payment being built, and what it still needs.

    `suspended` is the review's point 7. A half-built payment currently survives any number of
    unrelated turns in silence, so "hagamos un bizum / ¿cuánto gasté? / 15 euros" resumes a payment
    the user had stopped thinking about. Suspending it makes that visible: the state is still there,
    and a bare amount no longer completes it unasked.
    """

    draft: PaymentDraft
    awaiting: Slot = Slot.AMOUNT
    candidates: tuple[Contact, ...] = ()
    suspended: bool = False


@dataclass(frozen=True)
class TransferInProgress:
    """Money on its way between the holder's own accounts, and what the sentence did not say.

    This path used to hold no state at all, on the argument that reading a sentence is all it does
    and preparing is a `payments:prepare` scope on another route. That is still true of the
    *preparing*, and it was the wrong conclusion about the *reading*: the path asks two questions —
    which account, and how much — and could hear the answer to neither.

        "quiero traspasar del fondo a hucha"  →  "¿Cuánto quieres mover a Hucha?"
        "1000 pavos"                          →  "¿De qué periodo?"

    A question whose answer falls through to the financial planner is worse than no question. So the
    two things a transfer needs are remembered here, and exactly those: the account it is going to,
    once one is resolved, and the amount, once one is said. Nothing is staged and no money moves —
    the answer is still a sentence telling the holder where to confirm it.
    """

    # The account, when the sentence named one and only one matched. `None` while that is the
    # question being asked.
    to_account_id: UUID | None = None
    amount_minor: int | None = None
    awaiting: Slot = Slot.AMOUNT


@dataclass(frozen=True)
class PaymentConfirmation:
    """A payment the bank is holding, waiting for a yes. The one state that can move money next."""

    intent_id: UUID
    recipient: str
    amount_minor: int
    currency: str = "EUR"
    concept: str | None = None
    # How the money leaves and where it goes. Here rather than only on the answer, because this is
    # the state the client holds while the preview is on screen: a chat that cannot tell a Bizum
    # from a transfer draws the same prompt for both, and the one difference that matters to the
    # person approving it — twenty-four characters of account number — would not be on it.
    rail: Rail = Rail.BIZUM
    recipient_iban: str | None = None


type ActiveWorkflow = (
    FinancialClarification
    | UnsupportedCapability
    | PaymentInProgress
    | TransferInProgress
    | PaymentConfirmation
)


@dataclass(frozen=True)
class Conversation:
    """Everything this service needs to know about a conversation it does not store.

    Held by whoever is having it, exactly like the five fields were and for the same reason: there
    are no sessions here. What changes is that the client no longer decides what the state means —
    it round-trips one value.
    """

    active: ActiveWorkflow | None = None
    # The last answered question, whichever kind it was. An aggregate and a ranking are both
    # things a follow-up inherits from — "¿y en gasolina?" narrows either — so this is the union
    # rather than a field per kind, which would let both be set and neither mean anything.
    last_plan: PlannedQuery | None = None
    # The answered plan immediately before `last_plan`. Two compatible plans with different
    # windows are enough to answer "compáralos" without storing prose or financial results.
    prior_plan: PlannedQuery | None = None
    # A validated read that could not reach banking. It is memory, not a workflow: a suspended
    # payment may coexist with it, and retrying must execute this exact plan instead of asking the
    # model to interpret "reintenta" as a new financial question.
    retry_plan: PlannedQuery | None = None
    # What the last turn asked for. Memory too, and it exists because `awaiting` cannot cover this:
    # a clarification records its slot on the workflow, and a question asked with nothing
    # half-built behind it leaves no workflow to record it on. Without this, that question can be
    # asked forever — and the user cannot escape by rephrasing, because rephrasing is what already
    # failed. It survives exactly one turn: everything that is not another question clears it.
    last_asked: Slot | None = None
    # Whether the last turn answered a balance. Memory again, and the narrowest form of it: "¿y en
    # la hucha?" is a follow-up about spending after a spending question and a follow-up about the
    # balance after a balance, and the sentence is identical in both. Without this the second one
    # reached the financial planner and came back "¿de qué periodo?" — a question about a window,
    # to somebody who had just been told a figure and asked which account it was in.
    #
    # A flag rather than the whole answer: nothing else needs remembering, and a field that holds
    # more than one thing is a field the next branch reads wrongly.
    answered_the_balance: bool = False

    @property
    def awaiting(self) -> Slot | None:
        """Which slot the last turn asked for, if it asked for one.

        Read off the state rather than off the question's wording, which is the point: the sentence
        is written by a model, and "de este" only means something against the slot.
        """
        if isinstance(self.active, FinancialClarification | PaymentInProgress | TransferInProgress):
            return self.active.awaiting
        if isinstance(self.active, PaymentConfirmation):
            return Slot.CONFIRMATION
        return None

    @property
    def at_rest(self) -> bool:
        """Whether anything is waiting for a reply.

        The one fact that decides whether an acknowledgement is an acknowledgement. «Vale» after an
        answer is a person being polite; «vale» while something is waiting for a value might be an
        answer to it, and reading it as manners would swallow the turn. Thirty of the corpus's turns
        are a bare «vale», so this is not a corner.

        Built on `awaiting` rather than on `active`, and the difference is a real turn: a refusal
        leaves `UnsupportedCapability` open, and that workflow is a *record* of the last turn rather
        than a question. Reading "something is active" as "something is waiting" sent a «vale» two
        turns after a refused forecast back to the planner.

        `last_asked` as well, because a question this service asked and then closed is still a
        question the *user* is answering on the next turn.
        """
        return self.awaiting is None and self.last_asked is None

    def with_active(self, active: ActiveWorkflow | None) -> "Conversation":
        """The same conversation with a different workflow — replacing, never accumulating.

        One active workflow is the invariant this type exists for, so there is no method that adds a
        second. A payment starting while a clarification is open ends the clarification, which is
        also what happens in the conversation.

        `last_asked` is deliberately dropped. This builds the state a turn *leaves behind*, and a
        turn that asked something says so by calling `asking` — so the memory lasts one turn and a
        slot from three questions ago cannot make an unrelated one look like a repeat.
        """
        return Conversation(
            active=active,
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=self.retry_plan,
        )

    def asking(self, slot: Slot) -> "Conversation":
        """Remember the question this turn asked, whether or not it left a workflow behind."""
        return Conversation(
            active=self.active,
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=self.retry_plan,
            last_asked=slot,
        )

    def refused(self, capability: str) -> "Conversation":
        """A capability this service does not have, recorded — and nothing else disturbed.

        A refusal answers nothing, so it must not end the thread the user was on. `last_plan`
        already survived one and `answered_the_balance` did not, which made the spending thread
        outlive a refusal while the balance thread died on it:

            "¿cuánto tengo en el fondo?"           the fund's balance
            "¿qué porcentaje tengo en el fondo?"   refused
            "¿y en la hucha?"                      …a question about spending

        `last_asked` still goes, and that is not the same thing: the refusal genuinely asked
        nothing, so there is no open question for the next turn to be answering.
        """
        return Conversation(
            active=UnsupportedCapability(capability=capability),
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=self.retry_plan,
            answered_the_balance=self.answered_the_balance,
        )

    def answering_the_balance(self) -> "Conversation":
        """This turn said how much money there is, so a bare account name is about that.

        Everything else about the conversation survives: a balance is a turn and not a reset, and
        asking it in the middle of an analysis of last month must not cost the window.
        """
        return Conversation(
            active=self.active,
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=self.retry_plan,
            last_asked=self.last_asked,
            answered_the_balance=True,
        )

    def awaiting_retry(self, plan: PlannedQuery) -> "Conversation":
        """Remember the exact validated read that banking could not execute."""
        return Conversation(
            active=self.active,
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=plan,
            last_asked=self.last_asked,
        )

    def without_retry(self) -> "Conversation":
        """Abandon a failed read while preserving the rest of the conversation."""
        return Conversation(
            active=self.active,
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            last_asked=self.last_asked,
        )

    def answered(self, plan: PlannedQuery) -> "Conversation":
        """Remember a financial answer and close only the workflow that answer resolves.

        A suspended payment belongs to the conversation before this answer and deliberately stepped
        aside for it. It survives; a financial clarification or refusal does not, because the new
        answer supersedes it.
        """
        active = (
            self.active
            if isinstance(self.active, PaymentInProgress) and self.active.suspended
            else None
        )
        prior = self.prior_plan if plan == self.last_plan else self.last_plan
        return Conversation(active=active, last_plan=plan, prior_plan=prior)

    def suspend(self) -> "Conversation":
        """The active payment steps aside for a turn about something else.

        Only payments suspend. A clarification does not: the question it asked is either answered or
        it is not, and keeping one alive across a change of subject is how "este año" completed a
        plan the user had moved on from.
        """
        if not isinstance(self.active, PaymentInProgress):
            return Conversation(
                active=None,
                last_plan=self.last_plan,
                prior_plan=self.prior_plan,
                retry_plan=self.retry_plan,
                last_asked=self.last_asked,
                answered_the_balance=self.answered_the_balance,
            )
        return Conversation(
            active=PaymentInProgress(
                draft=self.active.draft,
                awaiting=self.active.awaiting,
                candidates=self.active.candidates,
                suspended=True,
            ),
            last_plan=self.last_plan,
            prior_plan=self.prior_plan,
            retry_plan=self.retry_plan,
            last_asked=self.last_asked,
            # Kept, like every other piece of memory here. Dropping it meant a payment under way
            # silently turned "¿y en la hucha?" back into a question about spending, so a balance
            # follow-up worked or not depending on whether a Bizum happened to be half-built.
            answered_the_balance=self.answered_the_balance,
        )


def workflow_of(active: "ActiveWorkflow | None") -> Workflow | None:
    """Which state this is, by name. None when nothing is under way."""
    match active:
        case FinancialClarification():
            return Workflow.FINANCIAL_CLARIFICATION
        case UnsupportedCapability():
            return Workflow.UNSUPPORTED_CAPABILITY
        case PaymentInProgress():
            return Workflow.PAYMENT_IN_PROGRESS
        case TransferInProgress():
            return Workflow.TRANSFER_IN_PROGRESS
        case PaymentConfirmation():
            return Workflow.PAYMENT_CONFIRMATION
        case _:
            return None


def slot_for(missing: frozenset[PaymentField]) -> Slot:
    """Which question a half-built payment asks next.

    The recipient first when both are missing: it decides who gets the money, no preview exists
    without it, and asking for it first surfaces the ambiguity — the expensive part — earlier.
    """
    return Slot.RECIPIENT if PaymentField.RECIPIENT in missing else Slot.AMOUNT


# So a client can render an empty conversation without a special case.
IDLE = Conversation()

__all__ = [
    "IDLE",
    "ActiveWorkflow",
    "Conversation",
    "FinancialClarification",
    "PaymentConfirmation",
    "PaymentInProgress",
    "TransferInProgress",
    "Slot",
    "UnsupportedCapability",
    "Workflow",
    "slot_for",
    "workflow_of",
]
