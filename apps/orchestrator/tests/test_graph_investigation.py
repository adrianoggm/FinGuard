"""The investigation's pure parts: what banking's figures establish, the narrative's number check,
the checkpoint codec, and the assessor's closed schema. No graph, no model."""

import json
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.graph import codec, investigation
from finguard_orchestrator.application.graph.investigation import Cause, Finding, MerchantChange
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    Delegate,
    Patch,
    PlanFields,
    Reset,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import Measure, Missing
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.ports.investigation_assessor import Inspect, Stop
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.graph import llm_assessor

JUNE = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))


def answer(*groups: tuple[str, int, int]) -> QueryAnswer:
    rows = tuple(Group(*row) for row in groups)
    return QueryAnswer(
        "EUR", sum(g.total_minor for g in rows), sum(g.movement_count for g in rows), groups=rows
    )


def test_a_rise_is_more_purchases_bigger_amounts_both_or_new() -> None:
    before = answer(("Mercadona", 10_000, 4), ("Amazon", 5_000, 1), ("Cepsa", 6_000, 2))
    after = answer(
        ("Mercadona", 15_000, 6),  # mean 25 → 25: more purchases only
        ("Amazon", 17_000, 2),  # 1 → 2 and mean 50 → 85: both
        ("Cepsa", 9_000, 2),  # same count, mean 30 → 45: bigger amounts
        ("Spotify", 999, 1),  # absent before
    )

    risen = investigation.risen(before, after)

    assert [(c.merchant, c.cause) for c in risen] == [
        ("Amazon", Cause.BOTH),
        ("Mercadona", Cause.MORE_PURCHASES),
        ("Cepsa", Cause.BIGGER_AMOUNTS),
        ("Spotify", Cause.NEW),
    ]
    assert investigation.worth_inspecting(risen) == ("Amazon", "Cepsa")


def test_fewer_purchases_at_a_higher_mean_is_bigger_amounts() -> None:
    risen = investigation.risen(answer(("Cepsa", 6_000, 3)), answer(("Cepsa", 7_000, 2)))

    assert [c.cause for c in risen] == [Cause.BIGGER_AMOUNTS]


def test_merchants_that_fell_or_held_and_the_unknown_merchant_are_not_named() -> None:
    before = answer(("Mercadona", 10_000, 4), ("UNKNOWN_MERCHANT", 100, 1))
    after = answer(("Mercadona", 10_000, 5), ("UNKNOWN_MERCHANT", 9_000, 3))

    assert investigation.risen(before, after) == ()


def test_the_mean_is_compared_without_rounding() -> None:
    # 1000/3 = 333.33… and 1001/3 = 333.67: equal once rounded to the cent, still bigger.
    risen = investigation.risen(answer(("A", 1_000, 3)), answer(("A", 1_001, 3)))

    assert risen[0].cause is Cause.BIGGER_AMOUNTS


def test_one_purchase_explains_a_rise_only_when_without_it_there_is_none() -> None:
    change = MerchantChange("Amazon", 5_000, 1, 17_000, 2, Cause.BOTH)

    def largest(minor: int) -> Movement:
        return Movement(datetime(2026, 7, 12, tzinfo=UTC), "", minor, "EXPENSE")

    assert investigation.inspected(change, largest(12_000)).finding is Finding.ONE_PURCHASE
    assert investigation.inspected(change, largest(11_999)).finding is Finding.SPREAD
    assert investigation.inspected(change, None).finding is None


# --- the narrative --------------------------------------------------------------------------------

GROUNDED = "  · Amazon: 50,00 € (1 compra) → 170,00 € (2 compras); por importes mayores."


def test_a_narrative_quoting_grounded_numbers_passes() -> None:
    narrative = "Amazon pasa de 1 compra a 2 y de 50,00 € a 170,00 €."

    assert investigation.checked_narrative(narrative, GROUNDED) == narrative


@pytest.mark.parametrize(
    "narrative",
    [
        "Amazon sube un 240 %.",  # arithmetic the model did
        "Amazon pasa de 50 € a 170 €.",  # a grounded figure, rewritten
        "Amazon sube 120,00 €.",  # a difference nobody wrote
    ],
)
def test_a_narrative_with_an_unsupported_number_is_refused(narrative: str) -> None:
    assert investigation.unsupported_numbers(narrative, GROUNDED)
    assert investigation.checked_narrative(narrative, GROUNDED) is None


def test_an_empty_or_overlong_narrative_is_refused() -> None:
    assert investigation.checked_narrative("  ", GROUNDED) is None
    assert (
        investigation.checked_narrative("a" * (investigation.MAX_NARRATIVE + 1), GROUNDED) is None
    )


def test_the_grounded_answer_says_both_windows_every_figure_and_the_cause() -> None:
    earlier = FinancialPlan(direction=Direction.EXPENSE, date_range=JUNE)
    latest = FinancialPlan(direction=Direction.EXPENSE, date_range=JULY)
    changes = (MerchantChange("Amazon", 5_000, 1, 17_000, 2, Cause.BOTH),)
    found = investigation.inspected(
        changes[0], Movement(datetime(2026, 7, 12, tzinfo=UTC), "", 15_000, "EXPENSE")
    )

    text = investigation.answer_text(
        changes,
        (found,),
        earlier=earlier,
        latest=latest,
        earlier_answer=answer(("Amazon", 5_000, 1)),
        latest_answer=answer(("Amazon", 17_000, 2)),
        partial=True,
    )

    assert text == (
        "Comercios en los que gastas más del 2026-07-01 al 2026-07-31 frente a del 2026-06-01 al "
        "2026-06-30:\n"
        "  · Amazon: 50,00 € (1 compra) → 170,00 € (2 compras); "
        "por más compras y por importes mayores.\n"
        "  En Amazon, la subida se debe a una compra: sin la de 150,00 € del 2026-07-12 no "
        "habrías gastado más que antes.\n"
        "No he investigado más: he llegado al límite de consultas de esta pregunta."
    )


def test_no_rise_says_so_with_both_totals() -> None:
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))

    text = investigation.answer_text(
        (),
        (),
        earlier=plan,
        latest=plan,
        earlier_answer=answer(("A", 9_000, 3)),
        latest_answer=answer(("A", 8_000, 3)),
        partial=False,
    )

    assert "Ningún comercio sube" in text
    assert "90,00 € antes y 80,00 € ahora" in text


# --- the checkpoint codec -------------------------------------------------------------------------


def test_every_operation_survives_the_checkpoint_codec_exactly() -> None:
    fields = PlanFields(
        direction=Direction.EXPENSE,
        window=JULY,
        categories=("GROCERIES",),
        merchants=(),
        metric=Metric.COUNT,
        group_by=GroupBy.MERCHANT,
        limit=3,
    )
    ops = (
        Ask(fields),
        Patch("focus", PlanFields(window=RelativePeriod(Period.LAST_N_MONTHS, n=3))),
        Compare(PlanFields(group_by=GroupBy.MERCHANT), PlanFields(window=JUNE), fields),
        ChangeMeasure(Measure.PERCENT_CHANGE),
        SelectOperand("earlier"),
        Reset(),
        Balance(("hucha",)),
        Clarify(Missing.WINDOW, "x", ("a", "b")),
        Smalltalk("THANKS"),
        Delegate("TRANSFER"),
    )

    assert codec.loads(codec.dumps(ops)) == ops


def test_the_codec_rebuilds_no_type_it_does_not_list() -> None:
    planted = json.dumps([{"$type": "Popen", "f": {"args": "calc"}}])

    with pytest.raises(codec.UnknownCheckpointTypeError):
        codec.loads(planted)


# --- the assessor's schema and reader -------------------------------------------------------------


def test_the_schema_closes_inspection_to_the_allow_list() -> None:
    schema = llm_assessor.schema_for(("Amazon", "Cepsa"))
    inspect, stop = schema["properties"]["next"]["anyOf"]

    merchant = inspect["properties"]["inspect"]["properties"]["merchant"]
    assert merchant == {"type": "string", "enum": ["Amazon", "Cepsa"]}
    assert stop["required"] == ["stop"]
    assert schema["additionalProperties"] is False


def test_with_nothing_to_inspect_the_only_branch_is_to_stop() -> None:
    (only,) = llm_assessor.schema_for(())["properties"]["next"]["anyOf"]

    assert only["required"] == ["stop"]


@pytest.mark.parametrize(
    ("answer_text", "read"),
    [
        ('{"next": {"inspect": {"merchant": "Amazon"}}}', Inspect("Amazon")),
        ('{"next": {"stop": {"why": "Nada más."}}}', Stop("Nada más.")),
        ('{"next": {"stop": {}}}', Stop()),
        ('{"next": {"inspect": {"merchant": 3}}}', Stop()),
        ("not json", Stop()),
        ('{"next": {"transfer": {"to": "Marta"}}}', Stop()),
    ],
)
def test_the_reader_takes_the_two_shapes_and_nothing_else(
    answer_text: str, read: Inspect | Stop
) -> None:
    assert llm_assessor.read(answer_text, ("Amazon",)) == read
