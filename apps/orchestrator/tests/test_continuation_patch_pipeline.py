from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not a balance query")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


class NoCards:
    async def all_of(self, account_id, **kwargs):
        return None


ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 25)


class PlannerSpy:
    def __init__(self, result: PlanningResult) -> None:
        self.result = result
        self.calls: list[str] = []
        self.arguments: list[dict[str, object]] = []

    async def plan(self, sentence: str, **kwargs: object) -> PlanningResult:
        self.calls.append(sentence)
        self.arguments.append(kwargs)
        return self.result


class LedgerSpy:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(currency="EUR", total_minor=100, movement_count=1)


def make_handler(planner: PlannerSpy, ledger: LedgerSpy) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts(), cards=NoCards()
    )


def previous_plan() -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("FOOD",),
        group_by=GroupBy.CATEGORY,
        only_account_id=ACCOUNT,
    )


def ratio_conversation() -> Conversation:
    part = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("HOUSING_RENT",),
        only_account_id=ACCOUNT,
    )
    return Conversation(
        last_plan=part,
        last_ratio=RatioPlan(
            part=part,
            whole=FinancialPlan(
                direction=Direction.INCOME,
                period=RelativePeriod(Period.LAST_MONTH),
            ),
            kind=RatioKind.SHARE,
        ),
    )


async def ask(handler: AskFinancialQuestionHandler, sentence: str, conversation=None):
    from finguard_orchestrator.domain.models.conversation import IDLE

    return await handler.handle(
        AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=conversation or IDLE)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sentence", ["Sin el alquiler", "No, durante todo 2025"])
async def test_grounded_continuation_skips_planner_and_preserves_scope(sentence: str) -> None:
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))
    ledger = LedgerSpy()
    handler = make_handler(planner, ledger)
    first = await ask(handler, "Cuánto gasté en comida")
    planner.calls.clear()
    result = await ask(handler, sentence, first.conversation)
    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == []
    assert len(ledger.plans) == 2
    plan = ledger.plans[-1]
    assert plan.only_account_id == ACCOUNT
    assert plan.group_by is GroupBy.CATEGORY
    if sentence.startswith("Sin"):
        assert plan.period == RelativePeriod(Period.LAST_MONTH)
        assert plan.excluded_categories == ("HOUSING_RENT",)
    else:
        assert plan.date_range == AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31))
    assert first.conversation.last_plan == previous_plan()


@pytest.mark.asyncio
async def test_autocontained_question_uses_planner() -> None:
    planner_plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31)),
        categories=("LEISURE",),
        only_account_id=ACCOUNT,
    )
    planner = PlannerSpy(PlanningResult.planned(planner_plan))
    ledger = LedgerSpy()
    result = await ask(make_handler(planner, ledger), "Cuánto gasté en ocio durante 2025")
    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == ["Cuánto gasté en ocio durante 2025"]
    assert planner.arguments == [{"today": TODAY, "previous": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sentence", "inherits_previous"),
    [("No, durante 2025 en Amazon", True), ("Cuánto gasté durante 2025", False)],
)
async def test_non_grounded_statuses_fall_through_to_planner(
    sentence: str, *, inherits_previous: bool
) -> None:
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))
    ledger = LedgerSpy()

    result = await ask(
        make_handler(planner, ledger),
        sentence,
        Conversation(last_plan=previous_plan()),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == [sentence]
    expected = (
        {"today": TODAY, "previous": previous_plan()}
        if inherits_previous
        else {"today": TODAY, "previous": None}
    )
    assert planner.arguments == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sentence", "expected_window"),
    [
        ("De junio me refiero", AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))),
        (
            "Ahora en la segunda quincena de julio",
            AbsoluteRange(date(2026, 7, 16), date(2026, 7, 31)),
        ),
    ],
)
async def test_natural_window_follow_ups_stay_on_the_deterministic_path(
    sentence: str, expected_window: AbsoluteRange
) -> None:
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))
    ledger = LedgerSpy()

    result = await ask(
        make_handler(planner, ledger),
        sentence,
        Conversation(last_plan=previous_plan()),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == []
    assert ledger.plans[-1].date_range == expected_window


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sentence", "conversation"),
    [
        (
            "Y por meses",
            Conversation(answered_the_balance=True, last_plan=previous_plan()),
        ),
        (
            "Sin alquiler",
            ratio_conversation(),
        ),
    ],
)
async def test_special_focus_never_reuses_the_mirrored_single_plan(
    sentence: str, conversation: Conversation
) -> None:
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))

    await ask(make_handler(planner, LedgerSpy()), sentence, conversation)

    assert planner.calls == [sentence]
    assert planner.arguments[-1] == {"today": TODAY, "previous": None}


@pytest.mark.asyncio
async def test_temporal_group_follow_up_clears_the_previous_ranking_limit() -> None:
    ranked = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.CATEGORY,
        group_limit=5,
    )
    planner = PlannerSpy(PlanningResult.planned(ranked))
    ledger = LedgerSpy()

    await ask(
        make_handler(planner, ledger),
        "Y por meses",
        Conversation(last_plan=ranked),
    )

    assert planner.calls == []
    assert ledger.plans[-1].group_by is GroupBy.MONTH
    assert ledger.plans[-1].group_limit is None


@pytest.mark.asyncio
async def test_incompatible_grounded_patch_falls_through_instead_of_raising() -> None:
    cumulative = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MONTH,
        metric=Metric.CUMULATIVE,
    )
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))

    result = await ask(
        make_handler(planner, LedgerSpy()),
        "Y por categorías",
        Conversation(last_plan=cumulative),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == ["Y por categorías"]


@pytest.mark.asyncio
async def test_handle_materializes_same_final_answer_as_stream() -> None:
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))
    handler = make_handler(planner, LedgerSpy())
    first = await ask(handler, "Cuánto gasté en comida")
    planner.calls.clear()
    query = AskFinancialQuestionQuery(
        "Sin el alquiler", ACCOUNT, TODAY, conversation=first.conversation
    )
    handled = await handler.handle(query)
    streamed = [step async for step in handler.stream(query) if hasattr(step, "outcome")][-1]
    assert handled == streamed


@pytest.mark.asyncio
async def test_balance_focus_blocks_a_ranking_mirror_in_the_pipeline() -> None:
    mirrored_ranking = TopMovements(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        rank=1,
    )
    planner = PlannerSpy(PlanningResult.planned(previous_plan()))
    ledger = LedgerSpy()

    result = await ask(
        make_handler(planner, ledger),
        "y el segundo",
        Conversation(answered_the_balance=True, last_plan=mirrored_ranking),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == ["y el segundo"]
    assert planner.arguments == [{"today": TODAY, "previous": None}]
    assert ledger.plans == [previous_plan()]


@pytest.mark.asyncio
async def test_comparison_focus_blocks_shifting_only_the_latest_mirror() -> None:
    earlier = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
        only_account_id=ACCOUNT,
    )
    latest = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
        only_account_id=ACCOUNT,
    )
    comparison = ComparisonPlan(earlier=earlier, latest=latest)
    planned = previous_plan()
    planner = PlannerSpy(PlanningResult.planned(planned))
    ledger = LedgerSpy()

    result = await ask(
        make_handler(planner, ledger),
        "y el año anterior",
        Conversation(
            last_plan=latest,
            prior_plan=earlier,
            last_comparison=comparison,
        ),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == ["y el año anterior"]
    assert planner.arguments == [{"today": TODAY, "previous": None}]
    assert len(ledger.plans) == 1
    assert ledger.plans[0].categories == planned.categories
    assert ledger.plans[0].date_range == AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31))
    assert ledger.plans[0].date_range != AbsoluteRange(date(2025, 7, 1), date(2025, 7, 31))
