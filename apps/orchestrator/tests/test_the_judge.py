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

    def __init__(self, reply: str, then: str | None = None) -> None:
        self.replies = [reply] if then is None else [reply, then]
        self.attempts = 0
        self.prompt: str | None = None
        self.schema: dict[str, Any] | None = None
        self.temperature: float | None = None

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        self.prompt = prompt
        self.schema = kwargs.get("schema")
        self.temperature = kwargs.get("temperature")
        self.attempts += 1
        return self.replies[min(self.attempts - 1, len(self.replies) - 1)]


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


async def test_an_answer_that_is_not_an_object_is_retried_and_then_given_up_on() -> None:
    """The contract changed and the property behind it did not.

    It used to raise, which took a whole run down when one case of seventy-two came back as eight
    kilobytes of repetition. It now retries once and returns None — but None, and never a verdict:
    a judge that stopped answering must not read as a judge that found everything defensible,
    which is the failure that looks like success.
    """
    provider = Answers("no puedo juzgarlo")

    assert await judge(provider, "m", CASE, PLAN) is None
    assert provider.attempts == 2, "one retry, and only one"


async def test_a_second_attempt_that_parses_is_the_verdict() -> None:
    """The retry has to be able to succeed, or it is a delay with a comment on it."""
    provider = Answers("no puedo juzgarlo", then=_all("right"))

    verdict = await judge(provider, "m", CASE, PLAN)

    assert verdict is not None
    assert set(verdict) == {*FIELDS, "answers_the_question"}


def test_what_the_judge_could_not_read_is_not_in_the_tally() -> None:
    """Where the None goes, which is the whole reason it is not `unjudgeable`.

    `unjudgeable` is a statement about the *sentence* — it admits more than one defensible reading.
    A judge that returned nothing is a statement about the judge. Counting one as the other would
    put a defect in the measuring instrument into a column that reports on the corpus, and the run
    would look like a corpus full of hard cases.
    """
    counted = tally([verdict for verdict in (json.loads(_all("right")), None) if verdict])

    assert counted["window"] == {"right": 1, "wrong": 0, "unjudgeable": 0}


def test_a_missing_field_counts_as_unjudgeable_rather_than_right() -> None:
    """The direction the tally has to lean. A field the judge omitted is one nobody assessed, and
    counting it as right is how a coverage number flatters itself."""
    counted = tally([{"window": {"verdict": "right", "why": ""}}])

    assert counted["window"]["right"] == 1
    assert counted["metric"]["unjudgeable"] == 1
    assert counted["metric"]["right"] == 0
