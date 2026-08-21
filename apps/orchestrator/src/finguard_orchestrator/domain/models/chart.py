"""Value-free chart vocabulary and deterministic defaults."""

import re
from dataclasses import dataclass, field
from typing import Literal, cast

Mark = Literal["line", "area", "bar", "point", "donut"]
Channel = Literal["x", "y", "color", "tooltip", "theta"]
FieldType = Literal["nominal", "quantitative", "temporal"]


@dataclass(frozen=True)
class ChartEncoding:
    channel: Channel
    field: str
    type: FieldType
    title: str | None = None

    def __post_init__(self) -> None:
        if self.channel not in {"x", "y", "color", "tooltip", "theta"}:
            raise ValueError("unsupported chart channel")
        if self.type not in {"nominal", "quantitative", "temporal"}:
            raise ValueError("unsupported chart field type")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.field):
            raise ValueError("chart fields must be identifiers")


@dataclass(frozen=True)
class ChartSpec:
    mark: Mark
    encodings: tuple[ChartEncoding, ...]
    title: str | None = None
    stack: Literal["zero", "normalize"] | None = None
    sort: Literal["ascending", "descending", "input"] | None = None
    sort_field: str | None = None
    limit: int | None = None
    responsive: bool = True

    def __post_init__(self) -> None:
        if self.mark not in {"line", "area", "bar", "point", "donut"}:
            raise ValueError("unsupported chart mark")
        if self.mark == "donut" and self.stack is not None:
            raise ValueError("donut cannot be stacked")
        channels = [item.channel for item in self.encodings if item.channel != "tooltip"]
        if len(channels) != len(set(channels)):
            raise ValueError("chart channels must be unique")
        if self.limit is not None and not 1 <= self.limit <= 100:
            raise ValueError("chart limit must be between 1 and 100")
        if self.sort_field is not None and self.sort is None:
            raise ValueError("sort_field requires sort")

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
class VisualProfile:
    """Value-free facts about a chart, safe to send in a conversation round trip."""

    fields: tuple[tuple[str, FieldType], ...]
    cardinality: int
    metric: str | None = None
    unit: str | None = None
    positive: bool = False
    mutually_exclusive: bool = False
    verified_total: bool = False

    def __post_init__(self) -> None:
        if self.cardinality < 0 or len({name for name, _ in self.fields}) != len(self.fields):
            raise ValueError("visual profile fields/cardinality are invalid")
        if self.metric is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.metric):
            raise ValueError("visual profile metric must be uppercase")
        if self.unit is not None and self.unit not in {"money", "count", "ratio", "unknown"}:
            raise ValueError("unsupported visual profile unit")

    @classmethod
    def from_spec(
        cls,
        spec: ChartSpec,
        *,
        cardinality: int = 0,
        metric: str | None = None,
        unit: str | None = None,
        positive: bool = False,
        mutually_exclusive: bool = False,
        verified_total: bool = False,
    ) -> "VisualProfile":
        typed_fields = tuple(dict((item.field, item.type) for item in spec.encodings).items())
        return cls(
            typed_fields,
            cardinality,
            metric,
            unit,
            positive,
            mutually_exclusive,
            verified_total,
        )

    def field_names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.fields)


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
    stack: Literal["zero", "normalize"] | None = None
    sort: Literal["ascending", "descending", "input"] | None = None
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
    stack = cast(Literal["zero", "normalize"] | None, values["stack"])
    if stack not in {None, "zero", "normalize"}:
        stack = None
    sort = cast(Literal["ascending", "descending", "input"] | None, values["sort"])
    if sort not in {None, "ascending", "descending", "input"}:
        sort = None
    sort_field = values["sort_field"] if isinstance(values["sort_field"], str) else None
    if values["stack"] is not None and not any(item.channel == "color" for item in spec.encodings):
        raise ValueError("stacking requires a series/color dimension")
    if mark == "donut":
        if not (profile.positive and profile.mutually_exclusive and profile.verified_total):
            raise ValueError("donut requires positive, exclusive parts and a verified total")
        if not 2 <= profile.cardinality <= 5:
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
        sort="descending" if not temporal else None,
    )
