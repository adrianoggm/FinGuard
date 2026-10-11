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

from typing import Final

# What `period` carries when the window is an explicit range instead of a named one.
ABSOLUTE: Final = "ABSOLUTE"

# What a category-less question looks like. Kept out of the category enum so the model
# picks from real categories only.
NO_CATEGORY: Final = "NONE"
