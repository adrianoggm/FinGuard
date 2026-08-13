"""How far a sentence can drift from the one that was tested before dispatch breaks.

Every other measurement in this repository scores *accuracy*: does this sentence reach the right
place. This one scores *robustness*: do eight ways of saying one thing reach the same place. They
are different numbers and the second is the one somebody feels — a person does not phrase their
question the way the dataset did, and a service that only works on the phrasings it was built
against is fragile in a way no accuracy figure reveals.

It runs against the deterministic router alone, so it is free and instant, and it measures exactly
the layer where the rigidity lives: a hundred regular expressions deciding which of ten places a
turn belongs to. A vocabulary that knows "gasolina" and not "el coche" scores perfectly on the
corpus and fails a person.

It now passes 95 of 96. The remaining sentence lost the only ownership markers from an internal
transfer, so deterministic routing deliberately leaves it to semantic interpretation instead of
turning an ambiguous phrase into an operation.

Nobody should read the original 37/96 as "the service answers 39 % of questions". This measures the
first deterministic decision, not the final hybrid reply. Raising it to 95/96 removes avoidable
model calls while retaining the semantic fallback for a phrase whose ownership signal was lost.

The floor moves up and never down, like the corpus gate. What it is not is a target: the honest use
of this file is to run it before and after a change to the interpretation layer, because a
restructure that does not move this number did not fix the thing it was for.
"""

from pathlib import Path

import yaml
from support.ratchets import Floor

from finguard_orchestrator.domain.services.intent_router import Intent, route

PARAPHRASES = Path(__file__).resolve().parents[1] / "evaluation" / "paraphrases"

# The sole remaining miss is intentionally ambiguous after its ownership words are lost.
FLOOR = Floor(95, "paraphrases that dispatch where their group does")


def _groups() -> list[dict[str, object]]:
    loaded: list[dict[str, object]] = []
    for path in sorted(PARAPHRASES.glob("*.yaml")):
        found = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        loaded.extend(found.get("groups", []))
    return loaded


GROUPS = _groups()
CASES = [
    (str(group["means"]), str(group["intent"]), said)
    for group in GROUPS
    for said in group["says"]  # type: ignore[union-attr]
]


def test_the_corpus_of_paraphrases_is_not_empty() -> None:
    """A file nobody loaded is a measurement nobody made."""
    assert len(CASES) > 60, "the paraphrase corpus did not load"


def test_the_rate_only_goes_up(capsys) -> None:  # noqa: ANN001
    """The number itself, so a change to the interpretation layer can be judged by it.

    `has_previous` and `at_rest` are both true, which is the ordinary middle of a conversation and
    the state most of these turns arrive in. A sentence whose reading legitimately depends on what
    came before belongs in the episode corpus, which replays state; this file is about the words.

    Printed as well as asserted, because the useful output is the list rather than the rate: each
    line names a phrasing the vocabulary has never met, and `-s` is how somebody working on this
    reads their progress. The floor is only there so the number cannot quietly regress while
    somebody is improving something else.
    """
    failures = [
        (means, said, route(said, has_previous=True, at_rest=True).value)
        for means, intent, said in CASES
        if route(said, has_previous=True, at_rest=True) is not Intent(intent)
    ]
    reached = len(CASES) - len(failures)
    with capsys.disabled():
        print(f"\n{reached}/{len(CASES)} paraphrases reach the same place as their group")
        for means, said, got in failures:
            print(f"  {got:<16} «{said}»   ({means})")

    assert not FLOOR.undercut_by(reached), FLOOR.undercut_by(reached)
