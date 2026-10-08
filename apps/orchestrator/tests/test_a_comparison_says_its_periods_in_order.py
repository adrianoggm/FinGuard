"""A comparison says its two periods in the order they happened, earlier first.

Validation fix 3, G7. Since validation fix 1 (F3) a comparison runs from the period that came first,
so the change and its word are right; the text kept writing the later period first, so «junio vs
julio» read «En julio…; en junio…» — backwards from how it was asked and from how a calendar runs.
The text now lists the earlier period first, «En junio de 2026: …; en julio de 2026: …», and the
change is still computed from the earlier to the later. The groups and the chart's rows follow the
text (fix round 1, M-4); the plan line and the stored pair are as they were.

Driven over HTTP on the episodes stack, with a ledger whose two months differ.
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, _with_planner, ask

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

JUNE, JULY = 6, 7


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ByMonth(episodes.Ledger):
    """June and July of the real conversations: fuel went up, restaurants went down."""

    FIGURES = {
        ("TRANSPORT_FUEL", JUNE): (10_280, 2),
        ("TRANSPORT_FUEL", JULY): (15_946, 3),
        ("RESTAURANTS", JUNE): (20_244, 5),
        ("RESTAURANTS", JULY): (13_357, 6),
    }

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
        assert plan.date_range is not None
        total, count = self.FIGURES[(plan.categories[0], plan.date_range.start.month)]
        return QueryAnswer(currency="EUR", total_minor=total, movement_count=count)


def _stack() -> episodes.Stack:
    stack = episodes.a_stack()
    stack.ledger = ByMonth()
    _with_planner(stack, stack.planner)
    return stack


@pytest.mark.parametrize(
    "said",
    [
        "junio vs julio en gasolina",
        "julio vs junio en gasolina",
        "compara mis gastos de gasolina de junio y julio",
    ],
)
def test_the_earlier_month_is_said_first_whichever_was_said_first(said: str) -> None:
    answer = ask(_stack(), said, SETTLED)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert answer["text"].startswith(
        "En junio de 2026: 102,80 €; en julio de 2026: 159,46 €. "
        "El gasto en gasolina fue 56,66 € mayor en julio de 2026."
    ), answer["text"]
    assert answer["difference_minor"] == 15_946 - 10_280, "from the earlier to the later"


def test_a_fall_is_said_in_order_too_and_its_word_stays_right() -> None:
    """impro-comparar-meses 1 and 3: restaurants went down from June to July."""
    stack = _stack()
    compared = ask(stack, "en q gaste mas en restaurantes, junio o julio?", SETTLED)

    assert compared["text"].startswith(
        "En junio de 2026: 202,44 €; en julio de 2026: 133,57 €. "
        "El gasto en restaurantes fue 68,87 € mayor en junio de 2026."
    ), compared["text"]
    assert compared["difference_minor"] == 13_357 - 20_244

    percentage = ask(stack, "en porcentaje", compared["conversation"])

    assert percentage["text"].startswith(
        "El gasto disminuyó un 34,02 %: de 202,44 € en junio de 2026 a 133,57 € en julio de 2026."
    ), percentage["text"]


def test_the_plan_line_alone_keeps_the_later_side_first() -> None:
    """The groups and the chart's rows read in the text's order, the earlier period first (fix
    round 1, M-4). The plan line keeps the later side first: it travels back to the model in the
    turn history, which is pinned."""
    answer = ask(_stack(), "junio vs julio en gasolina", SETTLED)

    assert answer["plan"].index("2026-07-01") < answer["plan"].index("2026-06-01")
    assert [group["key"] for group in answer["groups"]] == ["en junio de 2026", "en julio de 2026"]
    assert [row["dimension"] for row in answer["visualization"]["data"]] == [
        "en junio de 2026",
        "en julio de 2026",
    ]


def test_a_comparison_by_account_says_each_account_earlier_first() -> None:
    """The comparison of the same accounts over two windows: each row earlier first, and the
    change from the earlier to the later."""
    earlier = _account_plan(JUNE)
    latest = _account_plan(JULY)
    text = answer_writer.grouped_period_comparison(
        QueryAnswer(
            currency="EUR", total_minor=1_000, movement_count=1, groups=(Group("Hucha", 1_000, 1),)
        ),
        QueryAnswer(
            currency="EUR", total_minor=1_500, movement_count=2, groups=(Group("Hucha", 1_500, 2),)
        ),
        earlier,
        latest,
        earlier_window="en junio de 2026",
        latest_window="en julio de 2026",
    )

    assert text is not None
    assert "  · Hucha: en junio de 2026, 10,00 €; en julio de 2026, 15,00 €; +5,00 €." in text


def _account_plan(month: int) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=window_words.whole_month(2026, month),
        group_by=GroupBy.ACCOUNT,
    )
