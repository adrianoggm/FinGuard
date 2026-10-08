"""The battery's report says which mechanism failed and whether the next turn recovered.

`scripts/real_conversation.py` computes the same table for the live stack; the tags and the
recovery definition here must stay identical to it so the two runs can be compared.
"""

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# By path: `scripts/` is not a package. The script's only import-time work is a sys.path insertion
# and an environment default, both harmless here.
_SPEC = importlib.util.spec_from_file_location(
    "evaluate_conversation_ux", ROOT / "scripts" / "evaluate_conversation_ux.py"
)
assert _SPEC and _SPEC.loader
battery = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_conversation_ux"] = battery
_SPEC.loader.exec_module(battery)

TURNS = [
    {"episode": "a", "turn": 1, "mechanism": "follow_up", "failures": []},
    {"episode": "a", "turn": 2, "mechanism": "follow_up", "failures": ["x"]},
    {"episode": "a", "turn": 3, "mechanism": "reset", "failures": []},
    {"episode": "b", "turn": 1, "mechanism": "intent", "failures": ["y"]},
    {"episode": "b", "turn": 2, "mechanism": "intent", "failures": ["z"]},
]


def test_failures_are_counted_per_mechanism_and_recovery_is_the_next_turn() -> None:
    report = battery.summarise(TURNS)
    assert report["by_mechanism"]["follow_up"] == {"turns": 2, "failed": 1}
    assert report["by_mechanism"]["intent"] == {"turns": 2, "failed": 2}
    # a/2 failed and a/3 passed: recovered; b/1 failed and b/2 failed: not recovered
    assert report["recovery_rate"] == 0.5


def test_a_failure_on_the_last_turn_has_no_next_turn_to_recover_on() -> None:
    turns = [
        {"episode": "a", "turn": 1, "mechanism": "intent", "failures": []},
        {"episode": "a", "turn": 2, "mechanism": "intent", "failures": ["x"]},
        {"episode": "b", "turn": 1, "mechanism": "intent", "failures": []},
    ]
    assert battery.summarise(turns)["recovery_rate"] is None


def test_every_turn_of_the_battery_carries_one_known_mechanism() -> None:
    real_spec = importlib.util.spec_from_file_location(
        "real_conversation_tags", ROOT / "scripts" / "real_conversation.py"
    )
    assert real_spec and real_spec.loader
    real = importlib.util.module_from_spec(real_spec)
    sys.modules["real_conversation_tags"] = real
    real_spec.loader.exec_module(real)

    data = yaml.safe_load((ROOT / "evaluation/ux/conversations.yaml").read_text(encoding="utf-8"))
    tags = [turn.get("mechanism") for raw in data["episodes"] for turn in raw["turns"]]
    assert len(tags) == 60
    assert set(tags) <= real.MECHANISMS
