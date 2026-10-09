"""The validated operations as checkpoint data: plain JSON, from a closed list of types.

LangGraph's own serializer would store the dataclasses, but it hands a tuple back as a list (so a
plan read back is not equal to the plan written, and `_check_kept` would call that a moved field)
and it warns that loading arbitrary classes from a checkpoint is going away. A checkpoint is read
back into code that authorises bank reads, so what it may rebuild is listed here and nothing else.
"""

import dataclasses
import json
from datetime import date
from enum import StrEnum
from typing import Any, Final

from finguard_orchestrator.domain.engine import operations
from finguard_orchestrator.domain.engine.operations import Operation, PlanFields
from finguard_orchestrator.domain.engine.state import Measure, Missing
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

_CLASSES: Final[dict[str, type]] = {
    cls.__name__: cls
    for cls in (
        operations.Ask,
        operations.Patch,
        operations.Compare,
        operations.ChangeMeasure,
        operations.SelectOperand,
        operations.Reset,
        operations.Balance,
        operations.Clarify,
        operations.Smalltalk,
        operations.Delegate,
        PlanFields,
        RelativePeriod,
        AbsoluteRange,
    )
}
_ENUMS: Final[dict[str, type[StrEnum]]] = {
    cls.__name__: cls for cls in (Direction, GroupBy, Metric, Period, Measure, Missing)
}


class UnknownCheckpointTypeError(ValueError):
    """A checkpoint names a type this version does not rebuild."""


def dumps(ops: tuple[Operation, ...]) -> str:
    return json.dumps([_encoded(op) for op in ops], sort_keys=True)


def loads(text: str) -> tuple[Operation, ...]:
    return tuple(_decoded(item) for item in json.loads(text))


def _encoded(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return {"$enum": type(value).__name__, "v": value.value}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _encoded(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"$type": type(value).__name__, "f": fields}
    if isinstance(value, tuple):
        return {"$tuple": [_encoded(item) for item in value]}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
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
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    raise UnknownCheckpointTypeError(str(sorted(value)))
