"""Value-free chart vocabulary and deterministic defaults."""

import re
from dataclasses import dataclass, field
from typing import Final, Literal, cast, get_args

Mark = Literal["line", "area", "bar", "point", "donut"]
Channel = Literal["x", "y", "color", "tooltip", "theta", "xOffset", "yOffset"]
FieldType = Literal["nominal", "quantitative", "temporal"]
Stack = Literal["zero", "normalize", "disabled"]
# Named once so `EncodingSort.mode` and `ChartSpec.sort` share one definition instead of two
# copies drifting apart -- the failure mode `Channel`/`get_args(Channel)` already had in
# `VisualContextPayload._value_free_spec` before `get_args` replaced a hand-written copy there.
SortMode = Literal["input", "ascending", "descending"]

# The identifier shape every field name -- a chart field, a visual field, a sort field -- must
# have. Promoted to a module constant so `VisualContextPayload._value_free_spec`
# (infrastructure) can enforce the same rule on a wire `sort_field` instead of writing a second
# copy of this pattern.
FIELD_NAME_PATTERN: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class EncodingSort:
    """How one channel orders itself, which is not always how the chart orders itself.

    `ChartSpec.sort` stays for the single-axis case it already serves. This exists because a
    breakdown wants its dimension by magnitude *and* its legend in producer order, and one
    setting cannot carry both.
    """

    mode: SortMode
    field: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in get_args(SortMode):
            raise ValueError("unsupported sort mode")
        if self.mode == "input" and self.field is not None:
            raise ValueError("input order names no field to sort by")
        if self.field is not None and not FIELD_NAME_PATTERN.fullmatch(self.field):
            raise ValueError("sort fields must be identifiers")


@dataclass(frozen=True)
class ChartEncoding:
    channel: Channel
    field: str
    type: FieldType
    title: str | None = None
    sort: EncodingSort | None = None

    def __post_init__(self) -> None:
        if self.channel not in get_args(Channel):
            raise ValueError("unsupported chart channel")
        if self.type not in get_args(FieldType):
            raise ValueError("unsupported chart field type")
        if not FIELD_NAME_PATTERN.fullmatch(self.field):
            raise ValueError("chart fields must be identifiers")


@dataclass(frozen=True)
class ChartSpec:
    mark: Mark
    encodings: tuple[ChartEncoding, ...]
    title: str | None = None
    stack: Stack | None = None
    sort: SortMode | None = None
    sort_field: str | None = None
    limit: int | None = None
    responsive: bool = True

    def __post_init__(self) -> None:
        if self.mark not in get_args(Mark):
            raise ValueError("unsupported chart mark")
        # `mark` above and `sort` below were each checked against a literal set of their own
        # values; `stack` and `sort` were not -- an unrecognised value passed `_structure`
        # (`visual_plan_verifier.py`) and reached the wire verbatim, the one path the DTO's own
        # `stack in get_args(Stack)` / `sort in get_args(SortMode)` guard does not cover, because
        # nothing here checked membership at all.
        if self.stack is not None and self.stack not in get_args(Stack):
            raise ValueError("unsupported chart stack")
        if self.mark == "donut" and self.stack is not None:
            raise ValueError("donut cannot be stacked")
        if self.stack is not None and not any(
            item.type == "quantitative" and item.channel in {"x", "y"} for item in self.encodings
        ):
            # The presenter looks up the stacked channel with a bare `next(...)` and no default,
            # relying on this invariant to make that lookup safe -- so a spec that carries a stack
            # with nothing to hang it on is refused here instead of raising `StopIteration` later.
            raise ValueError("a stacked chart needs a quantitative x or y encoding")
        if self.sort is not None and not any(
            item.type in {"nominal", "temporal"} and item.channel in {"x", "y"}
            for item in self.encodings
        ):
            # Its mirror: `present()` (`vega_lite.py:84-85`) raises "a sorted chart needs a
            # categorical or temporal dimension" when a chart-wide sort has no nominal/temporal
            # x/y encoding to land on. The verifier used to authorise exactly that shape -- a
            # normalised RATIO_SHARE bar (measure on the axis, the dimension in `color`) and a
            # donut (no axes at all) can each carry a chart-wide `sort` -- because nothing
            # upstream of the presenter enforced this. Refused here instead, so `_structure`
            # turns it into a `Rejection` rather than an unhandled exception at the presenter.
            raise ValueError("a sorted chart needs a categorical or temporal x or y encoding")
        channels = [item.channel for item in self.encodings if item.channel != "tooltip"]
        if len(channels) != len(set(channels)):
            raise ValueError("chart channels must be unique")
        if self.limit is not None and not 1 <= self.limit <= 100:
            raise ValueError("chart limit must be between 1 and 100")
        if self.sort is not None and self.sort not in get_args(SortMode):
            raise ValueError("unsupported chart sort")
        if self.sort_field is not None and self.sort is None:
            raise ValueError("sort_field requires sort")
        # `EncodingSort.field` and `ChartEncoding.field` both run this pattern; `sort_field` was
        # the one field-name slot left unchecked. Not exploitable today -- the verifier's
        # `_fields` refuses a `sort_field` outside the profile and `_value_free_spec` enforces the
        # pattern on the wire -- but it is the same class of name and belongs to the same check.
        if self.sort_field is not None and not FIELD_NAME_PATTERN.fullmatch(self.sort_field):
            raise ValueError("sort fields must be identifiers")

    def fields(self) -> frozenset[str]:
        return frozenset(item.field for item in self.encodings)

    def valid_for(self, fields: frozenset[str]) -> bool:
        if not self.fields() <= fields:
            return False
        if self.mark == "donut":
            # A donut has a single categorical part and one positive measure.
            channels = {item.channel for item in self.encodings}
            if "theta" not in channels or "color" not in channels:
                return False
        return self.limit is None or 1 <= self.limit <= 100


@dataclass(frozen=True)
class VisualRow:
    dimension: str
    value_minor: int
    series: str | None = None
    occurred_at: str | None = None


@dataclass(frozen=True)
class VisualDataset:
    rows: tuple[VisualRow, ...]
    metric: str
    title: str = "Resultados"


FieldRole = Literal["dimension", "measure", "series"]
FieldOrder = Literal["MEASURE_DESC", "INPUT", "TEMPORAL", "NONE"]

_ROLE_TYPES: Final[dict[FieldRole, frozenset[str]]] = {
    # The role's type is a fact about the dataset rather than a choice. A `measure` that is not
    # quantitative is a mislabelled dataset, and every rule downstream would inherit the lie.
    "measure": frozenset({"quantitative"}),
    "series": frozenset({"nominal"}),
    "dimension": frozenset({"nominal", "temporal"}),
}


@dataclass(frozen=True)
class VisualField:
    """One column of the dataset, described so a planner can choose a channel for it.

    `role` exists because `type` cannot carry it. The dataset has two nominal fields --
    `dimension` and `series` -- and nothing in their type says which is the axis and which is the
    colour split. `cardinality` is per field for the same reason: two series are a comparison and
    fifteen are an unreadable legend, and a single count on the profile could not tell them apart.
    """

    name: str
    type: FieldType
    role: FieldRole
    cardinality: int
    order: FieldOrder = "NONE"

    def __post_init__(self) -> None:
        if not FIELD_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("visual fields must be identifiers")
        if self.type not in {"nominal", "quantitative", "temporal"}:
            raise ValueError("unsupported visual field type")
        if self.role not in _ROLE_TYPES:
            raise ValueError("unsupported visual field role")
        if self.type not in _ROLE_TYPES[self.role]:
            raise ValueError(f"a {self.role} cannot be {self.type}")
        if self.cardinality < 0:
            raise ValueError("cardinality cannot be negative")
        if self.order not in {"MEASURE_DESC", "INPUT", "TEMPORAL", "NONE"}:
            raise ValueError("unsupported visual field order")


# Rows a chart draws when nobody said a number. A discrete dimension is one band per row, and at
# the band height the web uses this is about the height the plot had when it was a fixed box — so it
# is "what fits" rather than a preference. It is a presentation limit: the rows are all still there,
# the table under the chart lists every one of them, and the caveat on the chart says so.
#
# Declared here, above `VisualProfile`, rather than beside `derive_chart` below, because
# `DEFAULT_PRESENTATION` needs it too and a domain constant belongs in exactly one place.
ROWS_THAT_FIT = 8


@dataclass(frozen=True)
class VisualProfile:
    """Value-free facts about the *dataset*, safe to send in a conversation round trip.

    It used to be derived from a `ChartSpec` through `from_spec`, which is backwards for a planner:
    the profile is the input to the chart decision, not a summary of its output. Nothing ephemeral
    belongs here -- not the question, not the viewport -- because `conversation.py` validates this
    object coming back from the browser, so anything added to it becomes wire surface.
    """

    fields: tuple[VisualField, ...]
    # The dataset's *shape* marker (`COMPARISON`, `RATIO`, `RATIO_SHARE`, `RECURRENCE`, ...), not
    # always the measure. An axis title wants `measure_metric()` below, not this field directly.
    metric: str
    unit: str
    positive: bool = False
    mutually_exclusive: bool = False
    verified_total: bool = False
    comparison: bool = False
    # How many rows the chart draws. Not `measure.cardinality`, which counts *distinct amounts*:
    # two rows carrying the same amount are still two rows, and a floor reading the cardinality
    # would decline that chart for having one. It is a shape fact and not a value -- no amount and
    # no label leaks through a count -- so the "nothing here reads a value" guarantee holds.
    row_count: int = 0
    # Whether the plan already cut these rows -- somebody asked for their top 25. The floor reads
    # it beside `row_count` on one line of `derive_chart`, and without it a ranking of twenty-five
    # gets cut to eight a second time, answering a question about 25 with 8 and signing the 8 into
    # the saved metric. A fact about the rows and not about the chart, which is why it is here.
    plan_limited: bool = False
    # What the plan measured, when that is not what `metric` above says.
    #
    # `metric` is the *dataset's* marker and it is overloaded: sometimes it names the measure
    # (`SUM`, `COUNT`, `CUMULATIVE`) and sometimes the shape (`COMPARISON`, `RATIO`,
    # `RATIO_SHARE`, `RECURRENCE`). The shape is what selects a chart family; the measure is what
    # says whether the axis counts movements or euros. A comparison of two *counts* carries
    # `metric="COMPARISON"` and `plan_metric="COUNT"`, and reading only the first labels an axis of
    # movements "Importe" -- measured, 32 charts in this suite, before this field existed.
    #
    # `None` means "no plan context", which is exactly what the expression this replaces fell back
    # on. Unvalidated for the same reason: it is compared against literals, and a marker nobody
    # recognises has always just meant "none of those".
    plan_metric: str | None = None

    def __post_init__(self) -> None:
        names = [item.name for item in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("duplicate field name in visual profile")
        if self.row_count < 0:
            raise ValueError("row_count cannot be negative")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.metric):
            raise ValueError("visual profile metric must be uppercase")
        if self.unit not in {"money", "count", "ratio", "unknown"}:
            raise ValueError("unsupported visual profile unit")

    def field_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.fields)

    def field(self, name: str) -> VisualField | None:
        return next((item for item in self.fields if item.name == name), None)

    def role(self, role: FieldRole) -> tuple[VisualField, ...]:
        return tuple(item for item in self.fields if item.role == role)

    def temporal(self) -> bool:
        """Whether any dimension is temporal. Not a stored field: it is a question about types."""
        return any(item.type == "temporal" for item in self.role("dimension"))

    def measure_metric(self) -> str:
        """The plan's metric when there is one, falling back to the dataset's shape marker.

        This is what an axis title should read, never `metric` alone: `metric` is overloaded
        between the dataset's shape and its measure, and `plan_metric` is the one fact that tells
        them apart. Writing `profile.metric` here is the exact mistake that labelled 32 charts'
        axes "Importe" for a count -- this accessor exists so that mistake cannot be written again.
        """
        return self.plan_metric or self.metric


@dataclass(frozen=True)
class PresentationContext:
    """The circumstances of *this* execution, which are not facts about the data.

    Deliberately not part of `VisualProfile`: that object is validated coming back from the
    browser, so a field added to it becomes wire surface and travels on every turn.
    """

    channel: Literal["TEXT", "VOICE"]
    viewport: Literal["MOBILE", "DESKTOP"]
    max_visible_rows: int

    def __post_init__(self) -> None:
        if self.channel not in {"TEXT", "VOICE"}:
            raise ValueError("unsupported presentation channel")
        if self.viewport not in {"MOBILE", "DESKTOP"}:
            raise ValueError("unsupported viewport")
        if not 1 <= self.max_visible_rows <= 100:
            raise ValueError("max_visible_rows must be between 1 and 100")


# What a client that says nothing gets. `channel` already exists on the request DTO; `viewport`
# and `max_visible_rows` are new wire surface, and a default is what stops the visual work from
# waiting on a web deployment.
DEFAULT_PRESENTATION: Final = PresentationContext(
    channel="TEXT", viewport="DESKTOP", max_visible_rows=ROWS_THAT_FIT
)


@dataclass(frozen=True)
class VisualContext:
    chart_id: str
    spec: ChartSpec
    profile: VisualProfile

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.chart_id):
            raise ValueError("invalid chart id")


@dataclass(frozen=True)
class EncodingPatch:
    """An allow-listed, value-free change to a chart specification.

    ``changed`` is intentional: it distinguishes an omitted property from clearing one.
    Callers should use ``clear("stack")`` for the latter.
    """

    mark: Mark | None = None
    stack: Stack | None = None
    sort: SortMode | None = None
    sort_field: str | None = None
    limit: int | None = None
    title: str | None = None
    changed: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.changed:
            inferred = frozenset(
                name
                for name, value in (
                    ("mark", self.mark),
                    ("stack", self.stack),
                    ("sort", self.sort),
                    ("sort_field", self.sort_field),
                    ("limit", self.limit),
                    ("title", self.title),
                )
                if value is not None
            )
            object.__setattr__(self, "changed", inferred)

    @classmethod
    def clear(cls, *names: str) -> "EncodingPatch":
        allowed = {"stack", "sort", "sort_field", "limit", "title"}
        if not names or not set(names) <= allowed:
            raise ValueError("only optional chart properties may be cleared")
        return cls(changed=frozenset(names))


def merge_encoding_patch(
    spec: ChartSpec, patch: EncodingPatch, profile: VisualProfile
) -> ChartSpec:
    """Atomically apply a visual patch, rejecting malformed/incompatible results."""
    values = {
        "mark": spec.mark,
        "stack": spec.stack,
        "sort": spec.sort,
        "sort_field": spec.sort_field,
        "limit": spec.limit,
        "title": spec.title,
    }
    for name in patch.changed:
        if name not in values:
            raise ValueError(f"unsupported chart patch property: {name}")
        values[name] = getattr(patch, name)
    limit = values["limit"]
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 100):
        raise ValueError("chart limit must be between 1 and 100")
    if values["sort_field"] is not None and values["sort_field"] not in profile.field_names():
        raise ValueError("sort field is not present in the chart data")
    mark = cast(Mark, values["mark"])
    title = values["title"] if isinstance(values["title"], str) else None
    stack = cast(Stack | None, values["stack"])
    if stack not in (None, *get_args(Stack)):
        stack = None
    sort = cast(SortMode | None, values["sort"])
    if sort not in (None, *get_args(SortMode)):
        sort = None
    sort_field = values["sort_field"] if isinstance(values["sort_field"], str) else None
    if values["stack"] is not None and not any(item.channel == "color" for item in spec.encodings):
        raise ValueError("stacking requires a series/color dimension")
    if mark == "donut":
        if not (profile.positive and profile.mutually_exclusive and profile.verified_total):
            raise ValueError("donut requires positive, exclusive parts and a verified total")
        # A donut's parts are the nominal dimension's distinct labels -- the field that lands in
        # `color`. `profile.cardinality` used to be this number directly; now it is per field.
        nominal_dimension_count = next(
            (item.cardinality for item in profile.role("dimension") if item.type == "nominal"), 0
        )
        if not 2 <= nominal_dimension_count <= 5:
            raise ValueError("donut requires between 2 and 5 parts")
    encodings = spec.encodings
    if mark == "donut":
        nominal = next(
            (
                item
                for item in encodings
                if item.type == "nominal" and item.channel in {"x", "y", "color"}
            ),
            None,
        )
        quantitative = next(
            (
                item
                for item in encodings
                if item.type == "quantitative" and item.channel in {"x", "y", "theta"}
            ),
            None,
        )
        if nominal is None or quantitative is None:
            raise ValueError("donut needs a categorical field and a quantitative measure")
        encodings = tuple(
            ChartEncoding(
                "color" if item.field == nominal.field else "theta",
                item.field,
                item.type,
                item.title,
            )
            if item.channel in {"x", "y"} and item.field in {nominal.field, quantitative.field}
            else item
            for item in encodings
        )
        stack = None
    elif spec.mark == "donut":
        encodings = tuple(
            ChartEncoding(
                "y" if item.channel == "color" else "x",
                item.field,
                item.type,
                item.title,
            )
            if item.channel in {"color", "theta"}
            else item
            for item in encodings
        )
    result = ChartSpec(
        mark=mark,
        encodings=encodings,
        title=title,
        stack=stack,
        sort=sort,
        responsive=spec.responsive,
        sort_field=sort_field,
        limit=limit if isinstance(limit, int) else None,
    )
    if not result.valid_for(profile.field_names()):
        raise ValueError("chart patch is incompatible with visual profile")
    return result


# Short aliases used by adapters; keeping the operation named makes it easy to test without
# importing an LLM or HTTP layer.
apply_encoding_patch = merge_encoding_patch


@dataclass(frozen=True)
class Visualization:
    spec: ChartSpec
    data: tuple[dict[str, object], ...] = field(default_factory=tuple)


def derive_chart(
    *,
    group_by: str,
    metric: str,
    rows: int,
    fields: frozenset[str],
    dimension_field: str = "key",
    value_field: str | None = None,
    plan_limited: bool = False,
) -> ChartSpec | None:
    value_field = value_field or ("movement_count" if metric == "COUNT" else "total_minor")
    if rows < 2 or dimension_field not in fields or value_field not in fields:
        return None
    temporal = group_by in {"DAY", "WEEK", "MONTH", "PERIOD", "DATE"}
    mark: Mark = "area" if metric == "CUMULATIVE" else ("line" if temporal else "bar")
    measure_title = "Movimientos" if metric in {"COUNT", "FREQUENCY"} else "Importe"
    base = (
        (
            ChartEncoding("x", dimension_field, "temporal", "Periodo"),
            ChartEncoding("y", value_field, "quantitative", measure_title),
        )
        if temporal
        else (
            ChartEncoding("y", dimension_field, "nominal", "Grupo"),
            ChartEncoding("x", value_field, "quantitative", measure_title),
        )
    )
    return ChartSpec(
        mark=mark,
        encodings=base
        + (
            ChartEncoding("tooltip", dimension_field, "temporal" if temporal else "nominal"),
            ChartEncoding("tooltip", value_field, "quantitative"),
        ),
        # No spec-level `sort` here any more. A bare `"descending"` on a nominal channel is
        # Vega-Lite's *label* order (Z to A), not the measure -- it was never the ranking this
        # chart wants, and `DeterministicVisualPlanner.plan`'s own per-encoding `EncodingSort`
        # (`{"field": "value_minor", "order": "descending"}`) is what actually ranks by magnitude
        # and wins under `present()`'s precedence. Emitting the bare string anyway meant it sat
        # inert behind the per-encoding sort on the live path -- and came live again, wrong, the
        # moment a serialisation boundary that does not carry `EncodingSort` (a conversation's
        # round-tripped visual memory, a saved metric's encoded chart) dropped the per-encoding
        # sort and let this string stand in its place.
        #
        # Only when nobody said a number, and never on a temporal axis, where trimming rows would
        # drop months out of the middle of a series rather than tail off a ranking. If the plan
        # already cut -- somebody asked for their top 25 -- the chart does not cut again: cutting
        # twice would answer a question about twenty-five with eight, and a saved metric would
        # carry a limit nobody asked for.
        limit=None if temporal or plan_limited or rows <= ROWS_THAT_FIT else ROWS_THAT_FIT,
    )
