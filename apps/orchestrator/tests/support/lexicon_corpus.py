"""Every sentence this repository has written down, for asking a phrasing whether anything uses it.

All four evaluation trees, because the question "has this vocabulary ever been measured?" does not
care which harness measures it. Planning, conversations, dispatch paraphrases and payment drift
are all replayed by tests; omitting the latter two called vocabulary unwatched while a dedicated
robustness test was exercising it. `scripts/lexicon_coverage.py` reports the same number for a
human.

Loaded once and folded once. 1,800-odd sentences against the lexicons is cheap exactly once and not
cheap per test.
"""

import functools
import pathlib
import re

import yaml  # type: ignore[import-untyped]

from finguard_orchestrator.domain.services import spanish

ROOT = pathlib.Path(__file__).resolve().parents[2]
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


@functools.cache
def folded_sentences() -> tuple[str, ...]:
    """Every written-down sentence, folded the way every reader receives it."""
    found: list[str] = []
    for folder in EVALUATION_FOLDERS:
        for path in sorted((ROOT / folder).glob("*.yaml")):
            _harvest(yaml.safe_load(path.read_text(encoding="utf-8")), found)
    return tuple(spanish.folded(sentence) for sentence in dict.fromkeys(found))


@functools.cache
def exercises(says: str) -> bool:
    """Whether any sentence we have written down reaches this phrasing."""
    pattern = re.compile(says)
    return any(pattern.search(sentence) for sentence in folded_sentences())
