"""What a reworded follow-up still inherits from the turn in front of it.

The fourth layer measured this way, and the first one that is about a conversation rather than a
sentence. Dispatch, planning and the payment slots all score a turn in isolation; this one holds a
conversation still and moves only its last turn, because "¿y en gasolina?" means nothing alone and
narrows last month's spending in place.

Sixty follow-ups across eight conversations, written by a model that was shown the conversation and
told what the customer means next — never how any of it is read. The expected routes are copied out
of evaluation/conversations/, where each of these shapes is already asserted; only the wording here
is new.

Replayed through `episodes.walk`, so a variant is judged by the same harness as the corpus: the
whole conversation runs, and what is asserted is the tool the last turn reached. A follow-up that
reaches the planner and comes back asking which period is the failure this measures — it is the
shape of half the failures that were reported from the demo.
"""

from pathlib import Path

import yaml
from support import episodes

CORPUS = Path(__file__).resolve().parents[1] / "evaluation" / "conversations_drift.yaml"

# Where it stood when the file was written: 50 of 60. Raised whenever a change earns it, never
# lowered to make a change pass.
#
# The ten are worth reading before touching this number, because eight of them are one bug. Seven
# come from a single group: "¿de dónde sale eso?" is understood and "¿de dónde has sacado esa
# cifra?", "¿en qué te basas?", "¿qué has consultado?" and "¿cómo has llegado a esa cifra?" all
# fall through to the planner, which answers with a figure instead of explaining the last one. That
# is `_ABOUT_THE_ANSWER` matching word sequences, the same disease the dispatch corpus measured —
# and here it is measured through the whole stack, so it is what a person actually gets.
#
# The other three are typos landing on the one word that carries the meaning: "cuanto tngo",
# "cuanto tiengo en mi huch", and "son venti euros", which has no digit in it at all and so has no
# amount to read.
FLOOR = 50


def _variants() -> list[tuple[str, episodes.Episode]]:
    loaded = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    built: list[tuple[str, episodes.Episode]] = []
    for group in loaded["groups"]:
        before = tuple(
            episodes.Turn(say=turn["say"], route=turn["route"]) for turn in group["before"]
        )
        for said in group["says"]:
            last = episodes.Turn(say=said, route=group["route"])
            built.append(
                (
                    said,
                    episodes.Episode(name=f"drift/{group['means']}", why="", turns=(*before, last)),
                )
            )
    return built


VARIANTS = _variants()


def test_the_corpus_loaded() -> None:
    assert len(VARIANTS) == 60


def test_the_rate_only_goes_up(capsys) -> None:  # noqa: ANN001
    """Printed as well as asserted: the list of sentences is the useful output.

    Only the last turn's verdict is counted. The turns in front are identical in every variant and
    are already asserted by the corpus, so a failure among them would be a broken harness rather
    than a finding — and it would be counted sixty times.
    """
    failures = []
    for said, episode in VARIANTS:
        # `walk` numbers turns from one. Off by one, this scores the *first* turn of every episode
        # instead of the last, which is identical across all eight variants of a group — so the
        # rate stays plausible and stops measuring the thing the file is named after.
        last = len(episode.turns)
        broke = [found for found in episodes.walk(episode) if found.turn == last]
        if broke:
            failures.append((episode.name, said, broke[0].complaint))

    reached = len(VARIANTS) - len(failures)
    with capsys.disabled():
        print(f"\n{reached}/{len(VARIANTS)} reworded follow-ups inherit what they should")
        for name, said, complaint in failures:
            print(f"  «{said}»\n      {complaint}   ({name})")

    assert reached >= FLOOR, f"{reached}/{len(VARIANTS)} correct, below the floor of {FLOOR}"
