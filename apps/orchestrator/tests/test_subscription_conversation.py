"""The reported subscription answer, followed by ordinary changes of merchant and period."""

from collections.abc import Iterator

import pytest
from test_a_conversation_that_wanders import Chat, wired

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


@pytest.fixture(autouse=True)
def restore_dependencies() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_subscription_query_is_described_in_the_users_language() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=9),
        categories=("SUBSCRIPTIONS",),
    )

    assert answer_writer.describe(plan) == "en los últimos 9 meses, gastos, suscripciones"


def test_subscription_follow_ups_keep_the_period_category_and_account_scope() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=9),
        categories=("SUBSCRIPTIONS",),
        across_accounts=True,
    )
    client, planner, ledger, _ = wired({"suscripciones": PlanningResult.planned(plan)})
    chat = Chat(client)

    first = chat.say("Cuanto gaste en suscripciones en los ultimos 9 meses en todas mis cuentas")
    assert first["outcome"] == "PLANNED"
    assert "expense" not in first["plan"]
    assert "suscripciones" in first["text"].lower()

    for sentence, merchant, period in (
        ("Y solo Netflix?", "Netflix", plan.period),
        ("Y este mes?", "Netflix", RelativePeriod(Period.THIS_MONTH)),
        ("Y Spotify?", "Spotify", RelativePeriod(Period.THIS_MONTH)),
        ("no, Netflix", "Netflix", RelativePeriod(Period.THIS_MONTH)),
        ("Y el mes pasado?", "Netflix", RelativePeriod(Period.LAST_MONTH)),
    ):
        answer = chat.say(sentence)
        assert answer["outcome"] == "PLANNED", answer["text"]
        executed = ledger.plans[-1]
        assert executed.period == period
        assert executed.merchants == (merchant,)
        assert executed.categories == ("SUBSCRIPTIONS",)
        assert executed.across_accounts

    assert len(planner.asked) == 1


@pytest.mark.parametrize("sentence", ["y restaurantes?", "y restauantes?", "y restaurntes?"])
def test_a_category_ellipsis_preserves_context_even_with_a_typo(sentence: str) -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=9),
        categories=("SUBSCRIPTIONS",),
        across_accounts=True,
    )
    client, planner, ledger, _ = wired({"suscripciones": PlanningResult.planned(plan)})
    chat = Chat(client)
    chat.say("Cuanto gaste en suscripciones en los ultimos 9 meses en todas mis cuentas")

    answer = chat.say(sentence)

    assert answer["outcome"] == "PLANNED"
    assert ledger.plans[-1].categories == ("RESTAURANTS",)
    assert ledger.plans[-1].period == plan.period
    assert ledger.plans[-1].across_accounts
    assert len(planner.asked) == 1
