from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)


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

    async def plan(self, sentence: str, **kwargs: object) -> PlanningResult:
        self.calls.append(sentence)
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
