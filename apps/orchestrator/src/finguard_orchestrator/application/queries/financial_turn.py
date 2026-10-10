"""What a turn is, what an answer looks like, and the few facts both sides read off one.

**Underneath both the handler and the readers, and that is what it is for.** Every one of the
nineteen readers takes an `AskFinancialQuestionQuery`, so the handler cannot be the module that
defines it: a reader importing the handler would import the module that imports the table that names
the reader. The type moves down a layer instead and both sides import it — a cycle broken by putting
the shared thing underneath, rather than by an import hidden inside a function.

It sits beside `reader_registry` and not inside `readers/`, because it is not about readers. It is
the query this handler answers and the shape of the answer, which is the contract the edge and every
test have always used.

It holds nothing that decides anything. `_clean_turn` folds a sentence, `_direction_of` reads a
list of categories, `_query_for_reader` says which question a reader is being asked about. They are
here because they are read by several reader modules **and** by the handler, and a helper copied
into two of them is how two readers come to disagree about the same sentence — the defect this
whole section exists to stop repeating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.enums import (
    Direction,
)
from finguard_orchestrator.domain.errors import (
    InvalidValueError,
)
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    PresentationContext,
    VisualDataset,
)
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    PlannedQuery,
)
from finguard_orchestrator.domain.models.planning_context import (
    BalanceFocus,
    ComparisonFocus,
    QueryFocus,
    RatioFocus,
    focus_of,
)
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
)
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.domain.services import (
    spanish,
    turn_arbiter,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_arbiter import Arbitration
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan


def _clean_turn(sentence: str) -> str:
    """Fold a short conversational turn and remove only punctuation at its edges."""
    return re.sub(r"[^a-z0-9]+", " ", spanish.folded(sentence)).strip()


def _across_every_account(plan: FinancialPlan) -> FinancialPlan:
    """Widen to the whole holder, and drop the narrowing that is being given up.

    Written once because it was written five times and four of them forgot the second half. A plan
    reaching `_scoped` may already name an account — `merge` rebuilds a follow-up out of the earlier
    plan and the scope survives that — so widening with `replace(plan, across_accounts=True)` alone
    left the account in place. Banking reads that pair as a request for every account of the holder
    while `scoped_to` goes on naming the one that was asked for: a real figure about a wider scope
    than the question had. It is refused outright on that side now, so leaving it here only trades a
    wrong answer for a 422. Widening means widening.
    """
    return replace(plan, across_accounts=True, only_account_id=None, only_account_ids=())


def _starts_new_financial_scope(sentence: str, *, today: date) -> bool:
    """Whether this turn states a complete new question rather than an ellipsis.

    `turn_arbiter.states_a_question_of_its_own`, under the name every reader already imports. The
    rule moved down a layer because the arbitration needs it too — a question of its own is not a
    reply to the one left open — and a domain service cannot read the application; one rule in two
    places is how a reader and the arbitration would come to disagree about the same sentence.
    """
    return turn_arbiter.states_a_question_of_its_own(sentence, today=today)


@dataclass(frozen=True)
class SpokenGroup:
    key: str
    total_minor: int
    movement_count: int
    # Backend-derived value for the requested metric (never recomputed by clients).
    value_minor: int | None = None


@dataclass(frozen=True)
class SpokenPlan:
    """The plan in fields rather than in words, flat like every other Result piece.

    It exists so a follow-up can carry its context back without the client having to
    re-type a sentence — and, more to the point, without the *description* travelling,
    which put a client-supplied string inside the prompt's instructions.
    """

    direction: str
    period: str | None = None
    n: int | None = None
    start: str | None = None
    end: str | None = None
    categories: list[str] = field(default_factory=list)
    excluded_categories: list[str] = field(default_factory=list)
    merchants: list[str] = field(default_factory=list)
    merchant_terms: list[str] = field(default_factory=list)
    excluded_merchants: list[str] = field(default_factory=list)
    counterparties: list[str] = field(default_factory=list)
    excluded_counterparties: list[str] = field(default_factory=list)
    counterparty_types: list[str] = field(default_factory=list)
    group_by: str = "NONE"
    metric: str = "SUM"
    group_limit: int | None = None
    include_internal: bool = False
    internal_only: bool = False
    across_accounts: bool = False
    only_account_id: str | None = None
    only_account_ids: list[str] = field(default_factory=list)
    transaction_ids: list[str] = field(default_factory=list)
    transaction_types: list[str] = field(default_factory=list)
    rank: int | None = None
    result_kind: str | None = None
    query_id: str | None = None
    artifact_id: str | None = None
    view_id: str | None = None


@dataclass(frozen=True)
class SpokenContact:
    """Somebody who can be paid, flat like every Result piece.

    The identifier is here so a client can offer a choice and send back *which* contact rather
    than a name to be interpreted again — which is how money reaches the wrong Marta.
    """

    contact_id: str
    display_name: str
    phone: str


@dataclass(frozen=True)
class SpokenMovement:
    occurred_at: str
    description: str
    # Unsigned, as banking returns it: a query filtered by EXPENSE comes back with positive
    # amounts and the direction beside them. The direction is what makes the sign, and dropping
    # it here is why every expense in the chat rendered as "+22,11 €" — money coming in.
    amount_minor: int
    direction: str
    transaction_id: str | None = None
    currency: str | None = None
    merchant_name: str | None = None
    category: str | None = None
    transaction_type: str | None = None
    counterparty_name: str | None = None
    counterparty_type: str = "UNKNOWN"
    receipt_category: str | None = None


@dataclass(frozen=True)
class AskFinancialQuestionResult:
    """Flat, like every Result: no domain object leaves the application layer.

    `plan` is the plan in words. It is here because an answer whose derivation the user
    cannot inspect is a number to be believed, and this pipeline has a language model in
    it — showing the question it actually asked is the difference between a tool and an
    oracle.

    Two audiences read that line, so it is held twice. `plan` is the ledger's identifiers
    (`answer_writer.describe`): `stream()` records it on the turn, and the turn interpreter's prompt
    quotes the log back to a model whose measurements were taken against exactly that text.
    `plan_in_words` is the same line for the person (`answer_writer.plan_in_words`), and it is what
    the edge sends as the wire's `plan`. A result carrying only `plan` — a line already written in
    Spanish — is shown as it is.
    """

    outcome: str
    text: str
    question: str | None = None
    plan: str | None = None
    plan_in_words: str | None = None
    plan_context: SpokenPlan | None = None
    # Where the conversation is after this turn: at most one active workflow, plus the memory of
    # the last answered question. The client stores it and sends it back without reading it.
    #
    # A domain object leaving this layer, which every other Result field avoids. The exception is
    # deliberate: the alternative is a third representation of the same five states — domain, wire,
    # and a flat one here — kept in sync by hand, and states drifting apart between layers is the
    # failure this type was introduced to end. The query already accepts domain objects coming in.
    conversation: Conversation = IDLE
    currency: str | None = None
    # The figure the answer's own sentence leads with -- `None` when it has no single one, and a
    # client that gets `None` renders the sentence, which was the whole answer either way.
    # `answer_writer.leading_figure` is where that rule is written down and why: the field and the
    # prose are two representations of one decision, and a client showing 2.270,42 € under a
    # sentence saying 7.755,92 € is what happens when they disagree. Never a signed difference --
    # `difference_minor` below carries those.
    total_minor: int | None = None
    # The signed change between two compared figures, `latest - earlier`, in the unit the
    # comparison measured. `None` unless the answer *is* a comparison of two operands, and also on
    # a counted one, where the change is movements rather than money and `_minor` would be a lie
    # about the unit.
    #
    # It exists because `total_minor` was carrying it. «¿He gastado más este mes que el anterior?»
    # answered `total_minor: -189597` beside groups summing to `+189597`, so a client rendering the
    # leading figure showed −1.895,97 € for a question about spending, under a sentence saying
    # "1.895,97 € mayor el mes pasado". Measured over HTTP in
    # `evaluation/planning/RESULTS-E2E.md`, which is where gate 1's *Evidence reconciliation* row
    # took its first real reading and failed 1 of 11 breakdowns.
    #
    # Its presence is also the only thing on the wire that says the rows in `groups` are the two
    # *operands* of a difference rather than *parts* of a total. Nothing else discriminates: the
    # `metric="COMPARISON"` marker stops at `VisualDataset` and never reaches `visualization`,
    # `plan` is prose no client parses, and `outcome` is `PLANNED` either way.
    #
    # Two other shapes were weighed and rejected.
    #
    # **Make `total_minor` the figure the groups add up to.** It would satisfy the reconciliation
    # property by construction, and it puts a number in the field that the sentence never says:
    # 1.000 € this month against 1.895,97 € last month would report 2.895,97 €. That is exactly
    # the failure `leading_figure` exists to prevent, so it trades a wrong sign for a wrong figure.
    #
    # **Take the operands out of `groups`.** That also satisfies the property, while leaving
    # `total_minor` a signed difference under a name that says total -- it silences the measurement
    # and keeps the client bug. And it silences it in the worst available way: `reconcilable()`
    # calls an empty breakdown *not inspectable*, so the answer would leave the denominator too and
    # "breakdowns reconciled" would fall from 11 to 10 with the failure gone from both halves of
    # the ratio. That is the vacuous zero `RESULTS-E2E.md` exists to prevent, one field lower.
    # Keeping the rows is also what a screen needs: the web renders them in `AnswerEvidence`, the
    # only place a person sees the two windows as data.
    #
    # **Teach the property that a comparison is a third shape, and change nothing here.** The
    # cheapest, and it concedes that one field means "a total", "a signed difference" and "a signed
    # net" depending on the answer -- the shape `docs/next.md` lists as a known defect under
    # "Discriminated responses". The property does have to learn: `CUMULATIVE` groups are running
    # totals and cannot sum to their total either, correctly, so its assumption was already wrong
    # on a path nobody disputes. But it had nothing on the wire to key on until this field existed.
    #
    # What it can key on now is a tag, not a hint, and it narrows one check rather than dropping
    # both: with `difference_minor` present the rows are operands and their amounts do not add up
    # to a total, while their *counts* still must -- 28 = 28 held on the measured case and holds on
    # every path here, because `movement_count` is the sum of both operands' counts.
    difference_minor: int | None = None
    movement_count: int | None = None
    # Which of the holder's accounts a fully-read transfer is going to, once one is settled.
    #
    # This handler reads and never writes: resolving the account is reading, and staging money is a
    # `payments:prepare` scope the questions route does not have. So the read comes out here and the
    # route above decides whether it may be turned into a preview. Before this, the account was
    # resolved, the amount was resolved, and the answer was a sentence telling the holder to go and
    # do it again on a screen — with everything needed to offer it already in hand.
    transfer_to: str | None = None
    groups: list[SpokenGroup] = field(default_factory=list)
    movements: list[SpokenMovement] = field(default_factory=list)
    # Filled only by a question about who can be paid. Data as well as prose, so a client
    # can draw one button per contact instead of parsing names out of a paragraph.
    contacts: list[SpokenContact] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    explicit_visualization: bool = False
    visual_dataset: VisualDataset | None = None
    # Which chart was chosen for `visual_dataset`, and by whom. `None` is "no chart", and it is
    # the answer for a dataset the floor declined as much as for one nobody asked to draw.
    #
    # It is here because the decision used to be taken inside `llm_mapper`, twice per turn -- once
    # for the response DTO and again for the conversational state -- and a mapper is not somewhere
    # a model call can live. The application decides; the mapper renders what it is handed.
    visual_decision: VisualDecision | None = None
    # What this turn wanted from the chart it followed, in service vocabulary (kind + claimant) --
    # never the sentence. `None` when no chart was in context; set in `stream()`'s own convergence,
    # the same one that decides `visual_decision`, for the same reason: forty branches build a
    # result and this is one fact common to all of them, not one any branch is asked to know.
    visual_follow_up: VisualFollowUp | None = None
    # Internal, typed source for a save candidate. Never serialized as financial data.
    metric_definition: FinancialPlan | ComparisonPlan | RatioPlan | None = None
    # R14 (Wave 4.5, closes D-4.5-6/M-2): True when `metric_definition` came from
    # `conversation.last_plan` (`stream()`'s own back-fill, `~:630-633`) rather than from this
    # turn's own producer. A reader that only needs *a* plan (the save candidate, a drill's origin
    # check) keeps reading `metric_definition` regardless -- a borrowed plan is still a real plan,
    # just not necessarily the one that drew THIS chart. A reader that would NAME the plan in
    # prose -- the presenter's spoken noun -- must not when this is True, because naming a plan
    # nobody ran this turn is exactly M-2 (`branch-re-review.md`'s `probe_m2`).
    metric_definition_borrowed: bool = False
    # F-H3 (Wave 3 hardening, fix round): True when `metric_definition` was built by one of the
    # continuation readers (`contextual_follow_up_plan`, or one of the five comparison-continuity
    # readers) -- a reshape, regroup, split or refinement of whatever plan/comparison the
    # conversation already carried, as opposed to a fresh plan the sentence alone produced. This is
    # the causal fact `llm_mapper._remember_visual_context` now reads to decide `derived_from`: the
    # reader that built the plan states the lineage, and the mapper's own structural comparison
    # (`_same_lineage`) stays only as a sanity check on top of it, never the sole test. Two turns
    # that coincidentally ask about the same bare window are not lineage; a chart the reader
    # actually reshaped is, whatever verb the sentence used to ask for it.
    plan_continues_conversation: bool = False
    # A fingerprint of `metric_definition` (back-fill included), taken in `stream()` and fixed for
    # this turn -- the value `_remember_visual_context` seals onto a chart drawn THIS turn. Sealed
    # as data because 4.3's drill binds to it; not read by the presenter (see
    # `metric_definition_borrowed` above -- a digest of the same field cannot tell borrowed from
    # owned, only `stream()` itself can, which is why this stays a fingerprint and that is a flag).
    # `None` when `metric_definition` is not a `FinancialPlan` at all (a comparison, a ratio, or no
    # plan ran this turn).
    visual_plan_digest: str | None = None


@dataclass(frozen=True)
class AskFinancialQuestionQuery(Query[AskFinancialQuestionResult]):
    sentence: str
    account_id: UUID
    today: date
    # The zone `today` was measured in. It travels with the date because neither describes a
    # window alone, and it belongs to the holder rather than the deployment: between
    # midnight and 01:00 peninsular time, today in the Canaries is yesterday in Madrid.
    reporting_timezone: str = "Europe/Madrid"
    # Where the conversation was before this turn, echoed back by the caller. Held by whoever is
    # having it rather than by this service, and already typed by the time it arrives here — the
    # edge rebuilt it through the domain, so nothing in here has to believe a string.
    conversation: Conversation = IDLE
    # What this turn was read as, when the caller already knows.
    #
    # The edge has to classify the sentence before it can decide whether to dispatch this query at
    # all — a payment order never reaches here. Left out, the classification happened again inside,
    # and two readings of one sentence is a thing that can disagree with itself. It already did:
    # the edge and the handler each grew their own exception for "¿a quién puedo hacer un bizum?"
    # and only one of them got it.
    #
    # It is *not* a client field. Nothing deserialises into it; the edge fills it from the
    # interpreter it just asked, and a caller that leaves it out gets the same reading computed
    # here. That keeps the handler usable on its own — every test and the corpus call it that way —
    # without letting a second reading exist on the path a user is actually on.
    intent: Intent | None = None
    analysis: TurnAnalysis | None = None
    # What kind of turn this is — `turn_arbiter.arbitrate`, decided once at the edge from the
    # analysis above, for the same reason the analysis travels: the edge already acted on it (an
    # order to pay never reaches here), and a second arbitration in here is a second opinion that
    # could disagree with the first. Not a client field either. Left out, the handler reads the one
    # kind that needs no analysis — a reset is the sentence alone — first, and arbitrates the rest
    # itself once it has the analysis, with the same function: a reply to the question left open is
    # one whoever built the query.
    arbitration: Arbitration | None = None
    # How this answer will be consumed. It belongs to the execution request rather than to the
    # dataset profile. Kept last so adding it does not shift established positional callers.
    presentation: PresentationContext = DEFAULT_PRESENTATION

    def __post_init__(self) -> None:
        if (
            self.intent is not None
            and self.analysis is not None
            and self.intent is not self.analysis.intent
        ):
            raise InvalidValueError("intent and turn analysis disagree")


def _same_window(left: FinancialPlan, right: FinancialPlan) -> bool:
    return (left.period, left.date_range) == (right.period, right.date_range)


_READER_RETURN_FROM_BALANCE: Final = re.compile(
    r"^(?:(?:vale|bueno|y)(?:\s+|\s*[,;:]\s*))?"
    r"(?:(?:vuelve|volvamos)\s+(?:a\s+)?(?:la\s+consulta\s+anterior|lo\s+anterior)"
    r"|volviendo\s+a\s+(?:la\s+consulta\s+anterior|lo\s+de\s+antes))"
    r"\s*[,;:]\s*(?P<request>\S(?:.*\S)?)$"
)


@dataclass(frozen=True)
class _QueryForReader:
    plan: PlannedQuery | None
    wording: str
    returned_from_balance: bool = False


def _query_for_reader(query: AskFinancialQuestionQuery) -> _QueryForReader:
    """Select query memory for readers, including an explicit return from a balance aside."""
    focus = focus_of(query.conversation)
    if isinstance(focus, QueryFocus):
        return _QueryForReader(focus.current, query.sentence)
    if isinstance(focus, BalanceFocus) and focus.suspended_query is not None:
        returned = _READER_RETURN_FROM_BALANCE.fullmatch(spanish.folded(query.sentence))
        if returned is not None:
            return _QueryForReader(
                focus.suspended_query,
                returned.group("request"),
                returned_from_balance=True,
            )
    return _QueryForReader(None, query.sentence)


# Categories that are money coming in. Read from the sentence's category rather than guessed from
# its verb: "¿qué intereses me ha dado la hucha?" has no verb of spending or earning that this
# service reads, and interest is income by what it *is*.
_INCOME_CATEGORIES: Final = frozenset({"SALARY", "INTEREST"})


def _direction_of(categories: tuple[str, ...]) -> Direction:
    if all(category in _INCOME_CATEGORIES for category in categories):
        return Direction.INCOME
    if any(category in _INCOME_CATEGORIES for category in categories):
        # A salary and a supermarket in one question. ALL rather than picking, because a signed
        # total over both is the only figure that is not wrong about one of them.
        return Direction.ALL
    return Direction.EXPENSE


def _window_of(plan: PlannedQuery | None) -> str | None:
    """A window as one short string, for a log line.

    `None` when there is no plan to read one off, which is itself the interesting value: it is
    what "nothing was inherited" looks like in the record.
    """
    if plan is None:
        return None
    if plan.period is not None:
        named = plan.period.period.value
        return f"{named}:{plan.period.n}" if plan.period.n else named
    assert plan.date_range is not None  # the plan's own invariant guarantees one
    return f"{plan.date_range.start}..{plan.date_range.end}"


def _a_plan_is_in_view(query: AskFinancialQuestionQuery) -> bool:
    return isinstance(query.conversation.last_plan, FinancialPlan)


def _the_pair_in_view(conversation: Conversation) -> ComparisonPlan | None:
    """The two periods on screen: a comparison's pair, or the pair a percentage change was taken
    over (validation fix 3, G4).

    A percentage change is the same two reads with arithmetic on top (`RatioPlan` checks its
    operands as a `ComparisonPlan` does), so after «y eso en %?» the comparison is still what the
    holder is looking at. Read as no comparison, «¿y de ocio?» after it asked the period and lost
    June and July both (impro-comparar-meses 4). `whole` is the earlier side, `part` the later.
    """
    focus = focus_of(conversation)
    if isinstance(focus, ComparisonFocus):
        return focus.value
    if isinstance(focus, RatioFocus) and focus.value.kind is RatioKind.PERCENT_CHANGE:
        return ComparisonPlan.between(focus.value.whole, focus.value.part)
    return None
