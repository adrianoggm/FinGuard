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
_STACKABLE = frozenset({"bar", "area"})  # the two marks Vega-Lite stacks by default


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
    except (ValueError, TypeError) as exc:
        # Catching is not repairing: `field=None`, `limit="5"`, `encodings=None` raise `TypeError`
        # rather than `ValueError` (a `None`/wrong-shaped argument reaching `re.fullmatch` or a
        # comparison, not a value `__post_init__` recognised and refused on purpose), and would
        # otherwise escape `verify` as an exception instead of becoming this rejection.
        return Rejection("structure", str(exc))


def _fields(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    names = profile.field_names()
    if spec.fields() - names:
        return Rejection("fields", "unknown field")
    # A sort field is a field too: `sort_field` and every encoding's `sort.field` must exist in
    # the profile, or the name reaches the presenter as a column the data does not have --
    # `merge_encoding_patch` already refuses exactly that on the user-patch path.
    named_sorts = {
        item.sort.field
        for item in spec.encodings
        if item.sort is not None and item.sort.field is not None
    }
    if spec.sort_field is not None:
        named_sorts.add(spec.sort_field)
    if named_sorts - names:
        return Rejection("fields", "sort names an unknown field")
    return None


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
        if spec.stack == "normalize":
            # Section D gives `normalize` to `bar` alone. `_donut_claims` gates on
            # `stack == "normalize"` mark-agnostically, so without this a `line`/`area` whose
            # profile is positive, mutually exclusive and verified-total slipped through it --
            # and the only shape that can qualify with no series is a single-series 100% band
            # that says nothing, drawn as a line whose stack is simply inert.
            return Rejection("mark", f"normalize composes a bar, not a {spec.mark}")
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
        # No `theta` in the channels checked here: the top-of-function check already refuses it
        # on any non-donut mark, point included, before this branch is ever reached, so `theta`
        # never appears in `spec.encodings` by the time this line runs.
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
    # A normalised bar claims the same thing a donut does -- one whole, composed of these parts
    # -- just drawn as a single 100% bar instead of a circle. `mark != "donut"` alone let a
    # breakdown with `verified_total=False` (the donut is refused for exactly this) draw as a
    # normalised bar instead, and a comparison's series (`mutually_exclusive=False`: its rows are
    # not disjoint parts of one whole) compose into one bar the same way.
    if spec.mark != "donut" and spec.stack != "normalize":
        return None
    if not (profile.positive and profile.mutually_exclusive and profile.verified_total):
        return Rejection(
            "donut_claims",
            "a donut or a normalised bar asserts positive, exclusive parts of a verified whole",
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
    # General reasons first, on purpose: an offset without its colour is refused here even on a
    # comparison profile, because "put the same series on both" is the actionable repair.
    if offsets and color is None:
        # An offset separates bars by a series nobody can tell apart: the legend is the colour.
        return Rejection("grouped", "an offset needs its color: the same series on both")
    if offsets and color is not None and color.field != offsets[0].field:
        return Rejection("grouped", "color and the offset must name the same series")
    if profile.comparison and (color is None or color.field not in series):
        # Section D: a comparison keeps its series, and colour is where the series lives on every
        # mark. Until this rule a proposal that dropped the series -- dimension on one axis, the
        # measure on the other, nothing else -- verified as a reasonable bar with the comparison
        # gone. Tooltip does not count: a series nobody can see is not a comparison.
        return Rejection("grouped", "a comparison keeps its series: put it on color")
    if profile.comparison and spec.mark in _STACKABLE and spec.stack != "disabled":
        # Section D's row is unqualified: a comparison's periods are never added together, over
        # categories or over time. The offset below is only meaningful on a categorical axis; the
        # stack rule is not -- a temporal comparison drawn as a bar or an area stacks by Vega-Lite's
        # default (and `stack: "zero"` says so outright), and the bar's length becomes a number
        # nobody asked for.
        return Rejection(
            "grouped", "a comparison is grouped, never stacked: stack must be disabled"
        )
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
    if profile.plan_limited:
        # The model cannot be trusted to obey the prompt's "never limit again" prose; a domain
        # rule can. Without this, a ranking the plan already cut to its own top N gets cut a
        # second time, answering a question about 25 with 8 and signing the 8 into the record.
        return Rejection(
            "limit", "the plan already limited these rows; a second cut would double-truncate"
        )
    dimension = next((f for f in profile.role("dimension") if f.type == "nominal"), None)
    wrong_family = (
        spec.mark != "bar"
        # A normalised bar is a `bar`, so `mark != "bar"` alone let `limit` through onto it too --
        # the presenter's rank-and-filter transform over a composition, exactly the row Section
        # D's limit table names.
        or spec.stack == "normalize"
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
    """Ordering is a property of the *field*, and the series' order is not the dimension's.

    Runs over every `dimension` **or** `series` role field: a comparison's legend (`color`,
    its offset) can carry its own `INPUT` order even while the axis dimension it sits beside
    wants `MEASURE_DESC` -- one setting on the profile would have forced the legend to inherit
    the axis's rule, which is why `order` lives on `VisualField` rather than on the profile.
    """
    measure = profile.role("measure")
    measure_name = measure[0].name if measure else None
    for item in spec.encodings:
        if item.channel == "tooltip":
            continue
        held = profile.field(item.field)
        if held is None or held.role not in {"dimension", "series"}:
            continue
        sort = item.sort
        # `present()` (`vega_lite.py:96-107`) picks the first nominal/temporal x/y encoding and
        # writes the chart-wide sort onto *that* channel, unconditionally -- `sort_field` is the
        # key to sort by, never the target, so it names a different field far more often than it
        # names this one (a magnitude ranking's `sort_field` is the measure). Comparing it against
        # `item.field` (fix round 1's reading) meant a chart-wide sort whose `sort_field` named
        # some other real field looked like it was not targeting this axis at all, when the
        # presenter would apply it here regardless.
        is_axis_dimension = item.channel in _AXES and held.role == "dimension"
        chart_wide = spec.sort is not None and is_axis_dimension
        if held.order == "MEASURE_DESC":
            # The *requirement* is scoped to the axis for the same reason `INPUT`'s is below: an
            # axis reorders itself the moment nothing says otherwise, which is what the ranking
            # requirement is against. The floor's own normalised RATIO_SHARE bar places its
            # `MEASURE_DESC` dimension in `color`, never on an axis, and never sorts it -- a
            # requirement that reached every `MEASURE_DESC` field regardless of channel refused
            # that chart against itself. But gating the whole check on `is_axis_dimension` (an
            # `elif`, so an off-axis field skipped it entirely) meant an off-axis field's sort, if
            # it had one, was never checked either: a `color` legend ranked *ascending* --
            # backwards from what `MEASURE_DESC` means -- was unrefusable. Off-axis, a sort may be
            # omitted; a sort that is there may not contradict the rank.
            ranked = sort is not None and sort.mode == "descending" and sort.field == measure_name
            ranked_wide = spec.sort == "descending" and spec.sort_field == measure_name
            if not (ranked or ranked_wide) and (is_axis_dimension or sort is not None):
                return Rejection(
                    "order", f"{item.field} ranks by magnitude: descending by {measure_name}"
                )
        elif held.order == "INPUT":
            wrong_encoding_sort = sort is not None and sort.mode != "input"
            wrong_chart_sort = chart_wide and spec.sort != "input"
            if wrong_encoding_sort or wrong_chart_sort:
                return Rejection("order", f"{item.field} keeps the producer's order")
            # An axis reorders itself the moment nothing says otherwise -- Vega-Lite alphabetises
            # a nominal domain with no explicit sort. A legend (`color`/an offset) is never an
            # axis, and the floor's own RECURRENCE-with-series chart never sorts its `color`, so
            # the requirement stops at the axis rather than at every `INPUT` field.
            has_input_sort = (sort is not None and sort.mode == "input") or (
                chart_wide and spec.sort == "input"
            )
            if is_axis_dimension and not has_input_sort:
                return Rejection("order", f"{item.field} keeps the producer's order")
        elif held.order == "TEMPORAL":
            if sort is not None and sort.field not in (None, item.field):
                return Rejection("order", f"{item.field} is ordered by time, not by {sort.field}")
            # `chart_wide` alone no longer says *which* field the chart-wide sort ranks by --
            # only that it lands on this axis -- so the "another field" check has to read
            # `spec.sort_field` directly: naming this field (or nothing) is a direction choice on
            # the field's own values, Section D's TEMPORAL row allows that; naming another one is
            # exactly "an explicit sort on another field", the same violation the per-encoding
            # branch above refuses.
            if chart_wide and spec.sort_field not in (None, item.field):
                return Rejection(
                    "order", f"{item.field} is ordered by time, not by {spec.sort_field}"
                )
        elif held.order == "NONE" and (sort is not None or chart_wide):
            return Rejection("order", f"ordering does not apply to {item.field} on a {spec.mark}")
    return None
