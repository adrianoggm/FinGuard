"""How a reader is asked: the precondition, the memo, and the one telemetry row.

`_recogniser` used to sit in `ask_financial_question` beside the readers it wraps. It is here now
because the readers are, and it is the only piece of this package every reader module imports.

**The table it gates with is installed rather than imported**, which is the same trick the
decorator has always used one level lower down: `_reading` looked `READERS` up as a module global
because the table is built *after* the readers it names. `readers/__init__` assigns it here once
the nineteen exist. Until it does, `READERS` is an empty table — and an empty table says yes to
every name, which is `Registry.worth_asking`'s documented fallback, so a reader imported and called
before the table is built behaves exactly as it did before any of this existed instead of raising.

**One authoritative name, deliberately.** The gate `_reading` applies and the table the handler
dispatches from are the same module attribute, so a test that swaps one has swapped both. Two
names — a copy in the handler and the real one here — is a test that passes because half of it
never ran.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from finguard_orchestrator.application.queries import reader_registry
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.reader_registry import Registry
from finguard_orchestrator.domain.services import (
    explicit_facts,
)
from finguard_orchestrator.observability import (
    Step,
    Verdict,
    consulting,
    record,
)

# The table, installed by `readers/__init__` once the nineteen it names exist. This is the one
# authoritative binding: `_reading` gates with it and the handler dispatches from it, so a test
# that swaps this attribute has swapped both halves at once. A second copy held in the handler
# would be a test that passes because half of it never ran.
#
# An *empty* table until then, not `None`. `Registry.worth_asking` documents its own fallback — a
# reader with no row is worth asking — so an empty one says yes to every name, and a reader called
# before the table is built behaves exactly as it did before this seam had a table at all. There is
# no branch here to be wrong about and no import-order failure to diagnose.
READERS: Registry = Registry(())


def _reading(reader: Callable[..., Any], query: AskFinancialQuestionQuery) -> Any:
    """What one reader answers about one turn, computed at most once.

    Split out of the decorator below so the two halves stay legible: that one is about the
    instrument, this one is about the work. Both the precondition and the answer are cached, on the
    query, because a reader consulted five times would otherwise check its precondition five times
    as well — one kind of waste traded for another.

    `READERS` is looked up rather than closed over: the table is built after the readers it names,
    which it has to be, and by the time any of them is called it exists. It is a *module attribute*
    of this file for the same reason it was one of the handler before A2 — assignment is how the
    table reaches the seam without the seam importing the package that builds it — and a lookup
    rather than a capture is what lets a test swap the whole table for one turn.
    """
    cache = reader_registry.memo_of(query)
    name = reader.__name__
    if name in cache:
        return cache[name]
    answer = reader(query) if READERS.worth_asking(name, query) else None
    cache[name] = answer
    return answer


def _recogniser[**P, T](reader: Callable[P, T]) -> Callable[P, T]:
    """Mark a deterministic reader, and record the turns it claims.

    **This is the whole `recogniser` seam: one `record` call, and not one line inside `_steps`.**
    The chain in there is twenty-odd `plan = _reader(query)` / `if plan is not None: … return`
    blocks, and a `record` in each would be twenty telemetry blocks in the file that already does
    too much — the trade this design refuses. A decorator puts the decision where the decision is:
    a reader returning something *is* the claim, and there is nothing else to work out.

    **There is no `agreed` verdict here, and that is deliberate rather than an omission.** A reader
    that returns `None` has not decided anything — it has declined to read, and recording twenty
    "not mine" decisions per turn would bury the one that matters. So every decision this seam
    records is `CLAIMED` with `changed=True`, which is exactly what `Step.RECOGNISER` says it
    means: the model never ran. The denominator is turns, and `Step.OUTCOME` records one of those
    per turn.

    **A turn can record two of these**, on one path: `_ratio_request` reads a percent-change
    follow-up by asking `_direct_period_comparison`, and both claim. Both are true — the ratio
    reader took the turn, on the strength of the comparison reader — and the alternative was
    leaving one of the two undecorated, which is an instrument with a hole in it rather than one
    with an honest extra row. The other three readers consulted from inside another
    (`_comparison_with_previous_period`, `_internal_movement_request`, and
    `_direct_period_comparison` under `_a_comparison_owns_it`) are all reached only after the chain
    has already asked them and been told `None`, so they cannot double.

    **The claim in the paragraph above is no longer a paragraph.** It is a claim about *claims* —
    those three are asked by the cascade first and consulted again only after they declined, so no
    turn records two `CLAIMED` decisions for them — and `test_no_reader_claims_one_turn_twice` now
    checks it over 1,341 sentences instead of over the four its author had in mind. It is also what
    makes the memo below free: a memoised repeat still records, so `decisions_total` only stays
    identical while no reader claims twice.

    **What the spans then made visible, and what has since been paid off.** The claim never said
    anything about *consultations*, and there were many more of those: `_direct_period_comparison`
    up to five times, once by the cascade and once inside each of
    `_comparison_with_a_named_window`, `_natural_period_aggregate_plan`,
    `_deterministic_breakdown_plan` and `_average_plan`, all four of which ask
    `_a_comparison_owns_it`, which is that reader under another name. Four of the five were
    measured on an ordinary spending question — twenty-two consultations for one turn.

    It is nineteen now, and the thing that removed the three is the *precondition*, not the memo:
    none of those three readers can claim a sentence with no natural period, no breakdown request
    and no average in it, so none of them runs and none of them asks. The memo is what stops the
    repeats that remain, on the sentences where those readers do run, from costing four passes over
    the same date grammar.

    **`consulting` is the second half, and it deliberately records what `record` refuses to.** A
    reader returning `None` still declined *in an order*, and the order is the question this whole
    change exists to answer: which nineteen functions did the reasoning fall through, and which
    called which. That is a tree, not a rate — so it goes to the trace and never to
    `decisions_total`, whose shape a live dashboard is built on and which this leaves untouched.

    The nesting is not programmed. `_ratio_request` becomes the parent of
    `_direct_period_comparison` because its frame was open when the inner one started, and nothing
    here names either function.

    **The registry is consulted from inside the frame, and that placement is the whole of it.**
    `READERS` holds each reader's precondition and the turn's memo, and this seam is the one place
    both are applied — so the cascade's call sites, the four readers that consult another, and
    anything `reader_registry` asks all get them without a line changing anywhere else.

    Inside the frame rather than around it, so the nineteen the cascade asks directly are still
    nineteen rows in the tree with a verdict each. A reader whose precondition does not hold is not
    run, and is recorded as having declined — which is what it would have done anyway, because a
    precondition is a *necessary* condition for claiming and never a sufficient one. That is the
    property `test_no_precondition_refuses_a_sentence_its_reader_would_claim` checks for all
    nineteen readers over every sentence there is, which is what makes it a fact rather than a hope.
    """

    @wraps(reader)
    def read(*args: P.args, **kwargs: P.kwargs) -> T:
        # One positional query and nothing else is every reader in the table. `_recogniser` is
        # generic, so anything else falls through uncached rather than hanging a memo off an object
        # nobody has established is a turn.
        asked = args[0] if len(args) == 1 and not kwargs else None
        with consulting(reader.__name__) as consulted:
            claimed = (
                _reading(reader, asked)
                if isinstance(asked, AskFinancialQuestionQuery)
                else reader(*args, **kwargs)
            )
            consulted.claimed = claimed is not None
        # Outside the frame on purpose, so the `CLAIMED` decision is recorded exactly where it was
        # recorded before this line existed. It would make no difference to the export — decision
        # spans are built afterwards from the sealed record and are all children of `turn` — but
        # "the counter path is untouched" is a property worth being able to read off the source.
        if claimed is not None:
            record(Step.RECOGNISER, Verdict.CLAIMED, changed=True, detail=reader.__name__)
        return claimed

    return read


def _stated(query: AskFinancialQuestionQuery) -> explicit_facts.ExplicitFacts:
    """What the sentence says outright, read once for every precondition that wants it.

    The readers each call `explicit_facts.read` again for themselves and this does not stop them —
    that duplication is Section B's and not this change's. What it stops is nineteen preconditions
    adding a twentieth reading apiece.
    """
    cache = reader_registry.memo_of(query)
    if "facts" not in cache:
        cache["facts"] = explicit_facts.read(query.sentence, today=query.today)
    facts: explicit_facts.ExplicitFacts = cache["facts"]
    return facts
