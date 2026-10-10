"""The validated operations as checkpoint data: plain JSON, from a closed list of types.

LangGraph's own serializer would store the dataclasses, but it hands a tuple back as a list (so a
plan read back is not equal to the plan written, and `_check_kept` would call that a moved field)
and it warns that loading arbitrary classes from a checkpoint is going away. A checkpoint is read
back into code that authorises bank reads, so what it may rebuild is listed here and nothing else.
"""

import dataclasses
import inspect
import json
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from finguard_orchestrator.application.commands import pay
from finguard_orchestrator.application.engine import executor as engine_executor
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.queries import financial_turn
from finguard_orchestrator.domain import enums
from finguard_orchestrator.domain.engine import operations
from finguard_orchestrator.domain.engine import state as dialogue_state
from finguard_orchestrator.domain.engine.operations import Operation, PlanFields
from finguard_orchestrator.domain.engine.state import (
    BalanceFocus,
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    Measure,
    Missing,
    MovementListFocus,
    Pending,
    QueryFocus,
    RatioFocus,
    Scope,
)
from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.models import (
    account_summary,
    chart,
    contact,
    conversation,
    expert_agents,
    payment_result,
    query_answer,
    result_reference,
    visual_follow_up,
    visual_plan,
)
from finguard_orchestrator.domain.services import deterministic_visual_planner
from finguard_orchestrator.domain.value_objects import (
    comparison_plan,
    financial_plan,
    movement_list_plan,
    payment_draft,
    ratio_plan,
    top_movements,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

STATE_SCHEMA_VERSION: Final = 25
ANSWER_SCHEMA_VERSION: Final = 2
_PREVIOUS_ANSWER_SCHEMA_VERSION: Final = 1

_CLASSES: Final[dict[str, type]] = {
    cls.__name__: cls
    for cls in (
        operations.Ask,
        operations.Patch,
        operations.Compare,
        operations.ComparePrevious,
        operations.InvestigateComparison,
        operations.ChangeMeasure,
        operations.SelectOperand,
        operations.Reset,
        operations.RestorePrevious,
        operations.Balance,
        operations.ListContacts,
        operations.ListCategories,
        operations.ListMovements,
        operations.PatchMovementList,
        operations.AskCorrection,
        operations.Clarify,
        operations.Smalltalk,
        operations.Delegate,
        operations.Visualize,
        operations.PrepareBizum,
        operations.PrepareTransfer,
        operations.PrepareOwnTransfer,
        operations.ResumeFocus,
        PlanFields,
        RelativePeriod,
        AbsoluteRange,
        FinancialPlan,
        TopMovements,
        ComparisonPlan,
        RatioPlan,
        DialogueState,
        QueryFocus,
        MovementListFocus,
        ComparisonFocus,
        RatioFocus,
        BalanceFocus,
        Pending,
        Scope,
        DialogueTurn,
    )
}
_ENUMS: Final[dict[str, type[StrEnum]]] = {
    cls.__name__: cls
    for cls in (
        Direction,
        GroupBy,
        Metric,
        Period,
        Measure,
        Missing,
        CounterpartyType,
        TransactionType,
        RatioKind,
    )
}

# Reservation replay serializes a completed answer too. Register dataclasses and string enums
# only from these source modules; JSON payloads cannot name an arbitrary import path or class.
_SERIALIZATION_MODULES = (
    engine_executor,
    financial_turn,
    pay,
    dialogue_state,
    conversation,
    chart,
    account_summary,
    contact,
    expert_agents,
    query_answer,
    result_reference,
    payment_result,
    visual_follow_up,
    visual_plan,
    deterministic_visual_planner,
    comparison_plan,
    financial_plan,
    payment_draft,
    ratio_plan,
    top_movements,
    movement_list_plan,
    enums,
)
for _module in _SERIALIZATION_MODULES:
    for _name, _candidate in vars(_module).items():
        if not inspect.isclass(_candidate) or _candidate.__module__ != _module.__name__:
            continue
        if dataclasses.is_dataclass(_candidate):
            existing = _CLASSES.get(_name)
            if existing is not None and existing is not _candidate:
                raise RuntimeError(f"duplicate checkpoint type name: {_name}")
            _CLASSES[_name] = _candidate
        elif issubclass(_candidate, StrEnum):
            existing_enum = _ENUMS.get(_name)
            if existing_enum is not None and existing_enum is not _candidate:
                raise RuntimeError(f"duplicate checkpoint enum name: {_name}")
            _ENUMS[_name] = _candidate
_CLASSES[EngineAnswer.__name__] = EngineAnswer
# Schema 1–16 stored this value as `ActiveQueryResult` under `active_result`. The name remains a
# decoder alias only; all current writes and state access use `FinancialArtifact`/`active_artifact`.
_CLASSES["ActiveQueryResult"] = dialogue_state.FinancialArtifact


class UnknownCheckpointTypeError(ValueError):
    """A checkpoint names a type this version does not rebuild."""


def dumps(ops: tuple[Operation, ...]) -> str:
    return json.dumps([_encoded(op) for op in ops], sort_keys=True)


def dumps_typed(value: Any) -> str:
    """Encode one value from the checkpoint's closed registry as safe JSON text."""
    return json.dumps(_encoded(value), sort_keys=True, separators=(",", ":"))


def loads_typed(value: str) -> Any:
    """Decode one value using only the checkpoint's explicitly registered types."""
    return _decoded(json.loads(value))


def loads(text: str) -> tuple[Operation, ...]:
    return tuple(_decoded(item) for item in json.loads(text))


def dumps_state(state: DialogueState) -> str:
    """Encode the canonical conversation using only explicitly registered domain types."""
    return json.dumps(
        {"schema_version": STATE_SCHEMA_VERSION, "state": _encoded(state)},
        sort_keys=True,
        separators=(",", ":"),
    )


def loads_state(text: str) -> DialogueState:
    """Decode current state and migrate earlier typed state schemas in memory."""
    value = json.loads(text)
    schema = value.get("schema_version")
    if schema not in {*range(1, STATE_SCHEMA_VERSION), STATE_SCHEMA_VERSION}:
        raise UnknownCheckpointTypeError(f"dialogue schema {value.get('schema_version')}")
    stored_state = value.get("state", {}).get("f", {})
    if schema < 19 and isinstance(stored_state.get("pending"), dict):
        # Earlier schemas kept one shared scope for both the visible result and any open
        # clarification. Preserve that captured scope when migrating; new turns store the scope
        # on Pending so the two tasks can diverge safely.
        pending_fields = stored_state["pending"].get("f", {})
        if "scope" not in pending_fields:
            pending_fields["scope"] = stored_state.get(
                "scope", {"$type": "Scope", "f": {"account_ids": {"$tuple": []}}}
            )
    if schema < STATE_SCHEMA_VERSION and "active_result" in stored_state:
        stored_state["active_artifact"] = stored_state.pop("active_result")
        _migrate_financial_artifact(stored_state.get("active_artifact"))
    state = _decoded(value.get("state"))
    if not isinstance(state, DialogueState):
        raise UnknownCheckpointTypeError("payload is not DialogueState")
    stored_artifact = stored_state.get("active_artifact")
    artifact_fields = stored_artifact.get("f", {}) if isinstance(stored_artifact, dict) else {}
    if state.active_artifact is not None and "result_version" not in artifact_fields:
        state = dataclasses.replace(
            state,
            active_artifact=dataclasses.replace(
                state.active_artifact, result_version=max(1, state.version)
            ),
        )
    return state


def _migrate_financial_artifact(encoded: Any) -> None:
    """Upgrade the old aggregate cache to the explicit artifact contract before decoding."""
    if not isinstance(encoded, dict):
        return
    fields = encoded.get("f")
    if not isinstance(fields, dict):
        return
    if encoded.get("$type") == "ActiveQueryResult":
        encoded["$type"] = "FinancialArtifact"
    if "result_kind" not in fields:
        comparison = fields.get("comparison")
        plan_value = fields.get("plan", {})
        plan = plan_value.get("f", {}) if isinstance(plan_value, dict) else {}
        plan_kind = plan.get("result_kind")
        group_by = plan.get("group_by")
        if comparison is not None:
            kind = "COMPARISON"
        elif isinstance(plan_kind, dict):
            kind = plan_kind.get("v", "SCALAR")
        elif isinstance(group_by, dict) and group_by.get("v") == "MONTH":
            kind = "TIME_SERIES"
        elif isinstance(group_by, dict) and group_by.get("v") not in {None, "NONE"}:
            kind = "BREAKDOWN"
        else:
            kind = "SCALAR"
        fields["result_kind"] = {"$enum": "ResultKind", "v": kind}
    fields.setdefault("status", {"$enum": "ArtifactStatus", "v": "VERIFIED"})
    fields.setdefault("source", {"$enum": "ArtifactSource", "v": "BANKING_API"})
    query_id = fields.get("query_id")
    fields.setdefault("provenance", {"$tuple": [query_id] if query_id else []})


def dumps_answer(answer: EngineAnswer) -> str:
    """Encode a completed turn for idempotent replay after a process restart."""
    return json.dumps(
        {"schema_version": ANSWER_SCHEMA_VERSION, "answer": _encoded(answer)},
        sort_keys=True,
        separators=(",", ":"),
    )


def loads_answer(text: str) -> EngineAnswer:
    value = json.loads(text)
    if value.get("schema_version") not in {
        _PREVIOUS_ANSWER_SCHEMA_VERSION,
        ANSWER_SCHEMA_VERSION,
    }:
        raise UnknownCheckpointTypeError(f"answer schema {value.get('schema_version')}")
    answer = _decoded(value.get("answer"))
    if not isinstance(answer, EngineAnswer):
        raise UnknownCheckpointTypeError("payload is not EngineAnswer")
    return answer


def _encoded(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return {"$enum": type(value).__name__, "v": value.value}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _encoded(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"$type": type(value).__name__, "f": fields}
    if isinstance(value, tuple):
        return {"$tuple": [_encoded(item) for item in value]}
    if isinstance(value, list):
        return {"$list": [_encoded(item) for item in value]}
    if isinstance(value, dict):
        return {"$dict": [[_encoded(key), _encoded(item)] for key, item in value.items()]}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise UnknownCheckpointTypeError(type(value).__name__)


def _decoded(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$enum" in value:
        enum = _ENUMS.get(value["$enum"])
        if enum is None:
            raise UnknownCheckpointTypeError(value["$enum"])
        return enum(value["v"])
    if "$type" in value:
        cls = _CLASSES.get(value["$type"])
        if cls is None:
            raise UnknownCheckpointTypeError(value["$type"])
        return cls(**{name: _decoded(field) for name, field in value["f"].items()})
    if "$tuple" in value:
        return tuple(_decoded(item) for item in value["$tuple"])
    if "$list" in value:
        return [_decoded(item) for item in value["$list"]]
    if "$dict" in value:
        return {_decoded(key): _decoded(item) for key, item in value["$dict"]}
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    if "$uuid" in value:
        return UUID(value["$uuid"])
    raise UnknownCheckpointTypeError(str(sorted(value)))
