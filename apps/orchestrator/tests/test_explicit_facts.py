"""The model may not contradict what the user wrote down.

The worst answer in the reported conversation was not a misunderstanding. It was this:

    "¿A qué personas hice bizum en los últimos 5 meses?"   → five months
    "¿Cuánto he gastado en los últimos 3 meses en alquiler?" → answered over five months

The model emitted `changed: ["categories"]`, the merge is arithmetic on that list, and the window
was therefore inherited. Every layer behaved correctly and the number was about a period nobody had
asked for — which is the failure this whole product exists to prevent, since the figure itself was
real and there was nothing to disbelieve it with.

The fix is not a wider regex. It is that the sentence outranks the model on the small set of things
it states outright, and this file is the check on that ordering: through the pipeline, because a
unit test of the extractor would have passed before the bug existed.
"""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import explicit_facts
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

FIVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=5)
THREE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=3)
TWELVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=12)
ALL_OF_2025 = AbsoluteRange(start=date(2025, 1, 1), end=date(2025, 12, 31))

BIZUMS_OVER_FIVE_MONTHS = FinancialPlan(
    direction=Direction.EXPENSE, period=FIVE_MONTHS, categories=("TRANSFER_BIZUM",)
)


class FakePlanner:
    """Answers with whatever plan it was built with, and records what it was asked."""

    def __init__(self, result: PlanningResult) -> None:
        self._result = result
        self.asked: str | None = None

    async def plan(self, sentence: str, *, today: date, previous=None, force_follow_up=False):
        self.asked = sentence
        return self._result


class FakeLedger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self.plans.append(plan)
        return QueryAnswer(
            currency="EUR",
            total_minor=46_556,
            movement_count=3,
            groups=(Group("Elena", 4_656, 2), Group("Sergio", 3_166, 1)),
        )

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        return Movement(
            occurred_at=datetime(2026, 6, 7, 12, 0),
            description="Bizum",
            amount_minor=3_948,
            direction="EXPENSE",
            merchant_name="Elena",
            currency="EUR",
        )


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


# --- the window the user wrote down ---------------------------------------------------


async def test_an_explicit_window_is_not_inherited_from_the_previous_turn() -> None:
    """The reported failure, exactly. The model kept the previous window; the sentence says three
    months, and three months is not a judgement call."""
    inherited = FinancialPlan(
        direction=Direction.EXPENSE, period=FIVE_MONTHS, categories=("HOUSING_RENT",)
    )
    ledger = FakeLedger()

    await ask(
        "¿cuánto he gastado en total en los últimos 3 meses en alquiler?",
        FakePlanner(PlanningResult.planned(inherited)),
        ledger,
        conversation=Conversation(last_plan=BIZUMS_OVER_FIVE_MONTHS),
    )

    assert ledger.plans[0].period == THREE_MONTHS
    assert ledger.plans[0].categories == ("HOUSING_RENT",)


async def test_an_explicit_year_replaces_an_inherited_relative_window() -> None:
    """ "Durante todo el año 2025" is a range, and setting a range while leaving the inherited
    period behind would build a plan the domain refuses — so the two move as a pair."""
    inherited = FinancialPlan(
        direction=Direction.EXPENSE, period=TWELVE_MONTHS, categories=("RESTAURANTS",)
    )
    ledger = FakeLedger()

    await ask(
        "no los últimos 12 meses, sino durante todo el año 2025",
        FakePlanner(PlanningResult.planned(inherited)),
        ledger,
        conversation=Conversation(last_plan=inherited),
    )

    assert ledger.plans[0].period is None
    assert ledger.plans[0].date_range == ALL_OF_2025


async def test_a_correction_about_the_window_keeps_the_filter_it_never_mentioned() -> None:
    """The second half of the same turn. The model emitted `categories: []`, and an empty list from
    a model cannot be told apart from a filter it simply did not copy — so a question about
    restaurants in one year came back as every expense in another."""
    previous = FinancialPlan(
        direction=Direction.EXPENSE, period=TWELVE_MONTHS, categories=("RESTAURANTS",)
    )
    lost_the_filter = FinancialPlan(direction=Direction.EXPENSE, period=TWELVE_MONTHS)
    ledger = FakeLedger()

    await ask(
        "no, durante todo el año 2025",
        FakePlanner(PlanningResult.planned(lost_the_filter)),
        ledger,
        conversation=Conversation(last_plan=previous),
    )

    assert ledger.plans[0].categories == ("RESTAURANTS",)
    assert ledger.plans[0].date_range == ALL_OF_2025


async def test_a_correction_that_asks_to_widen_still_clears_the_filter() -> None:
    """ "¿Y en general?" is about the filter, so losing it is what was asked for. Restoring it here
    would make a real instruction unsayable, which is the cost of a rule that only ever adds."""
    previous = FinancialPlan(
        direction=Direction.EXPENSE, period=TWELVE_MONTHS, categories=("RESTAURANTS",)
    )
    widened = FinancialPlan(direction=Direction.EXPENSE, period=TWELVE_MONTHS)
    ledger = FakeLedger()

    await ask(
        "no, en general",
        FakePlanner(PlanningResult.planned(widened)),
        ledger,
        conversation=Conversation(last_plan=previous),
    )

    assert ledger.plans[0].categories == ()


async def test_a_ranking_takes_the_explicit_window_too() -> None:
    """A maximum over the wrong months is as wrong as a total over them."""
    ranking = TopMovements(
        direction=Direction.EXPENSE, period=FIVE_MONTHS, categories=("TRANSFER_BIZUM",)
    )
    ledger = FakeLedger()
    planner = FakePlanner(PlanningResult.planned(ranking))

    result = await ask("¿cuál fue el bizum más alto de los últimos 3 meses?", planner, ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value


# --- the shape the question asks for --------------------------------------------------


async def test_asking_who_produces_groups_and_not_a_total() -> None:
    """ "¿A qué personas les he hecho Bizum?" does not ask for a total, and answering with one was
    a correct number to a question nobody asked."""
    total = FinancialPlan(
        direction=Direction.EXPENSE, period=FIVE_MONTHS, categories=("TRANSFER_BIZUM",)
    )
    ledger = FakeLedger()

    result = await ask(
        "¿a qué personas les he hecho bizum en los últimos 5 meses?",
        FakePlanner(PlanningResult.planned(total)),
        ledger,
    )

    assert ledger.plans[0].group_by is GroupBy.MERCHANT
    assert [group.key for group in result.groups] == ["Elena", "Sergio"]


# --- what the extractor deliberately does not claim -----------------------------------


def test_a_sentence_that_names_no_window_states_nothing_about_one() -> None:
    """None is the useful answer as often as a window is: it means the model's reading stands."""
    assert explicit_facts.read("¿y en gasolina?", today=TODAY).window is None


def test_the_direction_is_left_to_the_verifier() -> None:
    """Refusing a spending question planned as ALL is a control with a test of its own. Rewriting
    it here would also be the pipeline quietly repairing a prompt injection instead of reporting
    one, so this module states the window and stays out of that."""
    facts = explicit_facts.read("¿cuánto gasté el mes pasado?", today=TODAY)
    planned = FinancialPlan(direction=Direction.ALL, period=RelativePeriod(Period.LAST_MONTH))

    assert explicit_facts.enforce(planned, facts).direction is Direction.ALL
