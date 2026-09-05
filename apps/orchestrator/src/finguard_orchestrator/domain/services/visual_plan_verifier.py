"""The domain authorises what the model proposed, or says which rule it broke. Never repairs.

A repaired plan is a disagreement nobody can see -- `plan_verifier`'s reason, applied to charts.
Each rule is a function returning `Rejection | None`; the first rejection wins, so a rejection
rate can be read per rule and a test can name the rule it expects.
"""

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    VisualProfile,
)
from finguard_orchestrator.domain.models.visual_plan import Rejection, VisualPlanDraft

_MATRIX: dict[tuple[str, str], frozenset[str]] = {
    ("dimension", "nominal"): frozenset({"x", "y", "color", "tooltip"}),
    ("dimension", "temporal"): frozenset({"x", "y", "tooltip"}),
    ("measure", "quantitative"): frozenset({"x", "y", "theta", "tooltip"}),
    ("series", "nominal"): frozenset({"color", "xOffset", "yOffset", "tooltip"}),
}
_AXES = frozenset({"x", "y"})
_OFFSETS = frozenset({"xOffset", "yOffset"})
_OFFSET_FOR = {"x": "xOffset", "y": "yOffset"}


def verify(draft: VisualPlanDraft, profile: VisualProfile) -> ChartSpec | Rejection:
    built = _structure(draft)
    if isinstance(built, Rejection):
        return built
    for rule in (_fields, _types, _matrix, _mark, _donut_claims, _grouped, _limit, _order):
        rejected = rule(built, profile)
        if rejected is not None:
            return rejected
    return built


def _structure(draft: VisualPlanDraft) -> ChartSpec | Rejection:
    try:
        encodings = tuple(
            ChartEncoding(
                item.channel,  # type: ignore[arg-type]  # refused at runtime, the point
                item.field,
                item.type,  # type: ignore[arg-type]
                sort=(
                    EncodingSort(item.sort.mode, item.sort.field)  # type: ignore[arg-type]
                    if item.sort is not None
                    else None
                ),
            )
            for item in draft.encodings
        )
        return ChartSpec(
            mark=draft.mark,  # type: ignore[arg-type]
            encodings=encodings,
            stack=draft.stack,  # type: ignore[arg-type]
            sort=draft.sort,  # type: ignore[arg-type]
            sort_field=draft.sort_field,
            limit=draft.limit,
        )
    except ValueError as exc:
        return Rejection("structure", str(exc))


def _fields(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    missing = sorted(spec.fields() - profile.field_names())
    return Rejection("fields", f"unknown field {missing[0]}") if missing else None


def _types(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    for item in spec.encodings:
        held = profile.field(item.field)
        if held is not None and held.type != item.type:
            return Rejection("types", f"{item.field} is {held.type}, not {item.type}")
    return None


def _matrix(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    for item in spec.encodings:
        held = profile.field(item.field)
        assert held is not None  # _fields ran first
        allowed = _MATRIX.get((held.role, held.type), frozenset())
        if item.channel not in allowed:
            return Rejection("matrix", f"{held.role} {held.type} cannot take {item.channel}")
    return None


def _on(spec: ChartSpec, channel: str) -> ChartEncoding | None:
    return next((item for item in spec.encodings if item.channel == channel), None)


def _mark(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    x, y = _on(spec, "x"), _on(spec, "y")
    roles = {item.field: profile.field(item.field) for item in spec.encodings}

    def role_on(item: ChartEncoding | None) -> str | None:
        if item is None:
            return None
        held = roles[item.field]
        return held.role if held is not None else None

    if spec.mark == "bar":
        if {role_on(x), role_on(y)} != {"dimension", "measure"}:
            return Rejection("mark", "a bar puts the dimension and the measure on opposite axes")
    elif spec.mark in {"line", "area"}:
        axes = [item for item in (x, y) if item is not None]
        has_temporal_axis = any(item.type == "temporal" for item in axes)
        roles_match = {role_on(x), role_on(y)} == {"dimension", "measure"}
        if len(axes) != 2 or not has_temporal_axis or not roles_match:
            return Rejection(
                "mark",
                f"a {spec.mark} needs a temporal dimension on one axis and the measure on the "
                "other",
            )
    elif spec.mark == "point":
        wrong_x = x is None or x.field != "occurred_at" or x.type != "temporal"
        if wrong_x or role_on(y) != "dimension":
            return Rejection("mark", "a point chart puts occurred_at on x and the dimension on y")
        if any(item.channel in _AXES and role_on(item) == "measure" for item in spec.encodings):
            return Rejection("mark", "a point chart carries the amount only in tooltip")
    elif spec.mark == "donut":
        theta, color = _on(spec, "theta"), _on(spec, "color")
        if color is None or role_on(theta) != "measure" or role_on(color) != "dimension":
            return Rejection(
                "mark", "a donut puts the measure in theta and one nominal dimension in color"
            )
        if color.type != "nominal":
            return Rejection(
                "mark", "a donut puts the measure in theta and one nominal dimension in color"
            )
        has_offset = any(item.channel in _OFFSETS for item in spec.encodings)
        if x is not None or y is not None or has_offset:
            return Rejection("mark", "a donut has no axes and no offsets")
    return None


def _donut_claims(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    if spec.mark != "donut":
        return None
    if not (profile.positive and profile.mutually_exclusive and profile.verified_total):
        return Rejection(
            "donut_claims", "a donut asserts positive, exclusive parts of a verified whole"
        )
    return None


def _dimension_axis(spec: ChartSpec, profile: VisualProfile) -> ChartEncoding | None:
    """The encoding on `x`/`y` whose field is a `dimension`, or `None` if there is none.

    Not inlined into `_grouped`'s `next(...)` -- that put `profile.field(i.field).role` on one
    line, and `profile.field` returns `VisualField | None`: a fresh call per item, so mypy cannot
    carry a truthiness check on one call over to the attribute access on another.
    """
    for item in spec.encodings:
        if item.channel not in _AXES:
            continue
        held = profile.field(item.field)
        if held is not None and held.role == "dimension":
            return item
    return None


def _grouped(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    series = {item.name for item in profile.role("series")}
    offsets = [item for item in spec.encodings if item.channel in _OFFSETS]
    for item in offsets:
        if item.field not in series:
            return Rejection("grouped", "an offset takes only a series field")
    color = _on(spec, "color")
    if offsets and color is not None and color.field != offsets[0].field:
        return Rejection("grouped", "color and the offset must name the same series")
    uses_series = any(item.field in series for item in spec.encodings if item.channel != "tooltip")
    if spec.mark == "bar" and uses_series and not profile.temporal():
        dimension = _dimension_axis(spec, profile)
        wanted = _OFFSET_FOR[dimension.channel] if dimension is not None else None
        if wanted is None or _on(spec, wanted) is None:
            return Rejection(
                "grouped", "a grouped comparison needs the offset on the dimension's axis"
            )
        if spec.stack != "disabled":
            return Rejection(
                "grouped", "a comparison is grouped, never stacked: stack must be disabled"
            )
    return None


def _limit(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    if spec.limit is None:
        return None
    dimension = next((f for f in profile.role("dimension") if f.type == "nominal"), None)
    wrong_family = (
        spec.mark != "bar"
        or profile.role("series")
        or dimension is None
        or dimension.order != "MEASURE_DESC"
    )
    if wrong_family:
        return Rejection(
            "limit",
            "limit is a ranking transform: categorical bar, single series, MEASURE_DESC only",
        )
    return None


def _order(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    measure = profile.role("measure")
    measure_name = measure[0].name if measure else None
    for item in spec.encodings:
        if item.channel == "tooltip":
            continue
        held = profile.field(item.field)
        if held is None or held.role != "dimension":
            continue
        sort = item.sort
        chart_wide = spec.sort is not None and (
            spec.sort_field == item.field or spec.sort_field is None
        )
        if held.order == "MEASURE_DESC":
            ranked = sort is not None and sort.mode == "descending" and sort.field == measure_name
            ranked_wide = spec.sort == "descending" and spec.sort_field == measure_name
            if not (ranked or ranked_wide):
                return Rejection(
                    "order", f"{item.field} ranks by magnitude: descending by {measure_name}"
                )
        elif held.order == "INPUT":
            wrong_encoding_sort = sort is not None and sort.mode != "input"
            wrong_chart_sort = chart_wide and spec.sort != "input"
            if wrong_encoding_sort or wrong_chart_sort:
                return Rejection("order", f"{item.field} keeps the producer's order")
        elif held.order == "TEMPORAL":
            if sort is not None and sort.field not in (None, item.field):
                return Rejection("order", f"{item.field} is ordered by time, not by {sort.field}")
        elif held.order == "NONE" and sort is not None:
            return Rejection("order", f"ordering does not apply to {item.field} on a {spec.mark}")
    return None
