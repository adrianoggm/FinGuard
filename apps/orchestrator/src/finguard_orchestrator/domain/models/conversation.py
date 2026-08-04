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

# How far down a ranking anybody may ask. Bounded because it crosses the wire and becomes an
# OFFSET the day RANK_MOVEMENTS exists: "el gasto número 40000" is not a question, it is a scan.
MAX_RANK = 100


class Workflow(StrEnum):
    """The tag of the active state.

    The union above is the type; this is its name, for the two places that need one — the wire, and
    a client switching on what to render. Deriving it from `isinstance` at the edge would put the
    same four-way decision in every layer that touches a conversation.
    """

    FINANCIAL_CLARIFICATION = "FINANCIAL_CLARIFICATION"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    PAYMENT_IN_PROGRESS = "PAYMENT_IN_PROGRESS"
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
class PaymentConfirmation:
    """A payment the bank is holding, waiting for a yes. The one state that can move money next."""

    intent_id: UUID
    recipient: str
    amount_minor: int
    currency: str = "EUR"
    concept: str | None = None


type ActiveWorkflow = (
    FinancialClarification | UnsupportedCapability | PaymentInProgress | PaymentConfirmation
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
    # A validated read that could not reach banking. It is memory, not a workflow: a suspended
    # payment may coexist with it, and retrying must execute this exact plan instead of asking the
    # model to interpret "reintenta" as a new financial question.
    retry_plan: PlannedQuery | None = None

    @property
    def awaiting(self) -> Slot | None:
        """Which slot the last turn asked for, if it asked for one.

        Read off the state rather than off the question's wording, which is the point: the sentence
        is written by a model, and "de este" only means something against the slot.
        """
        if isinstance(self.active, FinancialClarification | PaymentInProgress):
            return self.active.awaiting
        if isinstance(self.active, PaymentConfirmation):
            return Slot.CONFIRMATION
        return None

    def with_active(self, active: ActiveWorkflow | None) -> "Conversation":
        """The same conversation with a different workflow — replacing, never accumulating.

        One active workflow is the invariant this type exists for, so there is no method that adds a
        second. A payment starting while a clarification is open ends the clarification, which is
        also what happens in the conversation.
        """
        return Conversation(
            active=active,
            last_plan=self.last_plan,
            retry_plan=self.retry_plan,
        )

    def awaiting_retry(self, plan: PlannedQuery) -> "Conversation":
        """Remember the exact validated read that banking could not execute."""
        return Conversation(active=self.active, last_plan=self.last_plan, retry_plan=plan)

    def without_retry(self) -> "Conversation":
        """Abandon a failed read while preserving the rest of the conversation."""
        return Conversation(active=self.active, last_plan=self.last_plan)

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
        return Conversation(active=active, last_plan=plan)

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
                retry_plan=self.retry_plan,
            )
        return Conversation(
            active=PaymentInProgress(
                draft=self.active.draft,
                awaiting=self.active.awaiting,
                candidates=self.active.candidates,
                suspended=True,
            ),
            last_plan=self.last_plan,
            retry_plan=self.retry_plan,
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
    "Slot",
    "UnsupportedCapability",
    "Workflow",
    "slot_for",
    "workflow_of",
]
