"""«¿Cuántas suscripciones tengo?» — four, not thirty-six.

Reported. The answer was "36 movimientos en suscripciones en los últimos 12 meses" and the holder
said what anybody would: "yo pensaba que tenía solo netflix altafir spoti y steam". Both numbers are
true and only one is the answer — thirty-six is how many times they were charged, four is how many
things they are paying for.

Two separate things were wrong. The sentence they actually typed said "subscripciones" with a b, so
it named no category at all and the whole deterministic path was missed; and even spelled correctly,
nothing counted distinct payees, so the question reached the planner and came back as a count of
rows.

No new metric and nothing new asked of banking: a breakdown by merchant already returns one group
per payee, so the count is the number of groups and the names are their keys.
"""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)

# Thirty-six charges from four shops, which is the reported ledger.
SUBSCRIPTIONS = QueryAnswer(
    currency="EUR",
    total_minor=64_476,
    movement_count=36,
    groups=(
        Group("Netflix", 16_788, 12),
        Group("Gimnasio Altafit", 35_880, 12),
        Group("Spotify", 9_684, 9),
        Group("Steam", 2_124, 3),
    ),
)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("counting the payees of a named category must not reach the model")


class Ledger:
    def __init__(self, answer: QueryAnswer) -> None:
        self.answer = answer
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return self.answer


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 100_000, 100_000, datetime(2026, 8, 5, 10))


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


class PlansAnything:
    """For the sentences that legitimately reach a model. A sum over a named category has no
    deterministic reader and is not supposed to grow one here."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        self.calls += 1
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.LAST_MONTH),
                categories=("SUBSCRIPTIONS",),
            )
        )


async def ask(  # type: ignore[no-untyped-def]
    sentence: str,
    answer: QueryAnswer = SUBSCRIPTIONS,
    planner: object | None = None,
):
    planner = planner or ModelMustNotPlan()
    ledger = Ledger(answer)
    result = await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=IDLE
        )
    )
    return result, ledger, planner


async def test_the_answer_is_how_many_payees_and_who_they_are() -> None:
    result, ledger, planner = await ask("¿cuántas suscripciones tengo?")

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "4 suscripciones" in result.text
    assert "36" not in result.text, "thirty-six is how many charges, not how many subscriptions"
    for shop in ("Netflix", "Gimnasio Altafit", "Spotify", "Steam"):
        assert shop in result.text
    assert result.movement_count == 4, "the figure a client draws large is the size of the list"
    assert planner.calls == 0


async def test_the_query_is_an_ordinary_merchant_breakdown() -> None:
    """No new metric and nothing new asked of banking. That is the whole reason this was cheap: the
    grouping that answers "¿en qué comercios gasté?" already returns one row per payee."""
    _, ledger, _ = await ask("¿cuántas suscripciones tengo?")

    plan = ledger.plans[0]
    assert plan.group_by is GroupBy.MERCHANT
    assert plan.metric is Metric.SUM
    assert plan.categories == ("SUBSCRIPTIONS",)
    assert plan.direction is Direction.EXPENSE


async def test_the_misspelling_everybody_writes_is_read() -> None:
    """The sentence from the report, verbatim. "Subscripciones" with the b named no category, so the
    turn missed every deterministic reader and the model counted rows."""
    result, _, planner = await ask(
        "Perdona sabrias decirme que subscripciones tengo activas ahora mismo ?"
    )

    assert "4 suscripciones" in result.text
    assert planner.calls == 0


async def test_a_window_the_sentence_names_is_used_and_said() -> None:
    """The default is twelve months and the answer names it, so narrowing is one short turn. Asking
    "¿de qué periodo?" instead is what the reported conversation did, and the holder answered "en el
    último año" and then did not recognise the figure."""
    result, ledger, _ = await ask("¿cuántas suscripciones tengo este mes?")

    assert ledger.plans[0].period is not None
    assert ledger.plans[0].period.period is Period.THIS_MONTH
    assert "lo que va de mes" in result.text


async def test_one_payee_is_written_in_the_singular() -> None:
    only_netflix = QueryAnswer(
        currency="EUR",
        total_minor=16_788,
        movement_count=12,
        groups=(Group("Netflix", 16_788, 12),),
    )

    result, _, _ = await ask("¿cuántas suscripciones tengo?", only_netflix)

    assert "1 suscripción" in result.text
    assert "suscripciones" not in result.text


async def test_nothing_charged_is_not_a_list_of_none() -> None:
    empty = QueryAnswer(currency="EUR", total_minor=0, movement_count=0, groups=())

    result, _, _ = await ask("¿cuántas suscripciones tengo?", empty)

    assert "No te han cobrado nada en suscripciones" in result.text


async def test_a_question_about_money_is_still_about_money() -> None:
    """The three sentences this must not steal. Each names the same category and asks something the
    service already answered — a sum, a count of rows, an average — and reading any of them as a
    list of payees replaces a right answer with a right answer to another question."""
    for sentence in (
        "¿cuánto gasté en suscripciones?",
        "¿cuántos movimientos tengo en suscripciones?",
    ):
        result, ledger, _ = await ask(sentence, planner=PlansAnything())
        assert ledger.plans[0].group_by is not GroupBy.MERCHANT, sentence
        assert "suscripciones" in result.text.lower()

    # The mean is the third of them and it asks for a period rather than running, which is the
    # existing behaviour and is also proof this did not swallow it: a payee list needs no period.
    result, ledger, _ = await ask("¿cuál es mi gasto medio en suscripciones?")
    assert result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans == []
