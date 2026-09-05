import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "export_chart_fixtures",
    Path(__file__).resolve().parents[1] / "scripts" / "export_chart_fixtures.py",
)
assert _SPEC and _SPEC.loader
export_chart_fixtures = importlib.util.module_from_spec(_SPEC)
sys.modules["export_chart_fixtures"] = export_chart_fixtures
_SPEC.loader.exec_module(export_chart_fixtures)


def test_the_exported_fixtures_match_what_the_presenter_emits_today() -> None:
    """A committed fixture is a snapshot, and a snapshot nobody regenerates is a gate that has
    stopped measuring. This fails the build when the presenter changes and the export does not."""
    exported = json.loads(export_chart_fixtures.FIXTURES.read_text(encoding="utf-8"))
    assert exported == export_chart_fixtures.build_chart_families()
