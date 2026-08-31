"""What this service knows how to say, and which of it has ever been measured.

    uv run python scripts/lexicon_coverage.py
    uv run python scripts/lexicon_coverage.py --uncovered
    uv run python scripts/lexicon_coverage.py --lexicon intent
    uv run python scripts/lexicon_coverage.py --concept count

Before the lexicons this question had no answer short of reading the regexes and imagining
sentences. The point of a table is that it can be counted, and the number that matters is the one
below: how many of the phrasings these modules claim to understand are exercised by no sentence in
an evaluation corpus.

That is a coverage figure for the vocabulary, not for the code. A phrasing no measured sentence
reaches is not dead — users say it, which is why somebody wrote it down — it is *unwatched*: it can
break, and every test would stay green. Section C's generator reads the `like` seeds of exactly
these rows, because for them the corpus offers nothing to paraphrase.

**Two tables now, and the second one is worse.** `explicit_facts` reads the sentence *after* the
router has decided what kind of turn it is, so an unwatched phrasing there is one reading of a
question that still reaches a question-answering path. `intent_router` decides that in the first
place, so an unwatched phrasing there is a whole turn shape — a payment, a courtesy, a question
about the assistant — that has never been exercised end to end. The two are reported separately for
that reason and summed only at the end.

Read through the two fields the tables share, so neither has to import the other's `Phrasing`.
"""

import argparse
import pathlib
import re
import sys
from typing import Protocol

import yaml  # type: ignore[import-untyped]

from finguard_orchestrator.domain.services import explicit_lexicon, intent_lexicon, spanish


class Phrasing(Protocol):
    """The two fields a coverage count needs: what it matches, and what it is an example of."""

    @property
    def says(self) -> str: ...

    @property
    def like(self) -> tuple[str, ...]: ...


LEXICONS: dict[str, dict[str, tuple[Phrasing, ...]]] = {
    "explicit": dict(explicit_lexicon.PHRASINGS),
    "intent": dict(intent_lexicon.PHRASINGS),
}

# The one slot any table uses, filled here with the rule's own output so a row that carries it can
# be searched for like any other. `intent_router` does the same thing for real, and the hole is
# named by the lexicon rather than spelled again here — a coverage tool that guessed the token
# would silently count five rows as reached by nothing.
SLOTS = {intent_lexicon.HOW_MUCH_SLOT: "|".join(spanish.transposed("cuanto"))}

ROOT = pathlib.Path(__file__).resolve().parents[1]
SENTENCE_KEYS = {"question", "say", "says", "sentence", "text", "utterance", "reply"}
EVALUATION_FOLDERS = (
    "evaluation/planning",
    "evaluation/conversations",
    "evaluation/paraphrases",
    "evaluation/payments",
)


def _harvest(node: object, into: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SENTENCE_KEYS and isinstance(value, str):
                into.append(value)
            elif key in SENTENCE_KEYS and isinstance(value, list):
                into.extend(item for item in value if isinstance(item, str))
            else:
                _harvest(value, into)
    elif isinstance(node, list):
        for item in node:
            _harvest(item, into)


def measured_sentences() -> list[str]:
    """Every sentence this repository has written down in an executed evaluation corpus."""
    found: list[str] = []
    for folder in EVALUATION_FOLDERS:
        for path in sorted((ROOT / folder).glob("*.yaml")):
            _harvest(yaml.safe_load(path.read_text(encoding="utf-8")), found)
    return sorted(dict.fromkeys(found))


def resolved(says: str) -> str:
    """A row's regex with any slot filled, which is what the reader compiles."""
    for hole, filling in SLOTS.items():
        says = says.replace(hole, filling)
    return says


def report(
    name: str,
    concepts: dict[str, tuple[Phrasing, ...]],
    sentences: list[str],
    folded: list[str],
    *,
    uncovered_only: bool,
) -> tuple[int, int, int, int]:
    """One table's coverage, and the four counts that make up its headline."""
    total = covered = seeded = naked = 0
    printed: list[str] = []
    for concept, phrasings in concepts.items():
        rows = []
        for phrasing in phrasings:
            total += 1
            pattern = re.compile(resolved(phrasing.says))
            hits = [s for s, f in zip(sentences, folded, strict=True) if pattern.search(f)]
            if hits:
                covered += 1
            elif phrasing.like:
                seeded += 1
            else:
                naked += 1
            if uncovered_only and hits:
                continue
            mark = "corpus" if hits else ("seed  " if phrasing.like else "NAKED ")
            example = hits[0] if hits else (phrasing.like[0] if phrasing.like else "")
            rows.append(f"  {mark} {len(hits):4}  {phrasing.says}\n           {example}")
        if rows:
            printed.append(f"\n{concept}\n" + "\n".join(rows))

    if printed:
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", end="")
        print("".join(printed))
    unwatched = seeded + naked
    share = f"{100 * unwatched / total:.0f} %" if total else "n/a"
    print(
        f"\n{name}: {total} phrasings, {covered} exercised by a measured sentence, "
        f"{unwatched} reached by none ({share}) — {seeded} seeded with an example, "
        f"{naked} with neither."
    )
    return total, covered, seeded, naked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--uncovered",
        action="store_true",
        help="list only the phrasings no measured sentence reaches",
    )
    parser.add_argument("--concept", default=None, help="report one concept rather than all")
    parser.add_argument(
        "--lexicon",
        default=None,
        choices=sorted(LEXICONS),
        help="report one table rather than both",
    )
    args = parser.parse_args()

    sentences = measured_sentences()
    folded = [spanish.folded(s) for s in sentences]

    tables = {args.lexicon: LEXICONS[args.lexicon]} if args.lexicon else LEXICONS
    if args.concept:
        tables = {
            name: {args.concept: table[args.concept]}
            for name, table in tables.items()
            if args.concept in table
        }
        if not tables:
            parser.error(f"no lexicon defines {args.concept!r}")

    totals = [0, 0, 0, 0]
    for name, table in tables.items():
        counts = report(name, table, sentences, folded, uncovered_only=args.uncovered)
        totals = [running + one for running, one in zip(totals, counts, strict=True)]

    if len(tables) > 1:
        total, covered, seeded, naked = totals
        unwatched = seeded + naked
        print(
            f"\nboth tables: {total} phrasings, {covered} exercised by a measured sentence, "
            f"{unwatched} reached by none ({100 * unwatched / total:.0f} %)."
        )
    print(f"\nmeasured over {len(sentences)} sentences from all evaluation corpora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
