"""The turns this service declines to read, as evaluation cases.

    uv run python scripts/handover_cases.py            # the split, as a report
    uv run python scripts/handover_cases.py --write    # evaluation/planning/handover.yaml

Every case here is a sentence the deterministic path handed to a planner, taken from the routing
corpus rather than chosen by anybody. That is the point: what a vocabulary declines to answer is
exactly what the model has to be good at, and no dataset was built from that set — `planning/`
scores sentences somebody picked, `conversations/` stubs the planner and measures the routing.

**Derived, never hand-maintained.** Every fix that stops a vocabulary guessing moves a turn into
this file, so a static copy would be wrong the day after it was written. Regenerate it; do not edit
it.

Two groups come out, and the split is the whole design:

    anchor      the sentence already has a hand-written expectation in planning/conversations.yaml
                or planning/dev.yaml (D-H-8: both are hand-written expectations, just two file
                shapes -- a conversation's own turn versus a bare scored case)
    extension   nothing measures this turn against a model at all

The anchor is what validates a judge. A model scoring plans is an opinion until it agrees with
expectations a person wrote, so the judge runs on the anchor first and its disagreements are read
one at a time — either the expectation is wrong or the judge is. Only if that holds does its score
on the extension mean anything.

The corpus reader lives in `tests/support`, which is where the conversations are read from and
where their stubs are. Importing it here is deliberate: a second reader would be a second
definition of what a turn is.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

# The same credential the test suite configures for itself, and for the same reason: the service
# refuses to start with nobody configured, and that guard is worth keeping rather than softening
# for a script. `setdefault`, so a caller with its own token keeps it.
os.environ.setdefault(
    "ORCHESTRATOR_DEV_TOKENS",
    '["assistant-test-token-0001:ES-TEST:11111111-1111-1111-1111-111111111111"]',
)

from support import episodes  # noqa: E402

from finguard_orchestrator.domain.services import (  # noqa: E402
    account_words,
    category_words,
    courtesy,
    explicit_facts,
    payment_words,
    query_recovery,
    spanish,
)

PLANNING = ROOT / "evaluation" / "planning"
WRITTEN = PLANNING / "conversations.yaml"
# D-H-8 (fix wave): `evaluation/planning/dev.yaml` -- the harness's own baseline dataset,
# "THE PROMPT IS TUNED AGAINST THIS FILE" per its own header -- writes a plan expectation the
# same way `conversations.yaml` does: a hand-written case, `evaluate_planning.py --dataset dev`
# scores a model against it, and a sentence in it is measured in every sense the "anchor" group
# below claims. It went unread here from the day this script was written, not by a decision: the
# two files even use different keys for the sentence itself (`say` in a conversation turn,
# `question` in a bare planning case), and only the first was ever taught. A sentence with only a
# `dev.yaml` case still read as `extension` (unmeasured) -- the wrong half of the split for
# exactly the case the split exists to name correctly.
DEV = PLANNING / "dev.yaml"
DESTINATION = PLANNING / "handover.yaml"


def _folded(sentence: str) -> str:
    return spanish.folded(sentence).strip(" ¿?¡!.,;:")


def _sentences(node: object) -> set[str]:
    """Every `say`/`question` in a planning dataset, however deeply the file nests its turns.

    Two keys, not one, since D-H-8: `conversations.yaml`'s turns are `say`, `dev.yaml`'s bare
    cases are `question` -- the same sentence-in-a-dataset fact, spelled two ways by two file
    shapes this reader was always meant to cover.
    """
    if isinstance(node, dict):
        found = set().union(*(_sentences(value) for value in node.values())) if node else set()
        for key in ("say", "question"):
            value = node.get(key)
            if isinstance(value, str):
                found = found | {_folded(value)}
        return found
    if isinstance(node, list):
        return set().union(*(_sentences(item) for item in node)) if node else set()
    return set()


def _measured_sentences() -> set[str]:
    """Every sentence a hand-written expectation exists for, in either dataset."""
    measured = _sentences(yaml.safe_load(WRITTEN.read_text(encoding="utf-8")))
    measured |= _sentences(yaml.safe_load(DEV.read_text(encoding="utf-8")))
    return measured


def _states_a_query(sentence: str, today: Any) -> bool:
    """Whether any vocabulary here recognises something to build a query out of.

    Asked of the service's own readers rather than of a list written for this file: a window, a
    direction, a metric, a grouping, a category, an account. Nothing new to maintain, and no
    opinion about what a question is beyond what the service already holds.

    False is the interesting answer. "Déjalo", "sí", "20 euros", "a ver" and "1000 pavos" all
    reach a planner, and about every one of them this service knows *nothing* — so what it hands
    over is a sentence and a shrug, and whatever comes back is an answer to somebody who was
    cancelling, confirming or thinking out loud. An expected plan cannot be written for those, and
    writing one would record that it is fine.
    """
    facts = explicit_facts.read(sentence, today=today)
    return bool(
        facts.window
        or facts.direction
        or facts.metric
        or facts.group_by
        or category_words.named_in(sentence)
        or account_words.named_in(sentence)
    )


def _another_vocabulary_has_a_word_for_it(sentence: str) -> bool:
    """Whether some reader here already knows this kind of turn, without owning this one.

    The half of the mute set that is a routing gap rather than a planning one. "Déjalo" is a
    control `query_recovery` reads, "20 euros" is an amount `payment_words` reads, "gracias" is
    courtesy — each is a word this service has, arriving at a moment where nothing consults it, so
    the turn reaches a model that has nothing to plan.

    Separated from the rest because the work is different. These want a reader to claim them; the
    others are real questions missing a window, and those want an expectation written.
    """
    return bool(
        payment_words.amount_in(sentence) is not None
        or query_recovery.retry_action(sentence) is not None
        or courtesy.is_courtesy(sentence, at_rest=True)
    )


def cases() -> list[dict[str, Any]]:
    """One case per turn the corpus handed to the planner, oldest conversation first."""
    measured = _measured_sentences()
    found: list[dict[str, Any]] = []
    for episode in episodes.load():
        # Through the same stack and from the same opening state the corpus replays in. A second
        # reader here would be a second definition of what a turn is, and the account scope is
        # part of it: replaying from an empty conversation would hand the planner every first
        # spending question twice over, once as itself and once as the account clarification.
        stack = episodes.a_stack_for(episode)
        conversation = episodes.opening(episode)
        for number, turn in enumerate(episode.turns, start=1):
            asked = len(stack.planner.asked)
            # The query in view when the turn arrived, which is the field the handler passes to the
            # planner as `previous`. Read from the conversation rather than from the ledger: what
            # was last *executed* and what the thread is on are not the same thing after a balance
            # or a refusal, and it is the thread the model is given.
            before = (conversation or {}).get("last_plan")
            # The same override `episodes.replay()` sets and clears around every turn: a turn whose
            # own routing reads the chart the ledger just drew -- a drill-down resolving «agosto»
            # against a month line's own labels, not merely its sentence -- would otherwise see the
            # fake's one hardcoded group instead of the breakdown the episode actually describes,
            # and reach the planner for a reason this script's own count should never carry.
            stack.ledger.groups_for_next_turn = episodes._groups_from(turn)
            stack.ledger.movements_for_next_turn = episodes._movements_from(turn)
            response = stack.client.post(
                "/v1/questions", json={"question": turn.say, "conversation": conversation}
            )
            stack.ledger.groups_for_next_turn = None
            stack.ledger.movements_for_next_turn = None
            if response.status_code != 200:
                continue
            conversation = response.json()["conversation"]
            if len(stack.planner.asked) <= asked:
                continue
            found.append(
                {
                    "say": turn.say,
                    "from": f"{episode.name} · turn {number}",
                    # A case without this is a different question: most of what we hand over only
                    # means anything after another turn. Trimmed to what is set, so a case reads.
                    "previous": {k: v for k, v in before.items() if v} if before else None,
                    "group": "anchor" if _folded(turn.say) in measured else "extension",
                    "states_a_query": _states_a_query(turn.say, episodes.TODAY),
                    "known_elsewhere": _another_vocabulary_has_a_word_for_it(turn.say),
                }
            )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive the handover evaluation cases.")
    parser.add_argument("--write", action="store_true", help=f"write {DESTINATION.name}")
    written = parser.parse_args().write

    found = cases()
    anchor = [case for case in found if case["group"] == "anchor"]
    print(f"handed to the planner : {len(found)}")
    print(f"  anchor (expectation): {len(anchor)}")
    print(f"  extension (nothing) : {len(found) - len(anchor)}")

    if written:
        DESTINATION.write_text(
            "# Generated by scripts/handover_cases.py. Regenerate; do not edit.\n"
            + yaml.safe_dump({"cases": found}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"wrote {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
