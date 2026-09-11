"""`domain/services/visual_explanation.py` (Wave 4.5, Task 2): red first.

Contexts for the seven `FAMILY_DATASETS` are built the same way `test_spoken_presenter.py` builds
them -- `visual_profile_of` then `DeterministicVisualPlanner().plan` for the spec, `drawn_labels`
for the labels/ranked pair -- loaded off `scripts/export_chart_fixtures.py` directly, since
`scripts/` is not a package.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    VisualContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_explanation import (
    _MEDIDA_WORDS,
    _NOUNS,
    explain_chart,
)
from finguard_orchestrator.domain.services.visual_profile import drawn_labels, visual_profile_of
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange

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
_WINDOW = AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 8, 31))


def _spec_and_profile(family_name: str) -> tuple[ChartSpec, Any]:
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
    return spec, profile


def _context(
    family_name: str,
    *,
    window: AbsoluteRange | None = _WINDOW,
    source: str | None = "deterministic",
    fallback_reason: str | None = None,
) -> VisualContext:
    spec, profile = _spec_and_profile(family_name)
    dataset, _ = FAMILY_DATASETS[family_name]
    result = drawn_labels(dataset)
    return VisualContext(
        chart_id="c1",
        spec=spec,
        profile=profile,
        labels=result.labels,
        ranked=result.ranked,
        window=window,
        source=source,
        fallback_reason=fallback_reason,
    )


# family -> expected as_text(), verified once the module exists and pinned in the report table.
_EXPECTED: dict[str, str] = {
    "breakdown": (
        "Barras: comparas grupos entre sí. Orden: de mayor a menor movimiento. "
        "Datos: 3 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "temporal": (
        "Líneas: ves cómo cambia el movimiento a lo largo de los grupos. Orden: cronológico. "
        "Datos: 3 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "cumulative": (
        "Área: ves cómo se acumula el movimiento a lo largo de los grupos. Orden: cronológico. "
        "Datos: 3 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "frequency": (
        "Barras: comparas grupos entre sí. "
        "Datos: 7 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "recurrence": (
        "Puntos: cada uno es un grupo y su movimiento. "
        "Datos: 3 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "ratio_share": (
        "Barras: comparas grupos entre sí. "
        "Datos: 2 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "categorical_comparison": (
        "Dos periodos lado a lado, para que compares el mismo movimiento en cada uno. "
        "Orden: de mayor a menor movimiento. "
        "Datos: 4 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
}


@pytest.mark.parametrize("family_name", sorted(_EXPECTED))
def test_every_family_explains_itself(family_name: str) -> None:
    context = _context(family_name)
    assert explain_chart(context).as_text() == _EXPECTED[family_name]


def test_a_donut_names_the_reparto_and_the_indefinite_noun() -> None:
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
    context = VisualContext(
        chart_id="c1", spec=spec, profile=profile, window=_WINDOW, source="deterministic"
    )
    explanation = explain_chart(context)
    assert explanation.chart_reason == "Reparto: cada porción es el peso de un grupo en el total."
    assert explanation.ordering_reason is None


def test_a_limit_makes_the_ordering_sentence_when_there_was_none() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=tuple(VisualRow(dimension=f"D{i}", value_minor=i + 1) for i in range(9)),
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    spec = _PLANNER.plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    assert spec.limit is not None
    context = VisualContext(chart_id="c1", spec=spec, profile=profile, source="deterministic")
    explanation = explain_chart(context)
    assert explanation.ordering_reason is not None
    assert explanation.ordering_reason.endswith(f"Solo {spec.limit} grupos.")


def test_plan_limited_appends_the_caveat() -> None:
    spec, profile = _spec_and_profile("breakdown")
    profile = profile.__class__(**{**profile.__dict__, "plan_limited": True})
    context = VisualContext(chart_id="c1", spec=spec, profile=profile, source="deterministic")
    explanation = explain_chart(context)
    assert explanation.scope_summary.endswith("(los primeros que pediste)")


def test_no_window_omits_the_date_range() -> None:
    spec, profile = _spec_and_profile("breakdown")
    context = VisualContext(chart_id="c1", spec=spec, profile=profile, source="deterministic")
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: 3 grupos."


def test_a_single_row_uses_the_singular_noun() -> None:
    dataset = VisualDataset(metric="SUM", rows=(VisualRow(dimension="A", value_minor=10),))
    profile = visual_profile_of(dataset, total_minor=10, group_by="NONE")
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", "Grupo"),
            ChartEncoding("x", "value_minor", "quantitative", "Importe"),
        ),
    )
    context = VisualContext(chart_id="c1", spec=spec, profile=profile, source="deterministic")
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: 1 grupo."


@pytest.mark.parametrize(
    ("source", "fallback_reason", "expected"),
    [
        ("llm", None, "Propuesto por el asistente y comprobado por las reglas de FinGuard."),
        ("deterministic", None, "Elegido por las reglas de FinGuard."),
        ("deterministic", "timeout", "Visualización segura seleccionada automáticamente."),
        ("patch", None, "Ajustado a petición tuya sobre el gráfico anterior."),
        (None, None, None),
    ],
)
def test_every_provenance_line(
    source: str | None, fallback_reason: str | None, expected: str | None
) -> None:
    spec, profile = _spec_and_profile("breakdown")
    context = VisualContext(
        chart_id="c1",
        spec=spec,
        profile=profile,
        source=source,
        fallback_reason=fallback_reason,
    )
    assert explain_chart(context).provenance == expected


def test_the_mark_vocabulary_is_closed() -> None:
    from typing import get_args

    from finguard_orchestrator.domain.models.chart import Mark
    from finguard_orchestrator.domain.services.visual_explanation import _MARK_REASONS

    assert set(_MARK_REASONS) == set(get_args(Mark))


def test_the_source_vocabulary_is_closed() -> None:
    from typing import get_args

    from finguard_orchestrator.domain.models.chart import VisualSource
    from finguard_orchestrator.domain.services.visual_explanation import _PROVENANCE_LINES

    assert set(_PROVENANCE_LINES) == set(get_args(VisualSource))


def test_every_familys_dimension_name_is_in_the_noun_mapping() -> None:
    for family_name in FAMILY_DATASETS:
        _, profile = _spec_and_profile(family_name)
        dimension = next(iter(profile.role("dimension")), None)
        assert dimension is not None
        assert dimension.name in _NOUNS


def test_measure_metric_values_found_map_to_a_word() -> None:
    for family_name in FAMILY_DATASETS:
        _, profile = _spec_and_profile(family_name)
        assert profile.measure_metric() in _MEDIDA_WORDS


_DATE_PATTERN = re.compile(r"\b\d{1,2} de [a-zé]+( de \d{4})?\b")
_COUNT_PATTERN = re.compile(
    r"\b\d+ (grupos?|categorías?|meses?|comercios?|días?|semanas?|cuentas?|contrapartes?)\b"
)


@pytest.mark.parametrize("family_name", sorted(_EXPECTED))
def test_no_amount_survives_into_the_text(family_name: str) -> None:
    text = explain_chart(_context(family_name)).as_text()
    stripped = _COUNT_PATTERN.sub("", _DATE_PATTERN.sub("", text))
    assert not any(character.isdigit() for character in stripped)
    assert "€" not in text
    assert "%" not in text
