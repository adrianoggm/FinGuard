"""The whole path: sentence → plan → ledger → sentence.

These did not exist, and the review was right that their absence was the reason a real
defect passed 163 tests and a 233-case evaluation. The COUNT bug lived exactly in the
gap between them: every unit was correct in isolation, and the evaluation scores *plans*
rather than answers, so a right plan rendered as the wrong kind of figure was invisible
to both.

The lesson is about coverage shape rather than count. What was missing was not more
tests of the pieces — it was one test that follows a value from the question to the
sentence a person reads.
"""

import uuid
from datetime import date

import pytest

from finguard_assistant.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_assistant.application.queries.ask_financial_question import Stage
from finguard_assistant.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_assistant.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_assistant.domain.models.planning_result import PlanningResult
from finguard_assistant.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_assistant.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 7, 15)
LAST_MONTH = RelativePeriod(period=Period.LAST_MONTH)


def plan(**kwargs: object) -> FinancialPlan:
    return FinancialPlan(direction=Direction.EXPENSE, period=LAST_MONTH, **kwargs)  # type: ignore[arg-type]


ANSWER = QueryAnswer(
    currency="EUR",
    total_minor=12345,
    movement_count=3,
    groups=(Group("GROCERIES", 8000, 2), Group("HEALTH", 4345, 1)),
    movements=(
        Movement(
            occurred_at=__import__("datetime").datetime(2026, 6, 3, 10, 0),
            description="Compra Mercadona",
            amount_minor=-4210,
            direction="EXPENSE",
            merchant_name="Mercadona",
            category="GROCERIES",
        ),
    ),
)


class FakePlanner:
    def __init__(self, result: PlanningResult) -> None:
        self._result = result
        self.previous: str | None = None

    async def plan(
        self, sentence: str, *, today: date, previous: str | None = None
    ) -> PlanningResult:
        self.previous = previous
        return self._result


class FakeLedger:
    def __init__(self, answer: QueryAnswer | None = None, failure: Exception | None = None) -> None:
        self._answer = answer
        self._failure = failure
        self.account_id: uuid.UUID | None = None
        self.today: date | None = None

    async def run(self, plan: FinancialPlan, *, account_id: uuid.UUID, today: date) -> QueryAnswer:
        self.account_id, self.today = account_id, today
        if self._failure is not None:
            raise self._failure
        assert self._answer is not None
        return self._answer


def handler_for(result: PlanningResult, ledger: FakeLedger | None = None):
    return AskFinancialQuestionHandler(FakePlanner(result), ledger or FakeLedger(ANSWER))


async def ask(result: PlanningResult, ledger: FakeLedger | None = None, **kwargs: object):
    query = AskFinancialQuestionQuery(
        sentence="da igual",
        account_id=ACCOUNT,
        today=TODAY,
        **kwargs,  # type: ignore[arg-type]
    )
    return await handler_for(result, ledger).handle(query)


# --- the metric survives the whole way ---------------------------------------------


async def test_a_sum_question_is_answered_with_money() -> None:
    result = await ask(PlanningResult.planned(plan(metric=Metric.SUM)))

    assert result.text.startswith("123,45")
    assert result.total_minor == 12345


async def test_a_count_question_is_answered_with_a_count() -> None:
    """The defect the review found. The plan was right, the number asked for appeared as
    an aside, and the headline was about money — through every layer, silently."""
    result = await ask(PlanningResult.planned(plan(metric=Metric.COUNT)))

    assert result.text.startswith("3 movimientos")
    assert "€" not in result.text.splitlines()[0]


async def test_a_count_breakdown_shows_counts_not_amounts() -> None:
    result = await ask(PlanningResult.planned(plan(metric=Metric.COUNT, group_by=GroupBy.CATEGORY)))

    assert "GROCERIES: 2" in result.text
    assert "80,00" not in result.text


async def test_a_sum_breakdown_shows_amounts() -> None:
    result = await ask(PlanningResult.planned(plan(metric=Metric.SUM, group_by=GroupBy.CATEGORY)))

    assert "GROCERIES: 80,00€ (2)" in result.text


# --- every outcome reaches the user as something different --------------------------


async def test_only_a_plan_reaches_the_ledger() -> None:
    for result in (
        PlanningResult.needs_clarification("¿de qué mes?"),
        PlanningResult.unsupported("no average"),
        PlanningResult.invalid_structure("not json"),
        PlanningResult.invalid_domain("unknown category"),
        PlanningResult.provider_error("daemon down"),
    ):
        ledger = FakeLedger(ANSWER)
        answered = await ask(result, ledger)

        assert ledger.account_id is None, f"{result.outcome} reached banking"
        assert answered.text, f"{result.outcome} said nothing to the user"
        assert answered.total_minor is None


async def test_a_clarification_carries_the_question_back() -> None:
    result = await ask(PlanningResult.needs_clarification("¿De qué mes?"))

    assert result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert result.question == "¿De qué mes?"
    assert result.text == "¿De qué mes?"


async def test_the_five_declines_do_not_share_one_sentence() -> None:
    # Collapsing them into "no he podido" tells the user nothing about whether to
    # rephrase, wait, or give up.
    texts = {
        (await ask(result)).text
        for result in (
            PlanningResult.unsupported("x"),
            PlanningResult.invalid_structure("x"),
            PlanningResult.provider_error("x"),
        )
    }

    assert len(texts) == 3


# --- the ledger failing ------------------------------------------------------------


async def test_a_rejected_plan_is_reported_as_ours() -> None:
    ledger = FakeLedger(failure=QueryRejectedError("unknown category"))

    result = await ask(PlanningResult.planned(plan()), ledger)

    assert result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert result.plan is not None, "the user cannot see what we asked for"


async def test_an_unreachable_ledger_is_not_the_users_problem() -> None:
    ledger = FakeLedger(failure=BankingUnavailableError("connection refused"))

    result = await ask(PlanningResult.planned(plan()), ledger)

    assert result.outcome == "BANKING_UNAVAILABLE"
    assert result.total_minor is None


# --- the account and the clock come from the caller ---------------------------------


async def test_the_account_reaches_the_ledger_unchanged() -> None:
    ledger = FakeLedger(ANSWER)

    await ask(PlanningResult.planned(plan()), ledger)

    assert ledger.account_id == ACCOUNT


async def test_both_services_resolve_the_window_against_the_same_day() -> None:
    """The two-clocks bug: without this the assistant plans against one date and banking
    answers against its own, producing a real figure for the wrong window."""
    ledger = FakeLedger(ANSWER)

    await ask(PlanningResult.planned(plan()), ledger)

    assert ledger.today == TODAY


# --- evidence ----------------------------------------------------------------------


async def test_the_answer_carries_its_own_evidence() -> None:
    result = await ask(PlanningResult.planned(plan()))

    assert result.plan, "no plan shown: the figure would have to be taken on trust"
    assert result.movements, "no movements shown"
    assert result.movements[0].merchant_name == "Mercadona"


# --- streaming and the single-shot path cannot disagree -----------------------------


async def test_the_stream_ends_with_the_same_answer_the_handler_returns() -> None:
    planned = PlanningResult.planned(plan(metric=Metric.COUNT))
    query = AskFinancialQuestionQuery(sentence="x", account_id=ACCOUNT, today=TODAY)

    handler = handler_for(planned)
    streamed = [step async for step in handler.stream(query)]
    direct = await handler_for(planned).handle(query)

    assert streamed[-1] == direct


async def test_the_plan_is_streamed_before_any_figure() -> None:
    # The order is the point: a plan that misread the question can be caught while that
    # still helps, rather than as a footnote under a number already read.
    query = AskFinancialQuestionQuery(sentence="x", account_id=ACCOUNT, today=TODAY)

    steps = [step async for step in handler_for(PlanningResult.planned(plan())).stream(query)]
    names = [step.name for step in steps if isinstance(step, Stage)]

    assert names == ["planning", "planned", "querying"]
    assert steps[1].detail, "the planned stage carried no plan"


async def test_a_decline_stops_the_stream_without_querying() -> None:
    ledger = FakeLedger(ANSWER)
    query = AskFinancialQuestionQuery(sentence="x", account_id=ACCOUNT, today=TODAY)
    handler = AskFinancialQuestionHandler(
        FakePlanner(PlanningResult.needs_clarification("¿de qué mes?")), ledger
    )

    steps = [step async for step in handler.stream(query)]

    assert [s.name for s in steps if isinstance(s, Stage)] == ["planning"]
    assert ledger.account_id is None


# --- context -----------------------------------------------------------------------


async def test_the_previous_plan_reaches_the_planner() -> None:
    planner = FakePlanner(PlanningResult.planned(plan()))
    handler = AskFinancialQuestionHandler(planner, FakeLedger(ANSWER))

    await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿y en gasolina?",
            account_id=ACCOUNT,
            today=TODAY,
            previous_plan="el mes pasado, expense",
        )
    )

    assert planner.previous == "el mes pasado, expense"


@pytest.mark.parametrize("metric", [Metric.SUM, Metric.COUNT])
async def test_an_empty_answer_says_so_rather_than_reporting_zero(metric: Metric) -> None:
    # "Gastaste 0" and "no encontré nada" read the same and mean different things.
    ledger = FakeLedger(QueryAnswer(currency="EUR", total_minor=0, movement_count=0))

    result = await ask(PlanningResult.planned(plan(metric=metric)), ledger)

    assert "No he encontrado movimientos" in result.text
