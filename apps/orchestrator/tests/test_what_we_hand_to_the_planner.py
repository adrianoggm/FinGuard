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

So the corpus reports what it handed over, and this file measures how much of that the planning
dataset exercises. The number is a ratchet: it may only go down, and it goes down either by writing
the missing cases or by a deterministic reader legitimately claiming the turn back.

**The obvious next layer is a judge.** 107 cases is more expected plans than anybody will hand-write
well, and most of these turns have no single right plan — "¿cuántas compras hice en Mercadona?"
needs a merchant filter this schema has, and which window it inherits is a judgement. A model
scoring the plan against the sentence and the conversation behind it is the shape that fits, and
this list is what it would score.
"""

import pathlib

import yaml
from support import episodes

from finguard_orchestrator.domain.services import spanish

CORPUS = episodes.load()
REPORT = episodes.run(CORPUS)
PLANNING = pathlib.Path(__file__).resolve().parents[1] / "evaluation" / "planning"


def _sentences(node: object) -> set[str]:
    """Every `say` in a planning dataset, however deeply the file nests its turns."""
    if isinstance(node, dict):
        found = set().union(*(_sentences(value) for value in node.values())) if node else set()
        said = node.get("say")
        return found | ({_folded(said)} if isinstance(said, str) else set())
    if isinstance(node, list):
        return set().union(*(_sentences(item) for item in node)) if node else set()
    return set()


def _folded(sentence: str) -> str:
    return spanish.folded(sentence).strip(" ¿?¡!.,;:")


MEASURED = _sentences(yaml.safe_load((PLANNING / "conversations.yaml").read_text("utf-8")))

# How many handed-over turns no planning case exercises. A ratchet, and it only moves one way:
# down by writing the case, or down by a deterministic reader claiming the turn back.
#
# 107 of 153 the day it was measured. Not a target to grind to zero by hand — the note in the
# docstring about a judge is the honest route for most of them — but a number that exists, where
# before there was an assumption that ceding a turn made things better.
UNMEASURED = 107


def test_the_turns_we_hand_over_are_measured_somewhere() -> None:
    unmeasured = sorted({say for say in REPORT.handed_over if _folded(say) not in MEASURED})

    assert len(unmeasured) <= UNMEASURED, "\n".join(
        [
            f"{len(unmeasured)} handed-over sentences no planning case exercises"
            f" (was {UNMEASURED}):",
            *unmeasured[:20],
        ]
    )


def test_the_corpus_reports_what_it_handed_over() -> None:
    """The measurement has to be able to be wrong. If nothing reached the planner the check above
    would pass by finding no work, which is how a floor lies."""
    assert len(REPORT.handed_over) > 100, "a corpus this size cedes turns; zero means a broken stub"
