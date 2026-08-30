"""The registry, measured against the cascade it replaces.

`reader_registry` turns nineteen readers tried in source order into a table with declared
preconditions and a derived order. The whole value of doing that alone, before anything moves, is
this file: **for every sentence we have, the reader the registry picks is the reader the cascade
picks.** Not asserted — run, over the five planning datasets and every query the 1,167-turn
conversation corpus actually hands a reader.

Three separate properties, because they fail for different reasons and a reader who sees one go red
should not have to work out which:

1. **The gate.** A precondition is a *necessary* condition. If one is too strong it silently steals
   a sentence from its reader, and `test_no_precondition_refuses_a_sentence_its_reader_would_claim`
   is what says so — for all nineteen readers on every sentence, not only for the winner.
2. **The order.** Given the same gate, does asking in the derived order pick what asking in source
   order picks? That is `test_the_registry_picks_what_the_cascade_picks`, and it is the one the
   migration turns on.
3. **The overlaps.** Two readers claiming one sentence is what makes any order load-bearing at all.
   `test_two_readers_claim_one_sentence_only_where_a_row_says_so` enumerates them, and the count is
   small enough to read: five pairs over 1,341 sentences.

**Both sides of every comparison get their own copy of the query.** The turn's memo lives on the
query object, so running the cascade and then the registry over one object would have the second
read what the first wrote — a test that passes because it never ran anything. `dataclasses.replace`
with no changes is a fresh object with a cold memo.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from support import episodes

from finguard_orchestrator.application.queries import ask_financial_question as handler
from finguard_orchestrator.application.queries.ask_financial_question import (
    READERS,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.reader_registry import (
    Precondition,
    Registry,
    free_pairs,
)
from finguard_orchestrator.domain.models.conversation import Conversation

# The planning harness's own reader for a dataset's `previous:` block, loaded the way
# `test_evaluation_scoring` already loads it. A second reader here would be a second opinion about
# what a case says, and a case this file misread would weaken the check without failing it.
_SPEC = importlib.util.spec_from_file_location(
    "evaluate_planning", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_planning.py"
)
assert _SPEC and _SPEC.loader
evaluate = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_planning"] = evaluate
_SPEC.loader.exec_module(evaluate)

PLANNING = Path(__file__).parents[1] / "evaluation" / "planning"

# The five the planning harness names, which is the definition that already exists — see
# `scripts/evaluate_planning.py --dataset`. `conversations.yaml` is the e2e set and reaches the
# readers through the corpus below rather than as bare sentences.
DATASETS = ("dev", "validation", "adversarial", "drift", "metamorphic")

# The order `_steps` asks them in, which until this change was the only thing that decided anything.
# A literal, because that is exactly the fact under test: deriving it from the table would be
# comparing the table with itself. `test_the_cascade_order_is_still_what_the_handler_does` is what
# keeps it honest when somebody moves a call.
CASCADE = (
    "_ratio_request",
    "_gambling_plan",
    "_how_many_payees_plan",
    "_internal_movement_request",
    "_salary_history_plans",
    "_period_comparison_plans",
    "_comparison_with_previous_period",
    "_direct_period_comparison",
    "_comparison_with_a_named_window",
    "_contextual_follow_up_plan",
    "_multiple_movement_ranking",
    "_group_ranking_plan",
    "_natural_period_aggregate_plan",
    "_deterministic_breakdown_plan",
    "_contextual_ranking_plan",
    "_cumulative_plan",
    "_recurrence_plan",
    "_frequency_plan",
    "_average_plan",
)

# The same nineteen readers with every precondition replaced by one that always holds — the cascade
# exactly as it ran before this table existed, nested consultations included. Swapping the module's
# `READERS` for this is how the baseline side of the comparison stays a real baseline instead of a
# second reading of the thing under test.
UNGATED = Registry(
    tuple(
        replace(row, when=Precondition("(ungated)", lambda query: True)) for row in READERS.readers
    )
)


def _from_datasets() -> list[tuple[str, AskFinancialQuestionQuery]]:
    found = []
    for name in DATASETS:
        data = yaml.safe_load((PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))
        today = data["reference"]
        if not isinstance(today, date):
            today = date.fromisoformat(str(today))
        for case in data["cases"]:
            spec = case.get("previous")
            found.append(
                (
                    f"{name}:{case['id']}",
                    AskFinancialQuestionQuery(
                        sentence=case["question"],
                        account_id=UUID(int=0),
                        today=today,
                        conversation=Conversation(
                            last_plan=evaluate.as_plan(spec) if spec else None
                        ),
                    ),
                )
            )
    return found


def _from_the_corpus() -> list[tuple[str, AskFinancialQuestionQuery]]:
    """Every distinct query the corpus hands a reader, taken from a real replay.

    Not the corpus's sentences — the *queries*. `_steps` rebuilds the query as a turn goes
    (a suspended payment, a settled scope, an abandoned ratio), so the object a reader at the end
    of the chain sees is not always the one the reader at the front saw. Capturing at the call
    sites is what makes the equivalence claim hold for every one of those variants rather than for
    a reconstruction of the first.
    """
    seen: list[AskFinancialQuestionQuery] = []
    original = {name: getattr(handler, name) for name in CASCADE}

    def spying(reader):
        def spy(query):
            seen.append(query)
            return reader(query)

        return spy

    for name in CASCADE:
        setattr(handler, name, spying(original[name]))
    try:
        episodes.run(episodes.load())
    finally:
        for name in CASCADE:
            setattr(handler, name, original[name])

    found, keys = [], set()
    for index, query in enumerate(seen):
        key = repr((query.sentence, query.today, query.conversation))
        if key not in keys:
            keys.add(key)
            found.append((f"corpus:{index}", query))
    return found


# One collection, read by every test below. The corpus replay is the slowest thing here and running
# it per test would make this file the slowest in the suite for no new information.
SENTENCES = _from_datasets() + _from_the_corpus()


def _cold(query: AskFinancialQuestionQuery) -> AskFinancialQuestionQuery:
    """The same question with no memo on it.

    Every comparison below needs its own copy: the memo lives on the query object, so two passes
    over one object would have the second read what the first wrote. Not a spare allocation to be
    tidied away — it is what stops a test passing because it never ran anything.
    """
    return replace(query)


def _walked() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], dict[str, bool]]:
    """One walk of every sentence, producing the three facts the tests below read.

    The same choice `test_conversation_corpus` makes about its own replay, for the same reason:
    these are facts about one walk of one set of sentences, and six tests each taking their own walk
    would make this file cost more than the rest of the suite put together.

    Returns, per sentence: who claims it ungated and in cascade order, who claims it through the
    registry in derived order, and which preconditions hold. Three query copies per sentence, never
    fewer — a shared one would let the ungated pass write its own `True` into the memo that the
    gated pass is about to read, and the gate would then be measuring itself.
    """
    cascade: dict[str, tuple[str, ...]] = {}
    registry: dict[str, tuple[str, ...]] = {}
    gates: dict[str, bool] = {}
    previously = handler.READERS
    try:
        for key, query in SENTENCES:
            handler.READERS = UNGATED
            ungated = _cold(query)
            cascade[key] = tuple(name for name in CASCADE if UNGATED.ask(name, ungated) is not None)
            handler.READERS = previously
            gate = _cold(query)
            for name in CASCADE:
                gates[f"{key}|{name}"] = READERS.worth_asking(name, gate)
            registry[key] = READERS.claimants(_cold(query))
    finally:
        handler.READERS = previously
    return cascade, registry, gates


CLAIMED_UNGATED, CLAIMED_THROUGH_REGISTRY, PRECONDITIONS = _walked()


def test_no_reader_claims_one_turn_twice() -> None:
    """**The decorator's prose, made a test.**

    `_recogniser`'s docstring argues in a paragraph that the three readers consulted from inside
    another — `_comparison_with_previous_period`, `_internal_movement_request`, and
    `_direct_period_comparison` under `_a_comparison_owns_it` — "are all reached only after the
    chain has already asked them and been told None, so they cannot double". That was the property
    this section was asked to turn from a paragraph into something checkable, and here it is over
    every sentence rather than over the four the author had in mind.

    **It is also exactly what makes the memo free.** A memoised repeat still records `CLAIMED`, so
    `decisions_total` only stays identical if no reader ever claims twice in one turn. If this ever
    fails, the memo is not the thing to fix: two claims from one reader was already two rows in the
    counter, and something has changed about the cascade.

    A turn recording two `CLAIMED` rows for *different* readers is expected and stays expected —
    `_ratio_request` claims on the strength of `_direct_period_comparison` and both are true. That
    is asserted in `test_a_real_callback_nests_without_anyone_programming_it`.
    """
    doubled = []
    claims: list[str] = []

    def claimed(*_args: object, detail: str | None = None, **_kwargs: object) -> None:
        if detail is not None:
            claims.append(detail)

    previously, real_record = handler.READERS, handler.record
    handler.READERS, handler.record = UNGATED, claimed
    try:
        for key, query in SENTENCES:
            claims.clear()
            turn = _cold(query)
            for name in CASCADE:
                if UNGATED.ask(name, turn) is not None:
                    break
            if len(claims) != len(set(claims)):
                doubled.append((key, query.sentence, list(claims)))
    finally:
        handler.record = real_record
        handler.READERS = previously

    assert not doubled, f"readers claiming one turn more than once: {doubled[:5]}"


def test_there_are_enough_sentences_for_this_to_mean_anything() -> None:
    """A floor, so a glob that matched nothing cannot pass by finding no work.

    The number is not the point and it will move as the corpus grows; a thousand is the point,
    because the property being checked is about Spanish and a dozen sentences do not check it.
    """
    assert len(SENTENCES) >= 1_000, f"only {len(SENTENCES)} sentences to check the registry against"


def test_every_reader_is_in_the_registry() -> None:
    """A `@_recogniser` with no row still works — `worth_asking` says yes to a name it does not
    know — and that is a deliberate fallback rather than a place to add the twentieth reader. This
    is what stops the fallback being how one arrives."""
    decorated = {
        name
        for name in dir(handler)
        if callable(getattr(handler, name, None))
        and getattr(getattr(handler, name), "__wrapped__", None) is not None
        and name.startswith("_")
        and name in CASCADE
    }

    assert set(READERS.order) == set(CASCADE), "the table and the cascade name different readers"
    assert decorated == set(CASCADE), "a decorated reader the cascade list does not know"


def test_the_cascade_order_is_still_what_the_handler_does() -> None:
    """`CASCADE` above is a literal and literals rot. The handler's source is the fact it copies,
    so read it: the readers appear in `_steps` in exactly that order, once each."""
    source = (Path(handler.__file__).read_text(encoding="utf-8")).split("async def _steps(")[1]
    body = source.split("\n    async def ")[0]
    appearing = []
    for line in body.splitlines():
        for name in CASCADE:
            if f"{name}(query)" in line and name not in appearing:
                appearing.append(name)

    assert tuple(appearing) == CASCADE, "the cascade moved and this file still says otherwise"


def test_the_derived_order_is_a_topological_sort_of_what_the_rows_declare() -> None:
    """The order is derived, and this is the derivation stated independently of the code that does
    it: every reader that yields to another is asked after it."""
    position = {name: index for index, name in enumerate(READERS.order)}

    for row in READERS.readers:
        for earlier in row.yields_to:
            assert position[earlier] < position[row.name], (
                f"{row.name} yields to {earlier} and is asked first"
            )


def test_no_precondition_refuses_a_sentence_its_reader_would_claim() -> None:
    """**The gate, proven necessary.** For every reader and every sentence: either the precondition
    holds, or the reader declines anyway.

    All nineteen on every sentence rather than only the one that wins, and that breadth is what
    makes the nested consultations safe too. `_ratio_request` asks `_direct_period_comparison`, and
    four readers ask it again through `_a_comparison_owns_it` — if its precondition were too
    strong, those callers would silently change their answers. The row for
    `_direct_period_comparison` itself is what rules that out, so the composition needs no separate
    argument.
    """
    stolen = [
        (key, name, READERS[name].when.name, query.sentence)
        for key, query in SENTENCES
        for name in CLAIMED_UNGATED[key]
        if not PRECONDITIONS[f"{key}|{name}"]
    ]

    assert not stolen, (
        f"{len(stolen)} of {len(SENTENCES)} sentences have a precondition that refuses a reader "
        f"which would have claimed them: {stolen[:5]}"
    )


def test_the_registry_picks_what_the_cascade_picks() -> None:
    """**The equivalence check. This is what Section A1 exists to produce.**

    Over every sentence in the five planning datasets and every query the conversation corpus hands
    a reader, the reader the registry selects is the reader the cascade selects.

    A disagreement here is not a number to be tuned green. It is either a precondition that is
    wrong — which the test above localises — or a latent ordering accident that nobody had written
    down, and those need different answers. Print it, do not silence it.

    The comparison is between the *first* claimant on each side, which is what each mechanism would
    act on: the cascade returns at the first reader that answers, and so does `Registry.select`.
    """
    disagreed = []
    for key, query in SENTENCES:
        cascade = CLAIMED_UNGATED[key][:1]
        registry = CLAIMED_THROUGH_REGISTRY[key][:1]
        if cascade != registry:
            disagreed.append((key, query.sentence, cascade, registry))

    assert not disagreed, (
        f"{len(disagreed)} of {len(SENTENCES)} sentences resolve to a different reader: "
        f"{disagreed[:10]}"
    )


def test_two_readers_claim_one_sentence_only_where_a_row_says_so() -> None:
    """**The property that was a paragraph.** Two readers claiming one sentence is the only thing
    that makes an order load-bearing, and until now it was discovered when a corpus turn went
    wrong.

    Every contested pair must be declared, in the direction the cascade already resolves it. The
    five that exist are in the table with the sentence that produces each. A sixth appearing here
    is not a failure of this test: it is a pair somebody has to decide about, and the message names
    it and the sentence.
    """
    declared = {(row.name, earlier) for row in READERS.readers for earlier in row.yields_to} | {
        (earlier, row.name) for row in READERS.readers for earlier in row.yields_to
    }
    contested: dict[tuple[str, str], str] = {}

    for key, query in SENTENCES:
        claimants = CLAIMED_THROUGH_REGISTRY[key]
        for index, left in enumerate(claimants):
            for right in claimants[index + 1 :]:
                if (left, right) not in declared:
                    contested.setdefault((left, right), query.sentence)

    assert not contested, (
        f"readers that both claim a sentence, with no row saying which wins: {contested}"
    )


def test_the_tiebreak_is_never_the_thing_that_decides() -> None:
    """**What makes `stood_at` honest.**

    Where no row declares a precedence, the order falls back to where the reader stood in the
    cascade — preservation, so an unseen Spanish sentence behaves exactly as it does today. That is
    only defensible while no such pair can both claim, because then the fallback provably cannot
    change an answer. This is that rule. If it fails, the fix is a row, not a new tiebreak.
    """
    undeclared = set(free_pairs(READERS.readers))
    deciding: dict[tuple[str, str], str] = {}

    for key, query in SENTENCES:
        claimants = CLAIMED_THROUGH_REGISTRY[key]
        for index, left in enumerate(claimants):
            for right in claimants[index + 1 :]:
                if (left, right) in undeclared or (right, left) in undeclared:
                    deciding.setdefault((left, right), query.sentence)

    assert not deciding, (
        f"where a reader stood is deciding {len(deciding)} contest(s) nothing declared: {deciding}"
    )


def test_the_general_reader_wins_and_that_is_deliberate() -> None:
    """**Why the order is not derived from specificity**, which was the obvious answer.

    Three of the five contested pairs are won by `_contextual_follow_up_plan`, whose precondition is
    the weakest in the table — "a query is in view" — against `_average_plan`, `_cumulative_plan`
    and `_deterministic_breakdown_plan`, each of which knows something specific about the sentence.
    Ordering by specificity would put all three ahead of it and flip twelve measured sentences.

    The rule that actually holds is about Spanish and not about set containment: on an elliptical
    turn, the reader that keeps the question in view knows more than the reader that would rebuild
    one. `_deterministic_breakdown_plan` says exactly that in its own comment about «desglósalo».
    This test is here so nobody re-derives the wrong order from the right instinct.
    """
    general = READERS["_contextual_follow_up_plan"]
    losers = {row.name for row in READERS.readers if "_contextual_follow_up_plan" in row.yields_to}

    assert losers == {"_average_plan", "_cumulative_plan", "_deterministic_breakdown_plan"}
    for name in losers:
        assert READERS[name].because, f"{name} yields to the general reader with no reason written"
    assert general.yields_to == (), "the general reader yields to nothing, which is the whole point"


ORDINARY = "¿cuánto gasté en los últimos 3 meses?"


def _consultations(registry: Registry, *, memoise: bool) -> tuple[int, int]:
    """How many times a reader is asked, and how many times `_direct_period_comparison` runs.

    Counted at the two places that mean different things. `consulting` is the seam, so spying on it
    counts *consultations* — the rows in the trace tree. The reader's own first line is
    `_DIRECT_PERIOD_COMPARISON.search`, so spying on that counts *executions* — the work.
    """
    asked: list[str] = []
    ran: list[str] = []
    real_consulting, real_memo = handler.consulting, handler.reader_registry.memo_of
    real_pattern, previously = handler._DIRECT_PERIOD_COMPARISON, handler.READERS

    class Counting:
        def search(self, *args, **kwargs):
            ran.append("run")
            return real_pattern.search(*args, **kwargs)

        def fullmatch(self, *args, **kwargs):
            return real_pattern.fullmatch(*args, **kwargs)

    handler.consulting = lambda name: (asked.append(name), real_consulting(name))[1]
    handler._DIRECT_PERIOD_COMPARISON = Counting()
    handler.READERS = registry
    if not memoise:
        handler.reader_registry.memo_of = lambda query: {}
    try:
        query = AskFinancialQuestionQuery(
            sentence=ORDINARY, account_id=UUID(int=0), today=date(2026, 7, 15)
        )
        for name in CASCADE:
            registry.ask(name, query)
    finally:
        handler.consulting = real_consulting
        handler.reader_registry.memo_of = real_memo
        handler._DIRECT_PERIOD_COMPARISON = real_pattern
        handler.READERS = previously
    return len(asked), len(ran)


def test_a_reader_is_asked_once_a_turn_instead_of_four_times() -> None:
    """**The measurement this change exists to move.**

    An ordinary spending question used to cost twenty-two consultations: the nineteen roots, plus
    `_direct_period_comparison` asked three more times from inside `_natural_period_aggregate_plan`,
    `_deterministic_breakdown_plan` and `_average_plan`, all of which ask `_a_comparison_owns_it`.
    Three of those four were pure waste.

    They go away because of the *precondition*, not the memo: none of those three readers can claim
    a sentence with no natural period, no breakdown request and no average in it, so none of them
    runs, so none of them asks. The memo is what stops the remaining repeats — on a sentence where
    those readers do run — from costing four passes over the same date grammar.
    """
    before_asked, before_ran = _consultations(UNGATED, memoise=False)
    after_asked, after_ran = _consultations(READERS, memoise=True)

    assert (before_asked, before_ran) == (22, 4), "the cascade, as it was"
    assert (after_asked, after_ran) == (19, 1), "the registry, asking each reader once"


def test_the_memo_is_what_stops_the_repeats_the_gate_cannot() -> None:
    """The two halves separated, so neither gets credit for the other.

    With every precondition holding — the cascade's own behaviour — the four consultations are all
    still made and the tree still shows them. What changes is that three of them cost nothing,
    because the answer was already worked out.
    """
    asked, ran = _consultations(UNGATED, memoise=True)

    assert asked == 22, "nothing is gated, so every consultation is still made"
    assert ran == 1, "and the reader behind them runs once"


@pytest.mark.parametrize("name", CASCADE)
def test_every_precondition_is_a_sentence_about_spanish(name: str) -> None:
    """A precondition's name is what a failure message says. `_gambling_precondition` tells the next
    reader nothing; "the sentence names a betting merchant" tells them where their turn went."""
    when = READERS[name].when

    assert when.name.islower() or " " in when.name, (
        f"{name}: {when.name!r} reads like an identifier"
    )
    assert len(when.name.split()) >= 4, f"{name}: {when.name!r} is too short to be an explanation"


def test_no_precondition_is_vacuous() -> None:
    """A precondition that always holds is a row that lies.

    Checked against the evidence rather than by reading the lambdas: over 1,341 sentences every one
    of the nineteen must refuse at least one. The general reader refuses every opening turn, which
    is the thinnest any of them gets and is still a fact.
    """
    always = [
        (name, READERS[name].when.name)
        for name in CASCADE
        if all(PRECONDITIONS[f"{key}|{name}"] for key, _query in SENTENCES)
    ]

    assert not always, f"preconditions that hold for every sentence we have: {always}"
