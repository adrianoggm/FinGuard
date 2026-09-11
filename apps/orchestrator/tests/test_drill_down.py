"""Task 4: the handler acts on a `DRILL_DOWN` classification -- the plan in view narrowed to the
bar named, regrouped by merchant, run once; an ambiguous bar asks instead of guessing; anything
else falls through to today's path, unnarrowed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

from support import episodes
from support.visual_planners import BREAKDOWN_PROFILE
from test_decisions_call_sites import (
    ACCOUNT,
    TODAY,
    FakeAccounts,
    FakeContacts,
    FakePlanner,
    NoCards,
    turn_for,
)

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
)
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers.drill_down import (
    DrillAsk,
    DrillDown,
    read_drill_down,
)
from finguard_orchestrator.domain.constants import MAX_SELECTED_TRANSACTIONS
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import explicit_facts
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.plan_digest import plan_digest
from finguard_orchestrator.domain.services.window_words import whole_month
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def _movement(*, merchant: str, category: str, amount_minor: int) -> Movement:
    return Movement(
        occurred_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        description=merchant,
        amount_minor=amount_minor,
        direction="OUT",
        merchant_name=merchant,
        category=category,
        transaction_id=str(uuid4()),
    )


class RecordingLedger:
    """Every `run(...)` call's plan and the kwargs it ran with; one canned merchant breakdown.

    `**kwargs` rather than a named `movements` parameter: the production seam
    (`_RecordedLedger.run`) omits `movements` from the forwarded call entirely when it is `None` --
    a `movements=None` that reached every adapter and fake would mean something different from "not
    asked" -- and a fixed parameter with its own default could never tell the two apart.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[FinancialPlan, dict[str, object]]] = []

    async def run(
        self,
        plan: FinancialPlan,
        *,
        account_id: object,
        today: object,
        reporting_timezone: str = "Europe/Madrid",
        **kwargs: object,
    ) -> QueryAnswer:
        self.calls.append((plan, kwargs))
        return QueryAnswer(
            currency="EUR",
            total_minor=18_000,
            movement_count=3,
            groups=(Group("Repsol", 12_000, 2), Group("Renfe", 6_000, 1)),
            movements=(
                _movement(merchant="Repsol", category="TRANSPORT_FUEL", amount_minor=-6_000),
                _movement(merchant="Repsol", category="TRANSPORT_FUEL", amount_minor=-6_000),
                _movement(merchant="Renfe", category="TRANSPORT_PUBLIC", amount_minor=-6_000),
            ),
        )

    async def ranked(
        self, ranking: object, *, account_id: object, today: object, reporting_timezone: str
    ) -> None:
        return None


# The fallback plan a real `ContextualQueryPlannerPort` would have produced -- deliberately unlike
# any drilled plan, so a test that sees it on the ledger call knows the drill never triggered and
# today's path ran, unnarrowed.
FALLBACK_PLAN = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))

MONTH_PLAN = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_N_MONTHS, n=3),
    categories=("TRANSPORT_PUBLIC", "TRANSPORT_FUEL"),
    group_by=GroupBy.MONTH,
    only_account_id=ACCOUNT,
)
# The full three months the chart's own rows cover -- wide enough that intersecting a drilled
# month against it is a no-op for every test below that is not itself testing the intersection
# (C-1's own probes construct a narrower window directly).
MONTH_CONTEXT = VisualContext(
    "chart-months",
    SPEC,
    BREAKDOWN_PROFILE,
    labels=("2026-08", "2026-07", "2026-06"),
    ranked=True,
    window=AbsoluteRange(date(2026, 6, 1), date(2026, 8, 31)),
    plan_digest=plan_digest(MONTH_PLAN),
)
MONTH_CONVERSATION = Conversation(last_plan=MONTH_PLAN, visual_contexts=(MONTH_CONTEXT,))


def _handler_with_planner(
    plan: FinancialPlan, ledger: RecordingLedger
) -> tuple[AskFinancialQuestionHandler, FakePlanner]:
    """`handler_for`'s own build, kept apart so a test can see whether the *planner* -- not just
    the ledger -- was the thing asked: proof that a turn fell all the way through to today's path
    rather than being drilled, independent of the window-inheritance every planned turn already
    carries (`inherited_window`, unrelated to this task)."""
    planner = FakePlanner(PlanningResult.planned(plan))
    handler = AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts(), cards=NoCards()
    )
    return handler, planner


# --- the handler branch, through a whole turn --------------------------------------------------


async def test_a_named_month_drills_into_the_bar_it_names() -> None:
    ledger = RecordingLedger()
    result, _ = await turn_for(
        "¿Por qué agosto es tan alto?",
        FALLBACK_PLAN,
        ledger=ledger,
        conversation=MONTH_CONVERSATION,
    )

    assert len(ledger.calls) == 1
    plan, kwargs = ledger.calls[0]
    # TODAY (2026-08-04) falls inside the named month, so M2's clamp applies here too: the window
    # this turn ran with ends today, not on the 31st.
    assert plan.date_range == AbsoluteRange(date(2026, 8, 1), TODAY)
    assert plan.period is None
    assert plan.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert plan.group_by is GroupBy.MERCHANT
    assert plan.group_limit is None
    assert plan.only_account_id == ACCOUNT
    assert kwargs["movements"] == MAX_SELECTED_TRANSACTIONS

    assert result.outcome == "PLANNED"
    assert len(result.movements) == 3
    reference = result.conversation.result_reference
    assert reference is not None
    assert len(reference.transaction_ids) == 3
    assert result.visual_dataset is not None
    assert len(result.visual_dataset.rows) == 2


async def test_the_rank_reading_drills_into_the_tallest_bar() -> None:
    ledger = RecordingLedger()
    result, _ = await turn_for(
        "¿Qué movimientos explican ese pico?",
        FALLBACK_PLAN,
        ledger=ledger,
        conversation=MONTH_CONVERSATION,
    )

    assert len(ledger.calls) == 1
    plan, _ = ledger.calls[0]
    # Same clamp as above: TODAY sits inside August.
    assert plan.date_range == AbsoluteRange(date(2026, 8, 1), TODAY)
    assert plan.group_by is GroupBy.MERCHANT
    assert result.outcome == "PLANNED"


async def test_an_unranked_chart_asks_instead_of_guessing() -> None:
    ledger = RecordingLedger()
    conversation = replace(
        MONTH_CONVERSATION,
        visual_contexts=(replace(MONTH_CONTEXT, ranked=False),),
    )

    result, _ = await turn_for(
        "¿Qué movimientos explican ese pico?",
        FALLBACK_PLAN,
        ledger=ledger,
        conversation=conversation,
    )

    assert ledger.calls == []
    assert result.outcome == "DRILL_AMBIGUOUS"
    assert result.text == "¿A qué te refieres: agosto de 2026, julio de 2026 o junio de 2026?"
    # Unchanged apart from the turn log every outcome gets appended to it (`handle`'s own
    # bookkeeping, unrelated to this task): no slot opened, nothing else on the conversation moved.
    assert replace(result.conversation, turns=()) == conversation


async def test_an_untrusted_chart_is_not_drilled() -> None:
    ledger = RecordingLedger()
    handler, planner = _handler_with_planner(FALLBACK_PLAN, ledger)
    sentence = "¿Por qué agosto es tan alto?"
    conversation = replace(
        MONTH_CONVERSATION,
        visual_contexts=(replace(MONTH_CONTEXT, trusted=False, trust_reason="content"),),
    )

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
    )

    # The planner -- not the drill reader -- was asked: today's path, unnarrowed. (The window it
    # ran with may still differ from `FALLBACK_PLAN`'s own -- `inherited_window` silently prefers
    # the conversation's window over the planner's whenever the sentence names no window of its
    # own, and that is true of every planned turn, not particular to a drill.)
    assert planner.asked == sentence
    assert len(ledger.calls) == 1
    plan, _ = ledger.calls[0]
    assert plan.group_by is not GroupBy.MERCHANT
    assert result.outcome == "PLANNED"


async def test_a_category_grouped_plan_is_not_drilled_by_a_month_label() -> None:
    """Feasibility admits CATEGORY (it is one of the two drillable groupings), so this plan clears
    the digest and feasibility gates and reaches resolution -- the reader still refuses there,
    because the label the chart's own month-shaped labels resolve to is not the kind of label a
    CATEGORY grouping can be narrowed by."""
    ledger = RecordingLedger()
    handler, planner = _handler_with_planner(FALLBACK_PLAN, ledger)
    sentence = "¿Por qué agosto es tan alto?"
    category_plan = replace(MONTH_PLAN, group_by=GroupBy.CATEGORY)
    conversation = replace(
        MONTH_CONVERSATION,
        last_plan=category_plan,
        visual_contexts=(replace(MONTH_CONTEXT, plan_digest=plan_digest(category_plan)),),
    )

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
    )

    assert planner.asked == sentence
    assert len(ledger.calls) == 1
    plan, _ = ledger.calls[0]
    assert plan.group_by is not GroupBy.MERCHANT
    assert result.outcome == "PLANNED"


async def test_a_breakdown_drills_into_the_category_it_names() -> None:
    ledger = RecordingLedger()
    breakdown_plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=3),
        group_by=GroupBy.CATEGORY,
        only_account_id=ACCOUNT,
    )
    context = VisualContext(
        "chart-categories",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("RESTAURANTS", "TRANSPORT_FUEL", "GROCERIES"),
        ranked=True,
        plan_digest=plan_digest(breakdown_plan),
    )
    conversation = Conversation(last_plan=breakdown_plan, visual_contexts=(context,))

    result, _ = await turn_for(
        "¿Por qué la gasolina es tan cara?",
        FALLBACK_PLAN,
        ledger=ledger,
        conversation=conversation,
    )

    assert len(ledger.calls) == 1
    plan, _ = ledger.calls[0]
    assert plan.categories == ("TRANSPORT_FUEL",)
    assert plan.group_by is GroupBy.MERCHANT
    assert plan.period == breakdown_plan.period
    assert result.outcome == "PLANNED"


async def test_an_ordinary_question_still_asks_for_no_movements_cap() -> None:
    ledger = RecordingLedger()
    result, _ = await turn_for(
        "¿Cuánto he gastado este mes?",
        FALLBACK_PLAN,
        ledger=ledger,
    )

    assert len(ledger.calls) == 1
    _, kwargs = ledger.calls[0]
    assert "movements" not in kwargs
    assert result.outcome == "PLANNED"


# --- `read_drill_down`, directly -----------------------------------------------------------

# Outside the drilled month (August 2026): every bare call below that is not itself testing M2's
# clamp uses this, so the window it gets back is never narrowed and the pre-M2 expectations --
# written for a plan `whole_month` alone -- still hold.
AFTER_AUGUST = date(2026, 9, 10)


def _month_conversation(*, metric: Metric) -> Conversation:
    plan = replace(MONTH_PLAN, metric=metric)
    context = replace(MONTH_CONTEXT, plan_digest=plan_digest(plan))
    return Conversation(last_plan=plan, visual_contexts=(context,))


def test_an_average_plan_drills_with_the_sum_metric() -> None:
    conversation = _month_conversation(metric=Metric.AVERAGE)
    drill = read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillDown)
    assert drill.plan.metric is Metric.SUM


def test_a_count_plan_keeps_the_count_metric() -> None:
    conversation = _month_conversation(metric=Metric.COUNT)
    drill = read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillDown)
    assert drill.plan.metric is Metric.COUNT


def test_a_drill_into_the_month_in_progress_ends_today() -> None:
    """M2: the calendar month is still running when `today` falls inside it -- the window this
    drill runs with ends there, not on the 31st (design §3.3: never widen the window silently)."""
    drill = read_drill_down(
        "¿Por qué agosto es tan alto?", MONTH_CONVERSATION, today=date(2026, 8, 4)
    )

    assert isinstance(drill, DrillDown)
    assert drill.plan.date_range == AbsoluteRange(date(2026, 8, 1), date(2026, 8, 4))
    assert drill.plan.period is None


def test_a_drill_into_a_finished_month_names_the_whole_of_it() -> None:
    """M2's other half: once `today` has moved past the month, the window is the whole of it --
    the clamp only ever narrows, it never widens past the calendar month either."""
    drill = read_drill_down(
        "¿Por qué agosto es tan alto?", MONTH_CONVERSATION, today=date(2026, 9, 10)
    )

    assert isinstance(drill, DrillDown)
    assert drill.plan.date_range == whole_month(2026, 8)


def test_an_ambiguous_reference_asks_by_name() -> None:
    conversation = replace(
        MONTH_CONVERSATION,
        visual_contexts=(replace(MONTH_CONTEXT, ranked=False),),
    )

    drill = read_drill_down("¿Qué movimientos explican ese pico?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillAsk)
    assert drill.text == "¿A qué te refieres: agosto de 2026, julio de 2026 o junio de 2026?"


def test_five_unranked_candidates_asks_for_the_label_instead() -> None:
    """M4: `_ask` names up to four alternatives in the chart's rank order; a fifth candidate means
    naming none of them and asking for the label instead."""
    five_month_context = replace(
        MONTH_CONTEXT,
        labels=("2026-08", "2026-07", "2026-06", "2026-05", "2026-04"),
        ranked=False,
    )
    conversation = Conversation(last_plan=MONTH_PLAN, visual_contexts=(five_month_context,))

    drill = read_drill_down("¿Qué movimientos explican ese pico?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillAsk)
    assert drill.text == "¿A qué barra te refieres? Dime su etiqueta."


def test_reshaped_refuses_period_average_beside_a_merchant_grouping() -> None:
    """Fix round 1 (I-1/M1): `read_drill_down` used to carry a guard for this exact pair --
    `explicit_facts.reshaped`'s own docstring names `PERIOD_AVERAGE`/`CUMULATIVE` beside a merchant
    grouping as its one refusal -- and the guard was removed because it could never fire: the
    ternary two lines above it always normalises the metric to `SUM`/`COUNT` before this call, and
    the domain only couples a metric to a grouping for `PERIOD_AVERAGE`/`CUMULATIVE`. This is
    `reshaped` itself, called with the raw, unnormalised metric, proving the pair the removed guard
    named really does refuse -- so this test is now the regression that would notice the day the
    domain grows a coupling `read_drill_down`'s normalisation no longer shields against.
    """
    plan = replace(MONTH_PLAN, metric=Metric.PERIOD_AVERAGE)

    result = explicit_facts.reshaped(plan, group_by=GroupBy.MERCHANT, metric=Metric.PERIOD_AVERAGE)

    assert result.group_by is GroupBy.MONTH
    assert result is plan


def test_a_plan_that_is_not_a_financial_plan_is_not_drilled() -> None:
    conversation = replace(MONTH_CONVERSATION, last_plan=None)

    assert read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST) is None


def test_no_visual_contexts_is_not_drilled() -> None:
    conversation = Conversation(last_plan=MONTH_PLAN)

    assert read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST) is None


# --- Fix wave: C-1 (never past the chart's own window), I-1 (bound to the chart's own plan), I-4
# --- (feasibility before ambiguity) --------------------------------------------------------------


def test_the_reviewers_c1_probe_never_speaks_past_the_charts_own_window() -> None:
    """The reviewer's own probe: a plan whose `date_range` is a slice of two calendar months (10
    June - 20 July), grouped by MONTH, with a chart whose own `window` is that same slice and whose
    labels are July and June -- drilling into July must never speak past 20 July, the day the
    chart's own rows stop, however far `today` has since moved."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 10), date(2026, 7, 20)),
        group_by=GroupBy.MONTH,
        only_account_id=ACCOUNT,
    )
    context = VisualContext(
        "chart-slice",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("2026-07", "2026-06"),
        ranked=True,
        window=AbsoluteRange(date(2026, 6, 10), date(2026, 7, 20)),
        plan_digest=plan_digest(plan),
    )
    conversation = Conversation(last_plan=plan, visual_contexts=(context,))

    drill = read_drill_down("¿Por qué julio es tan alto?", conversation, today=date(2026, 9, 10))

    assert isinstance(drill, DrillDown)
    assert drill.plan.date_range == AbsoluteRange(date(2026, 7, 1), date(2026, 7, 20))


def test_a_month_chart_with_no_known_window_is_not_drilled() -> None:
    """C-1's other failure mode: a MONTH-grouped plan whose chart carries no `window` at all (an
    older client's echo, or a dataset `_answered_window` could not resolve) has nothing to
    intersect the label against, and refuses rather than falling back to the calendar month
    alone."""
    context = replace(MONTH_CONTEXT, window=None)
    conversation = replace(MONTH_CONVERSATION, visual_contexts=(context,))

    assert read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST) is None


async def test_i1_a_different_plan_in_view_is_not_drilled_by_a_chart_it_did_not_draw() -> None:
    """I-1, end to end: the newest chart is a month chart, but `last_plan` is a genuinely different
    plan (different categories, a different digest) -- the chart is not this plan's, so the reader
    refuses and the planner answers as it always does for today's path."""
    ledger = RecordingLedger()
    different_plan = replace(MONTH_PLAN, categories=("GROCERIES",))
    handler, planner = _handler_with_planner(FALLBACK_PLAN, ledger)
    sentence = "¿Por qué marzo es tan alto?"
    conversation = replace(MONTH_CONVERSATION, last_plan=different_plan)

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
    )

    assert planner.asked == sentence
    assert len(ledger.calls) == 1
    plan, _ = ledger.calls[0]
    assert plan.group_by is not GroupBy.MERCHANT
    assert result.outcome == "PLANNED"


def test_i1_the_reader_refuses_a_chart_bound_to_a_different_plan() -> None:
    """I-1, direct: the chart's own digest names plan A; the conversation's `last_plan` is plan
    B -- unrelated plans, so the chart is provably not this turn's."""
    plan_a = MONTH_PLAN
    plan_b = replace(MONTH_PLAN, categories=("GROCERIES",))
    context = replace(MONTH_CONTEXT, plan_digest=plan_digest(plan_a))
    conversation = Conversation(last_plan=plan_b, visual_contexts=(context,))

    assert read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST) is None


def test_i1_a_client_edited_last_plan_is_not_drilled() -> None:
    """I-1: the same shape as above, from the more realistic direction -- a holder's own client
    echoed `last_plan` with one filter changed underneath a chart that was sealed for the
    unedited plan. The digest no longer matches, so the chart is refused rather than narrowed."""
    edited = replace(MONTH_PLAN, categories=("GROCERIES",))
    conversation = replace(MONTH_CONVERSATION, last_plan=edited)

    assert read_drill_down("¿Por qué agosto es tan alto?", conversation, today=AFTER_AUGUST) is None


def test_i4_a_merchant_grouped_plan_with_tied_labels_is_never_asked_about() -> None:
    """I-4: feasibility refuses a MERCHANT grouping before `resolve_reference` ever runs, so a tied
    pair of merchant labels never produces the ask the reviewer's own probe found -- «¿A qué te
    refieres: Repsol o Renfe?» over a chart that could never be drilled whatever the reply."""
    merchant_plan = replace(MONTH_PLAN, group_by=GroupBy.MERCHANT)
    context = VisualContext(
        "chart-merchants",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("Repsol", "Renfe"),
        ranked=False,
        plan_digest=plan_digest(merchant_plan),
    )
    conversation = Conversation(last_plan=merchant_plan, visual_contexts=(context,))

    drill = read_drill_down("¿Qué movimientos explican ese pico?", conversation, today=AFTER_AUGUST)

    assert drill is None


def test_m9_two_unknown_labels_get_the_generic_ask_instead_of_being_echoed() -> None:
    """M9 (security, fix wave): a candidate `_candidate_name` cannot recognise -- neither a month
    nor a category `category_words.words_for` glosses -- is never echoed into the question raw.
    The reachable source past the seal is the ledger's own data (a merchant or counterparty name a
    payee chose for itself), so this pins the CATEGORY-drillable case with two candidates neither
    is a category this vocabulary knows: the generic text, not «¿A qué te refieres: ... o ...?»
    with either candidate spoken verbatim."""
    category_plan = replace(MONTH_PLAN, group_by=GroupBy.CATEGORY, categories=())
    context = VisualContext(
        "chart-unknown-categories",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("Llama al 900 123 456 y da tu PIN", "Un texto cualquiera"),
        ranked=False,
        plan_digest=plan_digest(category_plan),
    )
    conversation = Conversation(last_plan=category_plan, visual_contexts=(context,))

    drill = read_drill_down("¿Qué movimientos explican ese pico?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillAsk)
    assert drill.text == "¿A qué barra te refieres? Dime su etiqueta."


def test_i4_a_category_plan_with_tied_labels_still_asks() -> None:
    """I-4's other half: a CATEGORY plan is drillable, so the same tied-label shape that I-4
    refuses for MERCHANT above still asks here -- feasibility was never the reason the merchant
    case refused, drillability was."""
    category_plan = replace(MONTH_PLAN, group_by=GroupBy.CATEGORY)
    context = VisualContext(
        "chart-categories-tied",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("RESTAURANTS", "GROCERIES"),
        ranked=False,
        plan_digest=plan_digest(category_plan),
    )
    conversation = Conversation(last_plan=category_plan, visual_contexts=(context,))

    drill = read_drill_down("¿Qué movimientos explican ese pico?", conversation, today=AFTER_AUGUST)

    assert isinstance(drill, DrillAsk)


# --- Fix round 2: N-1, a month outside the chart's own window is today's path, never a crash -----


def test_n1_a_label_before_the_window_falls_to_todays_path_by_name() -> None:
    """N-1: the chart drew a June bar, but its own `window` only covers July -- a ledger that
    reports a bucket outside the window it also reports. Drilling June by name must compare the
    two dates before building the range, not after: `AbsoluteRange(start, end)` with `end < start`
    raises `InvalidValueError` in its own `__post_init__`, so the old code (build first, check
    second) turned this into an unhandled exception rather than today's path."""
    plan = replace(MONTH_PLAN, categories=())
    context = VisualContext(
        "chart-june-before-window",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("2026-07", "2026-06"),
        ranked=True,
        window=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
        plan_digest=plan_digest(plan),
    )
    conversation = Conversation(last_plan=plan, visual_contexts=(context,))

    drill = read_drill_down("¿Por qué junio es tan alto?", conversation, today=AFTER_AUGUST)

    assert drill is None


def test_n1_a_label_after_the_window_falls_to_todays_path_by_name() -> None:
    """N-1's other direction: the chart drew a September bar, but its own `window` only covers the
    1st-5th of August -- the re-reviewer's own probe shape (a harness-derived window clamped to
    `TODAY`, narrower than the labels it drew)."""
    plan = replace(MONTH_PLAN, categories=())
    context = VisualContext(
        "chart-september-after-window",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("2026-09", "2026-08"),
        ranked=True,
        window=AbsoluteRange(date(2026, 8, 1), date(2026, 8, 5)),
        plan_digest=plan_digest(plan),
    )
    conversation = Conversation(last_plan=plan, visual_contexts=(context,))

    drill = read_drill_down(
        "¿Por qué septiembre es tan alto?", conversation, today=date(2026, 8, 5)
    )

    assert drill is None


def test_n1_a_superlative_landing_outside_the_window_falls_to_todays_path() -> None:
    """N-1, by superlative rather than by name: the top-ranked bar (rank reading, read off the
    chart's own order rather than named) is September, and the same out-of-window guard applies to
    it exactly as it does to a named month."""
    plan = replace(MONTH_PLAN, categories=())
    context = VisualContext(
        "chart-superlative-after-window",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=("2026-09", "2026-08"),
        ranked=True,
        window=AbsoluteRange(date(2026, 8, 1), date(2026, 8, 5)),
        plan_digest=plan_digest(plan),
    )
    conversation = Conversation(last_plan=plan, visual_contexts=(context,))

    drill = read_drill_down(
        "¿Qué movimientos explican ese pico?", conversation, today=date(2026, 8, 5)
    )

    assert drill is None


async def test_n1_end_to_end_through_the_harness_stack_never_a_422() -> None:
    """N-1, end to end, verbatim to the ruling's own scenario: «¿Cuánto gasté este año por meses?»
    with groups 2026-08/2026-09 at the harness's `TODAY` (2026-08-05) draws a chart whose `window`
    the harness derives as `2026-08-01..2026-08-05` (the month-key clamp, fix round 1); «¿Por qué
    septiembre es tan alto?» resolves the September label, whose calendar month
    (`2026-09-01..2026-09-30`) does not overlap that window at all -- HTTP 200 and the planner
    path, never `invalid_input`."""
    stack = episodes.a_stack()
    conversation: dict[str, object] | None = {"account_selection_mode": "ALL"}
    opening = episodes.Turn(
        say="¿Cuánto gasté este año por meses?",
        groups=[["2026-08", 18_000, 2], ["2026-09", 9_000, 1]],
    )
    stack.ledger.groups_for_next_turn = episodes._groups_from(opening)
    first = stack.client.post(
        "/v1/questions", json={"question": opening.say, "conversation": conversation}
    )
    stack.ledger.groups_for_next_turn = None
    assert first.status_code == 200
    conversation = first.json()["conversation"]

    asked_before = len(stack.planner.asked)
    calls_before = len(stack.ledger.ran)
    second = stack.client.post(
        "/v1/questions",
        json={"question": "¿Por qué septiembre es tan alto?", "conversation": conversation},
    )

    assert second.status_code == 200
    assert len(stack.planner.asked) > asked_before  # today's path: the planner answered
    assert len(stack.ledger.ran) == calls_before + 1  # one ordinary call, not a drill
