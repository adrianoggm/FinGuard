"""The investigation's pure parts: what banking's figures establish, the narrative's number check,
the checkpoint codec, and the assessor's closed schema. No graph, no model."""

import json
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.graph import codec, investigation
from finguard_orchestrator.application.graph.investigation import (
    Explanation,
    Finding,
    MerchantChange,
)
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


def test_a_rise_is_explained_from_counts_and_means() -> None:
    before = answer(
        ("Mercadona", 10_000, 4), ("Amazon", 5_000, 1), ("Cepsa", 6_000, 2), ("Bar", 10_000, 4)
    )
    after = answer(
        ("Mercadona", 15_000, 6),  # mean 25 → 25: more purchases only
        ("Amazon", 17_000, 2),  # 1 → 2 and mean 50 → 85: both
        ("Cepsa", 9_000, 2),  # same count, mean 30 → 45: higher amounts
        ("Spotify", 999, 1),  # absent before
        ("Bar", 10_100, 4),  # same count, mean 25 → 25,25 (+1 %): too little to tell
    )

    risen = investigation.risen(before, after)

    assert [(c.merchant, c.explanation) for c in risen] == [
        ("Amazon", Explanation.BOTH),
        ("Mercadona", Explanation.MORE_PURCHASES),
        ("Cepsa", Explanation.HIGHER_AMOUNTS),
        ("Spotify", Explanation.NEW_MERCHANT),
        ("Bar", Explanation.CANNOT_TELL),
    ]
    assert investigation.worth_inspecting(risen) == ("Amazon", "Cepsa")


def test_fewer_purchases_at_a_higher_mean_is_higher_amounts() -> None:
    risen = investigation.risen(answer(("Cepsa", 6_000, 3)), answer(("Cepsa", 7_000, 2)))

    assert [c.explanation for c in risen] == [Explanation.HIGHER_AMOUNTS]


def test_the_mean_threshold_is_exact_and_needs_no_rounding() -> None:
    """5 % on a mean of 20,00 € is 21,00 €: exactly there counts, a cent below does not."""
    explained = investigation.explained
    assert explained(2_000, 1, 2_100, 1) is Explanation.HIGHER_AMOUNTS
    assert explained(2_000, 1, 2_099, 1) is Explanation.CANNOT_TELL
    # More purchases at a mean only 1 % higher is more purchases, not both.
    assert explained(2_000, 1, 4_040, 2) is Explanation.MORE_PURCHASES


def test_merchants_that_fell_or_held_and_the_unknown_merchant_are_not_named() -> None:
    before = answer(("Mercadona", 10_000, 4), ("UNKNOWN_MERCHANT", 100, 1))
    after = answer(("Mercadona", 10_000, 5), ("UNKNOWN_MERCHANT", 9_000, 3))

    assert investigation.risen(before, after) == ()


def test_one_purchase_explains_a_rise_only_when_without_it_there_is_none() -> None:
    change = MerchantChange("Amazon", 5_000, 1, 17_000, 2, Explanation.BOTH)

    def largest(minor: int) -> Movement:
        return Movement(datetime(2026, 7, 12, tzinfo=UTC), "", minor, "EXPENSE")

    assert investigation.inspected(change, largest(12_000)).finding is Finding.ONE_PURCHASE
    assert investigation.inspected(change, largest(11_999)).finding is Finding.SPREAD
    assert investigation.inspected(change, None).finding is None


# --- each explanation, rendered with its figures --------------------------------------------------

EARLIER = FinancialPlan(direction=Direction.EXPENSE, date_range=JUNE)
LATEST = FinancialPlan(direction=Direction.EXPENSE, date_range=JULY)


def rendered(change: MerchantChange) -> str:
    text = investigation.answer_text(
        (change,),
        (),
        earlier=EARLIER,
        latest=LATEST,
        earlier_total=change.earlier_total,
        latest_total=change.latest_total,
        currency="EUR",
        partial=False,
    )
    return text.splitlines()[1]


@pytest.mark.parametrize(
    ("change", "line"),
    [
        (
            MerchantChange("Amazon", 5_000, 1, 17_000, 2, Explanation.BOTH),
            "  · Amazon: 50,00 € → 170,00 €; por más compras (de 1 compra a 2 compras) y por "
            "importes mayores (media de 50,00 € a 85,00 €).",
        ),
        (
            MerchantChange("Mercadona", 10_000, 4, 15_000, 6, Explanation.MORE_PURCHASES),
            "  · Mercadona: 100,00 € → 150,00 €; por más compras (de 4 compras a 6 compras; "
            "media de 25,00 € a 25,00 €).",
        ),
        (
            MerchantChange("Cepsa", 6_000, 2, 9_000, 2, Explanation.HIGHER_AMOUNTS),
            "  · Cepsa: 60,00 € → 90,00 €; por importes mayores (media de 30,00 € a 45,00 €; "
            "de 2 compras a 2 compras).",
        ),
        (
            MerchantChange("Spotify", 0, 0, 999, 1, Explanation.NEW_MERCHANT),
            "  · Spotify: 0,00 € → 9,99 €; es nuevo: ninguna compra antes, 1 compra ahora.",
        ),
        (
            MerchantChange("Bar", 10_000, 4, 10_100, 4, Explanation.CANNOT_TELL),
            "  · Bar: 100,00 € → 101,00 €; sube poco para atribuirlo a más compras o a importes "
            "mayores (de 4 compras a 4 compras; media de 25,00 € a 25,25 €).",
        ),
    ],
)
def test_each_explanation_renders_with_its_figures(change: MerchantChange, line: str) -> None:
    assert rendered(change) == line


def test_every_explanation_has_a_template() -> None:
    lines = {
        explanation: rendered(MerchantChange("X", 1_000, 2, 3_000, 3, explanation))
        for explanation in Explanation
    }
    assert len(set(lines.values())) == len(Explanation)


def test_the_answer_says_both_windows_the_inspection_and_a_partial_stop() -> None:
    changes = (MerchantChange("Amazon", 5_000, 1, 17_000, 2, Explanation.BOTH),)
    found = investigation.inspected(
        changes[0], Movement(datetime(2026, 7, 12, tzinfo=UTC), "", 15_000, "EXPENSE")
    )

    text = investigation.answer_text(
        changes,
        (found,),
        earlier=EARLIER,
        latest=LATEST,
        earlier_total=5_000,
        latest_total=17_000,
        currency="EUR",
        partial=True,
    )

    lines = text.splitlines()
    assert lines[0] == (
        "Comercios en los que gastas más del 2026-06-01 al 2026-06-30 frente a del 2026-07-01 al "
        "2026-07-31:"
    )
    assert lines[2:] == [
        "  En Amazon, la subida se debe a una compra: sin la de 150,00 € del 2026-07-12 no "
        "habrías gastado más que antes.",
        "No he investigado más: he llegado al límite de consultas de esta pregunta.",
    ]


def test_no_rise_says_so_with_both_totals() -> None:
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))

    text = investigation.answer_text(
        (),
        (),
        earlier=plan,
        latest=plan,
        earlier_total=9_000,
        latest_total=8_000,
        currency="EUR",
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
    # The stop carries nothing: no string the model writes has a place in the answer.
    assert stop["properties"]["stop"] == {"type": "object", "additionalProperties": False}
    assert schema["additionalProperties"] is False


def test_with_nothing_to_inspect_the_only_branch_is_to_stop() -> None:
    (only,) = llm_assessor.schema_for(())["properties"]["next"]["anyOf"]

    assert only["required"] == ["stop"]


@pytest.mark.parametrize(
    ("answer_text", "read"),
    [
        ('{"next": {"inspect": {"merchant": "Amazon"}}}', Inspect("Amazon")),
        # Text a model writes inside a stop is dropped unread (R42).
        ('{"next": {"stop": {"why": "MARKER-7f3"}}}', Stop()),
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
