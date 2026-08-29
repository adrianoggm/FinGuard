"""Silence about the window, on a follow-up, means the window from before.

`explicit_facts.enforce` imposes what the sentence *stated* over the model's reading. Silence is
not a statement, so when a continuation names no window at all `facts.window` is None, `enforce`
has nothing to impose, and whatever the model invented stands.

`plan_merger` already promises the opposite — "a field nobody named comes from the previous plan,
and there is no way for that to be lost, because no model is involved in it" — but whether the
field was named is read out of the model's own `changed` list rather than out of the sentence. A
model that emits `changed: ["window"]` about a sentence containing no period has contradicted the
user in exactly the way that module's docstring says it must not, and the merge obeys it.

Two cases from the held-out set, both measured against `qwen3.5:9b` at reference date 2026-07-15,
both `unsafe — these would have shown a figure`: a real euro total over a month nobody asked about.
"""

from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
# The reference date the two cases were measured at, so the invented windows below are the exact
# ones the model produced rather than a re-derivation of them.
TODAY = date(2026, 7, 15)


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not a balance query")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


class NoCards:
    async def all_of(self, account_id, **kwargs):
        return None


class PlannerSpy:
    """Stands in for the model, emitting exactly what it emitted on the held-out set."""

    def __init__(self, result: PlanningResult) -> None:
        self.result = result
        self.contexts: list[PlanningContext] = []

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        self.contexts.append(context)
        return self.result


class LedgerSpy:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(currency="EUR", total_minor=100, movement_count=1)

    async def ranked(self, ranking: TopMovements, **kwargs: object) -> None:
        return None


def make_handler(planner: PlannerSpy, ledger: LedgerSpy) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts(), cards=NoCards()
    )


async def ask(handler: AskFinancialQuestionHandler, sentence: str, previous: FinancialPlan):
    return await handler.handle(
        AskFinancialQuestionQuery(
            sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=previous)
        )
    )


@pytest.mark.asyncio
async def test_a_category_follow_up_keeps_the_week_it_never_mentioned() -> None:
    """`validation/t_follow_category`. «¿Y en el súper?» after LAST_WEEK, EXPENSE.

    The model read the category correctly and invented June for a sentence that names no month.
    """
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_WEEK),
        only_account_id=ACCOUNT,
    )
    invented = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
        categories=("GROCERIES",),
        only_account_id=ACCOUNT,
    )
    ledger = LedgerSpy()
    handler = make_handler(PlannerSpy(PlanningResult.planned(invented)), ledger)
    result = await ask(handler, "¿Y en el súper?", previous)

    assert result.outcome == PlanningOutcome.PLANNED.value
    ran = ledger.plans[-1]
    assert ran.categories == ("GROCERIES",)
    assert ran.date_range is None
    assert ran.period == RelativePeriod(Period.LAST_WEEK)


@pytest.mark.asyncio
async def test_a_direction_follow_up_keeps_the_month_it_never_mentioned() -> None:
    """`validation/t_follow_direction`. «¿Y lo que cobré?» after LAST_MONTH, EXPENSE.

    The invented window is a single day — the reference date — so the figure shown is one day of
    income under a headline about last month. The E2E harness reported the same collapse.
    """
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        only_account_id=ACCOUNT,
    )
    invented = FinancialPlan(
        direction=Direction.INCOME,
        date_range=AbsoluteRange(TODAY, TODAY),
        only_account_id=ACCOUNT,
    )
    ledger = LedgerSpy()
    handler = make_handler(PlannerSpy(PlanningResult.planned(invented)), ledger)
    result = await ask(handler, "¿Y lo que cobré?", previous)

    assert result.outcome == PlanningOutcome.PLANNED.value
    ran = ledger.plans[-1]
    assert ran.direction is Direction.INCOME
    assert ran.date_range is None
    assert ran.period == RelativePeriod(Period.LAST_MONTH)


@pytest.mark.asyncio
async def test_a_sentence_that_names_its_own_window_still_replaces_the_inherited_one() -> None:
    """The other side of the boundary, and the one `eight_windows_that_may_not_be_inherited` pins.

    Every sentence in that episode names its own period, so `facts.window` is not None for all
    eight and the rule below must not fire on any of them. `enforce` already imposes the stated
    window; inheriting here would be the defect that episode exists to catch, in reverse.
    """
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_WEEK),
        only_account_id=ACCOUNT,
    )
    stated = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        only_account_id=ACCOUNT,
    )
    ledger = LedgerSpy()
    handler = make_handler(PlannerSpy(PlanningResult.planned(stated)), ledger)
    await ask(handler, "¿Cuánto gasté durante este año?", previous)

    assert ledger.plans[-1].period == RelativePeriod(Period.THIS_YEAR)
