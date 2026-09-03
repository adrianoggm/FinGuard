"""Vega-Lite presenter, kept outside the domain and free of financial calculations."""

from typing import Any, cast

from finguard_orchestrator.domain.models.chart import ChartSpec, Visualization


def present(visualization: Visualization) -> dict[str, Any]:
    """Emit a Vega-Lite spec and the already measured rows as separate properties."""
    encoding: dict[str, Any] = {}
    for item in visualization.spec.encodings:
        encoded: dict[str, Any] = {
            "field": item.field,
            "type": item.type,
            **({"title": item.title} if item.title else {}),
        }
        if item.sort is not None:
            # Vega-Lite reads `"sort": null` as "keep the data's order", which is what `input`
            # means. A named field becomes the object form.
            if item.sort.mode == "input":
                encoded["sort"] = None
            elif item.sort.field is not None:
                encoded["sort"] = {"field": item.sort.field, "order": item.sort.mode}
            else:
                encoded["sort"] = item.sort.mode
        if item.channel == "tooltip" and item.channel in encoding:
            previous = encoding[item.channel]
            encoding[item.channel] = [previous] if isinstance(previous, dict) else previous
            encoding[item.channel].append(encoded)
        else:
            encoding[item.channel] = encoded
    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": (
            {"type": "arc", "innerRadius": 55}
            if visualization.spec.mark == "donut"
            else {"type": visualization.spec.mark}
        ),
        "encoding": encoding,
        "width": "container",
        "height": 220,
    }
    if visualization.spec.title:
        spec["title"] = visualization.spec.title
    if visualization.spec.limit is not None:
        # Structural transform only: rows remain outside the spec and are never copied into it.
        measure = next(
            (
                item.field
                for item in visualization.spec.encodings
                if item.type == "quantitative" and item.channel in {"x", "y", "theta"}
            ),
            None,
        )
        if measure is None:
            raise ValueError("a limited chart needs a quantitative measure")
        spec["transform"] = [
            {
                "window": [{"op": "rank", "as": "_rank"}],
                "sort": [{"field": measure, "order": "descending"}],
            },
            {"filter": f"datum._rank <= {visualization.spec.limit}"},
        ]
    if visualization.spec.stack is not None:
        # `ChartSpec.__post_init__` guarantees a quantitative x/y encoding whenever `stack` is
        # set, so this `next()` cannot raise `StopIteration` here.
        quantitative = next(
            item.channel
            for item in visualization.spec.encodings
            if item.type == "quantitative" and item.channel in {"x", "y"}
        )
        # `disabled` has to be an explicit null. Omitting the property is what Vega-Lite reads as
        # "you decide", and for a bar with a colour series it decides to stack -- so the absent
        # property is the bug, not the fix.
        spec["encoding"][quantitative]["stack"] = (
            None if visualization.spec.stack == "disabled" else visualization.spec.stack
        )
    if visualization.spec.sort:
        nominal = next(
            (
                item.channel
                for item in visualization.spec.encodings
                if item.type in {"nominal", "temporal"} and item.channel in {"x", "y"}
            ),
            None,
        )
        if nominal is None:
            raise ValueError("a sorted chart needs a categorical or temporal dimension")
        # A per-encoding sort wins for its own channel: the loop above already wrote a "sort"
        # key onto `nominal`'s encoding when that channel's `ChartEncoding.sort` was set, and it
        # says something more specific about that one channel than this chart-wide default does.
        # Vega-Lite has one "sort" property per channel, so the spec-level default only applies
        # where nothing more specific claimed it.
        if "sort" not in spec["encoding"][nominal]:
            if visualization.spec.sort_field:
                spec["encoding"][nominal]["sort"] = {
                    "field": visualization.spec.sort_field,
                    "order": (
                        None if visualization.spec.sort == "input" else visualization.spec.sort
                    ),
                }
            else:
                spec["encoding"][nominal]["sort"] = (
                    None if visualization.spec.sort == "input" else visualization.spec.sort
                )
    return {"spec": spec, "data": list(visualization.data)}


def present_spec(spec: ChartSpec) -> dict[str, Any]:
    """Present only a structural spec for a visual update (never rows)."""
    # Vega-Lite cannot sort a temporal scale by a measure through the nominal-only
    # path below.  A bar follow-up is a discrete ranking even when its source
    # dimension is a month, so expose that presentation detail without changing
    # the signed ChartSpec/context (or the source rows).
    presented = cast(dict[str, Any], present(Visualization(spec=spec, data=()))["spec"])
    if spec.mark == "bar" and spec.sort_field:
        encodings = cast(dict[str, Any], presented.get("encoding", {}))
        dimension_channel = next(
            (
                item.channel
                for item in spec.encodings
                if item.channel in {"x", "y"} and item.type in {"temporal", "nominal"}
            ),
            None,
        )
        if dimension_channel is not None:
            dimension = encodings.get(dimension_channel)
            if isinstance(dimension, dict):
                dimension["type"] = "nominal"
    return presented
