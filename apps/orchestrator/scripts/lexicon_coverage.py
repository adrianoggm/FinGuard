"""What `explicit_facts` knows how to say, and which of it has ever been measured.

    uv run python scripts/lexicon_coverage.py
    uv run python scripts/lexicon_coverage.py --uncovered
    uv run python scripts/lexicon_coverage.py --concept count

Before the lexicon this question had no answer short of reading the regexes and imagining
sentences. The point of a table is that it can be counted, and the number that matters is the one
below: how many of the phrasings this module claims to understand are exercised by no sentence in
`evaluation/planning` or `evaluation/conversations`.

That is a coverage figure for the vocabulary, not for the code. A phrasing no measured sentence
reaches is not dead — users say it, which is why somebody wrote it down — it is *unwatched*: it can
break, and every test would stay green. Section C's generator reads the `like` seeds of exactly
these rows, because for them the corpus offers nothing to paraphrase.
"""

import argparse
import pathlib
import re
import sys

import yaml  # type: ignore[import-untyped]

from finguard_orchestrator.domain.services import explicit_lexicon, spanish

ROOT = pathlib.Path(__file__).resolve().parents[1]
SENTENCE_KEYS = {"question", "say", "sentence", "text", "utterance", "reply"}


def _harvest(node: object, into: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SENTENCE_KEYS and isinstance(value, str):
                into.append(value)
            else:
                _harvest(value, into)
    elif isinstance(node, list):
        for item in node:
            _harvest(item, into)


def measured_sentences() -> list[str]:
    """Every sentence this repository has written down, from both evaluation trees."""
    found: list[str] = []
    for folder in ("evaluation/planning", "evaluation/conversations"):
        for path in sorted((ROOT / folder).glob("*.yaml")):
            _harvest(yaml.safe_load(path.read_text(encoding="utf-8")), found)
    return sorted(dict.fromkeys(found))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--uncovered",
        action="store_true",
        help="list only the phrasings no measured sentence reaches",
    )
    parser.add_argument("--concept", default=None, help="report one concept rather than all")
    args = parser.parse_args()

    sentences = measured_sentences()
    folded = [spanish.folded(s) for s in sentences]

    concepts = (
        {args.concept: explicit_lexicon.PHRASINGS[args.concept]}
        if args.concept
        else explicit_lexicon.PHRASINGS
    )
    total = covered = seeded = naked = 0
    for concept, phrasings in concepts.items():
        rows = []
        for phrasing in phrasings:
            total += 1
            pattern = re.compile(phrasing.says)
            hits = [s for s, f in zip(sentences, folded, strict=True) if pattern.search(f)]
            if hits:
                covered += 1
            elif phrasing.like:
                seeded += 1
            else:
                naked += 1
            if args.uncovered and hits:
                continue
            mark = "corpus" if hits else ("seed  " if phrasing.like else "NAKED ")
            example = hits[0] if hits else (phrasing.like[0] if phrasing.like else "")
            rows.append(f"  {mark} {len(hits):4}  {phrasing.says}\n           {example}")
        if rows:
            print(f"\n{concept}")
            print("\n".join(rows))

    print(
        f"\n{total} phrasings: {covered} exercised by a measured sentence, "
        f"{seeded} unmeasured but seeded with an example, {naked} with neither."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
