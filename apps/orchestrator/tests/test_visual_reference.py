"""A sentence resolves over the labels the chart in context drew -- one label, an ambiguity, or
nothing (Wave 4.3, Task 2). `resolve_reference` is a pure domain reader: no plan, no amount, no
model call. These tests build the same `SPEC`/`BREAKDOWN_PROFILE` pair `test_visual_follow_up.py`
uses -- the resolver never reads either, so one pair serves every context below."""

import pytest
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.visual_reference import Ambiguous, NoMatch, Resolved
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_reference import resolve_reference

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def _context(labels: tuple[str, ...], *, ranked: bool) -> VisualContext:
    return VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, labels=labels, ranked=ranked)


MONTH_LABELS = ("2026-08", "2026-07", "2026-06")
RANKED_MONTHS = _context(MONTH_LABELS, ranked=True)
UNRANKED_MONTHS = _context(MONTH_LABELS, ranked=False)
TWO_AUGUSTS = _context(("2026-08", "2025-08", "2026-07"), ranked=True)
BREAKDOWN_LABELS = ("RESTAURANTS", "TRANSPORT_FUEL", "GROCERIES")
BREAKDOWN = _context(BREAKDOWN_LABELS, ranked=True)
ONE_LABEL = _context(("GROCERIES",), ranked=True)
WEEKDAY_LABELS = ("Vie", "Mar", "Jue", "Lun", "Mié", "Sáb", "Dom")
WEEKDAYS = _context(WEEKDAY_LABELS, ranked=True)


# --- Months (R7) --------------------------------------------------------------------------------

MONTH_ROWS = [
    ("¿Por qué agosto es tan alto?", Resolved("2026-08", "month")),
    ("¿Por qué agosto de 2026 es tan alto?", Resolved("2026-08", "month")),
    ("¿Por qué septiembre es tan alto?", NoMatch()),
    ("¿Por qué es tan alto?", NoMatch()),
    ("¿Por qué agosto y julio son tan altos?", Ambiguous(("2026-08", "2026-07"))),
]


@pytest.mark.parametrize(("sentence", "expected"), MONTH_ROWS)
def test_a_month_line_resolves_by_name(sentence, expected) -> None:
    assert resolve_reference(sentence, RANKED_MONTHS) == expected


TWO_AUGUST_ROWS = [
    ("agosto", Ambiguous(("2026-08", "2025-08"))),
    ("agosto de 2025", Resolved("2025-08", "month")),
]


@pytest.mark.parametrize(("sentence", "expected"), TWO_AUGUST_ROWS)
def test_two_augusts_the_bare_month_is_ambiguous_the_full_name_picks_one(
    sentence, expected
) -> None:
    assert resolve_reference(sentence, TWO_AUGUSTS) == expected


# --- Superlatives, only when nothing was named --------------------------------------------------

RANKED_SUPERLATIVE_ROWS = [
    ("¿Qué movimientos explican ese pico?", Resolved("2026-08", "rank")),
    ("la barra más alta", Resolved("2026-08", "rank")),
    ("el mes más bajo", Resolved("2026-06", "rank")),
    ("el mayor", Resolved("2026-08", "rank")),
    ("del más alto al más bajo", NoMatch()),
]


@pytest.mark.parametrize(("sentence", "expected"), RANKED_SUPERLATIVE_ROWS)
def test_a_superlative_over_a_ranked_chart_names_the_top_or_bottom_bar(sentence, expected) -> None:
    assert resolve_reference(sentence, RANKED_MONTHS) == expected


def test_a_superlative_over_an_unranked_chart_asks_instead_of_guessing() -> None:
    assert resolve_reference("¿Qué movimientos explican ese pico?", UNRANKED_MONTHS) == Ambiguous(
        MONTH_LABELS
    )


def test_a_name_wins_over_a_superlative_in_the_same_sentence() -> None:
    assert resolve_reference("¿Por qué agosto es el más alto?", RANKED_MONTHS) == Resolved(
        "2026-08", "month"
    )


# --- Categories -----------------------------------------------------------------------------------

_RESTAURANT_WORD = category_words._PRECISE["RESTAURANTS"][0]

BREAKDOWN_ROWS = [
    (f"¿Por qué he gastado tanto en {_RESTAURANT_WORD}?", Resolved("RESTAURANTS", "category")),
    ("¿Por qué la gasolina es tan cara?", Resolved("TRANSPORT_FUEL", "category")),
    (
        f"¿Por qué gasté tanto en gasolina y en {_RESTAURANT_WORD}?",
        Ambiguous(("RESTAURANTS", "TRANSPORT_FUEL")),
    ),
    ("¿Por qué el alquiler es tan alto?", NoMatch()),
]


@pytest.mark.parametrize(("sentence", "expected"), BREAKDOWN_ROWS)
def test_a_breakdown_resolves_by_category_name_in_chart_order(sentence, expected) -> None:
    assert resolve_reference(sentence, BREAKDOWN) == expected


# --- No labels: every reading above is a NoMatch -------------------------------------------------

NO_LABEL_SENTENCES = (
    [sentence for sentence, _ in MONTH_ROWS]
    + [sentence for sentence, _ in TWO_AUGUST_ROWS]
    + [sentence for sentence, _ in RANKED_SUPERLATIVE_ROWS]
    + [sentence for sentence, _ in BREAKDOWN_ROWS]
    + ["¿Por qué agosto es el más alto?"]
)

NO_LABELS = _context((), ranked=True)


@pytest.mark.parametrize("sentence", NO_LABEL_SENTENCES)
def test_no_labels_is_always_no_match(sentence) -> None:
    assert resolve_reference(sentence, NO_LABELS) == NoMatch()


# --- One label ------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", ["el más alto", "el más bajo"])
def test_one_label_a_superlative_names_the_only_bar(sentence) -> None:
    assert resolve_reference(sentence, ONE_LABEL) == Resolved("GROCERIES", "rank")


# --- Weekdays are not resolved (R14, YAGNI) -------------------------------------------------------


def test_a_weekday_reference_is_not_resolved() -> None:
    assert resolve_reference("¿Por qué los martes gasto tanto?", WEEKDAYS) == NoMatch()


# --- The type itself ------------------------------------------------------------------------------


def test_an_ambiguity_needs_at_least_two_candidates() -> None:
    with pytest.raises(ValueError):
        Ambiguous(("x",))


# --- Architecture: domain imports nothing from application/infrastructure -------------------------


def test_the_new_domain_modules_import_nothing_from_application_or_infrastructure() -> None:
    """No suite-wide test sweeps every `domain/` module for this already (searched; the existing
    import-sweeps -- `test_visual_context_producers.py`'s two, `test_otel.py`'s one -- each pin a
    single named call site or package, not a domain-wide layering rule), so this pins it for the
    two modules this task adds."""
    import ast
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"
    paths = [
        root / "domain" / "models" / "visual_reference.py",
        root / "domain" / "services" / "visual_reference.py",
    ]
    banned = ("finguard_orchestrator.application", "finguard_orchestrator.infrastructure")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert not any(name.startswith(prefix) for prefix in banned), (
                    f"{path.name} imports {name}"
                )
