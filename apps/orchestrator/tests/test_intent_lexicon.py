"""The router's lexicon is only worth having if it can be asked questions. These are the questions.

`intent_router` used to hold its Spanish as 23 compiled patterns. The defects the comments in it
record are all one shape — a phrase the router did not know — and none of them was hard to fix. They
were hard to *find*, because "what does this system know how to say?" had no answer short of reading
regex and imagining sentences.

It matters more here than in `explicit_facts`. That reader runs after the router has decided what
kind of turn this is; the router decides it. A phrasing it does not know is not a worse answer, it
is an answer from the wrong path entirely — a balance where a list was asked for, a refusal about
averages to somebody asking what the assistant does.

So the tests here are questions that could not be asked before:

    does the move change any pattern?          `test_every_pattern_rebuilds_from_the_lexicon`
    what licenses storing them as data?        `test_no_pattern_reads_a_capture_group`
    which phrasings has nothing measured?      `test_every_phrasing_is_measured_or_seeded`
    which vocabulary do two concepts share?    `test_the_phrasings_two_concepts_share`
    do the examples in the table match?        `test_every_example_matches_its_phrasing`
    do the arguments in the comments hold?     `test_no_counter_example_matches_its_phrasing`

The first is the one worth defending. A lexicon whose whole licence is that it moves no sentence has
to be able to prove it, and comparing behaviour over a corpus proves only that the corpus did not
notice. Rebuilding each of the 23 patterns from its rows and comparing the pattern *string* is the
stronger claim: not "no measured sentence moved" but "no sentence can have moved, because the
regexes are the same bytes".
"""

import re

import pytest
from support import lexicon_corpus

from finguard_orchestrator.domain.services import intent_lexicon, intent_router, spanish

PHRASINGS = intent_lexicon.PHRASINGS
EVERY_PHRASING = [
    (concept, phrasing) for concept, phrasings in PHRASINGS.items() for phrasing in phrasings
]

# The one slot the table uses, filled the way `intent_router` fills it. A row carrying it is not a
# regex until it is filled, so every test that compiles a row has to fill it first.
HOW_MUCH = "|".join(spanish.transposed("cuanto"))
SLOT_FILLINGS = {intent_lexicon.HOW_MUCH_SLOT: HOW_MUCH}


def resolved(says: str) -> str:
    """One row's regex with its slot filled, for a test that compiles a row on its own."""
    for hole, filling in SLOT_FILLINGS.items():
        says = says.replace(hole, filling)
    return says


def alternation_of(concept: str) -> str:
    """The concept's rows joined, through the same call `intent_router` makes.

    Deliberately not `resolved(alternation(concept))`: `alternation` refuses to return a string with
    an unfilled slot in it, and going around that would test a path the router never takes.
    """
    if intent_lexicon.HOW_MUCH_SLOT in "".join(p.says for p in PHRASINGS[concept]):
        return intent_lexicon.alternation(concept, how_much=HOW_MUCH)
    return intent_lexicon.alternation(concept)


def _ids(concept: str, phrasing: intent_lexicon.Phrasing) -> str:
    return f"{concept}:{phrasing.says}"


# Every pattern in the module, and the frame it is assembled inside. Written out rather than derived
# so that the assertion below is a *comparison* and not a tautology: a frame that changed silently
# would rebuild itself and prove nothing.
FRAMES: dict[str, tuple[str, str, str, int]] = {
    "_ABOUT_THE_BALANCE": ("about_the_balance", "", "", re.IGNORECASE),
    "_ABOUT_ACCOUNTS": ("about_accounts", "", "", re.IGNORECASE),
    "_NOT_THE_BALANCE": ("not_the_balance", "", "", re.IGNORECASE),
    "_ABOUT_CARDS": ("about_cards", "", "", re.IGNORECASE),
    "_ABOUT_CARDS_BY_EACH": ("about_cards_by_each", "", "", re.IGNORECASE),
    "_ABOUT_CONTACTS": ("about_contacts", "", "", re.IGNORECASE),
    "_ABOUT_AGENDA": ("about_agenda", "", "", re.IGNORECASE),
    "_ABOUT_ITSELF": ("about_itself", r"\b(", ")", re.IGNORECASE),
    "_ASSISTANT_NOUN": ("assistant_noun", "", "", re.IGNORECASE),
    "_ASSISTANT_PURPOSE": ("assistant_purpose", "", "", re.IGNORECASE),
    "_CATEGORY_CATALOGUE": ("category_catalogue", "", "", 0),
    "_CORRECTING": ("correcting", r"\b(", r")\b", re.IGNORECASE),
    "_ANSWER_NOUN": ("answer_noun", "", "", re.IGNORECASE),
    "_ANSWER_REJECTION": ("answer_rejection", "", "", re.IGNORECASE),
    "_BARE_CORRECTION": ("bare_correction", r"(?:perdona[\s,]+)?(", ")", 0),
    "_ABOUT_THE_ANSWER": ("about_the_answer", r"\b(", r")\b", re.IGNORECASE),
    "_FRUSTRATION": ("frustration", r"^(?:pero\s+)?(?:", ")$", 0),
    "_MOVING": ("moving", r"\b(", r")\b", re.IGNORECASE),
    "_ASKING": ("asking", r"\b(", r")\b", re.IGNORECASE),
    "_BETWEEN_ACCOUNTS": ("between_accounts", "", "", re.IGNORECASE),
    "_OWN_ACCOUNT_RELATION": ("own_account_relation", "", "", re.IGNORECASE),
    "_CORRECTION_REPLACEMENT": ("correction_replacement", r"\b(?:", r")\b", re.IGNORECASE),
    "_ACTION_INTENT": ("action_intent", r"\b(?:", r")\b", re.IGNORECASE),
}


def compiled_patterns() -> dict[str, re.Pattern[str]]:
    return {
        name: value for name, value in vars(intent_router).items() if isinstance(value, re.Pattern)
    }


@pytest.mark.parametrize("name", sorted(FRAMES), ids=sorted(FRAMES))
def test_every_pattern_rebuilds_from_the_lexicon(name: str) -> None:
    """The frame plus the rows are the pattern, byte for byte, flags included.

    This is what makes the move checkable instead of trusted. `route` is a pure function of the
    sentence, so two patterns with the same bytes cannot read any sentence differently — no corpus
    and no model is needed to know that, and no corpus could establish it in the other direction:
    a set of sentences that happens not to notice a change is not evidence that nothing changed.

    It also pins the frames. A row that quietly acquired a `\\b`, or a concept whose group stopped
    capturing, would show up here as a difference rather than as a routing surprise a week later.
    """
    concept, prefix, suffix, flags = FRAMES[name]
    rebuilt = prefix + alternation_of(concept) + suffix
    pattern = compiled_patterns()[name]
    assert pattern.pattern == rebuilt, f"{name} is no longer the frame plus its rows"
    assert pattern.flags == re.compile(rebuilt, flags).flags, f"{name} changed flags"


def test_the_frames_account_for_every_pattern() -> None:
    """A pattern the table above does not know is a pattern nothing here checks.

    The parametrised test can only compare the patterns it is given, so this is the half that
    notices a twenty-fourth appearing — which is exactly how a vocabulary starts leaking back into
    code.
    """
    assert set(compiled_patterns()) == set(FRAMES)


def test_no_pattern_reads_a_capture_group() -> None:
    """The licence for storing these as data, and the reason none of them stayed behind.

    `explicit_facts` kept `_EXCLUSION_TAIL` in code because a group was read and *which* alternative
    won decided what got captured — a table whose rows must keep their order is a parser with a
    table's manners. Nothing here has that shape: all 23 patterns are consulted for truth only, so
    no alternative's extent is ever inspected and the row order carries no meaning.

    The 61 capture groups they do contain are `(` written where `(?:` was meant. Inert, and left
    exactly as they are — but "inert" is a claim about the reader, so it is checked here rather than
    asserted in a docstring. The day somebody reads a group, the order becomes load-bearing and
    that concept has to come back out of the table.
    """
    with open(intent_router.__file__, encoding="utf-8") as handle:
        source = handle.read()
    for reader in (".group(", ".groups(", ".groupdict(", "(?P<"):
        assert reader not in source, (
            f"{reader} makes an alternative's extent load-bearing, and the row order in "
            "`intent_lexicon` is not guaranteed to preserve it"
        )


@pytest.mark.parametrize(
    ("concept", "phrasing"),
    EVERY_PHRASING,
    ids=[_ids(c, p) for c, p in EVERY_PHRASING],
)
def test_every_example_matches_its_phrasing(
    concept: str, phrasing: intent_lexicon.Phrasing
) -> None:
    """`like` is what a reviewer who does not read regex reads, so it has to be true.

    Checked against the folded sentence, because that is what the router receives, and against the
    row alone rather than the whole pattern: the row is the unit somebody adds or removes.
    """
    pattern = re.compile(resolved(phrasing.says))
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
    concept: str, phrasing: intent_lexicon.Phrasing
) -> None:
    """An argument in a comment that nothing checks is a comment that can go stale silently.

    Every `unlike` here was a sentence some paragraph in `intent_router` claimed this phrasing does
    *not* read: the one letter between «cuánto» and «cuándo» that keeps a payday question off the
    balance path, the singular «tarjeta» that keeps a spending question off the card path, the
    «quiero saber» that turns an instruction back into a question, and the enumerated operations
    that stop «¿puedes hacer una consulta de julio?» being read as a question about the assistant.
    """
    pattern = re.compile(resolved(phrasing.says))
    for sentence in phrasing.unlike:
        assert not pattern.search(spanish.folded(sentence)), (
            f"{concept}: {sentence!r} is listed as *not* this phrasing and matches it"
        )


def test_every_phrasing_is_measured_or_seeded() -> None:
    """Every phrasing is either exercised by a sentence we wrote down, or carries its own example.

    A gate rather than a report, so that adding a phrasing means saying what sentence it is for. A
    phrasing that neither the datasets nor the corpus reaches and that names no example of itself is
    vocabulary nobody can review and nothing can break loudly.

    It is deliberately *not* a demand that everything be in a dataset. Eighty of the 168 rows here
    are unmeasured — `scripts/lexicon_coverage.py` prints the split, and 48 % is worse than
    `explicit_facts`' 35 % on a module that decides which path a turn takes at all. Closing that is
    Section C's job, using exactly these seeds.
    """
    naked = [
        f"{concept}:{phrasing.says}"
        for concept, phrasing in EVERY_PHRASING
        if not phrasing.like and not lexicon_corpus.exercises(resolved(phrasing.says))
    ]
    assert not naked, "phrasings with neither a measured sentence nor an example: " + ", ".join(
        naked
    )


def test_the_lexicon_is_enumerable_and_nothing_in_it_is_unread() -> None:
    """Both directions, because either alone rots.

    A concept `intent_router` reads and the table does not define is a crash at import. A concept
    the table defines and nothing reads is vocabulary that has quietly stopped applying to any
    sentence — the same failure as a stale regex, wearing a table's respectability.
    """
    with open(intent_router.__file__, encoding="utf-8") as handle:
        text = handle.read()
    unread = [
        concept
        for concept in PHRASINGS
        if f'"{concept}"' not in text and f"'{concept}'" not in text
    ]
    assert not unread, f"defined in the lexicon and read by nobody: {unread}"
    assert all(phrasings for phrasings in PHRASINGS.values()), "a concept with no phrasings"


def test_the_phrasings_two_concepts_share() -> None:
    """The defect shape this table exists to expose, pinned so a new instance has to be argued.

    All four are the correction family, and none of the four is a design. `correcting` and
    `bare_correction` are two hand-maintained lists of the same idea and neither knew about the
    other; `correction_replacement` is a third, and it overlaps both.

    **The sharpest instance is not in this dict at all, which is why it is written down here.**
    «me refería» is spelled three ways across the three concepts — `me\\s+refer[íi]a` in
    `correcting`, folded into `me\\s+(referia|refiero)` in `bare_correction`, and `me\\s+referia` in
    `correction_replacement` — so a string comparison sees three unrelated rows. Only the first
    carries the accented alternative, and no folded sentence can reach it. They are left exactly as
    they are, because merging them would move sentences and this table may not; the divergence is
    now countable rather than buried in three regexes a hundred lines apart.
    """
    shared: dict[str, list[str]] = {}
    for concept, phrasings in PHRASINGS.items():
        for phrasing in phrasings:
            shared.setdefault(phrasing.says, []).append(concept)
    found = {says: tuple(sorted(cs)) for says, cs in shared.items() if len(cs) > 1}
    assert found == {
        r"me\s+refiero": ("correcting", "correction_replacement"),
        r"no\s+me\s+has\s+entendido": ("bare_correction", "correcting"),
        r"queria\s+decir": ("bare_correction", "correction_replacement"),
        r"te\s+has\s+equivocado": ("bare_correction", "correcting"),
    }


def test_the_lexicon_holds_no_frame_it_cannot_explain() -> None:
    """A phrasing is one alternative, so a top-level `|` in one means the table has lost a row.

    The whole licence for storing regex fragments is that each is *one* way of saying something,
    countable and reviewable on its own. A fragment carrying its own alternation is two rows
    pretending to be one, and it would be invisible to every count above.

    Checked on the unresolved row, because the slot's filling is a generated alternation and is
    supposed to be one: four rows spell `(?:<how_much>)`, and the parentheses are theirs.
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


def test_a_slot_that_nobody_fills_is_an_error() -> None:
    """The one mechanism this table has beyond `explicit_lexicon`, so it fails loudly or not at all.

    An unfilled `<how_much>` would compile — it is a valid regex matching that literal text — and
    would then match nothing, silently, which is exactly the failure mode `tomllib` turning `\\b`
    into a backspace produced when the format question was first measured. So `alternation` refuses
    both halves of the mistake rather than producing a pattern that reads no sentence.
    """
    with pytest.raises(KeyError):
        intent_lexicon.alternation("about_the_balance")
    with pytest.raises(KeyError):
        intent_lexicon.alternation("moving", how_much="cuanto")
