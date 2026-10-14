"""The Spanish words for money going out and coming in, as a table somebody can read.

`window_words` reads the two concepts here to tell a question about spending from one about
income. A phrasing is a regex fragment, verbatim, one alternative of a top-level `|`; `like` and
`unlike` are sentences a reviewer who does not read regex can check, and the test that reads this
file keeps them true.
"""

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Phrasing:
    """One way of saying something, and the sentences that show it said that way.

    `says` is the alternation branch exactly as the matcher receives it. `like` and `unlike` are
    real sentences, asserted by `tests/test_explicit_lexicon.py` — `unlike` is how an argument in
    prose becomes a check, and several of the comments below were exactly that before this file.
    """

    says: str
    like: tuple[str, ...] = ()
    unlike: tuple[str, ...] = ()
    why: str = ""


# Sentences reach every reader here folded — lowercased and stripped of accents by
# `spanish.folded`. Some phrasings below still carry an accented alternative, `qu[ée]` and `m[áa]s`
# among them, and those alternatives are dead: no folded sentence can contain the accent. They are
# left exactly as they were, because removing them is a change to make on purpose and measure, not
# a tidy-up to smuggle in beside a move. Enumerable now, which is the point.
PHRASINGS: Final[dict[str, tuple[Phrasing, ...]]] = {
    # `income` and `expense` matched the same words and nobody knew, which is the defect this table
    # is built to make visible.
    "income": (
        Phrasing(r"\bingres(?:ar|amos|o|os|e|ado|aron)\b"),
        Phrasing(r"\bcobr(?:ar|amos|e|ado|aron|os?)\b"),
        Phrasing(r"\brecibi(?:do)?\b"),
        Phrasing(r"\bincome\b"),
        Phrasing(r"\bnomina\b"),
        Phrasing(r"\bsueldo\b"),
        Phrasing(r"\bsalario\b", like=("¿Cuándo me entra el salario?",)),
        Phrasing(r"\bme\s+(?:ha\s+)?entrado\b"),
    ),
    # `expense` **vetoes** an income reading; `leaving` **asserts** a spending one. Two lists
    # because the two powers are not worth the same. A veto costs a `None`: the model keeps
    # whichever direction it chose and `plan_verifier` still gets to look at it. An assertion
    # overwrites a field the model stated, on every sentence that matches, and nothing downstream
    # re-examines it.
    #
    # These four are good enough to veto — they say the sentence is not purely about money
    # arriving — and deliberately not good enough to assert. They are topic words: «¿en qué
    # categoría gasto más?» is about spending without stating a direction to impose. And `\bgast` is
    # the exact trigger of `plan_verifier._spending_answered_as_everything`, so a reader that forced
    # EXPENSE on every `gast` sentence would repair that disagreement before the verifier could ever
    # see it — which would leave the one guard written against the live prompt injection unreachable
    # on the sentences it was written for.
    "expense": (
        Phrasing(
            r"\bgast",
            why="a topic word, and the exact trigger of the verifier's spending-answered-as-ALL "
            "refusal, so it may veto an income reading and may not assert a spending one",
        ),
        Phrasing(r"\bpagu"),
        Phrasing(r"\bcompr"),
        Phrasing(r"\bcargo"),
        Phrasing(r"\bme\s+cobr"),
    ),
}


def phrasings(concept: str) -> tuple[Phrasing, ...]:
    """Every way this table knows of saying `concept`."""
    return PHRASINGS[concept]


def alternation(concept: str) -> str:
    """The concept's phrasings as one alternation, for a caller that supplies its own frame.

    Returned as a string rather than interpolated into a template here, because a frame is
    the caller's to write and several of these phrasings contain `{0,35}`: a `str.format`
    hole would make a repetition count look like a placeholder. The call sites use f-strings, so
    the frame stays visible beside the reader that owns it.
    """
    return "|".join(phrasing.says for phrasing in phrasings(concept))


def compiled(concept: str) -> re.Pattern[str]:
    """The concept as a pattern in its own right, where the vocabulary is the whole of it."""
    return re.compile(alternation(concept))
