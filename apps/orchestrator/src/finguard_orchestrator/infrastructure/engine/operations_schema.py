"""The closed shape a turn's operations take on their way out of the model, and the reading back.

One set of models does both jobs: their JSON schema is what the decoder is held to, and the same
models validate what comes back. A shape written twice is two shapes the first time one of them
changes.

The models are a projection of `domain/engine/operations.py` and nothing more: one object per
operation, keyed by `op`, each closed (`additionalProperties: false`), with every enum the domain
has and every list bounded by the domain's own ceiling. Categories are closed too, to the keys
this deployment has, so an invented one is unreachable for a decoder that honours the schema and
refused for one that does not.

**A plan is a list of the fields said, not an object of every field** (ruling R14). Each item is
`{"field": F, "value": V}`, closed, with `value` typed by `field`; a field not listed is not said,
and a patch keeps it. An object with every field as a key was tried first, with `null` for what
was not said: under a decoder that must write every key, qwen2.5-coder:7b filled three to
twenty-six unsaid fields per answer (a patch «¿y en ocio?» also moved July to the last month),
where the same prompt with this list filled none. Nothing is optional in the schema either way,
because strict decoders (Groq's `json_schema`) refuse an optional key: here nothing has to be.

**A window has three closed shapes**: a named period, a counted period (`LAST_N_DAYS`,
`LAST_N_MONTHS`) with its `n`, and a date range. An `n` on a named period is not a value the
decoder can reach, which it was while `n` was one nullable key on every period.
"""

import dataclasses
from collections.abc import Collection
from dataclasses import replace
from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    WithJsonSchema,
)
from pydantic_core import ErrorDetails

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORIES,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_PERIOD_DAYS,
    MAX_RANK,
    MAX_SELECTED_ACCOUNTS,
)
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Reset,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import Measure, Missing
from finguard_orchestrator.domain.enums import (
    PERIODS_NEEDING_COUNT,
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

# How many operations one turn may carry. «Olvídalo: compara junio con julio en porcentaje» is
# three; a fifth is a model narrating rather than a turn.
MAX_OPERATIONS: Final = 4

# How many fields a plan can say: each of `PlanFields`' at most once.
MAX_FIELDS: Final = len(dataclasses.fields(PlanFields))

# How many validation problems a repair quotes back. The first few say what to fix; the rest of a
# failed union is the same mistake seen from every branch.
_MAX_PROBLEMS: Final = 4
# The pydantic error types a union reports for the shapes a value is not, listed after the rest.
_SHAPE_MISFITS: Final = frozenset({"extra_forbidden", "missing", "too_short"})

# Where the catalogue goes: the validation context while reading, a marker in the schema until
# `schema_for` swaps it for the catalogue's keys.
_CATALOGUE: Final = "categories"
_CATEGORY_MARK: Final = {"$catalogue": _CATALOGUE}

_ISO_DATE: Final = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def _in_catalogue(name: str, info: ValidationInfo) -> str:
    catalogue = (info.context or {}).get(_CATALOGUE)
    if catalogue is not None and name not in catalogue:
        # Not quoted back: the problem is logged and sent to the model, and the value is the
        # model's text, which this module has no business repeating.
        raise ValueError("not a category key from the list")
    return name


def _named(period: Period) -> Period:
    if period in PERIODS_NEEDING_COUNT:
        raise ValueError("this period takes an n: write it as a counted window")
    return period


def _counted(period: Period) -> Period:
    if period not in PERIODS_NEEDING_COUNT:
        raise ValueError("only a counted period takes an n")
    return period


def _periods(counted: bool) -> dict[str, Any]:
    return {
        "type": "string",
        "enum": [p.value for p in Period if (p in PERIODS_NEEDING_COUNT) == counted],
    }


Category = Annotated[str, AfterValidator(_in_catalogue), WithJsonSchema(_CATEGORY_MARK)]
Categories = Annotated[tuple[Category, ...], Field(max_length=MAX_CATEGORIES)]
# A name as the person said it: a shop, a contact, an account. Grounding matches it later; here it
# only has to be a name and not a paragraph.
Name = Annotated[str, Field(max_length=MAX_MERCHANT_LENGTH)]
Names = Annotated[tuple[Name, ...], Field(max_length=MAX_MERCHANTS)]
AccountNames = Annotated[tuple[Name, ...], Field(max_length=MAX_SELECTED_ACCOUNTS)]
NamedPeriod = Annotated[Period, AfterValidator(_named), WithJsonSchema(_periods(counted=False))]
CountedPeriod = Annotated[Period, AfterValidator(_counted), WithJsonSchema(_periods(counted=True))]

# The domain's literals, restated because a type checker cannot read them off a dataclass. A test
# holds each to the dataclass field it mirrors.
Target = Literal["focus", "earlier", "latest", "pending"]
Side = Literal["earlier", "latest"]
Courtesy = Literal["GREETING", "THANKS", "IDENTITY", "HELP"]
Elsewhere = Literal["PAYMENT", "TRANSFER", "BOOKING", "CHART", "UNSUPPORTED"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- windows ----------------------------------------------------------------------------------


class NamedWindow(_Closed):
    period: NamedPeriod

    def as_window(self) -> RelativePeriod:
        return RelativePeriod(self.period)


class CountedWindow(_Closed):
    period: CountedPeriod
    n: Annotated[int, Field(ge=1, le=MAX_PERIOD_DAYS)]

    def as_window(self) -> RelativePeriod:
        return RelativePeriod(self.period, self.n)


class DatedWindow(_Closed):
    start: date
    end: date

    def as_window(self) -> AbsoluteRange:
        return AbsoluteRange(self.start, self.end)


# --- the fields of a plan, one item per field said --------------------------------------------


class DirectionItem(_Closed):
    field: Literal["direction"]
    value: Direction


class WindowItem(_Closed):
    field: Literal["window"]
    value: NamedWindow | CountedWindow | DatedWindow


class CategoriesItem(_Closed):
    field: Literal["categories"]
    value: Categories


class MerchantsItem(_Closed):
    field: Literal["merchants"]
    value: Names


class PeopleItem(_Closed):
    field: Literal["people"]
    value: Names


class AccountsItem(_Closed):
    field: Literal["accounts"]
    value: AccountNames


class ExcludedCategoriesItem(_Closed):
    field: Literal["excluded_categories"]
    value: Categories


class ExcludedMerchantsItem(_Closed):
    field: Literal["excluded_merchants"]
    value: Names


class MetricItem(_Closed):
    field: Literal["metric"]
    value: Metric


class GroupByItem(_Closed):
    field: Literal["group_by"]
    value: GroupBy


class LimitItem(_Closed):
    field: Literal["limit"]
    value: Annotated[int, Field(ge=1, le=MAX_RANK)]


FieldItem = Annotated[
    DirectionItem
    | WindowItem
    | CategoriesItem
    | MerchantsItem
    | PeopleItem
    | AccountsItem
    | ExcludedCategoriesItem
    | ExcludedMerchantsItem
    | MetricItem
    | GroupByItem
    | LimitItem,
    Field(discriminator="field"),
]


def _once_each(items: tuple[FieldItem, ...]) -> tuple[FieldItem, ...]:
    """A field said twice is two answers to one question, and keeping either is a guess."""
    said = [item.field for item in items]
    repeated = sorted({name for name in said if said.count(name) > 1})
    if repeated:
        raise ValueError(f"each field at most once; repeated: {', '.join(repeated)}")
    return items


Fields = Annotated[tuple[FieldItem, ...], Field(max_length=MAX_FIELDS), AfterValidator(_once_each)]


def _plan_fields(items: tuple[FieldItem, ...]) -> PlanFields:
    said: dict[str, Any] = {
        item.field: item.value.as_window() if isinstance(item, WindowItem) else item.value
        for item in items
    }
    return replace(PlanFields(), **said)


# --- operations -------------------------------------------------------------------------------


class AskOp(_Closed):
    op: Literal["ask"]
    fields: Fields

    def as_operation(self) -> Ask:
        return Ask(_plan_fields(self.fields))


class PatchOp(_Closed):
    op: Literal["patch"]
    target: Target
    changes: Fields

    def as_operation(self) -> Patch:
        return Patch(self.target, _plan_fields(self.changes))


class CompareOp(_Closed):
    op: Literal["compare"]
    shared: Fields
    earlier: Fields
    latest: Fields

    def as_operation(self) -> Compare:
        return Compare(
            _plan_fields(self.shared), _plan_fields(self.earlier), _plan_fields(self.latest)
        )


class MeasureOp(_Closed):
    op: Literal["measure"]
    measure: Measure

    def as_operation(self) -> ChangeMeasure:
        return ChangeMeasure(self.measure)


class SelectOperandOp(_Closed):
    op: Literal["select_operand"]
    which: Side

    def as_operation(self) -> SelectOperand:
        return SelectOperand(self.which)


class ResetOp(_Closed):
    op: Literal["reset"]

    def as_operation(self) -> Reset:
        return Reset()


class BalanceOp(_Closed):
    op: Literal["balance"]
    accounts: AccountNames = ()

    def as_operation(self) -> Balance:
        return Balance(self.accounts)


class ClarifyOp(_Closed):
    op: Literal["clarify"]
    missing: Missing
    about: Name | None = None

    def as_operation(self) -> Clarify:
        # A blank name is no name: quoted back, it would ask about nothing.
        return Clarify(self.missing, (self.about or "").strip() or None)


class SmalltalkOp(_Closed):
    op: Literal["smalltalk"]
    kind: Courtesy

    def as_operation(self) -> Smalltalk:
        return Smalltalk(self.kind)


class DelegateOp(_Closed):
    op: Literal["delegate"]
    to: Elsewhere

    def as_operation(self) -> Delegate:
        return Delegate(self.to)


AnyOp = Annotated[
    AskOp
    | PatchOp
    | CompareOp
    | MeasureOp
    | SelectOperandOp
    | ResetOp
    | BalanceOp
    | ClarifyOp
    | SmalltalkOp
    | DelegateOp,
    Field(discriminator="op"),
]


class TurnOperations(_Closed):
    operations: Annotated[tuple[AnyOp, ...], Field(min_length=1, max_length=MAX_OPERATIONS)]


# --- the schema the decoder is held to ---------------------------------------------------------


def schema_for(categories: Collection[str]) -> dict[str, Any]:
    """The schema the decoder is held to, with categories closed to `categories`."""
    portable: dict[str, Any] = _portable(TurnOperations.model_json_schema(), tuple(categories))
    return portable


def _portable(node: Any, categories: tuple[str, ...]) -> Any:
    """`node` said in the subset every constrained decoder here takes.

    `oneOf` becomes `anyOf` (the branches are disjoint on `op` and on `field`, so they mean the
    same), `const` becomes a one-value `enum`, `format: date` becomes the pattern of one (a
    format is a hint to some decoders and unknown to others; the reader still refuses a 30th of
    February, which no pattern can), and the annotations no decoder needs go: `title`,
    `default`, `discriminator`, and `description`, which pydantic fills from the enums' docstrings
    and a hosted runtime may show the model. Every object then requires every key it has, the
    only shape strict decoders take.
    """
    if isinstance(node, list):
        return [_portable(item, categories) for item in node]
    if not isinstance(node, dict):
        return node
    if node == _CATEGORY_MARK:
        return {"type": "string", "enum": list(categories)}
    said: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("title", "default", "discriminator", "description"):
            continue
        if key in ("properties", "$defs"):
            said[key] = {name: _portable(sub, categories) for name, sub in value.items()}
        elif key == "oneOf":
            said["anyOf"] = _portable(value, categories)
        elif key == "const":
            said["enum"] = [value]
        elif (key, value) == ("format", "date"):
            said["pattern"] = _ISO_DATE
        else:
            said[key] = _portable(value, categories)
    if "properties" in said:
        said["required"] = list(said["properties"])
    return said


# --- reading an answer back -------------------------------------------------------------------


class UnreadableError(Exception):
    """An answer that is not operations, said twice.

    `problem` is said to the model when it is asked again, and may quote what the model wrote.
    `logged` is for logs and is the exception's own message: error types and where they were,
    never a value, because what the model wrote can be the person's sentence echoed back.
    """

    def __init__(self, problem: str, logged: str) -> None:
        super().__init__(logged)
        self.problem = problem
        self.logged = logged


def read(answer: str, categories: Collection[str]) -> tuple[Operation, ...]:
    """The operations in `answer`, or `UnreadableError` saying why there are none.

    Two kinds of refusal, read as one: a shape the schema does not allow (a field said twice
    included), and a shape it allows with values the domain refuses (a range that ends before it
    starts, more months than a window may hold). Either way the model wrote something that is not
    a turn, and either way it is told what.
    """
    try:
        said = TurnOperations.model_validate_json(
            answer, context={_CATALOGUE: frozenset(categories)}
        )
        return tuple(op.as_operation() for op in said.operations)
    except ValidationError as invalid:
        errors = _first_problems(invalid)
        raise UnreadableError(
            "; ".join(f"{_where(error)}: {error['msg']}" for error in errors),
            "; ".join(f"{_where(error, logged=True)}: {error['type']}" for error in errors),
        ) from None
    except InvalidValueError as refused:
        raise UnreadableError(str(refused), "the domain refused a value") from None


def _first_problems(invalid: ValidationError) -> list[ErrorDetails]:
    """The first few problems, what is wrong before which shapes did not fit.

    A value that fits none of a union's shapes is reported once per shape, and the shapes it is not
    only say a key is extra or missing (or, after an item fails, that its list is now too short).
    The shape it nearly is says what is actually wrong, and that is the one worth sending back.
    """
    errors = sorted(
        invalid.errors(include_url=False), key=lambda error: error["type"] in _SHAPE_MISFITS
    )
    return errors[:_MAX_PROBLEMS]


def _where(error: ErrorDetails, *, logged: bool = False) -> str:
    """The error's path. Every part of it is ours (keys, indices, tags, shape names) except the
    key an extra-key error is about, which the model wrote and a log does not repeat."""
    path = [str(part) for part in error["loc"]]
    if logged and error["type"] == "extra_forbidden" and path:
        path[-1] = "<extra key>"
    return ".".join(path) or "answer"
