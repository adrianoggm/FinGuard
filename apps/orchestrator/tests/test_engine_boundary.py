"""The engine reads no sentence and consults no linguistic reader (spec "Hard boundaries").

Checked two ways (ruling R18), because each misses what the other sees:

- **statically**, on the source: no engine or graph module imports any of the vocabularies the
  spec lists, or `planning_context` (whose focus types share the engine's names, ruling R2). A
  package `__init__` that loads one of them transitively is not consultation;
- **at runtime**, over HTTP: every function and method those modules define is replaced by a wire
  that raises, references bound elsewhere by `from M import f` included, and a graph turn must
  still answer on both routes with nothing tripped. This is what "the new flow does not consult
  the readers" means, which an import list alone cannot show.

And the sentence itself (ruling R19): an ordinary turn reaches the interpreter and the remembered
turn; payment slots reach only the closed grounding adapter. A confirmation resume is reduced to an
allow-listed decision before it enters the checkpoint.
"""

import ast
import importlib
import inspect
import json
import sys
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import pytest
from support.episodes import Accounts, Contacts, Directory, a_stack
from support.memory_dialogue_store import MemoryDialogueStore

import finguard_orchestrator
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.engine.operations import Ask, Patch, PlanFields
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.services import expert_agent_words
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange
from finguard_orchestrator.infrastructure.llm.counting_provider import calls_so_far

PKG = "finguard_orchestrator"
SRC = Path(finguard_orchestrator.__file__).parent
# The spec's list, word for word, as modules.
# The rules handler, its readers and `query_recovery` were deleted with the rules route; what is
# left of the list still exists and is still out of bounds.
LINGUISTIC = tuple(
    f"{PKG}.domain.services.{name}"
    for name in (
        "turn_arbiter",
        "window_words",
        "account_words",
        "payment_words",
        "merchant_words",
        "explicit_facts",
        "continuation_patch",
        "plan_verifier",
        "capability_guard",
        "intent_lexicon",
    )
)
NEVER_IMPORTED = frozenset({*LINGUISTIC, f"{PKG}.domain.models.planning_context"})
# Every module of the engine, and the two this task added around it.
ENGINE = (
    *sorted((SRC / "domain" / "engine").glob("*.py")),
    *sorted((SRC / "application" / "engine").glob("*.py")),
    *sorted((SRC / "infrastructure" / "engine").glob("*.py")),
    SRC / "domain" / "ports" / "dialogue_store.py",
    SRC / "domain" / "ports" / "turn_operations.py",
    SRC / "infrastructure" / "controllers" / "engine_dispatch.py",
    SRC / "infrastructure" / "llm" / "counting_provider.py",
)
# The LangGraph experiment (Task 8) is held to the same boundary: every module of it, and the
# ports and wiring it added.
GRAPH = (
    *sorted((SRC / "application" / "graph").glob("*.py")),
    *sorted((SRC / "infrastructure" / "graph").glob("*.py")),
    SRC / "domain" / "ports" / "investigation_assessor.py",
    SRC / "domain" / "ports" / "turn_reservations.py",
    SRC / "bootstrap" / "graph_wiring.py",
)
# What `category_words` may be asked by the engine: the name of a category, for an answer or the
# model's glossary. Never what a sentence names.
NAMING = frozenset({"name_of", "names", "keys", "words_for"})


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported(path: Path) -> set[str]:
    """Every module a file imports, and every `module.name` it imports from one."""
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, f"{path.name}: relative import"
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def forbidden(path: Path) -> set[str]:
    return {name for name in imported(path) if name in NEVER_IMPORTED}


# --- statically ---------------------------------------------------------------------------------


def test_engine_imports_nothing_linguistic() -> None:
    assert all(path.is_file() for path in ENGINE)
    assert {path: forbidden(path) for path in ENGINE if forbidden(path)} == {}


def test_the_graph_imports_nothing_linguistic() -> None:
    assert len(GRAPH) > 8 and all(path.is_file() for path in GRAPH)
    assert {path: forbidden(path) for path in GRAPH if forbidden(path)} == {}


def test_the_check_sees_an_import_when_there_is_one() -> None:
    """The control: the financial turn imports two of the list, and the same check says so."""
    seen = forbidden(SRC / "application" / "queries" / "financial_turn.py")
    assert f"{PKG}.domain.services.turn_arbiter" in seen
    assert f"{PKG}.domain.models.planning_context" in seen


def test_category_words_only_names_categories() -> None:
    asked: dict[str, set[str]] = {}
    for path in ENGINE:
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "category_words"
            ):
                asked.setdefault(path.name, set()).add(node.attr)
            if isinstance(node, ast.ImportFrom) and node.module == (
                f"{PKG}.domain.services.category_words"
            ):
                asked.setdefault(path.name, set()).update(a.name for a in node.names)
    assert asked, "the presenter names categories through it"
    assert {name: uses - NAMING for name, uses in asked.items() if uses - NAMING} == {}


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _where(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """Where a value goes: `f(...)` as a positional argument, `f(name=)` as a keyword one."""
    parent = parents[node]
    if isinstance(parent, ast.keyword):
        call = parents[parent]
        assert isinstance(call, ast.Call)
        return f"{ast.unparse(call.func)}({parent.arg}=)"
    if isinstance(parent, ast.Call) and node in parent.args:
        return f"{ast.unparse(parent.func)}(...)"
    return ast.unparse(parent)


def test_the_graph_limits_sentence_uses_and_normalizes_confirmation_before_checkpoint() -> None:
    """Ordinary text reaches interpretation and remembered state; payment slots are grounded and
    free-form confirmation text is normalized before it is sent as a LangGraph resume value."""
    handler = _tree(SRC / "application" / "graph" / "graph_turn.py")
    nodes = _tree(SRC / "application" / "graph" / "graph.py")
    handler_parents, node_parents = _parents(handler), _parents(nodes)

    in_handler = sorted(
        _where(node, handler_parents)
        for node in ast.walk(handler)
        if isinstance(node, ast.Name) and node.id == "sentence"
    )
    in_nodes = sorted(
        _where(node, node_parents)
        for node in ast.walk(nodes)
        if isinstance(node, ast.Attribute) and node.attr == "sentence"
    )

    # The fingerprint binds a request id to its sentence (R45): `_fingerprint` hashes its bytes
    # (`sentence.encode`), and the hash is compared for equality, never read for what it says.
    assert "confirmation.canonical_reply(...)" in in_handler
    assert "Command(resume=)" not in in_handler
    assert "_fingerprint(...)" in in_handler
    assert "sentence.encode" in in_handler
    assert in_nodes == [
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(...)",
        "DialogueTurn(said=)",
        "_remember_result(...)",
        "_remember_result(...)",
        "_remember_result(...)",
        "_remember_result(...)",
        "candidate.turns[-1].said == ctx.sentence",
        "candidate.turns[-1].said == ctx.sentence",
        "ctx.ports.interpreter.interpret(...)",
        "ctx.ports.visual_policy.decide(...)",
        "payment_input.amount(...)",
        "payment_input.destination_candidates(...)",
        "payment_input.mentions_amount(...)",
        "payment_input.resolve_contact_payment_draft(...)",
        "payment_router.continues(...)",
        "payment_router.is_payment(...)",
        "payment_router.resumes(...)",
    ]


def test_no_other_graph_module_takes_a_sentence() -> None:
    named = {
        path.name
        for path in GRAPH
        if path.name not in ("graph.py", "graph_turn.py")
        for node in ast.walk(_tree(path))
        if (isinstance(node, ast.Name) and node.id == "sentence")
        or (isinstance(node, ast.arg) and node.arg == "sentence")
        or (isinstance(node, ast.Attribute) and node.attr == "sentence")
    }
    assert named == set()


def test_no_other_engine_step_takes_a_sentence() -> None:
    pure = [
        path
        for path in ENGINE
        if path.parent.name == "engine"
        and path.parent.parent.name in ("domain", "application")
        and path.name != "turn_handler.py"
    ]
    named = {
        path.name
        for path in pure
        for node in ast.walk(_tree(path))
        if (isinstance(node, ast.Name) and node.id == "sentence")
        or (isinstance(node, ast.arg) and node.arg == "sentence")
    }
    assert named == set()


def test_the_dispatch_hands_the_sentence_to_the_engine_alone() -> None:
    tree = _tree(SRC / "infrastructure" / "controllers" / "engine_dispatch.py")
    parents = _parents(tree)

    uses = [
        _where(node, parents)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "question"
    ]

    assert uses == ["engine.answer(...)"]


# --- at runtime ---------------------------------------------------------------------------------


class Tripped(BaseException):  # noqa: N818
    """A BaseException, so that no `except Exception` on the way can swallow it."""


def _boundary() -> list[ModuleType]:
    return [importlib.import_module(name) for name in LINGUISTIC]


def arm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every function and method the boundary modules define now raises `Tripped`, and records
    its name in the list returned. Undone by `monkeypatch` when the test ends."""
    tripped: list[str] = []

    def wire(label: str) -> Callable[..., NoReturn]:
        def tripwire(*_: object, **__: object) -> NoReturn:
            tripped.append(label)
            raise Tripped(label)

        tripwire.__name__ = f"tripwire_{label}"
        return tripwire

    boundary = _boundary()
    wires: dict[int, Callable[..., NoReturn]] = {}
    for module in boundary:
        for name, value in list(vars(module).items()):
            label = f"{module.__name__.removeprefix(PKG + '.')}.{name}"
            if inspect.isfunction(value) and value.__module__ == module.__name__:
                wires[id(value)] = wire(label)
                monkeypatch.setattr(module, name, wires[id(value)])
            elif inspect.isclass(value) and value.__module__ == module.__name__:
                for attribute, member in list(vars(value).items()):
                    if attribute in ("__init_subclass__", "__class_getitem__", "__subclasshook__"):
                        continue
                    target = (
                        member.__func__
                        if isinstance(member, (staticmethod, classmethod))
                        else member
                    )
                    if inspect.isfunction(target):
                        try:
                            monkeypatch.setattr(value, attribute, wire(f"{label}.{attribute}"))
                        except (AttributeError, TypeError):
                            pass
    # `from M import f` binds f under the importer's name too; those are the wires as well.
    for other in list(sys.modules.values()):
        if other in boundary or not getattr(other, "__name__", "").startswith(PKG):
            continue
        for name, value in list(vars(other).items()):
            if id(value) in wires:
                monkeypatch.setattr(other, name, wires[id(value)])
    assert len(wires) > 100, "the boundary modules define their readers as functions"
    return tripped


JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
QUESTION_ROUTES = ("/v1/questions", "/v1/questions/stream")
TURNS = (
    (
        "¿cuánto gasté en restaurantes en julio?",
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, categories=("RESTAURANTS",))),),
    ),
    ("¿y en ocio?", (Patch("focus", PlanFields(categories=("LEISURE",))),)),
)


class Scripted:
    def __init__(self, *turns: Any) -> None:
        self._turns = list(turns)

    async def interpret(self, sentence: str, state: Any, **_: Any) -> Any:
        return self._turns.pop(0)


def answer_of(text: str) -> dict[str, Any]:
    """The answer on either route: the JSON body, or the stream's last frame."""
    lines = [line.removeprefix("data: ") for line in text.splitlines() if line]
    answer: dict[str, Any] = json.loads(lines[-1])
    return answer


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "engine", None, raising=False)
    app.dependency_overrides.clear()


def test_the_wires_are_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The controls: a boundary function trips, and so does a copy of one bound elsewhere."""
    from finguard_orchestrator.domain.services import window_words

    reader = next(
        name
        for name, value in vars(window_words).items()
        if inspect.isfunction(value) and value.__module__ == window_words.__name__
    )
    tripped = arm(monkeypatch)

    with pytest.raises(Tripped):
        getattr(window_words, reader)("julio")
    # `expert_agent_words` holds `window_in` from `window_words` by name.
    with pytest.raises(Tripped):
        expert_agent_words.window_in("julio")

    assert len(tripped) == 2


class Inspecting:
    """The assessment: inspect the first merchant it may, then stop."""

    def __init__(self) -> None:
        self.asked = 0

    async def assess(self, summary: Any) -> Any:
        from finguard_orchestrator.domain.ports.investigation_assessor import Inspect, Stop

        self.asked += 1
        return Inspect(summary.may_inspect[0]) if summary.may_inspect else Stop()


class Rising:
    """June against July by merchant, Amazon up by both, and its largest July purchase."""

    def __init__(self) -> None:
        self.reads = 0

    async def run(self, plan: Any, **_: Any) -> Any:
        from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer

        self.reads += 1
        july = plan.date_range == JULY
        group = Group("Amazon", 17_000, 2) if july else Group("Amazon", 5_000, 1)
        return QueryAnswer("EUR", group.total_minor, group.movement_count, groups=(group,))

    async def ranked(self, ranking: Any, **_: Any) -> Any:
        from datetime import UTC, datetime

        from finguard_orchestrator.domain.models.query_answer import Movement

        self.reads += 1
        return Movement(datetime(2026, 7, 12, tzinfo=UTC), "", 15_000, "EXPENSE")


@pytest.mark.parametrize("route", QUESTION_ROUTES)
def test_a_graph_turn_consults_no_reader(monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    """The same tripwire over HTTP with the investigation graph answering: an ordinary turn, a
    follow-up, and a comparison by merchant that is assessed, inspects a purchase and is assessed
    again (R45)."""
    from langgraph.checkpoint.memory import InMemorySaver
    from support.memory_turn_reservations import MemoryTurnReservations

    from finguard_orchestrator.application.graph.graph_state import GraphPorts
    from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
    from finguard_orchestrator.domain.engine.operations import Compare
    from finguard_orchestrator.domain.enums import GroupBy

    june = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
    by_merchant = (
        Compare(
            PlanFields(direction=Direction.EXPENSE, group_by=GroupBy.MERCHANT),
            PlanFields(window=june),
            PlanFields(window=JULY),
        ),
    )
    stack = a_stack()
    rising, assessor = Rising(), Inspecting()

    class Banking:
        """The stack's ledger for ordinary turns, the rising one for the comparison."""

        async def run(self, plan: Any, **rest: Any) -> Any:
            if plan.group_by is GroupBy.MERCHANT:
                return await rising.run(plan, **rest)
            return await stack.ledger.run(plan, **rest)

        async def ranked(self, ranking: Any, **rest: Any) -> Any:
            return await rising.ranked(ranking, **rest)

    turns = (*TURNS, ("¿en qué comercios gasto más que antes?", by_merchant))
    ports = GraphPorts(
        Scripted(*(operations for _, operations in turns)),
        assessor,
        MemoryDialogueStore(),
        Banking(),
        Accounts(),
        Directory(),
        Contacts(),
        frozenset({"RESTAURANTS", "LEISURE"}),
        count_calls=calls_so_far,
    )
    app.state.engine = GraphTurnHandler(ports, MemoryTurnReservations(), InMemorySaver())
    tripped = arm(monkeypatch)

    answers = []
    conversation = None
    for sentence, _ in turns:
        # A graph turn is durable and needs the caller's own attempt key, a fresh one per turn.
        response = stack.client.post(
            route,
            json={"question": sentence, "conversation": conversation},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 200
        assert response.headers["x-finguard-engine"] == "model"
        answers.append(answer_of(response.text))
        conversation = answers[-1]["conversation"]

    assert tripped == []
    assert [answer["outcome"] for answer in answers] == ["PLANNED"] * 3
    assert answers[1]["text"].startswith("465,56 € en ocio del 2026-07-01 al 2026-07-31")
    # Assessed, inspected (three reads), and the inspection is in the answer.
    assert (assessor.asked, rising.reads) == (1, 3)
    assert "En Amazon, la subida se debe a una compra" in answers[2]["text"]
