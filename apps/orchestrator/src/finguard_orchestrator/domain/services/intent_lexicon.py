"""The Spanish `intent_router` knows, as a table somebody can read and a script can count.

`explicit_facts` went first and the argument is the same one: every defect fixed on 2026-08-29 was
one module knowing a phrase another did not, and none of them was hard to fix — they were hard to
*find*, because "what does this system know how to say?" had no answer short of reading regex and
imagining sentences.

It matters more here than it did there. The router sits upstream of everything: it decides whether
a turn is a financial question, a payment, a courtesy or out of domain, so a phrase it does not
know is a turn that never reaches the handler that could have answered it. Every story in the
comments below is that failure — a real figure about something nobody asked, or a refusal about
averages to somebody who said hello.

The line between what moved and what stayed is the one `explicit_lexicon` drew:

    vocabulary   an alternative in a top-level `|` — one way of saying a thing. Adding one is a
                 coverage decision, and a Spanish speaker is the right reviewer.
    structure    the frame each concept is read inside, the flags, and the order the router
                 consults them in. Adding one is a routing decision, and the reviewer is whoever
                 owns the cascade. All of it stayed in `intent_router`, beside the `route` whose
                 order is the design.

**A phrasing is a regex fragment, verbatim.** Storing a bare phrase and gluing the word boundaries
on at load moves sentences, because today's frames disagree about the glue: `moving` is framed
`\\b(…)\\b` and its rows carry no boundary of their own, while `about_the_balance` has no frame at
all and every row spells its own `\\b`. Normalising either way would move a sentence, and this
table's whole licence is that it moves none. `tests/test_intent_lexicon.py` asserts that all 23
patterns rebuild byte-for-byte from these rows.

**Where the router forced a deviation.** `explicit_lexicon` holds nothing but literals, and four
rows here cannot be literals: «cuánto» is spelled by a rule rather than by a list. The verb beside
it already carried three misspellings by hand — `tiengo`, `tieno`, `tngo` — and the question word
carried none, so "cunato tengo" was a balance question answered by the financial planner.
`spanish.transposed` fixed that by generating the transpositions, and a generated set is the exact
opposite of a table row: enumerating it here would repeat what the rule already teaches and would
go stale the moment the rule changed. So those four rows carry a `<how_much>` slot that
`intent_router` fills with the rule's output.

The slot is `<how_much>` and not `{how_much}` for a reason `explicit_lexicon` measured: a
`str.format` hole makes a repetition count look like a placeholder, and the ninth row of that very
concept contains `.{0,30}`. One slot, one filler, substituted by `str.replace`. It is deliberately
not a way for one row to reference another concept — that would be the rule engine the design
refuses, and the second thing anybody would have to build is a parser for it.

**Nothing stayed behind as a parser, and that is a real difference from `explicit_facts`.** Two
patterns stayed welded to that reader because a capture group was read and *which* alternative won
decided what got captured — a table whose rows must keep their order is a parser with a table's
manners. No pattern here reads a group. All 23 are consulted for truth only, and the 61 capture
groups they contain are `(` written where `(?:` was meant: inert, never inspected, and preserved
exactly as they are because removing them is a change to make on purpose and measure.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Phrasing:
    """One way of saying something, and the sentences that show it said that way.

    `says` is the alternation branch exactly as the router's frame receives it. `like` and `unlike`
    are real sentences, asserted by `tests/test_intent_lexicon.py` — `unlike` is how an argument in
    prose becomes a check, and most of the ones below were exactly that before this file.

    Deliberately its own five lines rather than an import of `explicit_lexicon.Phrasing`. The two
    are identical today and the duplication is real, but the alternative is a module the *intent*
    router depends on importing its type from a module about the *financial* reader downstream of
    it, and a layering inversion costs more than five lines. `scripts/lexicon_coverage.py` reads
    both tables through the two fields they share.
    """

    says: str
    like: tuple[str, ...] = ()
    unlike: tuple[str, ...] = ()
    why: str = ""


# Sentences reach the router folded — lowercased and stripped of accents by `spanish.folded`. Many
# phrasings below still carry an accented alternative, `qu[ée]` and `cu[áa]nto` among them, and
# those alternatives are dead: no folded sentence can contain the accent. They are left exactly as
# they were, because removing them is a change to make on purpose and measure, not a tidy-up to
# smuggle in beside a move. The same is true of the `re.IGNORECASE` on twenty of the frames.
# Enumerable now, which is the point.
PHRASINGS: Final[dict[str, tuple[Phrasing, ...]]] = {
    # What those same verbs mean when the sentence is asking rather than instructing. "¿Cuánto paso
    # a la hucha cada mes?" is a question about a habit and it carries a moving verb and an
    # account, so without this it read as an order to move money — and the answer to a question
    # would have been a preview asking somebody to confirm a payment they never asked for. That is
    # the worst shape a misreading can take here, which is why the exclusion is checked first
    # rather than tuned into the verb list.
    "asking": (
        Phrasing(r"cu[áa]nt[oa]s?", like=("¿Cuánto paso a la hucha cada mes?",)),
        Phrasing(r"qu[ée]", like=("¿Qué muevo a la hucha?",)),
        Phrasing(r"cu[áa]l", like=("¿Cuál es mi hucha?",)),
        Phrasing(r"cu[áa]ndo", like=("¿Cuándo paso dinero a la hucha?",)),
        Phrasing(r"c[óo]mo", like=("¿Cómo paso dinero a la hucha?",)),
        Phrasing(r"d[óo]nde", like=("¿Dónde guardo el dinero?",)),
        Phrasing(r"por\s+qu[ée]", like=("¿Por qué paso dinero a la hucha?",)),
    ),
}


def phrasings(concept: str) -> tuple[Phrasing, ...]:
    """Every way this table knows of saying `concept`."""
    return PHRASINGS[concept]


def alternation(concept: str, **slots: str) -> str:
    """The concept's phrasings as one alternation, for a caller that supplies its own frame.

    Returned as a string rather than compiled here, because the frame and the flags are
    `intent_router`'s to write: twenty of the twenty-three are read case-insensitively, three are
    not, and several are anchored or wrapped in a group whose absence would change what matches.
    The call sites use f-strings, so the frame stays visible beside the cascade that owns it.

    `slots` fills `<name>` holes — see the module docstring for why they are angle brackets and not
    `str.format`. A hole nobody fills and a filler that fits no hole are both mistakes, and both
    raise rather than silently producing a pattern that matches the wrong thing.
    """
    joined = "|".join(phrasing.says for phrasing in phrasings(concept))
    for name, value in slots.items():
        hole = f"<{name}>"
        if hole not in joined:
            raise KeyError(f"{concept} has no {hole} to fill")
        joined = joined.replace(hole, value)
    if "<" in joined and ">" in joined:
        unfilled = [phrasing.says for phrasing in phrasings(concept) if "<" in phrasing.says]
        if unfilled:
            raise KeyError(f"{concept} has an unfilled slot: {unfilled}")
    return joined
