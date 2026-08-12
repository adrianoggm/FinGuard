"""The judge, checked without a model.

A judge is a model scoring a model, so the things that can quietly be wrong about it are not its
opinions but its contract: what it is shown, what shapes of answer it may give, and whether a
verdict it returns is read the way it was meant. None of that needs a provider, and all of it is
what would make its numbers meaningless if it were wrong.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from judge_plans import FIELDS, VERDICT_SCHEMA, judge, prompt_for, tally  # noqa: E402

CASE = {
    "say": "¿cuántas compras hice en Mercadona?",
    "previous": {"window": "LAST_MONTH", "categories": ["RESTAURANTS"]},
    "group": "extension",
}
PLAN = {"window": "LAST_MONTH", "direction": "EXPENSE", "categories": ["RESTAURANTS"]}


class Answers:
    """A provider that records what it was asked and returns what it was told to."""

    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt: str | None = None
        self.schema: dict[str, Any] | None = None
        self.temperature: float | None = None

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        self.prompt = prompt
        self.schema = kwargs.get("schema")
        self.temperature = kwargs.get("temperature")
        return self.reply


def _all(verdict: str) -> str:
    return json.dumps(
        {field: {"verdict": verdict, "why": "x"} for field in (*FIELDS, "answers_the_question")}
    )


def test_the_judge_is_shown_the_thread_and_not_the_answer() -> None:
    """A sentence on its own is a different question — "¿y el mes pasado?" cannot be judged without
    the query it follows. And the figure is deliberately absent: a well-written answer about the
    wrong query must not be able to buy a good verdict."""
    written = prompt_for(CASE, PLAN)

    assert CASE["say"] in written
    assert "RESTAURANTS" in written, "the plan under judgement is in the prompt"
    assert "LAST_MONTH" in written, "so is the query the turn follows"
    assert "€" not in written and "movimientos" not in written


async def test_a_verdict_is_per_field_and_typed() -> None:
    provider = Answers(_all("right"))

    verdict = await judge(provider, "m", CASE, PLAN)

    assert provider.schema == VERDICT_SCHEMA, "the shape is enforced by the provider, not parsed"
    assert provider.temperature == 0.0, "a judge that varies between runs measures nothing"
    assert set(verdict) == {*FIELDS, "answers_the_question"}


async def test_an_answer_that_is_not_an_object_is_an_error() -> None:
    """Not a silent all-unjudgeable. A judge that stopped answering would otherwise read as a judge
    that found everything defensible, which is the failure that looks like success."""
    with pytest.raises((ValueError, json.JSONDecodeError)):
        await judge(Answers("no puedo juzgarlo"), "m", CASE, PLAN)


def test_a_missing_field_counts_as_unjudgeable_rather_than_right() -> None:
    """The direction the tally has to lean. A field the judge omitted is one nobody assessed, and
    counting it as right is how a coverage number flatters itself."""
    counted = tally([{"window": {"verdict": "right", "why": ""}}])

    assert counted["window"]["right"] == 1
    assert counted["metric"]["unjudgeable"] == 1
    assert counted["metric"]["right"] == 0
