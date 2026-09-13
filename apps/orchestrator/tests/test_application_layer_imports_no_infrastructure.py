"""F-5 (fix wave, A-12/Important 1): `application/` renders a chart through a port, not through
`infrastructure` directly.

The whole-branch review found one `application -> infrastructure` import --
`application/metrics_executor.py:12`, `from finguard_orchestrator.infrastructure.visualization.
vega_lite import present` -- the only such import in the service, and A-12's own finding (design
audit) silently dropped along the way. `domain/` already has an equivalent guard (the review's own
`grep -rE "application|infrastructure" domain/` returns zero); this is the same rule one layer up,
parsed rather than grepped so a docstring or a comment naming `infrastructure` in passing is not a
false failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APPLICATION = Path(__file__).resolve().parents[1] / "src" / "finguard_orchestrator" / "application"
_SOURCES = sorted(_APPLICATION.rglob("*.py"))


def test_the_glob_found_the_application_layer() -> None:
    assert len(_SOURCES) >= 20, [p.name for p in _SOURCES]


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: str(p.relative_to(_APPLICATION)))
def test_no_application_module_imports_infrastructure(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    offenders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and (
            node.module == "finguard_orchestrator.infrastructure"
            or node.module.startswith("finguard_orchestrator.infrastructure.")
        )
    ]
    assert offenders == [], (
        f"{source.relative_to(_APPLICATION)} line {offenders[0].lineno} imports "
        f"{offenders[0].module} -- application renders a chart (or reaches any other "
        f"infrastructure module) through a port it defines, never through the module directly."
    )
