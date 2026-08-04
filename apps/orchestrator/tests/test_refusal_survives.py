"""A refusal is a turn, and the next sentence may be about it.

From the report:

    "¿Cuál fue el gasto más caro en el año de gasolina?"
    → "Todavía no sé buscar el movimiento más alto de un periodo…"    correct
    "¿Y el segundo?"
    → "¿De qué periodo te refieres?"                                  a different conversation
    "del año"      → "¿De qué año? Por ejemplo 2025 o el año actual."
    "de este"      → "¿De qué periodo? Por ejemplo este mes…"          a loop

"¿Y el segundo?" asks for the second largest movement. The capability is the same one that was just
refused, and the sentence says nothing about size — so the guard cannot see it and the planner read
it as an ordinary follow-up with no window, which started a clarification about a period that could
never lead anywhere.

Repeating the refusal is the honest answer, and it needs one piece of state: which capability the
last turn was refused for. Held by the client like everything else, because this service keeps no
conversation.

**Narrow on purpose.** Only a sentence that is *nothing but* a continuation — "y el segundo", "¿y el
siguiente?" — is read this way. Anything carrying its own content is a new question, because a
refusal that spread forward would answer "¿cuánto gasté?" with "no sé buscar el más alto".
"""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    UnsupportedCapability,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)


class FakePlanner:
    def __init__(self) -> None:
        self.asked: str | None = None

    async def plan(
        self, sentence: str, *, today: date, previous: FinancialPlan | None = None
    ) -> PlanningResult:
        self.asked = sentence
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE, period=RelativePeriod(period=Period.THIS_MONTH)
            )
        )


class FakeLedger:
    def __init__(self) -> None:
        self.ran = False

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid"):
        self.ran = True
        return QueryAnswer(currency="EUR", total_minor=1_000, movement_count=1)


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not this path")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


async def ask(sentence: str, **extra: object):
    planner, ledger = FakePlanner(), FakeLedger()
    handler = AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts()
    )
    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            **extra,  # type: ignore[arg-type]
        )
    )
    return planner, ledger, result


async def test_the_refusal_names_the_capability_it_refused() -> None:
    """Without it on the way out there is nothing for the next turn to be about."""
    _, _, result = await ask("¿cuál fue el gasto más caro del año en gasolina?")

    assert result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert isinstance(result.conversation.active, UnsupportedCapability)
    assert result.conversation.active.capability == "LARGEST_MOVEMENT"


@pytest.mark.parametrize(
    "sentence",
    ["¿Y el segundo?", "y el segundo", "¿y el tercero?", "¿y el siguiente?", "y los siguientes"],
)
async def test_a_bare_continuation_after_a_refusal_is_refused_again(sentence: str) -> None:
    planner, ledger, result = await ask(
        sentence,
        conversation=Conversation(active=UnsupportedCapability(capability="LARGEST_MOVEMENT")),
    )

    assert planner.asked is None, "a continuation of a refused question reached the model"
    assert ledger.ran is False
    assert result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert "más alto" in result.text
    assert isinstance(result.conversation.active, UnsupportedCapability)
    assert result.conversation.active.capability == "LARGEST_MOVEMENT"


@pytest.mark.parametrize(
    "sentence",
    [
        # Its own question, whatever came before it. A refusal that spread forward would answer
        # these with "no sé buscar el movimiento más alto".
        "¿cuánto gasté en gasolina este mes?",
        "¿y en el supermercado?",
        "y este mes",
        "¿qué contactos tengo?",
    ],
)
async def test_a_sentence_with_its_own_content_is_a_new_question(sentence: str) -> None:
    planner, _, _ = await ask(
        sentence,
        conversation=Conversation(active=UnsupportedCapability(capability="LARGEST_MOVEMENT")),
    )

    assert planner.asked == sentence or planner.asked is None, sentence


async def test_a_continuation_with_no_refusal_behind_it_is_planned() -> None:
    """The state is what makes this readable. Without it "¿y el segundo?" is a sentence about
    nothing, and the planner asking what period it means is the best available answer."""
    planner, _, _ = await ask("¿y el segundo?")

    assert planner.asked == "¿y el segundo?"


async def test_the_capability_is_checked_rather_than_trusted() -> None:
    """It crosses the wire, so a caller can send anything. A name this build does not have is not a
    refusal it can repeat — the sentence takes the ordinary path instead."""
    planner, _, _ = await ask(
        "¿y el segundo?",
        conversation=Conversation(active=UnsupportedCapability(capability="NOT_A_RULE")),
    )

    assert planner.asked == "¿y el segundo?"
