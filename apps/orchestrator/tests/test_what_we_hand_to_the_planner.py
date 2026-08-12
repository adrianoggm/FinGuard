"""What this service declines to read, and whether anything measures the model that gets it.

Two datasets exist and neither covers the join between them. `evaluation/conversations/` runs whole
conversations with the planner stubbed, so `QUERY` means "it reached a model" and nothing about what
came back. `evaluation/planning/` scores a model on sentences somebody chose. Both are right on
their own, and between them is the question nobody asks: **of the turns we hand over, how many does
anybody check?**

It is the question this phase made load-bearing. Every fix that stopped a vocabulary guessing —
a bare word that is only a shop if the ledger has one, a name in a filter slot, a whole question
that is neither an ellipsis nor the answer to an open clarification, four ties where a general
reader stood aside for a specific one — moved a turn from "we answer it" to "we hand it over".
Ceding a turn is only an improvement if whoever receives it does better, and that has been an
assumption throughout.

The cases come from `scripts/handover_cases.py`, which is what a judge will read too. One
definition of "a turn we handed over" rather than one here and one there.

**The obvious next layer is a judge.** 107 cases is more expected plans than anybody will hand-write
well, and most of these turns have no single right plan — "¿cuántas compras hice en Mercadona?"
needs a merchant filter this schema has, and which window it inherits is a judgement. A model
scoring the plan against the sentence and the conversation behind it is the shape that fits; the
anchor group is what says whether its scores can be believed.

**And counting the work found something worse than a gap.** The 107 are 67 turns with no thread
behind them and 40 that follow one, and the 67 are only 42 distinct sentences — but among those
distinct sentences are "sí", "20 euros", "déjalo", "a ver", "no es eso", "no me has entendido" and
"1000 pavos". Those state no query at all. We are asking a model to plan them, and a model asked to
plan "déjalo" will return something, which becomes an answer to somebody who was cancelling.

That is not a hole in the planning dataset, it is a routing one, and the corpus cannot see it: its
`unnecessary model calls` rate counts only the turns an episode marked `no_model`, so a sentence
nobody thought to mark is a model call nobody counts. Writing an expected plan for "déjalo" would
be writing down that this is fine. The set has to be split first — sentences that state a query,
which can have an expectation, and sentences that do not, which are a question about who should
have answered them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from handover_cases import cases  # noqa: E402

CASES = cases()

# How many handed-over turns no planning case exercises. A ratchet, and it only moves one way:
# down by writing the case, or down by a deterministic reader claiming the turn back.
#
# 107 of 153 the day it was measured. Not a target to grind to zero by hand — the note above about
# a judge is the honest route for most of them — but a number that exists, where before there was
# an assumption that ceding a turn made things better.
UNMEASURED = 107


def test_the_turns_we_hand_over_are_measured_somewhere() -> None:
    unmeasured = sorted({case["say"] for case in CASES if case["group"] == "extension"})

    assert len(unmeasured) <= UNMEASURED, "\n".join(
        [f"{len(unmeasured)} handed-over sentences no planning case exercises:", *unmeasured[:20]]
    )


def test_the_anchor_is_big_enough_to_validate_a_judge() -> None:
    """The measurement has to be able to be wrong twice over.

    If nothing reached the planner the ratchet above would pass by finding no work, which is how a
    floor lies. And a judge validated against a handful of cases is a judge nobody should believe,
    so the group that validates it is measured too rather than assumed to be there.
    """
    anchor = [case for case in CASES if case["group"] == "anchor"]

    assert len(CASES) > 100, "a corpus this size cedes turns; near zero means a broken stub"
    assert len(anchor) >= 40, f"only {len(anchor)} handed-over turns have a written expectation"


# Turns we hand over knowing nothing at all about them: no window, no direction, no metric, no
# grouping, no category, no account — asked of the service's own readers, not of a list written for
# this file. 53 of 153, in 36 distinct sentences.
#
# A work-list rather than a defect count, and the split is the work. "¿Cuánto gasté?" is a real
# question missing a window and belongs with a model. "Déjalo", "sí", "1000", "20 euros", "a ver"
# and "pues no sé" are not questions at all, and what comes back for them is an answer to somebody
# who was cancelling, confirming or thinking out loud.
#
# Only ever down: by a reader claiming the sentence, or by a vocabulary learning the word.
MUTE = 53


def test_what_we_hand_over_knowing_nothing_about_it() -> None:
    """The other half of the same idea as the ratchet above, pointing the other way.

    That one counts turns nobody measures. This counts turns we cede having recognised nothing in
    them, which is a different failure: not an unmeasured model call but an unnecessary one, and
    the corpus cannot see it — its `unnecessary model calls` rate counts only turns an episode
    marked `no_model`, so a sentence nobody thought to mark is a call nobody counts.
    """
    mute = sorted({case["say"] for case in CASES if not case["states_a_query"]})

    assert len([case for case in CASES if not case["states_a_query"]]) <= MUTE, "\n".join(
        [f"{len(mute)} distinct sentences handed over with nothing recognised:", *mute[:20]]
    )


def test_the_mute_turns_split_into_two_different_jobs() -> None:
    """13 of the 53 are a word this service already has, arriving where nothing consults it.

    Amounts — "1000", "20 euros", "mejor 1000", "500" — plus "mueve 500 € a algún sitio" and "le
    he mandado 20 € a Marta Solis Vega". Those want a reader to claim them, and the work is a
    precondition. The other 40 are real questions missing a window, and the work there is an
    expectation somebody writes.

    Two jobs, and the smaller one is worth more per turn: a reader claiming "1000" removes a model
    call, its latency and an invented answer, while an expectation only records what should have
    happened.

    The check is `payment_words.amount_in`, and it is not clean: "En los ulltimos 4 meses" lands
    here on the 4. Harmless in this measurement and worth writing down rather than filtering away,
    because it says that reader answers about digits and not about money — behind a payment slot
    that is gated, and this is the first place it has been asked outside one.
    """
    mute = [case for case in CASES if not case["states_a_query"]]
    known = [case for case in mute if case["known_elsewhere"]]

    assert 5 <= len(known) <= 20, f"{len(known)} of {len(mute)} mute turns a reader could claim"


def test_the_work_is_counted_in_sentences_rather_than_turns() -> None:
    """What it would cost to write the missing expectations, which is not what the ratchet says.

    The corpus repeats itself on purpose — "¿cuánto gasté el mes pasado en el supermercado?" opens
    seven different episodes — so 67 turns without a thread are 42 distinct sentences. Counting
    turns makes the job look three times bigger than it is, and a job that looks impossible is a
    job nobody starts.
    """
    fresh = {case["say"] for case in CASES if case["group"] == "extension" and not case["previous"]}

    assert len(fresh) <= 45, f"{len(fresh)} distinct sentences with no thread behind them"


def test_a_case_carries_the_conversation_behind_it() -> None:
    """A sentence on its own is a different question: "¿y el mes pasado?" judged without the query
    it follows is unanswerable, so a case that dropped the thread would measure nothing.

    Fifty-six of the hundred and fifty-three arrive with a query in view, which is worth knowing
    before choosing how to evaluate them. The other ninety-seven are the *first* financial question
    of a thread — the sentence determines the plan on its own, which is the shape `planning/dev`
    already scores well and the shape a person can write an expectation for. The judge earns its
    keep on the fifty-six, where what the turn inherits is a judgement rather than a fact.
    """
    following = [case for case in CASES if case["previous"] is not None]

    assert len(following) >= 50, f"only {len(following)} cases carry the query they follow"
