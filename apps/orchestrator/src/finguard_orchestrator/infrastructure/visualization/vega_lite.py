"""Vega-Lite presenter, kept outside the domain and free of financial calculations."""

from typing import Any

from finguard_orchestrator.domain.models.chart import Visualization


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
        "mark": {"type": visualization.spec.mark},
        "encoding": encoding,
        "width": "container",
        "height": 220,
    }
    if visualization.spec.title:
        spec["title"] = visualization.spec.title
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
        spec["encoding"][nominal]["sort"] = (
            None if visualization.spec.sort == "input" else visualization.spec.sort
        )
    return {"spec": spec, "data": list(visualization.data)}
