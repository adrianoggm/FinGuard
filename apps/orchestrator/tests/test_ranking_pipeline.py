"""The first loop of the reported conversation, gone rather than answered better.

    "¿Cuál fue el gasto más caro en el año de gasolina?"   the sum of 56 movements, then a refusal
    "Y el segundo."                                        "¿De qué periodo te refieres?"
    "del año"  → "¿De qué año?"  → "de este"  → "¿De qué periodo?"

Every turn of that was a symptom of one thing: the capability did not exist, so the question had no
answer and the follow-up had nothing to be about. With RANK_MOVEMENTS the first turn is answered and
the second is a patch of one field — no model, no re-reading of a sentence that names neither the
period nor the category.

That last part is what the review asked for and what a regex over ordinals could not give: the
follow-up is robust because there is nothing left to recognise except "and the next one".
"""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)
THIS_YEAR = RelativePeriod(period=Period.THIS_YEAR)

FUEL = TopMovements(
    direction=Direction.EXPENSE, period=THIS_YEAR, categories=("TRANSPORT_FUEL",), rank=1
)

CEPSA = Movement(
    occurred_at=datetime(2026, 3, 14, 10, 0),
    description="Repostaje",
    amount_minor=8_240,
    direction="EXPENSE",
    merchant_name="Cepsa",
    category="TRANSPORT_FUEL",
)
REPSOL = Movement(
    occurred_at=datetime(2026, 6, 22, 19, 0),
    description="Repostaje",
    amount_minor=7_610,
    direction="EXPENSE",
    merchant_name="Repsol",
    category="TRANSPORT_FUEL",
)


class FakePlanner:
    def __init__(self, result: PlanningResult | None = None) -> None:
        self._result = result
        self.asked: str | None = None

    async def plan(self, sentence: str, *, today: date, previous: FinancialPlan | None = None):
        self.asked = sentence
        if self._result is None:
            raise AssertionError(f"the model was consulted about {sentence!r}")
        return self._result


class FakeLedger:
    """Answers rankings from a list, so the rank decides which row comes back."""

    def __init__(self, *rows: Movement) -> None:
        self._rows = rows
        self.rankings: list[TopMovements] = []
        self.aggregates: list[FinancialPlan] = []

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self.aggregates.append(plan)
        return QueryAnswer(currency="EUR", total_minor=397_737, movement_count=56)

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        self.rankings.append(ranking)
        position = ranking.rank - 1
        return self._rows[position] if position < len(self._rows) else None


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not this path")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


async def ask(sentence: str, planner: FakePlanner, ledger: FakeLedger, **extra: object):
    handler = AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts()
    )
    return await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            **extra,  # type: ignore[arg-type]
        )
    )


async def test_the_largest_expense_is_answered_with_the_movement() -> None:
    """A figure alone would send the user to the movement list to work out which charge it was, and
    a total would be a real number about a different question — which is what it used to be."""
    ledger = FakeLedger(CEPSA)

    result = await ask(
        "¿cuál fue el gasto más caro del año en gasolina?",
        FakePlanner(PlanningResult.planned(FUEL)),
        ledger,
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "82,40 €" in result.text
    assert "Cepsa" in result.text
    assert "14 de marzo" in result.text
    assert ledger.aggregates == [], "a maximum was answered with an aggregate"
    assert [r.rank for r in ledger.rankings] == [1]


async def test_the_ranking_becomes_the_memory_of_the_conversation() -> None:
    """Which is what makes the follow-up possible at all: the next turn inherits the ranking rather
    than a refusal or a plan."""
    result = await ask(
        "¿cuál fue el gasto más caro del año en gasolina?",
        FakePlanner(PlanningResult.planned(FUEL)),
        FakeLedger(CEPSA),
    )

    assert result.conversation.last_plan == FUEL


async def test_the_second_is_a_patch_of_one_field_and_no_model() -> None:
    """The payoff. "Y el segundo" changes the rank and nothing else — the window, the direction and
    the category are not read again, so the answer does not depend on the sentence naming them."""
    planner, ledger = FakePlanner(), FakeLedger(CEPSA, REPSOL)

    result = await ask("Y el segundo ?", planner, ledger, conversation=Conversation(last_plan=FUEL))

    assert planner.asked is None, "an ordinal follow-up reached the model"
    assert [r.rank for r in ledger.rankings] == [2]
    assert ledger.rankings[0].categories == ("TRANSPORT_FUEL",)
    assert ledger.rankings[0].period == THIS_YEAR
    assert "El segundo fue de 76,10 €" in result.text
    assert "Repsol" in result.text


async def test_walking_down_the_list_keeps_working() -> None:
    """Three turns of "y el siguiente", each one position deeper. The rank is state, so nothing has
    to remember how many times it was asked."""
    ledger = FakeLedger(CEPSA, REPSOL)
    conversation = Conversation(last_plan=FUEL)

    for _ in range(2):
        result = await ask("y el siguiente", FakePlanner(), ledger, conversation=conversation)
        conversation = result.conversation

    assert [r.rank for r in ledger.rankings] == [2, 3]
    assert "no hay" in result.text.lower(), "there were only two, and it says so"


async def test_an_ordinal_after_an_aggregate_is_not_a_ranking() -> None:
    """ "¿Y el segundo?" after a total is not about a second total. Reading it as one would answer a
    question nobody asked, which is the failure this whole path exists to avoid."""
    aggregate = FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR)
    planner = FakePlanner(PlanningResult.planned(aggregate))
    ledger = FakeLedger(CEPSA)

    await ask("y el segundo", planner, ledger, conversation=Conversation(last_plan=aggregate))

    assert planner.asked == "y el segundo", "it went to the model, like any other sentence"
    assert ledger.rankings == []


async def test_a_sentence_with_its_own_content_is_not_a_continuation() -> None:
    """The narrowness that makes the rule safe. "¿Cuánto gasté?" after a ranking is a new question,
    and answering it with the second largest expense would be worse than the loop this replaced."""
    planner = FakePlanner(
        PlanningResult.planned(FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR))
    )
    ledger = FakeLedger(CEPSA)

    await ask(
        "¿cuánto gasté en gasolina este año?",
        planner,
        ledger,
        conversation=Conversation(last_plan=FUEL),
    )

    assert planner.asked == "¿cuánto gasté en gasolina este año?"
    assert ledger.rankings == []
