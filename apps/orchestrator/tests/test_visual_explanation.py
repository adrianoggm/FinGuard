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
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_explanation import (
    _MEASURE_WORDS,
    WhyAsked,
    _nouns_of,
    asks_why,
    explain_chart,
)
from finguard_orchestrator.domain.services.visual_profile import (
    MAX_DRAWN_LABELS,
    drawn_labels,
    visual_profile_of,
)
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange
from finguard_orchestrator.infrastructure.mappers import conversation_mapper as mapper

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
# The nouns now come from the labels each family actually draws (`FAMILY_DATASETS` in
# `scripts/export_chart_fixtures.py`): "breakdown" mixes a category key (GROCERIES) with two plain
# Spanish words (ALQUILER, ZAPATOS) so it stays generic; "temporal"/"cumulative" draw `YYYY-MM`
# month labels; "frequency" draws weekday abbreviations (generic); "recurrence" draws merchant
# names (generic) but its dimension is now `occurred_at` (temporal) so it gains an ordering
# sentence (Task 2 review I-1); "ratio_share" draws "Parte"/"Resto" (generic); and
# "categorical_comparison" draws two real category keys (GROCERIES, RESTAURANTS), both in
# `category_words._NAMES`, so it is the one family that reads "categorías".
_EXPECTED: dict[str, str] = {
    "breakdown": (
        "Barras: comparas grupos entre sí. Orden: de mayor a menor importe. "
        "Datos: 3 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "temporal": (
        "Líneas: ves cómo cambia el importe a lo largo de los meses. Orden: cronológico. "
        "Datos: 3 meses entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "cumulative": (
        "Área: ves cómo se acumula el importe a lo largo de los meses. Orden: cronológico. "
        "Datos: 3 meses entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "frequency": (
        "Barras: comparas grupos entre sí. "
        "Datos: 7 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "recurrence": (
        "Puntos: cada uno es un grupo y su número de movimientos. Orden: cronológico. "
        "Datos: 2 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "ratio_share": (
        "Barras: comparas grupos entre sí. "
        "Datos: 2 grupos entre el 1 de junio y el 31 de agosto de 2026. "
        "Elegido por las reglas de FinGuard."
    ),
    "categorical_comparison": (
        "Dos periodos lado a lado, para que compares el mismo importe en cada uno. "
        "Orden: de mayor a menor importe. "
        "Datos: 2 categorías entre el 1 de junio y el 31 de agosto de 2026. "
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
    context = VisualContext(
        chart_id="c1",
        spec=spec,
        profile=profile,
        labels=("A", "B", "C"),
        source="deterministic",
    )
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
    context = VisualContext(
        chart_id="c1", spec=spec, profile=profile, labels=("A",), source="deterministic"
    )
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: 1 grupo."


def test_the_count_comes_from_the_labels_that_crossed_the_wire_not_row_count() -> None:
    """Task 2 (Wave 3 hardening, U-4/C-5): `profile.row_count` now survives `to_domain` (it is a
    real wire field), but `_scope_summary` must still count `context.labels` -- the one thing a
    later turn can resolve a reference against -- rather than switch to reading the now-available
    `row_count`, which is not authority and can legitimately differ (a plan-limited chart's rows
    are capped while its labels are not, or vice versa)."""
    context = _context("categorical_comparison")
    conversation = IDLE.with_visual_context(context)
    payload = mapper.to_payload(conversation)
    echoed = mapper.to_domain(payload)
    wired_context = echoed.visual_context()
    assert wired_context is not None
    assert wired_context.profile.row_count == context.profile.row_count
    minted_text = explain_chart(context).scope_summary
    wired_text = explain_chart(wired_context).scope_summary
    assert minted_text == wired_text
    assert not wired_text.startswith("Datos: 0 ")
    assert wired_text.startswith(f"Datos: {len(context.labels)} ")


def test_a_capped_label_list_omits_the_count() -> None:
    labels = tuple(f"L{i}" for i in range(MAX_DRAWN_LABELS))
    spec, profile = _spec_and_profile("breakdown")
    context = VisualContext(
        chart_id="c1", spec=spec, profile=profile, labels=labels, source="deterministic"
    )
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: los grupos."


def test_no_labels_omits_the_count() -> None:
    spec, profile = _spec_and_profile("breakdown")
    context = VisualContext(
        chart_id="c1", spec=spec, profile=profile, labels=(), source="deterministic"
    )
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: los grupos."


def test_omitting_the_count_agrees_in_gender_with_a_feminine_noun() -> None:
    """`categoría`/`categorías` is feminine ("una categoría"): the omitted-count phrasing must
    read "las categorías", not "los categorías" -- the bug a hard-coded "los" would have been.
    A capped label list of real category keys drives the noun to "categorías" and the omitted
    count at the same time."""
    labels = tuple(("GROCERIES", "RESTAURANTS")[i % 2] for i in range(MAX_DRAWN_LABELS))
    spec, profile = _spec_and_profile("categorical_comparison")
    context = VisualContext(
        chart_id="c1", spec=spec, profile=profile, labels=labels, source="deterministic"
    )
    explanation = explain_chart(context)
    assert explanation.scope_summary == "Datos: las categorías."


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


def test_measure_metric_values_found_map_to_a_word() -> None:
    for family_name in FAMILY_DATASETS:
        _, profile = _spec_and_profile(family_name)
        assert profile.measure_metric() in _MEASURE_WORDS


_DATE_PATTERN = re.compile(r"\b\d{1,2} de [a-zé]+( de \d{4})?\b")
_COUNT_PATTERN = re.compile(
    r"\b\d+ (grupos?|categorías?|meses?|comercios?|días?|semanas?|cuentas?|contrapartes?)\b"
)
# "número de movimientos" (the FREQUENCY/RECURRENCE measure word, R11) carries no digit of its own
# -- unlike the old "movimiento" word it replaces, it does not need a place in `_COUNT_PATTERN`.


def test_nouns_of_a_month_label_tuple() -> None:
    assert _nouns_of(("2026-01", "2026-02")) == ("mes", "meses", "un mes")


def test_nouns_of_an_iso_date_label_tuple() -> None:
    assert _nouns_of(("2026-01-05", "2026-01-12")) == ("día", "días", "un día")


def test_nouns_of_a_category_key_label_tuple() -> None:
    assert _nouns_of(("GROCERIES", "RESTAURANTS")) == ("categoría", "categorías", "una categoría")


def test_nouns_of_free_strings_is_generic() -> None:
    assert _nouns_of(("Netflix", "Spotify")) == ("grupo", "grupos", "un grupo")


def test_nouns_of_a_mixed_label_tuple_is_generic() -> None:
    assert _nouns_of(("2026-01", "GROCERIES")) == ("grupo", "grupos", "un grupo")


def test_nouns_of_an_empty_tuple_is_generic() -> None:
    assert _nouns_of(()) == ("grupo", "grupos", "un grupo")


@pytest.mark.parametrize(
    ("metric", "word"),
    [
        ("SUM", "importe"),
        ("COMPARISON", "importe"),
        ("CUMULATIVE", "importe"),
        ("FREQUENCY", "número de movimientos"),
        ("RECURRENCE", "número de movimientos"),
        ("RATIO_SHARE", "peso"),
    ],
)
def test_measure_word_per_metric(metric: str, word: str) -> None:
    assert _MEASURE_WORDS[metric] == word


def test_the_measure_word_vocabulary_is_closed() -> None:
    assert set(_MEASURE_WORDS) == {
        "SUM",
        "CUMULATIVE",
        "FREQUENCY",
        "RECURRENCE",
        "RATIO_SHARE",
        "COMPARISON",
    }


@pytest.mark.parametrize("family_name", sorted(_EXPECTED))
def test_no_amount_survives_into_the_text(family_name: str) -> None:
    text = explain_chart(_context(family_name)).as_text()
    stripped = _COUNT_PATTERN.sub("", _DATE_PATTERN.sub("", text))
    assert not any(character.isdigit() for character in stripped)
    assert "€" not in text
    assert "%" not in text


# --- Wave 4.5, Task 4: `asks_why` -- the recogniser for «¿Por qué este gráfico?» ----------------
#
# Grammar (over `fold(sentence)`, anchored to the whole sentence -- a longer sentence is a
# question, not this, the same principle as `_VISUAL_ALL`'s comment): optional leading punctuation,
# `por que`, then one of four shapes. Claimed sentences resolve to a `WhyAsked`; everything else,
# including any sentence carrying a `_FINANCIAL` word, is `None` -- the recogniser abstains rather
# than guesses.


@pytest.mark.parametrize(
    ("sentence", "target"),
    [
        ("¿Por qué este gráfico?", None),
        ("¿Por qué ese gráfico?", None),
        ("¿Por qué el gráfico?", None),
        ("¿Por qué el primer gráfico?", "primer"),
        ("¿Por qué el último gráfico?", "ultimo"),
        ("¿Por qué el segundo gráfico?", "segund"),
        ("¿Por qué en barras?", None),
        ("¿Por qué barras?", None),
        ("¿Por qué en líneas?", None),
        ("¿Por qué circular?", None),
        ("¿Por qué este orden?", None),
        ("¿Por qué así ordenado?", None),
        ("¿Por qué de esta forma ordenado?", None),
        ("¿Por qué me lo has puesto así?", None),
        ("¿Por qué está así?", None),
        ("Por qué este gráfico", None),  # no leading ¿, no trailing ?
    ],
)
def test_asks_why_claims_every_phrasing_the_brief_lists(sentence: str, target: str | None) -> None:
    why = asks_why(sentence)
    assert why is not None, sentence
    if target is None:
        assert why.target is None, sentence
    else:
        assert why.target is not None and target in why.target, sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Por qué gasté tanto en junio?",  # a drill: a _FINANCIAL word (a month)
        "¿Por qué agosto es tan alto?",  # a drill: a _FINANCIAL word (a month)
        "¿Por qué me has mostrado eso?",  # about_the_answer, not about the chart
        "Ponlo en barras",  # a restyle instruction, not a question
        "¿Por qué?",  # nothing to explain -- abstain
        "¿Por qué gasto tanto en restaurantes?",  # a _FINANCIAL word
    ],
)
def test_asks_why_does_not_claim_a_sentence_that_is_not_this(sentence: str) -> None:
    assert asks_why(sentence) is None, sentence


def test_why_asked_is_a_frozen_dataclass_with_a_target_field() -> None:
    why = WhyAsked(target=None)
    assert why.target is None
    with pytest.raises(Exception):  # noqa: B017, PT011 - frozen: any exception says immutable
        why.target = "primer"  # type: ignore[misc]
