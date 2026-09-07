"""The lexicon is only worth having if it can be asked questions, so these are the questions.

`explicit_facts` used to hold its Spanish as 29 compiled patterns. Every defect fixed on 2026-08-29
was one module knowing a phrase another did not, and none of them was hard to fix — they were hard
to *find*, because "what does this system know how to say?" had no answer short of reading regex and
imagining sentences.

So the tests here are not really tests of a data structure. Each one is a question that could not be
asked before:

    which phrasings has nothing ever measured?      `test_every_phrasing_is_measured_or_seeded`
    which vocabulary do two readers share?          `test_the_phrasings_two_concepts_share`
    do the examples in the table actually match?    `test_every_example_matches_its_phrasing`
    do the arguments in the comments hold?          `test_no_counter_example_matches_its_phrasing`

The last one is the one worth defending. `_COUNT` carried a paragraph explaining that the plural is
required on both sides, because three of the nouns it looks for are also verbs and "¿cuánto pago de
alquiler?" once came back "3 movimientos en alquiler". That paragraph was true and unchecked: a
later edit could have broken the claim and left the prose standing. It is now three `unlike`
sentences, and the argument fails the build if it stops being true.
"""

import re

import pytest
from support import lexicon_corpus

from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.services import (
    explicit_facts,
    explicit_lexicon,
    plan_verifier,
    spanish,
)

PHRASINGS = explicit_lexicon.PHRASINGS
EVERY_PHRASING = [
    (concept, phrasing) for concept, phrasings in PHRASINGS.items() for phrasing in phrasings
]


def _ids(concept: str, phrasing: explicit_lexicon.Phrasing) -> str:
    return f"{concept}:{phrasing.says}"


@pytest.mark.parametrize(
    ("concept", "phrasing"),
    EVERY_PHRASING,
    ids=[_ids(c, p) for c, p in EVERY_PHRASING],
)
def test_every_example_matches_its_phrasing(
    concept: str, phrasing: explicit_lexicon.Phrasing
) -> None:
    """`like` is what a reviewer who does not read regex reads, so it has to be true.

    Checked against the folded sentence, because that is what every reader here receives.
    """
    pattern = re.compile(phrasing.says)
    for sentence in phrasing.like:
        assert pattern.search(spanish.folded(sentence)), (
            f"{concept}: {sentence!r} is listed as a way of saying this and does not match"
        )


@pytest.mark.parametrize(
    ("concept", "phrasing"),
    [(c, p) for c, p in EVERY_PHRASING if p.unlike],
    ids=[_ids(c, p) for c, p in EVERY_PHRASING if p.unlike],
)
def test_no_counter_example_matches_its_phrasing(
    concept: str, phrasing: explicit_lexicon.Phrasing
) -> None:
    """An argument in a comment that nothing checks is a comment that can go stale silently.

    Every `unlike` here was a sentence some paragraph in `explicit_facts` claimed this phrasing
    does *not* read — the plural agreement in `count`, the one letter between «cuándo» and
    «cuánto», the determiners «El Corte Inglés» opens that `vague_tail` must not swallow.
    """
    pattern = re.compile(phrasing.says)
    for sentence in phrasing.unlike:
        assert not pattern.search(spanish.folded(sentence)), (
            f"{concept}: {sentence!r} is listed as *not* this phrasing and matches it"
        )


def test_every_phrasing_is_measured_or_seeded() -> None:
    """Every phrasing is either exercised by a sentence we wrote down, or carries its own example.

    This is the coverage figure a pile of regexes could not produce, and it is a gate rather than a
    report so that adding a phrasing means saying what sentence it is for. A phrasing that neither
    the datasets nor the corpus reaches and that names no example of itself is vocabulary nobody can
    review and nothing can break loudly.

    It is deliberately *not* a demand that everything be in a dataset. Most of this vocabulary is
    unmeasured — see `scripts/lexicon_coverage.py` for the split — and closing that is Section C's
    job, using exactly these seeds.
    """
    naked = [
        f"{concept}:{phrasing.says}"
        for concept, phrasing in EVERY_PHRASING
        if not phrasing.like and not lexicon_corpus.exercises(phrasing.says)
    ]
    assert not naked, "phrasings with neither a measured sentence nor an example: " + ", ".join(
        naked
    )


def test_the_lexicon_is_enumerable_and_nothing_in_it_is_unread() -> None:
    """Both directions, because either alone rots.

    A concept `explicit_facts` reads and the table does not define is a crash at import. A concept
    the table defines and nothing reads is vocabulary that has quietly stopped applying to any
    sentence — the same failure as a stale regex, wearing a table's respectability.
    """
    source = explicit_facts.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    # Either quoting: a concept read as a whole pattern is `compiled("x")`, and one whose frame the
    # reader supplies is interpolated as `alternation('x')` inside an f-string.
    unread = [
        concept
        for concept in PHRASINGS
        if f'"{concept}"' not in text and f"'{concept}'" not in text
    ]
    assert not unread, f"defined in the lexicon and read by nobody: {unread}"
    assert all(phrasings for phrasings in PHRASINGS.values()), "a concept with no phrasings"


def test_the_phrasings_two_concepts_share() -> None:
    """The defect shape this table exists to expose, pinned so a new instance has to be argued.

    Each of these is deliberate today:

    `recibos domiciliados` is both a recurrence phrase and a direct-debit phrase, and the reader
    separates them by whether an aggregate was also asked. «me cobr» is in both `expense` and
    `leaving`, which is the overlap `_direction` is written to resolve — the specific reading is
    consulted first, so «me cobraron» is money leaving. The four amount words are in both
    `asks_an_amount` and `aggregate_words`, and that one is not a design: they are two
    hand-maintained lists of the same idea, one a strict subset of the other, and neither knew about
    the other until this file put them side by side. It is left alone because merging them would
    move sentences, which this change may not do.
    """
    shared: dict[str, list[str]] = {}
    for concept, phrasings in PHRASINGS.items():
        for phrasing in phrasings:
            shared.setdefault(phrasing.says, []).append(concept)
    found = {says: tuple(sorted(cs)) for says, cs in shared.items() if len(cs) > 1}
    assert found == {
        r"\b(?:recibos?\s+domiciliados?|domiciliaciones?|adeudos?\s+domiciliados?)\b": (
            "direct_debit_query",
            "recurrence",
        ),
        r"\bme\s+cobr": ("expense", "leaving"),
        "cuanto": ("aggregate_words", "asks_an_amount"),
        "importe": ("aggregate_words", "asks_an_amount"),
        "suma": ("aggregate_words", "asks_an_amount"),
        "total": ("aggregate_words", "asks_an_amount"),
    }


def test_the_spending_veto_and_the_spending_assertion_stay_disjoint() -> None:
    """`explicit_facts`' docstring claims this, and it is checkable rather than hopeful.

    `plan_verifier._spending_answered_as_everything` triggers on the stem `gast`. If anything in
    `leaving` — the only vocabulary here that *asserts* EXPENSE — could match a `gast` sentence,
    this module would repair that disagreement before the verifier saw it, and the one guard written
    against the live prompt injection would be unreachable on the sentences it was written for.

    Disjoint by construction, says the comment. Which means: by the spelling, so a test can say it.
    """
    for phrasing in PHRASINGS["leaving"]:
        assert "gast" not in phrasing.says, (
            f"{phrasing.says!r} asserts EXPENSE and can reach a `gast` sentence, which is the "
            "verifier's refusal to make"
        )


def test_the_pin_never_repairs_a_division_the_verifier_would_report() -> None:
    """`divides_up` duplicates `plan_verifier._DIVIDES_UP`'s vocabulary, so pin the *outcome*.

    `_combined_total` is the one reading here that states a shape from an absence of instruction,
    and the one place that could go wrong quietly is a sentence which *does* ask how something
    divides
    up. `BREAKDOWN_ANSWERED_AS_ONE_GROUP` refuses a plan that groups by a dimension it filtered to
    one value, and its docstring names dropping the grouping as the repair it will not make: "a
    total is served to somebody who asked for a distribution … a figure that does not change is
    precisely how this class of defect reads as correct." A `GroupBy.NONE` reading is that repair.

    Written against the corpus rather than against the two spellings, because the failure mode here
    is the spellings drifting apart — which a test comparing them to each other would pass right up
    until one of them stopped matching a real sentence.
    """
    divisions = [
        sentence
        for sentence in lexicon_corpus.folded_sentences()
        if plan_verifier._DIVIDES_UP.search(sentence)
    ]
    assert divisions, "the corpus no longer exercises the verifier's division vocabulary"
    repaired = [
        sentence for sentence in divisions if explicit_facts.read(sentence).group_by is GroupBy.NONE
    ]
    assert not repaired, (
        "these ask how something divides up and the reading answers NONE, settling a "
        f"disagreement BREAKDOWN_ANSWERED_AS_ONE_GROUP exists to report: {repaired}"
    )


def test_charge_nouns_is_still_a_bare_alternation() -> None:
    """`readers/follow_up.py` interpolates this export into a frame of its own.

    So it must stay a list of alternatives with no anchor, no group and no boundary — anything else
    changes the meaning of a pattern in a module this change is not allowed to touch.
    """
    assert explicit_facts.CHARGE_NOUNS == explicit_lexicon.alternation("charge_nouns")
    assert not explicit_facts.CHARGE_NOUNS.startswith("^")
    assert "(" not in explicit_facts.CHARGE_NOUNS
    assert r"\b" not in explicit_facts.CHARGE_NOUNS
    re.compile(rf"^(?:y\s+)?cuant[oa]s?\s+(?:{explicit_facts.CHARGE_NOUNS})\b")


def test_the_lexicon_holds_no_frame_it_cannot_explain() -> None:
    """A phrasing is one alternative, so a top-level `|` in one means the table has lost a row.

    The whole licence for storing regex fragments is that each is *one* way of saying something,
    countable and reviewable on its own. A fragment carrying its own alternation is two rows
    pretending to be one, and it would be invisible to every count above.
    """
    for concept, phrasing in EVERY_PHRASING:
        depth = 0
        for index, char in enumerate(phrasing.says):
            if index and phrasing.says[index - 1] == "\\":
                continue
            depth += char == "("
            depth -= char == ")"
            assert not (char == "|" and depth == 0), (
                f"{concept}: {phrasing.says!r} holds a top-level alternation and is two phrasings"
            )


def test_the_plural_calendar_unit_is_read() -> None:
    """«por mes» already matched; «por meses» is the same phrasing, pluralised.

    Task 2 (Wave 4.2b): the reader's regroup patch depends on this — «ahora compáralo por meses»
    has to be seen as a monthly calendar unit the same way «por mes» already is.
    """
    pattern = explicit_lexicon.compiled("temporal_month")
    assert pattern.search(spanish.folded("gasto por meses")) is not None
    assert pattern.search(spanish.folded("gasto por mes")) is not None
