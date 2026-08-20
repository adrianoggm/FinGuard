"""Value-free chart vocabulary and deterministic defaults."""

from dataclasses import dataclass, field
from typing import Literal

Mark = Literal["line", "area", "bar", "point"]
Channel = Literal["x", "y", "color", "tooltip"]


@dataclass(frozen=True)
class ChartEncoding:
    channel: Channel
    field: str
    type: Literal["nominal", "quantitative", "temporal"]
    title: str | None = None


@dataclass(frozen=True)
class ChartSpec:
    mark: Mark
    encodings: tuple[ChartEncoding, ...]
    title: str | None = None
    stack: Literal["zero", "normalize"] | None = None
    sort: Literal["ascending", "descending", "input"] | None = None
    responsive: bool = True

    def fields(self) -> frozenset[str]:
        return frozenset(item.field for item in self.encodings)

    def valid_for(self, fields: frozenset[str]) -> bool:
        return self.fields() <= fields


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
    base = (
        (
            ChartEncoding("x", dimension_field, "temporal", "Periodo"),
            ChartEncoding("y", value_field, "quantitative", "Valor"),
        )
        if temporal
        else (
            ChartEncoding("y", dimension_field, "nominal", "Grupo"),
            ChartEncoding("x", value_field, "quantitative", "Valor"),
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
