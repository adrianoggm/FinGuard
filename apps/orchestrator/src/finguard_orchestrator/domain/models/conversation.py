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
    MAX_ENTITY_ALIAS_LENGTH,
    MAX_ENTITY_ALIASES,
    MAX_MERCHANT_LENGTH,
    MAX_SELECTED_TRANSACTIONS,
)
from finguard_orchestrator.domain.enums import CounterpartyType, GroupBy, Rail
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# What a sentence can turn into and a follow-up can inherit from. Declared here as well as on
# the planning result because both need the pair named, and importing one from the other would
# tie a conversation to a planner.
type PlannedQuery = FinancialPlan | TopMovements

# MAX_RANK was declared here as well as in `top_movements`, at 100 in both, with the DTO importing
# this one and the value object enforcing the other. It lives in `domain/constants` now, with every
# other limit the wire has to agree with banking about.


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
    # A category the last answer explicitly singled out (for example the largest group). The plan
    # alone remembers that a category breakdown was run, but not which row the answer called
    # "the one where you spent most". This small referent makes "esa categorÃ­a" resolvable without
    # storing financial results or asking a model to guess a row.
    referenced_category: str | None = None
    # The concrete row the last answer put in focus. Unlike `referenced_category`, this also
    # covers merchant and account breakdowns, while keeping only an identifier/name and never the
    # amount shown beside it. Both fields are one optional value and therefore travel together.
    referenced_group_by: GroupBy | None = None
    referenced_group_key: str | None = None
    # Exact ledger rows displayed as evidence by the last aggregate answer. This lets a later
    # "esos movimientos" select what was actually on screen instead of rerunning the broad query.
    referenced_transaction_ids: tuple[UUID, ...] = ()
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

    def __post_init__(self) -> None:
        if (self.referenced_group_by is None) != (self.referenced_group_key is None):
            raise InvalidValueError("a referenced group needs both its dimension and key")
        if self.referenced_group_by not in {
            None,
            GroupBy.CATEGORY,
            GroupBy.MERCHANT,
            GroupBy.COUNTERPARTY,
            GroupBy.ACCOUNT,
        }:
            raise InvalidValueError(
                "only category, merchant, counterparty and account rows can be referenced"
            )
        if self.referenced_group_key is not None:
            if not self.referenced_group_key.strip():
                raise InvalidValueError("a referenced group key cannot be blank")
            if len(self.referenced_group_key) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError("referenced group key is too long")
        if len(set(self.referenced_transaction_ids)) != len(self.referenced_transaction_ids):
            raise InvalidValueError("referenced transaction ids must be unique")
        if len(self.referenced_transaction_ids) > MAX_SELECTED_TRANSACTIONS:
            raise InvalidValueError("too many referenced transaction ids")
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

        Everything else about the conversation survives: a balance is a turn and not a reset, and
        asking it in the middle of an analysis of last month must not cost the window.
        """
        return replace(self, answered_the_balance=True)

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
            # Replaced by whatever this answer put in focus, never inherited: a referent from the
            # previous result points at rows this answer never showed.
            referenced_category=None,
            referenced_group_by=None,
            referenced_group_key=None,
            referenced_transaction_ids=(),
        )

    def answered(
        self,
        plan: PlannedQuery,
        *,
        referenced_category: str | None = None,
        referenced_group_by: GroupBy | None = None,
        referenced_group_key: str | None = None,
        referenced_transaction_ids: tuple[UUID, ...] = (),
    ) -> "Conversation":
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
            referenced_category=referenced_category,
            referenced_group_by=referenced_group_by,
            referenced_group_key=referenced_group_key,
            referenced_transaction_ids=referenced_transaction_ids,
        )

    def with_references(
        self,
        *,
        group_by: GroupBy | None = None,
        group_key: str | None = None,
        transaction_ids: tuple[UUID, ...] | None = None,
    ) -> "Conversation":
        """Focus an already answered result without changing the query behind it."""
        return replace(
            self,
            referenced_category=(
                group_key if group_by is GroupBy.CATEGORY else self.referenced_category
            ),
            referenced_group_by=group_by,
            referenced_group_key=group_key,
            referenced_transaction_ids=(
                self.referenced_transaction_ids if transaction_ids is None else transaction_ids
            ),
        )

    def waiting_for_ratio(self, draft: RatioDraft) -> "Conversation":
        """Remember both operands while asking for their shared period."""
        return replace(self, last_asked=Slot.WINDOW, pending_ratio=draft)

    def answered_comparison(
        self, plan: ComparisonPlan, *, referenced_category: str | None = None
    ) -> "Conversation":
        """Keep the compatible pair as one value while exposing both legacy plan slots."""
        return replace(
            self._answering(),
            last_plan=plan.latest,
            prior_plan=plan.earlier,
            referenced_category=referenced_category,
            last_comparison=plan,
        )

    def answered_ratio(self, plan: RatioPlan) -> "Conversation":
        """Put the exact pair in view and close the period question it answered."""
        return replace(
            self._answering(),
            last_plan=plan.part,
            prior_plan=plan.whole,
            referenced_category=(
                plan.part.categories[0] if len(plan.part.categories) == 1 else None
            ),
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
    "AccountAlias",
    "CounterpartyAlias",
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
