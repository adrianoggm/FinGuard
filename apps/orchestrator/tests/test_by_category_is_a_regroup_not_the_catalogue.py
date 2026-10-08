"""«Mejor por categoría» with an answer in view regroups it; it never lists the catalogue.

Validation fix 3, G8. On 2026-10-08 (ux-04-aclaraciones 5), after «y este mes» — the supermarket
this month, by shop — «mejor por categoria» answered «Puedo consultar estas categorías: …». The
deployment reads turns with the hybrid interpreter, and «categor» is one of the words that sends a
catch-all to the model for a second reading: the model called the turn LIST_CATEGORIES, and the
arbitration only reads a patch on the answer from a turn read as a question about money, so the
catalogue answered.

With a base in view, a whole-turn regroup is Task 7's patch (R29, R36) whatever the interpreter
called it. The catalogue answers when there is no base, or when the turn asks which categories
exist.

Driven over HTTP on the episodes stack, with an interpreter scripted as the model read the turn,
alone and inside the `Hybrid` arrangement the deployment runs.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import ask

from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic, Hybrid
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

THE_BATTERY = (
    "cuanto gaste en supermercados el mes pasado",
    "en todas",
    "por comercio",
    "y este mes",
)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ReadsTheCatalogue:
    """The model's reading on 2026-10-08: a turn about categories is the catalogue."""

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        if "categor" in spanish.folded(sentence):
            return Intent.LIST_CATEGORIES
        return await Deterministic().interpret(sentence, conversation=conversation)


INTERPRETERS = {
    "model": ReadsTheCatalogue(),
    "hybrid": Hybrid(ReadsTheCatalogue(), Deterministic()),
}


def _after(*turns: str, interpreter: Any) -> tuple[episodes.Stack, dict[str, Any]]:
    stack = episodes.a_stack(account_scope_default="all")
    app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = None
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


@pytest.mark.parametrize("interpreter", INTERPRETERS.values(), ids=INTERPRETERS.keys())
@pytest.mark.parametrize(
    "sentence",
    ["mejor por categoria", "mejor por categoría", "¿y por categoría?", "por categorías"],
)
def test_by_category_over_an_answer_regroups_it(sentence: str, interpreter: Any) -> None:
    """ux-04-aclaraciones 5: this month by category (R36 drops the one category it divides),
    never the catalogue and never the model."""
    stack, answer = _after(*THE_BATTERY, sentence, interpreter=interpreter)

    assert episodes.tool_of(answer) == episodes.QUERY, answer["text"]
    assert "Puedo consultar estas categorías" not in answer["text"]
    (plan,) = stack.ledger.plans
    assert isinstance(plan, FinancialPlan)
    assert plan.group_by is GroupBy.CATEGORY, plan
    assert plan.period is not None and plan.period.period is Period.THIS_MONTH, plan
    assert stack.planner.asked == []


@pytest.mark.parametrize("interpreter", INTERPRETERS.values(), ids=INTERPRETERS.keys())
def test_asking_which_categories_exist_is_still_the_catalogue(interpreter: Any) -> None:
    """With an answer in view, «¿qué categorías hay?» is a question about the product."""
    _, answer = _after(*THE_BATTERY, "¿qué categorías hay?", interpreter=interpreter)

    assert episodes.tool_of(answer) == episodes.CATEGORIES, answer["text"]


def test_with_nothing_in_view_the_catalogue_may_answer() -> None:
    """No base, nothing to regroup: the model's catalogue reading stands."""
    _, answer = _after("mejor por categoria", interpreter=ReadsTheCatalogue())

    assert episodes.tool_of(answer) == episodes.CATEGORIES, answer["text"]
