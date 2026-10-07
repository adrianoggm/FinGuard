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
from typing import TYPE_CHECKING
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_AGENT_NAME,
    MAX_CONTEXT_ID,
    MAX_ENTITY_ALIAS_LENGTH,
    MAX_ENTITY_ALIASES,
    MAX_GROUP_RANK,
    MAX_MERCHANT_LENGTH,
    MAX_OFFERS,
    MAX_REMEMBERED_TURNS,
    MAX_SELECTED_ACCOUNTS,
    MAX_TURN_NOTE,
    MAX_TURN_SENTENCE,
    MAX_VISUAL_CONTEXTS,
)
from finguard_orchestrator.domain.enums import CounterpartyType, Rail
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import CHART_ID_PATTERN, VisualContext
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.expert_agents import AGENT_ID, ActivitySlot
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

if TYPE_CHECKING:
    from finguard_orchestrator.domain.models.result_reference import ResultReference

# What a sentence can turn into and a follow-up can inherit from. Declared here as well as on
# the planning result because both need the pair named, and importing one from the other would
# tie a conversation to a planner.
type PlannedQuery = FinancialPlan | TopMovements

# MAX_RANK was declared here as well as in `top_movements`, at 100 in both, with the DTO importing
# this one and the value object enforcing the other. It lives in `domain/constants` now, with every
# other limit the wire has to agree with banking about.


@dataclass(frozen=True)
class Turn:
    """One exchange that already happened, as the two facts a later turn can lean on.

    The fields below are memory of *what the conversation was*; this is the record of *what it
    did*, and the difference matters. Every one of those fields describes the turn immediately
    before, and each is a projection taken by whichever branch happened to run — so a chain of
    them is lossy at every hop, which is exactly why an ellipsis three turns deep stops resolving.
    A log loses nothing, because nobody decides what it keeps.

    Deliberately not the state. The code still reads `last_plan` and `awaiting`, which are validated
    values a decision can safely rest on, and a sentence is neither. This exists so the *model* has
    the antecedent, and denying it one was measurably the largest thing wrong with the
    classification prompt.

    One branch reads it, as the note below asked: the account question the handler's backstop asks
    (`_scope_question`) has no plan to keep, so the turn that asked it is the only record of what
    was asked, and `_asked_which_account_about` takes the sentence back from here once `last_asked`
    says the question is still open. Nothing is decided on the sentence itself: it is asked again,
    through every gate a sentence passes, which is what the holder would get by retyping it.

    No other reply reads it. The resumable account question, a window given after «¿De qué
    periodo?» and a retry each complete a plan that was kept, and the plan already carries what
    the question named -- the kind of shop and the shop (`explicit_facts.of_its_kind`, and the
    handler's `_the_shop_it_names`) -- so the reply's words, which name only an account, a window
    or a retry, have nothing to look up. Reading the asking sentence back out of this log on those
    routes was tried, and each route found a way to pick the wrong turn.

    ponytail: two representations of the same history. If another branch needs the log, derive
    its field from this the same way instead of adding a seventeenth one beside it.
    """

    # As the holder wrote it, truncated rather than refused: a long sentence is still evidence of
    # what the conversation is about, and this is the one field here a person authored.
    sentence: str
    # What this service decided, by the name the result already carries. A closed vocabulary.
    outcome: str
    # The query behind the answer, in the ledger's identifiers (`answer_writer.describe`) — not the
    # Spanish line «Ver la consulta» showed the person (`plan_in_words`). The turn interpreter's
    # prompt quotes this back as "you answered: …" and was measured on these words, so they stay
    # its words. Absent when the turn ran no query — a greeting, a refusal, a question asked back.
    plan: str | None = None
    # The question this turn asked back, when it asked one.
    question: str | None = None

    def __post_init__(self) -> None:
        """Truncate rather than refuse, because a record is not a query.

        Every other value object here rejects what it cannot hold, and that is right: a plan is
        going to be executed. A turn is only ever read as evidence, and refusing one would mean a
        long sentence six turns ago could make the *next* turn fail. Cut it and keep going.
        """
        if not self.outcome.strip():
            raise InvalidValueError("a recorded turn needs its outcome")
        object.__setattr__(self, "sentence", self.sentence[:MAX_TURN_SENTENCE])
        for note in ("plan", "question"):
            written = getattr(self, note)
            if written is not None:
                object.__setattr__(self, note, written[:MAX_TURN_NOTE])


@dataclass(frozen=True)
class AccountAlias:
    """A user-chosen phrase grounded to one real account identifier."""

    alias: str
    account_id: UUID
    display_name: str

    def __post_init__(self) -> None:
        if not self.alias.strip() or len(self.alias) > MAX_ENTITY_ALIAS_LENGTH:
            raise InvalidValueError("account alias has an invalid length")
        if not self.display_name.strip() or len(self.display_name) > MAX_MERCHANT_LENGTH:
            raise InvalidValueError("aliased account name has an invalid length")


class AccountSelectionMode(StrEnum):
    ALL = "ALL"
    SELECTED = "SELECTED"


@dataclass(frozen=True)
class AccountSelection:
    """Persistent, holder-grounded account scope for subsequent questions."""

    mode: AccountSelectionMode
    account_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is AccountSelectionMode.ALL and self.account_ids:
            raise InvalidValueError("ALL account selection cannot contain account ids")
        if self.mode is AccountSelectionMode.SELECTED and not self.account_ids:
            raise InvalidValueError("SELECTED account selection needs account ids")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise InvalidValueError("account selection cannot contain duplicates")
        if len(self.account_ids) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError(f"at most {MAX_SELECTED_ACCOUNTS} selected accounts")


@dataclass(frozen=True)
class AccountCandidate:
    account_id: UUID
    display_name: str
    kind: str

    def __post_init__(self) -> None:
        if not self.display_name.strip() or not self.kind.strip():
            raise InvalidValueError("account candidate needs display name and kind")


@dataclass(frozen=True)
class CounterpartyAlias:
    """A user phrase grounded to a real, typed other side of a ledger movement."""

    alias: str
    counterparty_name: str
    counterparty_type: CounterpartyType

    def __post_init__(self) -> None:
        if not self.alias.strip() or len(self.alias) > MAX_ENTITY_ALIAS_LENGTH:
            raise InvalidValueError("counterparty alias has an invalid length")
        if not self.counterparty_name.strip() or len(self.counterparty_name) > MAX_MERCHANT_LENGTH:
            raise InvalidValueError("aliased counterparty name has an invalid length")


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
    """Everything this service needs to know about a conversation it does not store.

    Held by whoever is having it, exactly like the five fields were and for the same reason: there
    are no sessions here. What changes is that the client no longer decides what the state means —
    it round-trips one value.
    """

    # Which conversation this is. There are still no sessions and no store: the identifier is
    # minted on the first turn by the edge that maps the payload, travels back to the client with
    # everything else, and comes home on the next turn — the same rule as every field below it.
    #
    # **Nothing in this file, and nothing in the pipeline, may branch on it.** It is not state the
    # reasoning reads; it exists so that N turns of one conversation stop being N unrelated traces.
    # It lives on the conversation rather than beside it because the alternative was stamping it
    # onto fifteen `to_payload` call sites in the controller, and the fifteenth would be the one
    # that forgot — which is the failure mode this whole value replaced.
    #
    # `None` for a conversation nobody mapped from the wire: `IDLE` is a module-level singleton and
    # a default factory here would mint one identifier at import time and hand the same one to
    # every conversation in the process, which is worse than having none.
    conversation_id: UUID | None = None

    active: ActiveWorkflow | None = None
    account_selection: AccountSelection | None = None
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
    # Canonical, source-bound referent. Its wire projection lives in ConversationPayload.
    result_reference: "ResultReference | None" = None
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
    # A two-query calculation cannot fit in `last_plan`. The answered pair and the half-built pair
    # are memory rather than parallel workflows: one lets "¿y el mes pasado?" patch both sides;
    # the other lets a two-word period complete the proportion that asked for it.
    last_ratio: RatioPlan | None = None
    pending_ratio: RatioDraft | None = None
    # The last comparison as one validated value. ``last_plan`` and ``prior_plan`` remain for
    # backwards-compatible single-query follow-ups; this field prevents later comparison turns
    # from having to infer whether those two happen to form a compatible pair.
    last_comparison: ComparisonPlan | None = None
    # User-defined names are memory, not a workflow. They survive every turn and are re-grounded
    # against the authenticated holder's account directory whenever they are used.
    account_aliases: tuple[AccountAlias, ...] = ()
    counterparty_aliases: tuple[CounterpartyAlias, ...] = ()
    # A one-turn anaphora after a completed payment. Keeping only the rail means "otro" can reuse
    # the operation while the new amount and recipient still have to be explicit and grounded.
    recent_payment_rail: Rail | None = None
    # What actually happened, oldest first, bounded. Appended to once per turn and never edited,
    # so unlike every field above it there is no branch deciding what survives — which is what the
    # fields above it kept getting wrong.
    turns: tuple[Turn, ...] = ()
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
        folded_aliases = tuple(alias.alias.casefold().strip() for alias in self.account_aliases)
        if len(self.account_aliases) > MAX_ENTITY_ALIASES:
            raise InvalidValueError("too many account aliases")
        if len(set(folded_aliases)) != len(folded_aliases):
            raise InvalidValueError("account aliases must be unique")
        folded_counterparties = tuple(
            alias.alias.casefold().strip() for alias in self.counterparty_aliases
        )
        if len(self.counterparty_aliases) > MAX_ENTITY_ALIASES:
            raise InvalidValueError("too many counterparty aliases")
        if len(set(folded_counterparties)) != len(folded_counterparties):
            raise InvalidValueError("counterparty aliases must be unique")
        if len(self.turns) > MAX_REMEMBERED_TURNS:
            raise InvalidValueError("too many remembered turns")
        if (
            self.result_reference is not None
            and self.result_reference.source_query is not self.last_plan
        ):
            raise InvalidValueError("a result reference needs its exact source query")

    def with_visual_context(self, context: VisualContext) -> "Conversation":
        """Remember a structural chart context, newest last, without any chart rows."""
        contexts = tuple(item for item in self.visual_contexts if item.chart_id != context.chart_id)
        return replace(self, visual_contexts=(*contexts, context)[-MAX_VISUAL_CONTEXTS:])

    def visual_context(self, chart_id: str | None = None) -> VisualContext | None:
        if not self.visual_contexts:
            return None
        if chart_id is None:
            return self.visual_contexts[-1]
        return next((item for item in self.visual_contexts if item.chart_id == chart_id), None)

    def with_turn(self, turn: Turn) -> "Conversation":
        """Record what this turn did, dropping the oldest once the log is full.

        Truncating rather than refusing: a long conversation is the normal case, and the far end of
        it is what an ellipsis never reaches back to anyway.
        """
        return replace(self, turns=(*self.turns, turn)[-MAX_REMEMBERED_TURNS:])

    def with_account_alias(self, alias: AccountAlias) -> "Conversation":
        """Learn or replace one alias without disturbing the active conversation."""
        key = alias.alias.casefold().strip()
        kept = tuple(
            known for known in self.account_aliases if known.alias.casefold().strip() != key
        )
        return replace(self, account_aliases=(*kept, alias)[-MAX_ENTITY_ALIASES:])

    def with_counterparty_alias(self, alias: CounterpartyAlias) -> "Conversation":
        """Learn or replace a typed entity alias without disturbing the active workflow."""
        key = alias.alias.casefold().strip()
        kept = tuple(
            known for known in self.counterparty_aliases if known.alias.casefold().strip() != key
        )
        return replace(self, counterparty_aliases=(*kept, alias)[-MAX_ENTITY_ALIASES:])

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
        return replace(self, active=active, last_asked=None)

    def paid(self, rail: Rail) -> "Conversation":
        """Close a successful preview and remember what an immediate ``otro`` refers to."""
        return replace(self, active=None, last_asked=None, recent_payment_rail=rail)

    def without_recent_payment(self) -> "Conversation":
        """Consume the short-lived payment referent without disturbing financial memory."""
        return replace(self, recent_payment_rail=None)

    def asking(self, slot: Slot) -> "Conversation":
        """Remember the question this turn asked, whether or not it left a workflow behind."""
        return replace(self, last_asked=slot)

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
        return replace(self, active=UnsupportedCapability(capability=capability), last_asked=None)

    def answering_the_balance(self) -> "Conversation":
        """This turn said how much money there is, so a bare account name is about that.

        The analysis behind it survives: a balance is a turn and not a reset, and asking it in the
        middle of an analysis of last month must not cost the window. So does a half-built Bizum,
        which steps aside for the balance rather than ending.

        **The question left open about the plan does not** (ruling R12). «¿cuánto tengo?» while
        «¿De qué periodo?» waits is a clear new intent, and with the clarification still open behind
        the answer, «en julio» on the next turn ran the restaurants question the holder had walked
        away from — the cage `suspend` already refuses to build for a clarification. The slot asked
        and a half-built proportion go with it, as they do when any other answer supersedes them.

        The account question stays, and that is pinned rather than overlooked: «¿En qué cuenta…?»
        holds a question already complete and owed an answer, and `31-account-scope.yaml` walks away
        from it to a balance and back on purpose. Whether it should go too is a product decision,
        not this rule's.
        """
        keeps_the_account_question = self.last_asked is Slot.ACCOUNT
        return replace(
            self,
            answered_the_balance=True,
            active=None if isinstance(self.active, FinancialClarification) else self.active,
            last_asked=self.last_asked if keeps_the_account_question else None,
            pending_ratio=None,
        )

    def awaiting_retry(self, plan: PlannedQuery) -> "Conversation":
        """Remember the exact validated read that banking could not execute."""
        return replace(self, retry_plan=plan)

    def without_retry(self) -> "Conversation":
        """Abandon a failed read while preserving the rest of the conversation."""
        return replace(self, retry_plan=None)

    def _answering(self) -> "Conversation":
        """What answering a financial question resets, whichever of the three kinds it was.

        One place rather than three. `answered`, `answered_comparison` and `answered_ratio` each
        enumerated this separately and disagreed about it — a comparison forgot the rows a plain
        answer remembered — and nothing in the code said whether the difference was a decision.
        """
        return replace(
            self,
            # A suspended payment belongs to the conversation before this answer and deliberately
            # stepped aside for it. Anything else open is superseded by the answer.
            active=(
                self.active
                if isinstance(self.active, PaymentInProgress) and self.active.suspended
                else None
            ),
            # This read succeeded, so there is nothing left to retry, and the question the turn
            # was answering is closed.
            retry_plan=None,
            last_asked=None,
            # A figure over a period, not a balance.
            answered_the_balance=False,
            # A half-built proportion is abandoned by an answer to something else.
            pending_ratio=None,
            # These three describe the same fact — what the last answer was — so the kind that just
            # ran sets its own and the other two go. Leaving a stale one behind is how a plain
            # question inherited the operands of a comparison two turns earlier.
            last_plan=None,
            last_ratio=None,
            last_comparison=None,
            result_reference=None,
        )

    def answered(self, plan: PlannedQuery) -> "Conversation":
        """Remember a financial answer and close only the workflow that answer resolves.

        A suspended payment belongs to the conversation before this answer and deliberately stepped
        aside for it. It survives; a financial clarification or refusal does not, because the new
        answer supersedes it.
        """
        return replace(
            self._answering(),
            last_plan=plan,
            # Asking the same question twice must not push the plan behind it out of memory.
            prior_plan=self.prior_plan if plan == self.last_plan else self.last_plan,
        )

    def waiting_for_ratio(self, draft: RatioDraft) -> "Conversation":
        """Remember both operands while asking for their shared period."""
        return replace(self, last_asked=Slot.WINDOW, pending_ratio=draft)

    def answered_comparison(self, plan: ComparisonPlan) -> "Conversation":
        """Keep the compatible pair as one value while exposing both legacy plan slots."""
        return replace(
            self._answering(),
            last_plan=plan.latest,
            prior_plan=plan.earlier,
            last_comparison=plan,
        )

    def answered_ratio(self, plan: RatioPlan) -> "Conversation":
        """Put the exact pair in view and close the period question it answered."""
        return replace(
            self._answering(),
            last_plan=plan.part,
            prior_plan=plan.whole,
            last_ratio=plan,
        )

    def without_pending_ratio(self) -> "Conversation":
        """Abandon an unanswered proportion when the user starts another financial subject."""
        return replace(self, pending_ratio=None)

    def suspend(self) -> "Conversation":
        """The active payment steps aside for a turn about something else.

        Only payments suspend. A clarification does not: the question it asked is either answered or
        it is not, and keeping one alive across a change of subject is how "este año" completed a
        plan the user had moved on from.

        Every piece of memory is kept, which is now the default rather than a list. Dropping
        `answered_the_balance` here meant a payment under way silently turned "¿y en la hucha?" back
        into a question about spending, so a balance follow-up worked or not depending on whether a
        Bizum happened to be half-built.
        """
        if not isinstance(self.active, PaymentInProgress):
            return replace(self, active=None)
        return replace(self, active=replace(self.active, suspended=True))


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
    "AccountAlias",
    "AccountCandidate",
    "AccountScopeClarification",
    "AccountSelection",
    "AccountSelectionMode",
    "ActivityOffers",
    "BookingConfirmation",
    "CounterpartyAlias",
    "ActiveWorkflow",
    "Conversation",
    "FinancialClarification",
    "PaymentConfirmation",
    "PaymentInProgress",
    "TransferInProgress",
    "Slot",
    "Turn",
    "UnsupportedCapability",
    "Workflow",
    "slot_for",
    "workflow_of",
]
