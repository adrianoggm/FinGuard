"""What the visual model sees: the dataset's shape, the question, the circumstances -- no values.

`profile_payload` is the object the structural test in `test_llm_visual_planner.py` pins: its key
set is the proof that rows, amounts and labels cannot travel. The schema is built from the domain
literals with `get_args`, never written out -- this repository removed three hand-copies of
`Channel` after one went stale in a validator.
"""

import json
from typing import Any, get_args

from finguard_orchestrator.domain.models.chart import (
    Channel,
    FieldType,
    Mark,
    PresentationContext,
    SortMode,
    Stack,
    VisualProfile,
)


def profile_payload(profile: VisualProfile, presentation: PresentationContext) -> dict[str, object]:
    return {
        "fields": [
            {
                "name": f.name,
                "type": f.type,
                "role": f.role,
                "cardinality": f.cardinality,
                "order": f.order,
            }
            for f in profile.fields
        ],
        "metric": profile.measure_metric(),
        "unit": profile.unit,
        "positive": profile.positive,
        "mutually_exclusive": profile.mutually_exclusive,
        "verified_total": profile.verified_total,
        "comparison": profile.comparison,
        "row_count": profile.row_count,
        "presentation": {
            "viewport": presentation.viewport,
            "max_visible_rows": presentation.max_visible_rows,
        },
    }


def visual_schema() -> dict[str, Any]:
    sort = {
        "type": "object",
        "properties": {
            "mode": {"enum": list(get_args(SortMode))},
            "field": {"type": ["string", "null"]},
        },
        "required": ["mode"],
    }
    return {
        "type": "object",
        "properties": {
            "mark": {"enum": [*get_args(Mark), None]},
            "encodings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "channel": {"enum": list(get_args(Channel))},
                        "field": {"type": "string"},
                        "type": {"enum": list(get_args(FieldType))},
                        "sort": {**sort, "type": ["object", "null"]},
                    },
                    "required": ["channel", "field", "type"],
                },
            },
            "sort": {"enum": [*get_args(SortMode), None]},
            "sort_field": {"type": ["string", "null"]},
            "stack": {"enum": [*get_args(Stack), None]},
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
            "rationale": {"type": ["string", "null"]},
        },
        "required": ["mark", "encodings"],
    }


_RULES = """You choose how a financial answer is drawn. You never see the numbers: only the shape.
Answer with one JSON object matching the schema, or {"mark": null, "encodings": []} to abstain.
Field names must come from `fields`; each encoding's `type` must equal that field's `type`.
Roles: a dimension goes on x, y or color; a measure on x, y or theta; a series on color and the
offset (xOffset for a dimension on x, yOffset for a dimension on y). A comparison (comparison=true)
is grouped, never stacked: put the series on color and the matching offset, and set stack to
"disabled". A dimension with order MEASURE_DESC ranks by the measure (descending); order INPUT keeps
the given order (sort "input"); order TEMPORAL is a line or area over time. A donut needs positive,
mutually_exclusive and verified_total all true. limit only on a ranked categorical bar with no
series. Prefer the chart a careful analyst would draw for this question; do not decorate."""


def build_visual_prompt(
    question: str, profile: VisualProfile, presentation: PresentationContext
) -> str:
    shape = json.dumps(profile_payload(profile, presentation), ensure_ascii=False)
    return (
        f"{_RULES}\n\nQuestion (verbatim, may be in Spanish): {question}\n\n"
        f"Shape of the result:\n{shape}\n"
    )
