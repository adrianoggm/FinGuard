"""Task 4: the handler acts on a `DRILL_DOWN` classification -- the plan in view narrowed to the
bar named, regrouped by merchant, run once; an ambiguous bar asks instead of guessing; anything
else falls through to today's path, unnarrowed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

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
MONTH_CONTEXT = VisualContext(
    "chart-months",
    SPEC,
    BREAKDOWN_PROFILE,
    labels=("2026-08", "2026-07", "2026-06"),
    ranked=True,
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
        visual_contexts=(replace(MONTH_CONTEXT, trusted=False),),
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
    ledger = RecordingLedger()
    handler, planner = _handler_with_planner(FALLBACK_PLAN, ledger)
    sentence = "¿Por qué agosto es tan alto?"
    conversation = replace(
        MONTH_CONVERSATION,
        last_plan=replace(MONTH_PLAN, group_by=GroupBy.CATEGORY),
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
    return Conversation(last_plan=plan, visual_contexts=(MONTH_CONTEXT,))


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
