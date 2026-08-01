"""The JSON Schema the decoder is constrained to, generated from the enums.

Generated rather than written out, because a hand-copied list of nine period names is a
list that goes stale the first time someone adds a tenth. The enum is the source; this
is a projection of it.

Two decisions are load-bearing:

**The schema admits refusal.** If it only described a plan, a constrained decoder would
be physically unable to emit anything else — the model could not say "I don't know" even
when that is the only honest answer, so ambiguity would come back as a confident,
schema-valid guess. Allowing NEEDS_CLARIFICATION and UNSUPPORTED is what makes abstention
reachable at all; measuring whether the model takes it is the dataset's job.

**Every field is required, with sentinels for the unused ones.** Optional fields make a
small model choose twice — whether to emit the key and what to put in it — and it gets
the first question wrong often enough to matter. A flat object it must fill completely
turns that into one choice per field.
"""

from typing import Any, Final

from finguard_assistant.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
)

# The outcomes a model may choose. The other three are things that happen TO a model —
# malformed output, a rejected plan, a dead daemon — and it cannot report those about
# itself, so offering them would only invite a model to explain away its own mistakes.
CHOOSABLE_OUTCOMES: Final = (
    PlanningOutcome.PLANNED,
    PlanningOutcome.NEEDS_CLARIFICATION,
    PlanningOutcome.UNSUPPORTED,
)

# What `period` carries when the window is an explicit range instead of a named one.
ABSOLUTE: Final = "ABSOLUTE"

# What a category-less question looks like. Kept out of the category enum so the model
# picks from real categories only.
NO_CATEGORY: Final = "NONE"


def build_schema(categories: tuple[str, ...]) -> dict[str, Any]:
    """`categories` is the vocabulary that exists in the data.

    Passing it in — rather than hardcoding it — is what makes an invented category
    unreachable: a decoder constrained to an enum cannot emit COMPRAS_VARIAS because
    the token sequence is not available to it, not because a validator caught it after
    the fact.
    """
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": [o.value for o in CHOOSABLE_OUTCOMES]},
            "question": {"type": "string"},
            "period": {"type": "string", "enum": [p.value for p in Period] + [ABSOLUTE, ""]},
            "n": {"type": "integer"},
            "from": {"type": "string"},
            "to": {"type": "string"},
            "direction": {"type": "string", "enum": [d.value for d in Direction]},
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": [*categories, NO_CATEGORY]},
            },
            "groupBy": {"type": "string", "enum": [g.value for g in GroupBy]},
            "metric": {"type": "string", "enum": [m.value for m in Metric]},
        },
        "required": [
            "outcome",
            "question",
            "period",
            "n",
            "from",
            "to",
            "direction",
            "categories",
            "groupBy",
            "metric",
        ],
    }
