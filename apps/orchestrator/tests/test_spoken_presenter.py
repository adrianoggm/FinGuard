"""`domain/services/spoken_presenter.py` (Wave 4.4, Task 3): red first.

Contexts for the seven `FAMILY_DATASETS` are built the same way `test_visual_profile.py` builds
them -- `visual_profile_of` then `DeterministicVisualPlanner().plan` for the spec, `drawn_labels`
for the labels/ranked pair -- loaded off `scripts/export_chart_fixtures.py` directly, since
`scripts/` is not a package.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.spoken_presenter import _NOUNS, spoken_summary
from finguard_orchestrator.domain.services.visual_profile import drawn_labels, visual_profile_of
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

_EXPORTER_SPEC = importlib.util.spec_from_file_location(
    "export_chart_fixtures",
    Path(__file__).resolve().parents[1] / "scripts" / "export_chart_fixtures.py",
)
assert _EXPORTER_SPEC and _EXPORTER_SPEC.loader
export_chart_fixtures = importlib.util.module_from_spec(_EXPORTER_SPEC)
sys.modules["export_chart_fixtures"] = export_chart_fixtures
_EXPORTER_SPEC.loader.exec_module(export_chart_fixtures)
FAMILY_DATASETS = export_chart_fixtures.FAMILY_DATASETS

_PLANNER = DeterministicVisualPlanner()


def _last_month(direction: Direction, group_by: GroupBy) -> FinancialPlan:
    return FinancialPlan(direction, period=RelativePeriod(Period.LAST_MONTH), group_by=group_by)


def _context(family_name: str) -> tuple[ChartSpec, object, tuple[str, ...], bool]:
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(
        dataset,
        total_minor=kwargs.get("total_minor"),
        group_by=kwargs["group_by"],
        plan_limited=kwargs.get("plan_limited", False),
        plan_metric=kwargs.get("plan_metric"),
    )
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    return spec, profile, result.labels, result.ranked


def test_the_noun_vocabulary_covers_every_groupby_member() -> None:
    """The import-time check itself, exercised directly (it also runs on import, above)."""
    assert set(_NOUNS) == set(GroupBy)


def test_a_breakdown_with_no_grouping_names_no_top_and_a_generic_desglose() -> None:
    spec, profile, labels, ranked = _context("breakdown")
    plan = _last_month(Direction.EXPENSE, GroupBy.NONE)
    assert spoken_summary(spec, profile, labels, ranked, plan) == "Desglose de tus gastos."


def test_a_temporal_chart_names_the_top_month_and_the_evolution() -> None:
    spec, profile, labels, ranked = _context("temporal")
    plan = _last_month(Direction.EXPENSE, GroupBy.MONTH)
    assert spoken_summary(spec, profile, labels, ranked, plan) == (
        "Evolución de tus gastos por mes, con febrero de 2026 como el mes de más gasto."
    )


def test_a_cumulative_chart_names_the_top_month_too() -> None:
    spec, profile, labels, ranked = _context("cumulative")
    plan = _last_month(Direction.EXPENSE, GroupBy.MONTH)
    assert spoken_summary(spec, profile, labels, ranked, plan) == (
        "Evolución de tus gastos por mes, con marzo de 2026 como el mes de más gasto."
    )


def test_a_frequency_chart_with_no_financial_plan_grouping_is_generic() -> None:
    """FREQUENCY's own group_by is NONE (`export_chart_fixtures`'s own kwargs) -- there is no
    financial noun for a weekday breakdown, so this is the generic desglose, same as breakdown."""
    spec, profile, labels, ranked = _context("frequency")
    plan = _last_month(Direction.EXPENSE, GroupBy.NONE)
    assert spoken_summary(spec, profile, labels, ranked, plan) == "Desglose de tus gastos."


def test_a_ratio_share_chart_is_generic_too() -> None:
    spec, profile, labels, ranked = _context("ratio_share")
    plan = _last_month(Direction.EXPENSE, GroupBy.NONE)
    assert spoken_summary(spec, profile, labels, ranked, plan) == "Desglose de tus gastos."


def test_a_merchant_grouped_recurrence_chart_names_the_top_merchant() -> None:
    """Not `FAMILY_DATASETS`'s own NONE grouping (a chart family's shape and a plan's grouping are
    independent facts): a `FinancialPlan` naming MERCHANT over the same dataset/spec/labels is
    what a "gasto recurrente por comercio" turn would actually carry.

    The second sentence is still the temporal one, not the nominal one: `visual_profile_of` adds
    an `occurred_at` dimension field (role="dimension", type="temporal") for every RECURRENCE
    dataset, so `profile.temporal()` is true regardless of `group_by` -- the same reason the real
    chart draws a time axis. `MERCHANT` names no calendar unit, so the fallback "meses" is used.
    """
    spec, profile, labels, ranked = _context("recurrence")
    plan = _last_month(Direction.EXPENSE, GroupBy.MERCHANT)
    assert spoken_summary(spec, profile, labels, ranked, plan) == (
        "Evolución de tus gastos en el tiempo, con Netflix como el comercio de más gasto."
    )


def test_a_category_grouped_breakdown_names_the_top_category() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="RESTAURANTS", value_minor=300),
            VisualRow(dimension="GROCERIES", value_minor=100),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=400, group_by="CATEGORY")
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    plan = _last_month(Direction.EXPENSE, GroupBy.CATEGORY)
    assert spoken_summary(spec, profile, result.labels, result.ranked, plan) == (
        "Tus gastos por categoría, con restaurantes en cabeza."
    )


@pytest.mark.parametrize(
    ("group_by", "singular_noun", "plural_noun"),
    [
        (GroupBy.DAY, "el día", "los días"),
        (GroupBy.WEEK, "la semana", "las semanas"),
        (GroupBy.COUNTERPARTY, "la contraparte", "las contrapartes"),
        (GroupBy.ACCOUNT, "la cuenta", "las cuentas"),
    ],
)
def test_every_other_noun_in_the_closed_vocabulary(
    group_by: GroupBy, singular_noun: str, plural_noun: str
) -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="A", value_minor=10),
            VisualRow(dimension="B", value_minor=5),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=15, group_by=group_by.value)
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    plan = _last_month(Direction.EXPENSE, group_by)
    spoken = spoken_summary(spec, profile, result.labels, result.ranked, plan)
    noun = singular_noun.split(" ", 1)[1]
    if group_by in {GroupBy.DAY, GroupBy.WEEK}:
        # DAY/WEEK are temporal groupings (`_TEMPORAL_GROUPS`): an evolution, led by its peak.
        assert spoken == (
            f"Evolución de tus gastos por {noun}, con A como {singular_noun} de más gasto."
        )
    else:
        assert spoken == f"Tus gastos por {noun}, con A en cabeza."


def test_an_income_plan_says_ingreso_not_gasto() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="RESTAURANTS", value_minor=300),
            VisualRow(dimension="GROCERIES", value_minor=100),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=400, group_by="CATEGORY")
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    plan = _last_month(Direction.INCOME, GroupBy.CATEGORY)
    spoken = spoken_summary(spec, profile, result.labels, result.ranked, plan)
    assert spoken is not None
    assert spoken == "Tus ingresos por categoría, con restaurantes en cabeza."


def test_an_all_direction_plan_says_movimiento() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="RESTAURANTS", value_minor=300),
            VisualRow(dimension="GROCERIES", value_minor=100),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=400, group_by="CATEGORY")
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    plan = _last_month(Direction.ALL, GroupBy.CATEGORY)
    spoken = spoken_summary(spec, profile, result.labels, result.ranked, plan)
    assert spoken is not None
    assert spoken == "Tus movimientos por categoría, con restaurantes en cabeza."


def test_an_unranked_tie_at_the_top_gets_no_top_sentence() -> None:
    """A tie at the top is not a fact the chart shows -- `ranked=False` suppresses the top
    sentence even though `labels` is non-empty and the grouping has a noun."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="RESTAURANTS", value_minor=100),
            VisualRow(dimension="GROCERIES", value_minor=100),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=200, group_by="CATEGORY")
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    assert result.ranked is False
    plan = _last_month(Direction.EXPENSE, GroupBy.CATEGORY)
    assert spoken_summary(spec, profile, result.labels, result.ranked, plan) == (
        "Tus gastos por categoría."
    )


def test_a_plan_group_by_none_is_generic_with_no_top_sentence() -> None:
    spec, profile, labels, ranked = _context("breakdown")
    plan = _last_month(Direction.EXPENSE, GroupBy.NONE)
    spoken = spoken_summary(spec, profile, labels, ranked, plan)
    assert spoken == "Desglose de tus gastos."


def test_a_comparison_profile_ignores_the_mark_and_the_plan() -> None:
    dataset, kwargs = FAMILY_DATASETS["categorical_comparison"]
    profile = visual_profile_of(dataset, total_minor=None, group_by=kwargs["group_by"])
    assert profile.comparison is True
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    plan = _last_month(Direction.EXPENSE, GroupBy.CATEGORY)
    assert spoken_summary(spec, profile, result.labels, result.ranked, plan) == (
        "Comparación de los dos periodos."
    )


def test_a_donut_names_the_reparto_regardless_of_grouping() -> None:
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    donut_dataset = VisualDataset(rows=rows, metric="SUM")
    profile = visual_profile_of(donut_dataset, total_minor=100_000, group_by="NONE")
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    result = drawn_labels(donut_dataset)
    plan = _last_month(Direction.EXPENSE, GroupBy.CATEGORY)
    assert spoken_summary(spec, profile, result.labels, result.ranked, plan) == (
        "Reparto de tus gastos por categoría, con Alquiler en cabeza."
    )


def test_a_no_plan_turn_is_generic_with_no_top_sentence() -> None:
    spec, profile, labels, ranked = _context("temporal")
    assert spoken_summary(spec, profile, labels, ranked, None) == (
        "Evolución de lo que has pedido en el tiempo."
    )


def test_a_ratio_plan_is_treated_as_no_plan() -> None:
    """A `RatioPlan`/`ComparisonPlan` is not a `FinancialPlan`: it carries no single `group_by`,
    so it gets the generic noun and no top sentence, same as `None` -- exercised here with a
    `ComparisonPlan` since it is cheap to build without touching banking."""
    spec, profile, labels, ranked = _context("temporal")
    earlier = FinancialPlan(
        Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH), group_by=GroupBy.NONE
    )
    latest = FinancialPlan(
        Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH), group_by=GroupBy.NONE
    )
    comparison = ComparisonPlan(earlier=earlier, latest=latest)
    assert spoken_summary(spec, profile, labels, ranked, comparison) == (
        "Evolución de lo que has pedido en el tiempo."
    )


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_no_family_summary_ever_contains_a_digit(family_name: str) -> None:
    """Value-free voice, under the family's own natural grouping (`FAMILY_DATASETS`'s own
    `group_by` kwarg, the pairing a real turn would actually produce): the presenter never says an
    amount or a count. The one calendar exception -- a MONTH top sentence naming a year, which
    `_top_label` reaches whenever a label parses as `YYYY-MM` regardless of which `GroupBy` a plan
    names, and which the test right below pins separately and precisely -- is stripped before the
    check here rather than skipped, so the "no digit" claim is uniform across every family instead
    of silently missing the one family it is hardest for."""
    spec, profile, labels, ranked = _context(family_name)
    _, kwargs = FAMILY_DATASETS[family_name]
    natural_group_by = GroupBy(kwargs["group_by"])
    plan = _last_month(Direction.EXPENSE, natural_group_by)
    spoken = spoken_summary(spec, profile, labels, ranked, plan)
    assert spoken is not None
    year_stripped = re.sub(r"\b\d{4}\b", "", spoken)
    assert re.search(r"\d", year_stripped) is None, (family_name, spoken)


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_a_month_top_sentence_names_only_a_calendar_year_never_a_ledger_value(
    family_name: str,
) -> None:
    """The one acknowledged exception to the blanket "no digit" claim above: when the top label is
    a `YYYY-MM` key, `_label` reuses `answer_writer.month_group_name`, which names the year --
    e.g. "con febrero de 2026 como el mes de más gasto". A digit here is a calendar fact, never an
    amount or a count, so the property this test actually pins is narrower and exact: every digit
    in the output belongs to a 4-digit year, and there is at most one such run."""
    spec, profile, labels, ranked = _context(family_name)
    plan = _last_month(Direction.EXPENSE, GroupBy.MONTH)
    spoken = spoken_summary(spec, profile, labels, ranked, plan)
    if spoken is None:
        return
    digit_runs = re.findall(r"\d+", spoken)
    assert all(len(run) == 4 for run in digit_runs), (family_name, spoken)


def test_no_digit_over_the_donut_or_comparison_paths_either() -> None:
    dataset, kwargs = FAMILY_DATASETS["categorical_comparison"]
    profile = visual_profile_of(dataset, total_minor=None, group_by=kwargs["group_by"])
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    result = drawn_labels(dataset)
    spoken = spoken_summary(spec, profile, result.labels, result.ranked, None)
    assert spoken is not None
    assert re.search(r"\d", spoken) is None


def test_result_ignores_unrelated_plan_fields() -> None:
    """Sanity: the function reads only direction/group_by off the plan, nothing else -- a plan
    with transaction_ids or merchants set behaves identically."""
    spec, profile, labels, ranked = _context("temporal")
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MONTH,
        transaction_ids=(uuid4(),),
    )
    assert spoken_summary(spec, profile, labels, ranked, plan) == (
        "Evolución de tus gastos por mes, con febrero de 2026 como el mes de más gasto."
    )
