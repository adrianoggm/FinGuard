"""Natural calendar periods survive planning, querying and conversational follow-ups."""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 9)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("a precisely named calendar period must not reach the model")


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        is_previous = plan.period is not None and plan.period.period.value.startswith("LAST_")
        return QueryAnswer(
            currency="EUR",
            total_minor=10_000 if is_previous else 15_000,
            movement_count=2 if is_previous else 3,
        )


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 100_000, 100_000, datetime(2026, 8, 9, 10))


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(sentence: str, ledger: Ledger, conversation: Conversation = IDLE):  # type: ignore[no-untyped-def]
    planner = ModelMustNotPlan()
    result = await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )
    return result, planner


async def test_this_quarter_is_queried_without_a_model() -> None:
    ledger = Ledger()

    result, planner = await ask("¿Cuánto he gastado este trimestre?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[0].period is not None
    assert ledger.plans[0].period.period is Period.THIS_QUARTER
    assert "en lo que va de trimestre" in result.text
    assert planner.calls == 0


async def test_period_before_preserves_the_semester_unit() -> None:
    ledger = Ledger()
    first, _ = await ask("¿Cuánto he gastado este semestre?", ledger)

    result, planner = await ask(
        "Compáralo con el periodo anterior",
        ledger,
        first.conversation,
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.period.period for plan in ledger.plans[-2:] if plan.period] == [
        Period.LAST_SEMESTER,
        Period.THIS_SEMESTER,
    ]
    assert "50,00 € mayor" in result.text
    assert planner.calls == 0


async def test_two_fortnights_can_be_compared_in_one_sentence() -> None:
    ledger = Ledger()

    result, planner = await ask(
        "Compara mis gastos de esta quincena con la anterior",
        ledger,
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.period.period for plan in ledger.plans if plan.period] == [
        Period.LAST_FORTNIGHT,
        Period.THIS_FORTNIGHT,
    ]
    assert planner.calls == 0


async def test_this_weekend_keeps_its_own_window() -> None:
    ledger = Ledger()

    result, planner = await ask("Enséñame mis gastos de este fin de semana", ledger)

    assert ledger.plans[0].period is not None
    assert ledger.plans[0].period.period is Period.THIS_WEEKEND
    assert "este fin de semana" in result.text
    assert planner.calls == 0
