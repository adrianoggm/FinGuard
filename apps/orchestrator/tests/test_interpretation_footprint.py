"""The footprint counts non-blank, non-comment lines in two fixed lists of files."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "interpretation_footprint", ROOT / "scripts" / "interpretation_footprint.py"
)
assert _SPEC and _SPEC.loader
footprint = importlib.util.module_from_spec(_SPEC)
sys.modules["interpretation_footprint"] = footprint
_SPEC.loader.exec_module(footprint)


def test_blank_and_comment_lines_are_not_counted(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("# a comment\n\nx = 1\n    # indented comment\ny = 2  # trailing\n")
    assert footprint.count_lines(source) == 2


def test_globs_expand_and_a_missing_file_is_reported_not_skipped(tmp_path: Path) -> None:
    (tmp_path / "readers").mkdir()
    (tmp_path / "readers" / "a.py").write_text("x = 1\n")
    (tmp_path / "readers" / "b.py").write_text("y = 1\nz = 2\n")
    result = footprint.measure(tmp_path, ["readers/*.py", "gone.py"])
    assert result["files"] == {"readers/a.py": 1, "readers/b.py": 2}
    assert result["missing"] == ["gone.py"] and result["lines"] == 3


def test_the_lists_name_real_files_of_the_tree() -> None:
    report = footprint.build(footprint.default_root())
    for group in ("rules", "engine", "non_linguistic", "storage"):
        assert report[group]["missing"] == [], group
        assert report[group]["lines"] > 0, group
    assert "domain/engine/grounding.py" in report["non_linguistic"]["files"]
    assert not any("generalization" in name for group in report.values() for name in group["files"])
