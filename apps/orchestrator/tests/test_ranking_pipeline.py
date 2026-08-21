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
from dataclasses import replace
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.ask_financial_question import (
    _multiple_movement_ranking,
)
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


class NoCards:
    """The card directory, required like the balance and the address book so no build can be
    half-wired. A chat that could not reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from datetime import date

        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


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
    currency="EUR",
)
REPSOL = Movement(
    occurred_at=datetime(2026, 6, 22, 19, 0),
    description="Repostaje",
    amount_minor=7_610,
    direction="EXPENSE",
    merchant_name="Repsol",
    category="TRANSPORT_FUEL",
    currency="EUR",
)
# The same account, denominated in something else. It exists to prove the currency travels rather
# than being written here — a ranking used to answer "EUR" whatever the ledger said.
BROADWAY = Movement(
    occurred_at=datetime(2026, 5, 2, 21, 0),
    description="Teatro",
    amount_minor=19_500,
    direction="EXPENSE",
    merchant_name="Broadway",
    category="LEISURE",
    currency="USD",
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
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts(), cards=NoCards()
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


async def test_three_largest_expenses_are_read_as_concrete_ranked_movements() -> None:
    planner = FakePlanner()
    ledger = FakeLedger(CEPSA, REPSOL, BROADWAY)

    result = await ask("Muéstrame mis tres mayores gastos de este año", planner, ledger)

    assert planner.asked is None
    assert [ranking.rank for ranking in ledger.rankings] == [1, 2, 3]
    assert result.movement_count == 3
    assert [movement.merchant_name for movement in result.movements] == [
        "Cepsa",
        "Repsol",
        "Broadway",
    ]
    assert "Tus 3 mayores gastos" in result.text
    assert "1. Cepsa" in result.text
    assert "3. Broadway" in result.text
    assert result.currency is None
    assert result.total_minor is None, "amounts in EUR and USD cannot be added"
    remembered = result.conversation.last_plan
    assert isinstance(remembered, TopMovements)
    assert remembered.rank == 3
    assert remembered.categories == ()
    assert remembered.period == THIS_YEAR


async def test_unbounded_ordered_expenses_default_to_ten_rows_and_read_the_named_month() -> None:
    ledger = FakeLedger(CEPSA, REPSOL, BROADWAY)

    result = await ask(
        "Podrias decirme de abril que gastos he tenido de más grande a mas pequeño ?",
        FakePlanner(),
        ledger,
    )

    assert [ranking.rank for ranking in ledger.rankings] == [1, 2, 3, 4]
    assert result.movement_count == 3
    assert result.conversation.last_plan is not None
    remembered = result.conversation.last_plan
    assert isinstance(remembered, TopMovements)
    assert remembered.date_range == AbsoluteRange(date(2026, 4, 1), date(2026, 4, 30))
    assert "He encontrado 3 gastos en abril de 2026" in result.text
    assert "no hay 10" in result.text


async def test_unbounded_ordered_expenses_reads_all_ten_default_rows_in_rank_order() -> None:
    rows = tuple(replace(CEPSA, merchant_name=f"Merchant {position}") for position in range(10))
    ledger = FakeLedger(*rows)

    result = await ask("Lista mis gastos de abril de mayor a menor", FakePlanner(), ledger)

    assert [ranking.rank for ranking in ledger.rankings] == list(range(1, 11))
    assert result.movement_count == 10
    assert [movement.merchant_name for movement in result.movements] == [
        f"Merchant {position}" for position in range(10)
    ]


def _movement_ranking_for(sentence: str) -> tuple[TopMovements, int] | None:
    return _multiple_movement_ranking(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    )


def test_ordered_movement_recognizer_rejects_singular_and_ascending_requests() -> None:
    assert _movement_ranking_for("cuál fue el mayor gasto de abril") is None
    assert _movement_ranking_for("lista mis gastos de menor a mayor") is None
    assert _movement_ranking_for("ordena mis gastos de más pequeño a más grande") is None


async def test_an_incomplete_top_list_remembers_the_last_real_position() -> None:
    planner = FakePlanner()
    ledger = FakeLedger(CEPSA, REPSOL)

    first = await ask("Dime mis tres mayores gastos de este año", planner, ledger)
    second = await ask(
        "¿Y el siguiente?",
        planner,
        ledger,
        conversation=first.conversation,
    )

    assert [ranking.rank for ranking in ledger.rankings] == [1, 2, 3, 3]
    assert "He encontrado 2 gastos" in first.text
    assert "no hay 3" in first.text
    assert "no hay" in second.text.lower()


async def test_top_three_expenses_is_an_equivalent_natural_form() -> None:
    ledger = FakeLedger(CEPSA, REPSOL, BROADWAY)

    result = await ask("Top 3 gastos de este año", FakePlanner(), ledger)

    assert [ranking.rank for ranking in ledger.rankings] == [1, 2, 3]
    assert result.movement_count == 3


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


async def test_a_stated_ordinal_goes_to_the_position_it_states() -> None:
    """ "¿Y el tercero?" after the largest expense is the third, not the second.

    The recogniser returned a boolean and the caller always advanced by one, so the position the
    user wrote down was thrown away by the very feature added to read ordinals.
    """
    ledger = FakeLedger(CEPSA, REPSOL, BROADWAY)

    await ask("¿y el tercero?", FakePlanner(), ledger, conversation=Conversation(last_plan=FUEL))

    assert [r.rank for r in ledger.rankings] == [3]


async def test_an_ordinal_may_skip_backwards_or_forwards_from_wherever_it_is() -> None:
    """Absolute means absolute. After the second, "el quinto" is the fifth — not the third."""
    ledger = FakeLedger(CEPSA, REPSOL)

    await ask(
        "y el quinto",
        FakePlanner(),
        ledger,
        conversation=Conversation(last_plan=FUEL.at_rank(2)),
    )

    assert [r.rank for r in ledger.rankings] == [5]


async def test_the_next_one_is_still_relative() -> None:
    """ "El siguiente" names no position, so it is the only phrase that still means "one more"."""
    ledger = FakeLedger(CEPSA, REPSOL, BROADWAY)

    await ask(
        "y el siguiente",
        FakePlanner(),
        ledger,
        conversation=Conversation(last_plan=FUEL.at_rank(2)),
    )

    assert [r.rank for r in ledger.rankings] == [3]


async def test_the_currency_of_a_ranked_movement_comes_from_the_ledger() -> None:
    """It was written here as "EUR" whatever the account held, so a charge on a dollar account was
    labelled in euros beside a figure that was perfectly correct."""
    result = await ask(
        "¿cuál fue mi gasto más caro del año en gasolina?",
        FakePlanner(PlanningResult.planned(FUEL)),
        FakeLedger(BROADWAY),
    )

    assert result.currency == "USD"


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
