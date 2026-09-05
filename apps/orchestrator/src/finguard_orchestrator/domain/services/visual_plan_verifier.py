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
    # `theta` takes a measure and only with `donut` (Section D). The matrix's role/channel table
    # cannot say this -- it does not know about marks -- so it is a per-mark rule.
    if spec.mark != "donut" and any(item.channel == "theta" for item in spec.encodings):
        return Rejection("mark", "theta belongs only to a donut")

    x, y = _on(spec, "x"), _on(spec, "y")
    roles = {item.field: profile.field(item.field) for item in spec.encodings}

    def role_on(item: ChartEncoding | None) -> str | None:
        if item is None:
            return None
        held = roles[item.field]
        return held.role if held is not None else None

    if spec.mark == "bar":
        if spec.stack == "normalize":
            # The normalised RATIO_SHARE bar: the measure on **one** axis, the nominal dimension
            # in `color`, no opposite axis and no offsets -- one composed bar whose parts are the
            # dimension's values. An earlier version of this table omitted the row, and a verifier
            # faithful to it refused the floor's own RATIO_SHARE chart.
            color = _on(spec, "color")
            axes = [item for item in (x, y) if item is not None]
            measure_axis = next((item for item in axes if role_on(item) == "measure"), None)
            has_offset = any(item.channel in _OFFSETS for item in spec.encodings)
            wrong_shape = (
                len(axes) != 1
                or measure_axis is None
                or color is None
                or role_on(color) != "dimension"
                or color.type != "nominal"
                or has_offset
            )
            if wrong_shape:
                return Rejection(
                    "mark",
                    "a normalised bar puts the measure on one axis and the dimension in "
                    "color, with no opposite axis and no offsets",
                )
        elif {role_on(x), role_on(y)} != {"dimension", "measure"}:
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
        wrong_y = y is None or role_on(y) != "dimension" or y.type != "nominal"
        if wrong_x or wrong_y:
            return Rejection(
                "mark", "a point chart puts occurred_at on x and the nominal dimension on y"
            )
        amount_channels = _AXES | {"theta"}
        if any(
            item.channel in amount_channels and role_on(item) == "measure"
            for item in spec.encodings
        ):
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
    """The encoding on `x`/`y` whose field is a `dimension`, preferring a nominal one.

    Not inlined into `_grouped`'s `next(...)` -- that put `profile.field(i.field).role` on one
    line, and `profile.field` returns `VisualField | None`: a fresh call per item, so mypy cannot
    carry a truthiness check on one call over to the attribute access on another.

    Prefers a nominal candidate over a temporal one instead of whichever axis draft order lists
    first: a RECURRENCE profile's `occurred_at` (temporal, role `dimension`) and `dimension`
    (nominal) can both be axis-placed at once, and it is the nominal one a grouped comparison's
    own rule cares about, not whichever the model listed first.
    """
    candidates = [
        item
        for item in spec.encodings
        if item.channel in _AXES
        and (held := profile.field(item.field)) is not None
        and held.role == "dimension"
    ]
    nominal = next((item for item in candidates if item.type == "nominal"), None)
    return nominal if nominal is not None else next(iter(candidates), None)


def _grouped(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    series = {item.name for item in profile.role("series")}
    offsets = [item for item in spec.encodings if item.channel in _OFFSETS]
    # No "offset names a non-series field" check here: `_matrix` already refuses any channel in
    # `_OFFSETS` for a role/type pair other than `("series", "nominal")`, so an offset reaching
    # this rule always names a series field -- the check this replaced could never fire.
    if len(offsets) > 1:
        return Rejection("grouped", "a comparison offsets one axis, not both")
    color = _on(spec, "color")
    if offsets and color is not None and color.field != offsets[0].field:
        return Rejection("grouped", "color and the offset must name the same series")
    uses_series = any(item.field in series for item in spec.encodings if item.channel != "tooltip")
    if spec.mark == "bar" and uses_series:
        # Not `not profile.temporal()`: that is true for *any* dimension-role field being
        # temporal, and a RECURRENCE profile's `occurred_at` makes it true even when the field
        # actually placed on the axis is the nominal `dimension` -- which would let a stacked,
        # offset-less bar through on a RECURRENCE profile. The axis encoding's own type is what
        # decides whether this is a categorical grouped comparison.
        dimension = _dimension_axis(spec, profile)
        if dimension is not None and dimension.type == "nominal":
            wanted = _OFFSET_FOR[dimension.channel]
            if _on(spec, wanted) is None:
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
        # A dimension can rank by magnitude without being on an axis at all: the normalised
        # RATIO_SHARE bar places its `MEASURE_DESC` dimension in `color`, and the floor never
        # sorts it there. Requiring the rank regardless of channel refused that chart against
        # itself -- the requirement is really about an *axis*, which reorders itself the moment
        # nothing says otherwise, the same reasoning `INPUT` already carries below.
        on_axis = item.channel in _AXES
        if held.order == "MEASURE_DESC" and on_axis:
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
