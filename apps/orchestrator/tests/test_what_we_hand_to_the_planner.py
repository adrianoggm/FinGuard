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

**The obvious next layer is a judge.** 53 cases is more expected plans than anybody will hand-write
well, and most of these turns have no single right plan — "¿cuántas compras hice en Mercadona?"
needs a merchant filter this schema has, and which window it inherits is a judgement. A model
scoring the plan against the sentence and the conversation behind it is the shape that fits; the
anchor group is what says whether its scores can be believed.

**And counting the work found something worse than a gap.** The 53 are 34 turns with no thread
behind them and 19 that follow one, and the 34 are only 22 distinct sentences — but among those
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
from support.ratchets import Ceiling  # noqa: E402

CASES = cases()

# How many handed-over *sentences* no planning case exercises. A ratchet, and it only moves one
# way: down by writing the case, or down by a deterministic reader claiming the turn back.
#
# **It was 107, and 107 was the wrong unit.** That number counted turns; the assertion below counts
# distinct sentences, because the corpus repeats itself and writing an expectation is per sentence
# rather than per turn. On the day it was found the two were 104 and 69, so the ratchet was carrying
# thirty-five sentences of slack — it would have passed while half as many again appeared, which is
# the precise failure a ratchet exists to prevent. A literal only discriminates on the day it is
# written, and this one had never been level with what it compares against.
#
# Not a target to grind to zero by hand — the note above about a judge is the honest route for most
# of them — but a number that exists, where before there was an assumption that ceding a turn made
# things better.
UNMEASURED = Ceiling(38, "handed-over sentences no planning case exercises")


def test_the_turns_we_hand_over_are_measured_somewhere() -> None:
    unmeasured = sorted({case["say"] for case in CASES if case["group"] == "extension"})

    assert not UNMEASURED.exceeded_by(unmeasured), UNMEASURED.exceeded_by(unmeasured)


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
# this file. It was 53 of 153; it is 42 of 141, in 27 distinct sentences.
#
# A work-list rather than a defect count, and the split is the work. "¿Cuánto gasté?" is a real
# question missing a window and belongs with a model. "Déjalo", "sí", "1000", "20 euros", "a ver"
# and "pues no sé" are not questions at all, and what comes back for them is an answer to somebody
# who was cancelling, confirming or thinking out loud.
#
# Only ever down: by a reader claiming the sentence, or by a vocabulary learning the word.
#
# **And not to zero.** Two of the fifty-three were an order to move money and a payment already
# made, arriving on the reading path; both now have a reader. What is left of the half a vocabulary
# could claim is bare amounts, and those are unclaimed on purpose — see the test below.
MUTE = Ceiling(42, "turns handed over with nothing recognised in them")


def test_what_we_hand_over_knowing_nothing_about_it() -> None:
    """The other half of the same idea as the ratchet above, pointing the other way.

    That one counts turns nobody measures. This counts turns we cede having recognised nothing in
    them, which is a different failure: not an unmeasured model call but an unnecessary one, and
    the corpus cannot see it — its `unnecessary model calls` rate counts only turns an episode
    marked `no_model`, so a sentence nobody thought to mark is a call nobody counts.

    **Turns and not distinct sentences**, which is the distinction that made the ratchet above lie
    for as long as it existed. The two are 42 and 27 here, and the unit is declared where the
    number is so nobody has to read the assertion to find out which one it means.
    """
    mute = [case for case in CASES if not case["states_a_query"]]

    assert not MUTE.exceeded_by(mute), MUTE.exceeded_by(sorted({case["say"] for case in mute}))


def test_the_mute_turns_split_into_two_different_jobs() -> None:
    """13 of the 53 were a word this service already has, arriving where nothing consults it.

    Ten remain, and counting them is what said how big the job actually was. Two of the thirteen
    were defects: "mueve 500 € a algún sitio" is an order to move money that named no account, and
    "le he mandado 20 € a Marta Solis Vega" is a payment already made — both went to the planner as
    questions about a period. Both have a reader now.

    **The other ten are "1000", "20 euros", "mejor 1000", "500" and "1000 pavos", and they stay.**
    An amount arriving with nothing open is deliberately nobody's: `a_bare_amount_with_nothing_open`
    and `a_bare_amount_does_not_resume_a_suspended_bizum` are episodes written to keep a payment
    from completing itself with a number. Claiming them here would reopen exactly the defect those
    episodes exist to prevent, so this ratchet has a floor that is not zero — and knowing that is
    worth more than driving it down would have been.

    The check is `payment_words.amount_in`, and it used to be dirtier: "En los ulltimos 4 meses"
    landed here on the 4. That false positive is what found the defect — the reader answered about
    digits and not about money, behind a payment slot that gated it, and this was the first place
    it had been asked outside one.
    """
    mute = [case for case in CASES if not case["states_a_query"]]
    known = [case for case in mute if case["known_elsewhere"]]

    assert len(known) <= 10, f"{len(known)} of {len(mute)} mute turns a reader could claim"


def test_the_work_is_counted_in_sentences_rather_than_turns() -> None:
    """What it would cost to write the missing expectations, which is not what the ratchet says.

    The corpus repeats itself on purpose — "¿cuánto gasté el mes pasado en el supermercado?" opens
    seven different episodes — so 34 turns without a thread are 22 distinct sentences. Counting
    turns makes the job look three times bigger than it is, and a job that looks impossible is a
    job nobody starts.
    """
    fresh = {case["say"] for case in CASES if case["group"] == "extension" and not case["previous"]}

    assert len(fresh) <= 22, f"{len(fresh)} distinct sentences with no thread behind them"


def test_a_case_carries_the_conversation_behind_it() -> None:
    """A sentence on its own is a different question: "¿y el mes pasado?" judged without the query
    it follows is unanswerable, so a case that dropped the thread would measure nothing.

    Fifty of the hundred and forty-three arrive with a query in view, which is worth knowing before
    choosing how to evaluate them. The rest are the *first* financial question of a thread — the
    sentence determines the plan on its own, which is the shape `planning/dev` already scores well
    and the shape a person can write an expectation for. The judge earns its keep on the fifty,
    where what the turn inherits is a judgement rather than a fact.

    **A floor, and deliberately far below the count.** This number shrinks every time a reader
    claims a turn back — it was fifty-six and four fixes took it to fifty — so a floor kept level
    with it would fail for the work going *well*, which is the one thing a check must never do.
    What it guards is the generator dropping `previous` altogether, and that failure takes the
    number to zero rather than to forty-nine.
    """
    following = [case for case in CASES if case["previous"] is not None]

    assert len(following) >= 25, f"only {len(following)} cases carry the query they follow"
