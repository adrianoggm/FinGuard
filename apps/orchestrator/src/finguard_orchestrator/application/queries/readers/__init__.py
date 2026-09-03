"""The nineteen deterministic readers, and the one table they are asked through.

Until A2 these were nineteen `@_recogniser` functions in a 7,975-line file, tried in the order
their call sites happened to sit in. A1 replaced *position* with a table: each reader declares a
precondition and its precedence edges, and the order is derived. This package is the second half —
the readers themselves, out of the file, in modules that can be read.

**What the modules are grouped by, and what that refuses.**

By *what the reader reads the sentence for*. Not by how big it is, not by where it stood, and — the
one that had to be argued down — not by how its claim is dispatched:

| module | what its readers read for | n |
| --- | --- | ---: |
| `subjects` | a closed vocabulary naming the subject outright | 4 |
| `comparisons` | a second operand, so the answer is two figures | 5 |
| `follow_up` | the question already on screen | 1 |
| `rankings` | an order rather than a total | 3 |
| `metrics` | the calculation, one per `Metric` | 4 |
| `breakdowns` | one window split by a dimension, or not | 2 |

Grouping by dispatch was the tempting axis, because the dispatch is the part that is irregular —
five readers open a clarification, four funnel into one comparison call, three have an answer method
of their own. It is the wrong axis. `_group_ranking_plan` and `_contextual_ranking_plan` dispatch
two different ways and read one vocabulary of superlatives and numbers; splitting them would put
that vocabulary across a module boundary. **The next defect in this system will be about Spanish,
not about a return type** — every one of the four fixed on 2026-08-29 was — so the modules are cut
where the Spanish is shared.

`follow_up` holds one reader, which needs its own defence since one module per reader is what this
layout avoids. A1 measured it: that reader is the *most general* in the table and wins three of the
five contested pairs against readers in three other modules. A thing that contends with three
families and beats them is not a member of any of them.

Below the six sit three modules that hold no readers: `seam` (how a reader is asked), `windows`
(what shape a resolved window is) and `dimensions` (the nouns a question can be grouped by). Each
exists because two or more of the six read it, and a helper copied into two of them is how two
readers come to disagree about one sentence.

**The table is assembled here and installed into `seam`**, which is where `_reading` gates with it.
One authoritative binding, so a test that swaps it has swapped both the gate and the dispatch.
"""

from __future__ import annotations

from typing import Final

from finguard_orchestrator.application.queries.reader_registry import Reader, Registry
from finguard_orchestrator.application.queries.readers import (
    breakdowns,
    comparisons,
    follow_up,
    metrics,
    rankings,
    seam,
    subjects,
)

# ----------------------------------------------------------------------------------------------
# The nineteen, as a table.
#
# A1 wrote this paragraph in the handler, where the table was built beside the readers it named. The
# readers are in modules now and the sentence that opened it — "`_steps` still asks the readers
# where it always did" — is the thing A2 removed: `_steps` asks this table. The rest of it is
# unchanged, because none of it was about where the table lived.
#
# `reader_registry` holds the machinery and the argument; the modules above hold the Spanish,
# because the vocabulary a precondition reads has to be the vocabulary its reader reads, and that
# lives with the reader.
#
# **Preconditions are necessary, never sufficient**, so every one of them is allowed to be lazier
# than the guard it stands in front of — and several are, deliberately. `internal_movement` claims
# on three shapes and its row asks only whether the sentence mentions an account, an internal
# transfer or an investment at all; restating the three shapes there would be a second definition of
# them, and a second definition is the defect this codebase keeps finding in its own Spanish. Being
# lazy costs a call that returns None. Being clever costs a sentence.
#
# **All nineteen have one, and none of them says True.** That was the thing to check, because a
# precondition that always holds is a row that lies. The closest is `contextual_follow_up`, whose
# precondition is about the *turn* rather than the sentence: it is the general reader, and the only
# fact that constrains it is that some query is in view. That still refuses every opening turn, and
# it is exactly why it contends with three of the others.
#
# Concatenated in the order the modules are listed, which decides nothing: `Registry` derives the
# order from `yields_to` and `stood_at`, and `test_the_derived_order_is_a_topological_sort_of_what
# _the_rows_declare` is what says so. Listing them in the order their readers stood makes the
# derived order easy to read off against `CASCADE`, and that is the only reason for this order.
ROWS: Final[tuple[Reader, ...]] = (
    *subjects.ROWS,
    *comparisons.ROWS,
    *follow_up.ROWS,
    *rankings.ROWS,
    *breakdowns.ROWS,
    *metrics.ROWS,
)

READERS: Final = Registry(ROWS)

# Installed rather than imported, because the table names the readers and the readers need the
# seam: `seam` cannot import this module without importing itself back. This is the same shape the
# decorator has always had — `_reading` looked `READERS` up as a module global for exactly this
# reason — moved one level out now that the readers are in modules.
#
# Assigned to the module attribute and not passed as an argument, so that every existing call site
# keeps the gate and the memo without a line changing: the four readers that consult another one,
# `Registry.ask`, and the handler's five runs all reach the same binding.
seam.READERS = READERS

__all__ = ["READERS", "ROWS"]
