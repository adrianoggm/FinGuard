"""The hexagonal layering, checked on every run rather than remembered.

Read from the source with `ast`, so it needs nothing installed and sees lazy and type-only imports
too. The layers, innermost first: `domain` (models, value objects, ports, services), `application`
(use cases, buses, the conversation graph), `infrastructure` (HTTP, DTOs, mappers, clients),
`bootstrap` (the composition root). `observability` is the cross-cutting record the inner layers
write to; it imports no layer of its own.

Each rule here was broken in this codebase until October 2026, and two of the breaks stopped the
service from starting. An exception is a line in `ALLOWED` with the reason beside it, never a
silent edit of a rule.
"""

import ast
from pathlib import Path

PACKAGE = "finguard_orchestrator"
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE
LAYERS = ("domain", "application", "infrastructure", "bootstrap")
FRAMEWORKS = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "asyncpg",
    "httpx",
    "httpx2",
    "psycopg",
    "langgraph",
    "opentelemetry",
)

# (importing module prefix, imported top-level name) -> why it is tolerated.
ALLOWED = {
    # The graph is the application's turn orchestration and is written against LangGraph's state
    # graph and checkpointer. Moving the framework behind a port is open work (see
    # docs/architecture/langgraph-legacy-runtime-audit.md); until then it is confined here.
    ("application.graph", "langgraph"): "the conversation graph is built on LangGraph",
}


def _modules() -> dict[str, Path]:
    found = {}
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        found[".".join(p for p in parts if p != "__init__")] = path
    return found


MODULES = _modules()


def _imports(path: Path) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.lineno, node.module))
            out += [(node.lineno, f"{node.module}.{alias.name}") for alias in node.names]
    return out


def _layer(module: str) -> str:
    return module.split(".")[0]


def _allowed(module: str, target: str) -> bool:
    return any(module.startswith(prefix) and target == name for prefix, name in ALLOWED)


def test_layers_only_import_inwards() -> None:
    broken = []
    for module, path in MODULES.items():
        mine = _layer(module)
        for line, name in _imports(path):
            if not name.startswith(f"{PACKAGE}."):
                continue
            target = _layer(name.removeprefix(f"{PACKAGE}."))
            if mine in LAYERS and target in LAYERS and LAYERS.index(target) > LAYERS.index(mine):
                broken.append(f"{module}:{line} imports {name}")
            if mine == "observability" and target in LAYERS:
                broken.append(f"{module}:{line} imports {name}")
    assert broken == []


def test_the_inner_layers_import_no_framework() -> None:
    broken = []
    for module, path in MODULES.items():
        if _layer(module) not in ("domain", "application", "observability"):
            continue
        for line, name in _imports(path):
            top = name.split(".")[0]
            if top in FRAMEWORKS and not _allowed(module, top):
                broken.append(f"{module}:{line} imports {name}")
    assert broken == []


def test_ports_live_in_the_domain() -> None:
    """A `Protocol` outside `domain/ports` is a port somebody forgot to put where ports are."""
    allowed = {
        "application.bus.command_bus",  # the bus's own handler contract
        "application.bus.query_bus",
        "application.engine.turn_handler",  # the HTTP routes' contract with the graph runtime
        "infrastructure.telemetry.otel",  # the shape of the settings the adapter reads
        "domain.services.activity_writer",  # the shape of a notice a sentence is written from
    }
    misplaced = []
    for module, path in MODULES.items():
        if module.startswith("domain.ports") or module in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                getattr(base, "id", None) == "Protocol" for base in node.bases
            ):
                misplaced.append(f"{module}.{node.name}")
    assert misplaced == []


def test_packages_re_export_nothing() -> None:
    """A re-exporting `__init__` runs on every import of its subpackages and hides what loads:
    that is how two import cycles of 20 and 13 modules formed."""
    facades = []
    for module, path in MODULES.items():
        if path.name != "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body):
            facades.append(module or PACKAGE)
    assert facades == []


def test_no_import_cycle() -> None:
    graph: dict[str, set[str]] = {}
    for module, path in MODULES.items():
        targets = set()
        for _, name in _imports(path):
            name = name.removeprefix(f"{PACKAGE}.")
            while name and name not in MODULES:
                name = name.rpartition(".")[0]
            if name and name != module:
                targets.add(name)
        graph[module] = targets
    cycles = [sorted(cycle) for cycle in _strongly_connected(graph) if len(cycle) > 1]
    assert cycles == []


def _strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            found.append(component)

    for node in graph:
        if node not in index:
            visit(node)
    return found
