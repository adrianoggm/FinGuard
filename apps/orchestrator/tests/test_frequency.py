"""Frequency is a typed question over complete ledger dates, not a prose heuristic."""

import uuid
from datetime import UTC, date, datetime, timedelta

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import frequency_analyzer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 9)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("a grounded frequency question must not reach the model")


class Ledger:
    def __init__(self, answer: QueryAnswer) -> None:
        self.answer = answer
        self.plans: list[FinancialPlan] = []
        self.movement_limits: list[int | None] = []

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self.plans.append(plan)
        self.movement_limits.append(movements)
        return self.answer


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=100_000,
            available_minor=100_000,
            as_of=datetime(2026, 8, 9, 10, 0),
        )


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(sentence: str, ledger: Ledger, *, conversation: Conversation = IDLE):  # type: ignore[no-untyped-def]
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


def movements_on(days: list[datetime], *, direction: str = "EXPENSE") -> QueryAnswer:
    return QueryAnswer(
        currency="EUR",
        total_minor=5_000 * len(days),
        movement_count=len(days),
        movements=tuple(
            Movement(day, "Movimiento", 5_000, direction, merchant_name="Comercio") for day in days
        ),
    )


async def test_real_frequency_uses_all_intervals_and_a_default_twelve_month_window() -> None:
    mondays = [datetime(2026, 6, 1) + timedelta(days=7 * index) for index in range(6)]
    ledger = Ledger(movements_on(mondays))

    result, planner = await ask("¿Cada cuánto compro gasolina?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "aproximadamente cada 7 días" in result.text
    assert "sobre todo los lunes" in result.text
    assert ledger.plans[0].metric is Metric.FREQUENCY
    assert ledger.plans[0].categories == ("TRANSPORT_FUEL",)
    assert ledger.plans[0].period == RelativePeriod(Period.LAST_N_MONTHS, n=12)
    assert ledger.movement_limits == [100]
    assert planner.calls == 0


async def test_the_usual_pay_day_is_a_salary_frequency_without_saying_nomina() -> None:
    payslips = [
        datetime(2026, 1, 28),
        datetime(2026, 2, 28),
        datetime(2026, 3, 25),
        datetime(2026, 4, 27),
    ]
    ledger = Ledger(movements_on(payslips, direction="INCOME"))

    result, planner = await ask("¿Qué día suelen pagarme este año?", ledger)

    assert "una al mes" in result.text
    assert "entre el día 25 y el 28" in result.text
    assert ledger.plans[0].direction is Direction.INCOME
    assert ledger.plans[0].categories == ("SALARY",)
    assert planner.calls == 0


async def test_a_period_follow_up_preserves_frequency_instead_of_returning_money() -> None:
    weekly = [datetime(2026, 6, 1) + timedelta(days=7 * index) for index in range(4)]
    ledger = Ledger(movements_on(weekly))
    first, _ = await ask("¿Cada cuánto compro gasolina este año?", ledger)

    second, planner = await ask("¿Y el año pasado?", ledger, conversation=first.conversation)

    assert "cada 7 días" in second.text
    assert "€" not in second.text
    assert ledger.plans[-1].metric is Metric.FREQUENCY
    assert planner.calls == 0


async def test_incomplete_evidence_is_reported_instead_of_extrapolated() -> None:
    hundred = [datetime(2026, 1, 1) + timedelta(days=index) for index in range(100)]
    sampled = movements_on(hundred)
    sampled = QueryAnswer(
        currency=sampled.currency,
        total_minor=sampled.total_minor,
        movement_count=101,
        movements=sampled.movements,
    )
    ledger = Ledger(sampled)

    result, _ = await ask("¿Cada cuánto compro en el supermercado este año?", ledger)

    assert "sin extrapolar" in result.text
    assert "cada 1 días" not in result.text


def test_frequency_bins_dates_in_the_reporting_timezone() -> None:
    answer = movements_on(
        [
            datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
            datetime(2026, 2, 28, 23, 30, tzinfo=UTC),
        ]
    )

    pattern = frequency_analyzer.analyse(answer, reporting_timezone="Europe/Madrid")

    assert pattern.monthly
    assert pattern.day_of_month_min == pattern.day_of_month_max == 1


def test_skipped_months_are_not_called_a_monthly_recurrence() -> None:
    answer = movements_on([datetime(2026, 1, 10), datetime(2026, 3, 10)])

    pattern = frequency_analyzer.analyse(answer, reporting_timezone="Europe/Madrid")

    assert not pattern.monthly
    assert pattern.median_interval_days == 59
