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

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_AGENT_NAME,
    MAX_CONTEXT_ID,
    MAX_GROUP_RANK,
    MAX_OFFERS,
    MAX_SELECTED_ACCOUNTS,
    MAX_VISUAL_CONTEXTS,
)
from finguard_orchestrator.domain.enums import GroupBy, Rail
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import CHART_ID_PATTERN, VisualContext
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.expert_agents import AGENT_ID, ActivitySlot
from finguard_orchestrator.domain.value_objects.account_selection import (
    AccountSelection,
    AccountSelectionMode,
)
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


@dataclass(frozen=True)
class AccountCandidate:
    account_id: UUID
    display_name: str
    kind: str

    def __post_init__(self) -> None:
        if not self.display_name.strip() or not self.kind.strip():
            raise InvalidValueError("account candidate needs display name and kind")


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
    ACCOUNT_SCOPE = "ACCOUNT_SCOPE"
    # A class chosen from an activity search's offers, waiting for a yes on screen (spec §5.2).
    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"


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
class AccountScopeClarification:
    pending: PlannedQuery
    candidates: tuple[AccountCandidate, ...]
    requested_count: int | None = None
    explicit_visualization: bool = False
    awaiting: Slot = Slot.ACCOUNT

    def __post_init__(self) -> None:
        if not self.candidates:
            raise InvalidValueError("account clarification needs candidates")
        if len(self.candidates) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError(f"at most {MAX_SELECTED_ACCOUNTS} account candidates")
        candidate_ids = tuple(candidate.account_id for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise InvalidValueError("account clarification candidates cannot repeat")
        if self.requested_count is not None and self.requested_count < 2:
            raise InvalidValueError("requested ranking count must be at least two")
        if self.requested_count is not None and self.requested_count > MAX_GROUP_RANK:
            raise InvalidValueError(f"requested ranking count cannot exceed {MAX_GROUP_RANK}")
        if self.requested_count is not None and not isinstance(self.pending, TopMovements):
            raise InvalidValueError("requested ranking count requires a movement ranking")


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


@dataclass(frozen=True)
class BookingConfirmation:
    """A class chosen from the sealed offers, waiting for a yes on screen (spec §5.2).

    Unlike a payment preview nothing is held anywhere yet: the gym has been sent nothing, and the
    booking id minted here is what the one message sent after a yes is named by (`uuid5`), so an
    agent that deduplicates by message id sees one booking however often the yes arrives.
    """

    booking_id: UUID
    slot: ActivitySlot
    agent_name: str  # the name the holder was shown with the offers
    context_id: str | None  # the search's A2A context with this agent, when it gave one

    def __post_init__(self) -> None:
        if not 1 <= len(self.agent_name) <= MAX_AGENT_NAME:
            raise InvalidValueError("a booking names its gym by a bounded name")
        if self.context_id is not None and not 1 <= len(self.context_id) <= MAX_CONTEXT_ID:
            raise InvalidValueError("a booking's context is bounded")


type ActiveWorkflow = (
    FinancialClarification
    | AccountScopeClarification
    | UnsupportedCapability
    | PaymentInProgress
    | TransferInProgress
    | PaymentConfirmation
    | BookingConfirmation
)


@dataclass(frozen=True)
class ActivityOffers:
    """The last search's verified slots, and per agent its card name and A2A `contextId` (§5.3).

    Memory rather than a workflow, like the visual contexts: a booking resolves «la de las 19:00»
    against these, and only while `trusted` -- an echo whose seal does not check out is unusable,
    never repaired. `token` is that seal, kept only when it verified, so a turn about something
    else carries the offers forward still sealed; a set a search has just built has none until the
    edge seals it (`conversation_mapper.seal_offers_payload`).
    """

    slots: tuple[ActivitySlot, ...]  # at most MAX_OFFERS, ordered by start
    agent_names: tuple[tuple[str, str], ...]  # (agent_id, card name as shown)
    contexts: tuple[tuple[str, str], ...]  # (agent_id, A2A contextId)
    trusted: bool = True
    token: str | None = None

    def __post_init__(self) -> None:
        if len(self.slots) > MAX_OFFERS:
            raise InvalidValueError(f"at most {MAX_OFFERS} offers")
        starts = [slot.start for slot in self.slots]
        if starts != sorted(starts):
            raise InvalidValueError("offers are ordered by start")
        named = [agent_id for agent_id, _ in self.agent_names]
        if len(set(named)) != len(named) or any(
            not AGENT_ID.fullmatch(agent_id) or not 1 <= len(name) <= MAX_AGENT_NAME
            for agent_id, name in self.agent_names
        ):
            raise InvalidValueError("each offering agent is named once, by a bounded name")
        if any(slot.agent_id not in named for slot in self.slots):
            raise InvalidValueError("every offered slot comes from a named agent")
        continued = [agent_id for agent_id, _ in self.contexts]
        if len(set(continued)) != len(continued) or any(
            agent_id not in named or not 1 <= len(context_id) <= MAX_CONTEXT_ID
            for agent_id, context_id in self.contexts
        ):
            raise InvalidValueError("each context belongs to one offering agent")

    def name_of(self, agent_id: str) -> str:
        """The card name the holder was shown for this agent, or its id."""
        return dict(self.agent_names).get(agent_id, agent_id)


@dataclass(frozen=True)
class Conversation:
    """What the client holds of a conversation: the wire projection of the canonical state.

    The canonical state lives in the dialogue store; the client echoes this back so the next turn
    names which conversation, at which version, it continues.
    """

    # Which conversation this is, minted by the store on the first turn. `None` for a conversation
    # nobody mapped from the wire: `IDLE` is a module-level singleton, and a default factory here
    # would hand one identifier to every conversation in the process.
    conversation_id: UUID | None = None
    # The stored version the client last saw; a turn at an older one is refused as stale.
    version: int | None = None

    active: ActiveWorkflow | None = None
    # The last answered question, whichever kind it was. An aggregate and a ranking are both
    # things a follow-up inherits from — "¿y en gasolina?" narrows either — so this is the union
    # rather than a field per kind, which would let both be set and neither mean anything.
    last_plan: PlannedQuery | None = None
    # Structural chart contexts only; rows and financial values stay in the browser.
    visual_contexts: tuple[VisualContext, ...] = ()
    # The last activity search's verified slots, sealed on the wire (spec §5.3). Memory like the
    # visual contexts: every turn carries it forward, and only another search replaces it.
    activity_offers: ActivityOffers | None = None

    def __post_init__(self) -> None:
        if len(self.visual_contexts) > MAX_VISUAL_CONTEXTS:
            raise InvalidValueError("too many visual contexts")
        ids = [item.chart_id for item in self.visual_contexts]
        if len(set(ids)) != len(ids) or any(not CHART_ID_PATTERN.fullmatch(value) for value in ids):
            raise InvalidValueError("visual chart ids must be unique bounded identifiers")
        for context in self.visual_contexts:
            if not context.spec.valid_for(context.profile.field_names()):
                raise InvalidValueError("visual context spec is incompatible with its profile")

    def with_visual_context(self, context: VisualContext) -> "Conversation":
        """Remember a structural chart context, newest last, without any chart rows."""
        contexts = tuple(item for item in self.visual_contexts if item.chart_id != context.chart_id)
        return replace(self, visual_contexts=(*contexts, context)[-MAX_VISUAL_CONTEXTS:])

    @property
    def awaiting(self) -> Slot | None:
        """Which slot the last turn asked for, if it asked for one.

        Read off the state rather than off the question's wording, which is the point: the sentence
        is written by a model, and "de este" only means something against the slot.

        `AccountScopeClarification` was missing from this list for as long as the state existed. It
        declares `awaiting = Slot.ACCOUNT` on itself, so the field was right and only this reader
        was wrong — and since it is the one reader, the slot never reached the wire and never
        reached anything counting repeats. That mattered more than the other four put together:
        the account question is what a holder with more than one account is asked before any
        figure, so it is the clarification this service emits most often and the one most likely
        to be asked twice.
        """
        if isinstance(
            self.active,
            FinancialClarification
            | AccountScopeClarification
            | PaymentInProgress
            | TransferInProgress,
        ):
            return self.active.awaiting
        if isinstance(self.active, PaymentConfirmation | BookingConfirmation):
            return Slot.CONFIRMATION
        return None


def workflow_of(active: "ActiveWorkflow | None") -> Workflow | None:
    """Which state this is, by name. None when nothing is under way."""
    match active:
        case FinancialClarification():
            return Workflow.FINANCIAL_CLARIFICATION
        case AccountScopeClarification():
            return Workflow.ACCOUNT_SCOPE
        case UnsupportedCapability():
            return Workflow.UNSUPPORTED_CAPABILITY
        case PaymentInProgress():
            return Workflow.PAYMENT_IN_PROGRESS
        case TransferInProgress():
            return Workflow.TRANSFER_IN_PROGRESS
        case PaymentConfirmation():
            return Workflow.PAYMENT_CONFIRMATION
        case BookingConfirmation():
            return Workflow.BOOKING_CONFIRMATION
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
    "AccountCandidate",
    "AccountScopeClarification",
    "AccountSelection",
    "AccountSelectionMode",
    "ActivityOffers",
    "BookingConfirmation",
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


_REFERENCEABLE_GROUPS = {
    GroupBy.CATEGORY,
    GroupBy.MERCHANT,
    GroupBy.COUNTERPARTY,
    GroupBy.ACCOUNT,
}
