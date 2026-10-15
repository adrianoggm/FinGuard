"""The figures a chart and its card show agree with the answer above them, in the edge cases.

Each case here was a reproduced defect: a comparison over income and spending that lost its sign,
an average chart that plotted sums, a card showing money above a count, a change stated where an
average was undefined, a grouped comparison's card holding one side, a long merchant name that
turned the chart turn into a 422.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date
from typing import Any

from support.episodes import Ledger, a_stack
from test_engine_routes import Scripted, engine_on

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.engine.operations import Ask, Compare, PlanFields, Visualize
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange


def _month(year: int, month: int) -> AbsoluteRange:
    return AbsoluteRange(
        date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    )


JUNE, JULY = _month(2026, 6), _month(2026, 7)


class _TableLedger(Ledger):
    """Answers each window from a table of groups, as the bank would group them."""

    def __init__(self, table: dict[Any, tuple[Group, ...]]) -> None:
        super().__init__()
        self.table = table

    async def run(self, plan: Any, *, movements: Any = None, **_: Any) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
        groups = self.table.get(plan.date_range, ())
        total = sum(group.total_minor for group in groups)
        count = sum(group.movement_count for group in groups)
        if plan.group_by is GroupBy.NONE:
            groups = (Group("total", total, count),) if groups else ()
        start, end = plan.date_range.start, plan.date_range.end
        return QueryAnswer("EUR", total, count, start, end, groups=groups)


def _converse(
    table: dict[Any, tuple[Group, ...]], *turns: tuple[Any, ...], seen: list[Any] | None = None
) -> list[dict[str, Any]]:
    stack = a_stack()
    stack.ledger = _TableLedger(table)
    interpreter = Scripted(*turns)
    interpreter.seen = seen if seen is not None else interpreter.seen
    engine_on(stack, interpreter)
    conversation = None
    bodies = []
    try:
        for index in range(len(turns)):
            response = stack.client.post(
                "/v1/questions",
                json={"question": f"turno {index}", "conversation": conversation},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 200, response.text[:300]
            body = response.json()
            bodies.append(body)
            conversation = body["conversation"]
    finally:
        app.state.engine = None
    return bodies


def _card(body: dict[str, Any]) -> str | None:
    blocks = (body.get("visual_response") or {}).get("blocks", [])
    return next((block["field"] for block in blocks if block["type"] == "metric"), None)


def _values(body: dict[str, Any]) -> list[int]:
    return [row["value_minor"] for row in body["visualization"]["data"]]


def test_a_comparison_over_income_and_spending_keeps_its_signs() -> None:
    answered, charted = _converse(
        {JUNE: (Group("x", -50_000, 9),), JULY: (Group("x", 20_000, 6),)},
        (
            Compare(
                PlanFields(direction=Direction.ALL),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
    )
    # A net outflow of 500 € against a net inflow of 200 €: July is 700 € higher, not lower.
    assert answered["difference_minor"] == 70_000
    assert charted["difference_minor"] == 70_000
    assert _values(charted) == [-50_000, 20_000]


def test_an_average_chart_plots_the_averages_the_sentence_gives() -> None:
    groups = (
        Group("GROCERIES", 30_000, 5),
        Group("RESTAURANTS", 12_000, 4),
        Group("LEISURE", 8_000, 2),
    )
    _, charted = _converse(
        {JULY: groups},
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=JULY,
                    group_by=GroupBy.CATEGORY,
                    metric=Metric.AVERAGE,
                )
            ),
        ),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
    )
    assert sorted(_values(charted)) == [3_000, 4_000, 6_000]


def test_the_card_is_in_the_answers_own_unit() -> None:
    groups = (Group("GROCERIES", 30_000, 5), Group("RESTAURANTS", 20_000, 6))
    counted = _converse(
        {JULY: groups},
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, metric=Metric.COUNT)),),
    )[0]
    averaged = _converse(
        {JULY: groups},
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, metric=Metric.AVERAGE)),),
    )[0]
    # A count's card is its movements, never the money they add up to; an average has no total
    # worth a card, and its sentence carries the figure.
    assert _card(counted) == "movement_count"
    assert _card(averaged) is None


def test_the_answer_names_the_plan_the_web_reads_its_unit_from() -> None:
    """The web draws a breakdown's rows as counts, averages or money from `last_plan.metric`; it
    was always empty, so a count by category was shown in euros."""
    groups = (Group("GROCERIES", 30_000, 5), Group("RESTAURANTS", 20_000, 6))
    counted = _converse(
        {JULY: groups},
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=JULY,
                    group_by=GroupBy.CATEGORY,
                    metric=Metric.COUNT,
                )
            ),
        ),
    )[0]
    compared = _converse(
        {JUNE: (Group("x", 28_000, 7),), JULY: (Group("x", 30_000, 3),)},
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE, metric=Metric.AVERAGE),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
    )[0]

    assert counted["conversation"]["last_plan"]["metric"] == "COUNT"
    assert counted["conversation"]["last_plan"]["group_by"] == "CATEGORY"
    # A comparison's two sides share their metric; the later one is the plan in view.
    assert compared["conversation"]["last_plan"]["metric"] == "AVERAGE"


def test_an_average_compared_with_an_empty_period_states_no_change() -> None:
    answered, charted = _converse(
        {JUNE: (Group("x", 28_000, 7),), JULY: ()},
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE, metric=Metric.AVERAGE),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
    )
    assert "no está definida" in answered["text"]
    assert answered["difference_minor"] is None
    assert charted["difference_minor"] is None


def test_a_merchant_name_past_the_label_limit_is_shortened_not_refused() -> None:
    long_name = "Supermercados Ejemplo Muy Largo Sociedad Anonima Unipersonal Sucursal 1234"
    _, charted = _converse(
        {JULY: (Group(long_name, 30_000, 3), Group("Dia", 10_000, 1))},
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, group_by=GroupBy.MERCHANT)),),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
    )
    labels = [row["dimension"] for row in charted["visualization"]["data"]]
    assert all(len(label) <= 64 for label in labels)
    assert any(label.endswith("…") for label in labels)


def test_two_periods_read_in_the_order_they_happened_and_the_history_keeps_its_dates() -> None:
    answered, charted = _converse(
        {JUNE: (Group("x", 280_000, 1),), JULY: (Group("x", 300_000, 1),)},
        (
            Compare(
                PlanFields(direction=Direction.INCOME),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
    )
    labels = [row["dimension"] for row in charted["visualization"]["data"]]
    assert labels == ["Junio de 2026", "Julio de 2026"]
    # June first even though July is larger: no sort, so the rows' own order stands.
    encoding = charted["visualization"]["spec"]["encoding"]
    dimension = encoding["x"] if encoding["x"]["field"] == "dimension" else encoding["y"]
    assert dimension.get("sort") is None
    assert "2026-06-01" not in answered["text"] and "2026-06-01" not in (answered["plan"] or "")


def test_the_interpreter_still_reads_the_plan_with_its_iso_dates() -> None:
    """Only what a person reads is said in words: the history the model is shown is unchanged."""
    seen: list[Any] = []
    _converse(
        {JULY: (Group("x", 30_000, 3),)},
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),),
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),),
        seen=seen,
    )
    assert "del 2026-07-01 al 2026-07-31" in seen[1].turns[-1].answered
