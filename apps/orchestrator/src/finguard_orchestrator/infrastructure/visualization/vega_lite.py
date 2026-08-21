"""Vega-Lite presenter, kept outside the domain and free of financial calculations."""

from typing import Any, cast

from finguard_orchestrator.domain.models.chart import ChartSpec, Visualization


def present(visualization: Visualization) -> dict[str, Any]:
    """Emit a Vega-Lite spec and the already measured rows as separate properties."""
    encoding: dict[str, Any] = {}
    for item in visualization.spec.encodings:
        encoded = {
            "field": item.field,
            "type": item.type,
            **({"title": item.title} if item.title else {}),
        }
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
    if visualization.spec.stack:
        quantitative = next(
            item.channel
            for item in visualization.spec.encodings
            if item.type == "quantitative" and item.channel in {"x", "y"}
        )
        spec["encoding"][quantitative]["stack"] = visualization.spec.stack
    if visualization.spec.sort:
        nominal = next(
            item.channel
            for item in visualization.spec.encodings
            if item.type == "nominal" and item.channel in {"x", "y"}
        )
        if visualization.spec.sort_field:
            spec["encoding"][nominal]["sort"] = {
                "field": visualization.spec.sort_field,
                "order": None if visualization.spec.sort == "input" else visualization.spec.sort,
            }
        else:
            spec["encoding"][nominal]["sort"] = (
                None if visualization.spec.sort == "input" else visualization.spec.sort
            )
    return {"spec": spec, "data": list(visualization.data)}


def present_spec(spec: ChartSpec) -> dict[str, Any]:
    """Present only a structural spec for a visual update (never rows)."""
    return cast(dict[str, Any], present(Visualization(spec=spec, data=()))["spec"])
