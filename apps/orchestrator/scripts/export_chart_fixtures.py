"""One rendered spec per chart family, for the web compile gate.

The Vega-Lite compiler only exists in JavaScript, so the honest place for "does this actually
compile" is a vitest test -- and the specs it must compile are the ones this side emits. This
script is the bridge: it renders every family and writes them where the web test reads them.

Run it whenever a family is added or the presenter changes:

    uv run python scripts/export_chart_fixtures.py
"""

import json
from pathlib import Path
from typing import Any

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    VisualDataset,
    Visualization,
    VisualRow,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.infrastructure.visualization.vega_lite import present

# Where the web test reads what this script writes.
FIXTURES = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "__fixtures__" / "chart-families.json"
)


def _rows_to_dicts(rows: tuple[VisualRow, ...]) -> list[dict[str, Any]]:
    """The same row shape `llm_mapper._visualization_to_dto` puts on the wire."""
    serialized: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"dimension": row.dimension, "value_minor": row.value_minor}
        if row.series is not None:
            item["series"] = row.series
        if row.occurred_at is not None:
            item["occurred_at"] = row.occurred_at
        serialized.append(item)
    return serialized


def _family(
    name: str,
    dataset: VisualDataset,
    *,
    group_by: str,
    total_minor: int | None = None,
    plan_limited: bool = False,
    plan_metric: str | None = None,
) -> dict[str, Any]:
    """Run one dataset through the real path: profile -> floor -> presenter."""
    profile = visual_profile_of(
        dataset,
        total_minor=total_minor,
        group_by=group_by,
        plan_limited=plan_limited,
        plan_metric=plan_metric,
    )
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    if spec is None:
        raise ValueError(f"the floor produced no chart for family {name!r}")
    presented = present(Visualization(spec=spec, data=tuple(_rows_to_dicts(dataset.rows))))
    return {"name": name, "spec": presented["spec"], "data": presented["data"]}


def _donut_family() -> dict[str, Any]:
    # The floor never produces a donut: `DeterministicVisualPlanner.plan` has no branch that
    # returns `mark="donut"`. It is reachable only through a conversational transformation
    # (`merge_encoding_patch` in `chart.py`, driven by an explicit "make it a donut" patch), and
    # it is the one mark with its own `__post_init__` rule (`ChartSpec.__post_init__`: "donut
    # cannot be stacked"). A gate that skipped it would leave that mark -- and the presenter's
    # `"arc"` translation of it (`vega_lite.py`: `mark == "donut"` -> `{"type": "arc",
    # "innerRadius": 55}`) -- unmeasured. Built directly here for that reason.
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    presented = present(Visualization(spec=spec, data=tuple(_rows_to_dicts(rows))))
    return {"name": "donut", "spec": presented["spec"], "data": presented["data"]}


def build_chart_families() -> list[dict[str, Any]]:
    """Every family the backend can emit, rendered through the same path production uses.

    Ruling A: `group_by` is `"MONTH"` for the temporal and cumulative families (their mark
    depends on a calendar bucket) and `"NONE"` for the rest.
    """
    families = [
        _family(
            "breakdown",
            VisualDataset(
                metric="SUM",
                rows=(
                    VisualRow(dimension="ALQUILER", value_minor=51_300),
                    VisualRow(dimension="GROCERIES", value_minor=24_908),
                    VisualRow(dimension="ZAPATOS", value_minor=1_200),
                ),
            ),
            group_by="NONE",
        ),
        _family(
            "temporal",
            VisualDataset(
                metric="SUM",
                rows=(
                    VisualRow(dimension="2026-01", value_minor=10_000),
                    VisualRow(dimension="2026-02", value_minor=20_000),
                    VisualRow(dimension="2026-03", value_minor=15_000),
                ),
            ),
            group_by="MONTH",
        ),
        _family(
            "cumulative",
            VisualDataset(
                metric="CUMULATIVE",
                rows=(
                    VisualRow(dimension="2026-01", value_minor=10_000),
                    VisualRow(dimension="2026-02", value_minor=30_000),
                    VisualRow(dimension="2026-03", value_minor=45_000),
                ),
            ),
            group_by="MONTH",
        ),
        # `INPUT` order: weekday names in producer order, not ranked by magnitude.
        _family(
            "frequency",
            VisualDataset(
                metric="FREQUENCY",
                rows=(
                    VisualRow(dimension="Lun", value_minor=3),
                    VisualRow(dimension="Mar", value_minor=5),
                    VisualRow(dimension="Mié", value_minor=2),
                    VisualRow(dimension="Jue", value_minor=4),
                    VisualRow(dimension="Vie", value_minor=6),
                    VisualRow(dimension="Sáb", value_minor=1),
                    VisualRow(dimension="Dom", value_minor=0),
                ),
            ),
            group_by="NONE",
        ),
        # Mirrors `_visual_dataset`'s RECURRENCE branch in `ask_financial_question.py`: the only
        # production site that sets `occurred_at`, and it always sets `series` (the cadence)
        # alongside it.
        _family(
            "recurrence",
            VisualDataset(
                metric="RECURRENCE",
                rows=(
                    VisualRow(
                        dimension="Netflix",
                        value_minor=1_299,
                        series="MONTHLY",
                        occurred_at="2026-01-05",
                    ),
                    VisualRow(
                        dimension="Netflix",
                        value_minor=1_299,
                        series="MONTHLY",
                        occurred_at="2026-02-05",
                    ),
                    VisualRow(
                        dimension="Spotify",
                        value_minor=999,
                        series="MONTHLY",
                        occurred_at="2026-01-12",
                    ),
                ),
            ),
            group_by="NONE",
        ),
        # Mirrors `_answer_ratio`'s SHARE branch: two rows, "Parte" and "Resto".
        _family(
            "ratio_share",
            VisualDataset(
                metric="RATIO_SHARE",
                rows=(
                    VisualRow(dimension="Parte", value_minor=7_000),
                    VisualRow(dimension="Resto", value_minor=3_000),
                ),
            ),
            group_by="NONE",
            total_minor=10_000,
        ),
        # Mirrors the grouped-comparison rows built beside `comparison_visual_rows`: a category
        # per dimension, a window per series, and the producer's own metric marker
        # (`ask_financial_question.py:3318-3320` sets `metric="COMPARISON"` on this dataset).
        _family(
            "categorical_comparison",
            VisualDataset(
                metric="COMPARISON",
                rows=(
                    VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
                    VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
                    VisualRow(dimension="RESTAURANTS", value_minor=50, series="2026-07"),
                    VisualRow(dimension="RESTAURANTS", value_minor=70, series="2026-06"),
                ),
            ),
            group_by="NONE",
        ),
        _donut_family(),
    ]
    return families


if __name__ == "__main__":
    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES.write_text(
        json.dumps(build_chart_families(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
