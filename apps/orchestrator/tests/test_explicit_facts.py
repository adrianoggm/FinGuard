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

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
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


def test_an_explicit_average_overrides_a_sum_plan() -> None:
    facts = explicit_facts.read(
        "¿Cuál fue el importe medio de mis compras el mes pasado?", today=TODAY
    )
    planned = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))

    assert explicit_facts.enforce(planned, facts).metric is Metric.AVERAGE


def test_two_compared_categories_are_both_kept_and_grouped() -> None:
    facts = explicit_facts.read("Compara gasolina frente a restaurantes el mes pasado", today=TODAY)
    wrong = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("TRANSPORT_FUEL",),
    )

    repaired = explicit_facts.enforce(wrong, facts)

    assert repaired.categories == ("TRANSPORT_FUEL", "RESTAURANTS")
    assert repaired.group_by is GroupBy.CATEGORY


def test_salary_and_income_words_force_incoming_movements() -> None:
    for sentence in (
        "¿Qué nómina he recibido este año?",
        "¿Qué ingresos he tenido este mes?",
        "¿Cuánto he cobrado el mes pasado?",
    ):
        facts = explicit_facts.read(sentence, today=TODAY)
        planned = FinancialPlan(direction=Direction.ALL, period=RelativePeriod(Period.THIS_MONTH))

        assert explicit_facts.enforce(planned, facts).direction is Direction.INCOME


def test_income_and_expenses_together_do_not_get_narrowed_to_income() -> None:
    facts = explicit_facts.read("Compara mis ingresos y gastos este mes", today=TODAY)

    assert facts.direction is None


# --- when, not how much -----------------------------------------------------------------


def payslips(count: int) -> QueryAnswer:
    """One a month, on days a payroll actually lands on."""
    days = [(1, 28), (2, 28), (3, 25), (4, 27), (5, 25), (6, 28), (7, 28)][:count]
    return QueryAnswer(
        currency="EUR",
        total_minor=244_287 * count,
        movement_count=count,
        movements=tuple(
            Movement(
                occurred_at=datetime(2026, month, day, 9, 0),
                description="Nómina",
                amount_minor=244_287,
                direction="INCOME",
                merchant_name="Innovasur SL",
            )
            for month, day in days
        ),
    )


def test_asking_when_is_not_answered_with_a_total() -> None:
    """From the report: "¿Cuándo me suelen ingresar la nómina?" → "17.383,97 € en nómina en lo que
    va de año". Correct number, and the dates were already on screen underneath it."""
    from finguard_orchestrator.domain.services import answer_writer

    salary = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("SALARY",),
    )

    said = answer_writer.when(payslips(7), salary)

    assert "una al mes" in said
    assert "entre el día 25 y el 28" in said
    assert "€" not in said, "the question was when, not how much"


def test_a_pattern_is_only_claimed_when_the_evidence_is_complete() -> None:
    """Sixty-four supermarket trips came back as ten. Reading a cadence off the ten would be a
    fact about the evidence limit wearing an answer's clothes."""
    from finguard_orchestrator.domain.services import answer_writer

    sampled = QueryAnswer(
        currency="EUR",
        total_minor=350_891,
        movement_count=64,
        movements=payslips(7).movements,
    )
    groceries = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES",),
    )

    said = answer_writer.when(sampled, groceries)

    assert "patrón" in said
    assert "una al mes" not in said


def test_movements_that_are_not_monthly_are_not_called_monthly() -> None:
    """Two in the same month is not a monthly pattern, however complete the evidence is."""
    from finguard_orchestrator.domain.services import answer_writer

    twice_in_july = QueryAnswer(
        currency="EUR",
        total_minor=10_000,
        movement_count=2,
        movements=(
            Movement(datetime(2026, 7, 3, 9, 0), "Compra", 5_000, "EXPENSE"),
            Movement(datetime(2026, 7, 19, 9, 0), "Compra", 5_000, "EXPENSE"),
        ),
    )
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))

    said = answer_writer.when(twice_in_july, plan)

    assert "una al mes" not in said
    assert "fechas debajo" in said


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Cuándo me suelen ingresar la nómina?",
        "cuando suelo ir al supermercado a comprar?",
        "¿cada cuánto pago el alquiler?",
        "¿en qué fechas me cobran las suscripciones?",
    ],
)
def test_a_question_about_time_is_recognised(sentence: str) -> None:
    assert explicit_facts.asks_when(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        # One letter apart from "cuándo", and the sentence is folded before it is read — so the
        # boundary is what keeps a question about money out of the writer for questions about time.
        "¿cuánto gasté el mes pasado?",
        "¿cuántas veces fui al súper?",
        "¿cuánto me han ingresado este año?",
    ],
)
def test_a_question_about_amount_is_not_one_about_time(sentence: str) -> None:
    assert not explicit_facts.asks_when(sentence)


# --- a filter that outlived its question ---------------------------------------------


class EmptyThenFull:
    """Empty while the category is there, and an answer once it is gone — the Mercadona case."""

    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self.plans.append(plan)
        if plan.categories:
            return QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
        return QueryAnswer(currency="EUR", total_minor=21_778, movement_count=3)

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        return None


HEALTH_LAST_QUARTER = FinancialPlan(
    direction=Direction.EXPENSE, period=THREE_MONTHS, categories=("HEALTH",)
)


async def test_a_category_from_five_turns_ago_does_not_empty_a_question_about_a_shop() -> None:
    """The reported turn. `health` came from a question five turns earlier, survived a
    clarification loop that answered nothing, and made a question about Mercadona come back empty
    — with "buscar sin categoría" offered as a button the user then had to press."""
    both = FinancialPlan(
        direction=Direction.EXPENSE,
        period=THREE_MONTHS,
        categories=("HEALTH",),
        merchants=("Mercadona",),
    )
    ledger = EmptyThenFull()

    result = await ask(
        "¿Qué gastos tengo históricamente en el Mercadona?",
        FakePlanner(PlanningResult.planned(both)),
        ledger,  # type: ignore[arg-type]
        conversation=Conversation(last_plan=HEALTH_LAST_QUARTER),
    )

    assert [p.categories for p in ledger.plans] == [("HEALTH",), ()]
    assert result.movement_count == 3
    assert "salud" in result.text, "it says which filter it dropped"


async def test_an_ordinary_follow_up_keeps_its_category_even_when_empty() -> None:
    """The case that makes the rule narrow. "¿Y la semana pasada?" after a question about health
    names no category either, and answering it about every expense would be a different question.
    Only a shop the previous plan did not have makes the pair suspicious."""
    narrower = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_WEEK),
        categories=("HEALTH",),
    )
    ledger = EmptyThenFull()

    result = await ask(
        "¿y la semana pasada?",
        FakePlanner(PlanningResult.planned(narrower)),
        ledger,  # type: ignore[arg-type]
        conversation=Conversation(last_plan=HEALTH_LAST_QUARTER),
    )

    assert [p.categories for p in ledger.plans] == [("HEALTH",)], "asked once, kept the filter"
    assert result.movement_count == 0


async def test_a_category_the_sentence_names_is_never_dropped() -> None:
    """Naming it is the whole difference between an inherited filter and a chosen one."""
    named = FinancialPlan(
        direction=Direction.EXPENSE,
        period=THREE_MONTHS,
        categories=("HEALTH",),
        merchants=("Mercadona",),
    )
    ledger = EmptyThenFull()

    await ask(
        "¿cuánto he gastado en salud en el Mercadona?",
        FakePlanner(PlanningResult.planned(named)),
        ledger,  # type: ignore[arg-type]
        conversation=Conversation(last_plan=HEALTH_LAST_QUARTER),
    )

    # Twice now, and the category survives both. The second call is the shop lookup an empty
    # answer with a merchant filter makes before giving up: it asks which shops are in this
    # window so it can offer a spelling, finds the name spelled correctly and offers nothing.
    # What this test is about is that neither call dropped a category the sentence named.
    assert [p.categories for p in ledger.plans] == [("HEALTH",), ("HEALTH",)]
