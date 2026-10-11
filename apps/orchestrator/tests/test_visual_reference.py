"""What is left of the chart-reference reader (Wave 4.3, Task 2): `parsed_month`, the one reading
of a chart's month label the rest of the service still asks for."""

from finguard_orchestrator.domain.services.visual_reference import parsed_month
from finguard_orchestrator.domain.services.window_words import whole_month

# --- Architecture: domain imports nothing from application/infrastructure -------------------------


def test_the_new_domain_modules_import_nothing_from_application_or_infrastructure() -> None:
    """No suite-wide test sweeps every `domain/` module for this already (searched; the existing
    import-sweeps -- `test_visual_context_producers.py`'s two, `test_otel.py`'s one -- each pin a
    single named call site or package, not a domain-wide layering rule), so this pins it for the
    module this task added."""
    import ast
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"
    paths = [
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


# --- C-4 (Wave 3 hardening): a month label the resolver accepts is a month the calendar has ------
#
# `_MONTH_LABEL` is a bare `(\d{4})-(\d{2})` with no bound on the year, so a ledger group key of
# "0000-01" -- a boundary or unbucketed row -- parsed to `(0, 1)`, and `whole_month(0, 1)` then
# built `date(0, 1, 1)`, an unhandled `ValueError` neither a `DomainError` nor a pydantic
# `ValidationError` maps to anything but an HTTP 500. `20\d{2}` is the bound every other year this
# service reads already uses (`window_words.py`'s own named-year and quarter/semester patterns),
# so this is not a new range, only the one this reader had never enforced.


def test_year_zero_is_not_a_month_label() -> None:
    assert parsed_month("0000-01") is None


def test_a_year_outside_the_ledgers_range_is_not_a_month_label() -> None:
    for label in ("0000-01", "1999-12", "2100-01", "9999-12"):
        assert parsed_month(label) is None, label


def test_every_label_parsed_month_accepts_is_a_month_whole_month_can_build() -> None:
    for label in ("2000-01", "2026-08", "2099-12"):
        parsed = parsed_month(label)
        assert parsed is not None, label
        # Never raises -- the C-4 guarantee itself, not just a bound on the year.
        whole_month(*parsed)
