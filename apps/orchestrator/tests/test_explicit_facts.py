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
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
    TransactionType,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import explicit_facts
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
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

    async def plan(self, sentence: str, *, context: PlanningContext):
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


@pytest.mark.parametrize(
    ("sentence", "categories", "merchants"),
    [
        ("Todos mis gastos excepto alquiler", ("HOUSING_RENT",), ()),
        ("Si no contamos el supermercado", ("GROCERIES",), ()),
        ("Lo mismo, pero sin Mercadona", (), ("mercadona",)),
        # B-1 (Wave 3 hardening): the imperative and prepositional forms a holder actually says,
        # not only the gerund («quitando») the vocabulary already had.
        ("Quita restaurantes", ("RESTAURANTS",), ()),
        ("Elimina restaurantes", ("RESTAURANTS",), ()),
        ("Borra restaurantes", ("RESTAURANTS",), ()),
        ("Saca restaurantes", ("RESTAURANTS",), ()),
        ("Fuera restaurantes", ("RESTAURANTS",), ()),
        ("Excluye restaurantes", ("RESTAURANTS",), ()),
        ("Ya no cuentes restaurantes", ("RESTAURANTS",), ()),
    ],
)
def test_negative_filters_are_explicit_facts(
    sentence: str, categories: tuple[str, ...], merchants: tuple[str, ...]
) -> None:
    facts = explicit_facts.read(sentence, today=TODAY)

    assert facts.excluded_categories == categories
    assert facts.excluded_merchants == merchants


@pytest.mark.parametrize(
    "sentence",
    [
        # None of these names a shop, so none is a merchant exclusion. Accepting them produced a
        # plan carrying an `excluded_merchants` filter that matches nothing, and exempted the
        # capability guard that would otherwise have said the exclusion cannot be executed.
        "Cuánto gasté en gasolina sin contar algunas cosas",
        "Cuánto gasté en ocio excepto algunas cosas",
        "Cuánto gasté en gasolina si no contamos lo que ya sabes",
        "Cuánto gasté en ocio quitando lo de siempre",
        "Cuánto gasté en ocio sin contar eso",
        "Cuánto gasté en ocio sin contar unas cuantas cosas",
        # The exclusion marker's own verb, left as the tail when nothing follows it.
        "Cuánto gasté en gasolina sin contar",
        "Cuánto gasté en gasolina sin incluir",
    ],
)
def test_a_vague_exclusion_names_no_merchant(sentence: str) -> None:
    assert explicit_facts.excluded_merchants_in(sentence) == ()
    assert explicit_facts.read(sentence, today=TODAY).excluded_merchants == ()


@pytest.mark.parametrize(
    ("sentence", "merchant"),
    [
        ("Cuánto gasté en gasolina sin Mercadona", "mercadona"),
        ("Cuánto gasté en gasolina sin contar Cepsa", "cepsa"),
        ("Cuánto gasté en supermercado excepto Carrefour", "carrefour"),
        # Shop names that are ordinary words, or that open with a determiner. The vague-exclusion
        # vocabulary must not reach any of these — a fix that stops recognising real shops is
        # worse than the leak it closed.
        ("Cuánto gasté en supermercado sin contar Dia", "dia"),
        ("Cuánto gasté en ropa sin El Corte Inglés", "el corte ingles"),
        ("Cuánto gasté en ocio sin contar Casa del Libro", "casa del libro"),
        ("Cuánto gasté en supermercado sin contar Simply", "simply"),
        ("Cuánto gasté en ocio sin contar La Sirena", "la sirena"),
    ],
)
def test_a_named_shop_is_still_a_merchant_exclusion(sentence: str, merchant: str) -> None:
    assert explicit_facts.excluded_merchants_in(sentence) == (merchant,)
    assert explicit_facts.read(sentence, today=TODAY).excluded_merchants == (merchant,)


def test_a_shop_named_after_an_exclusion_verb_is_still_read() -> None:
    """«sin meter Cepsa» has no `sin meter` alternative in the marker pattern, so its tail keeps
    the verb — untidy, and pre-existing. What matters is that the shop is not thrown away with it:
    rejecting every tail that *opens* with an exclusion verb would have done exactly that.
    """
    assert explicit_facts.excluded_merchants_in("Cuánto gasté en gasolina sin meter Cepsa") == (
        "meter cepsa",
    )


async def test_a_direct_debit_amount_keeps_sum_and_adds_the_banking_rail() -> None:
    planned = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        metric=Metric.SUM,
    )
    ledger = FakeLedger()

    await ask(
        "¿Cuánto he pagado de recibos domiciliados este año?",
        FakePlanner(PlanningResult.planned(planned)),
        ledger,
    )

    assert ledger.plans[0].metric is Metric.SUM
    assert ledger.plans[0].transaction_types == (TransactionType.DIRECT_DEBIT,)


def test_showing_receipts_is_recurrence_but_asking_their_total_is_not() -> None:
    assert explicit_facts.asks_recurrence("Muéstrame mis recibos")
    assert not explicit_facts.asks_recurrence("¿Cuánto he pagado de recibos domiciliados este año?")


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

    assert ledger.plans[0].group_by is GroupBy.COUNTERPARTY
    assert [group.key for group in result.groups] == ["Elena", "Sergio"]


# --- what the extractor deliberately does not claim -----------------------------------


def test_a_sentence_that_names_no_window_states_nothing_about_one() -> None:
    """None is the useful answer as often as a window is: it means the model's reading stands."""
    assert explicit_facts.read("¿y en gasolina?", today=TODAY).window is None


def test_the_reported_short_weekly_follow_up_names_the_week_divisor() -> None:
    facts = explicit_facts.read("Y el semanal ?", today=TODAY)

    assert facts.group_by is GroupBy.WEEK
    assert facts.metric is None


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [("Y semanl?", GroupBy.WEEK), ("Y semnal?", GroupBy.WEEK), ("Y mensula?", GroupBy.MONTH)],
)
def test_a_small_typo_does_not_turn_an_average_divisor_into_a_merchant(
    sentence: str, expected: GroupBy
) -> None:
    assert explicit_facts.read(sentence, today=TODAY).group_by is expected


def test_the_reported_highest_category_is_a_category_ranking() -> None:
    facts = explicit_facts.read(
        "Vale en que categoria es la que he gastado mas a lo largo de este año?", today=TODAY
    )

    assert facts.asks_top_category
    assert facts.group_by is GroupBy.CATEGORY
    assert facts.window == RelativePeriod(Period.THIS_YEAR)


def test_the_reported_account_breakdown_is_not_a_category_question() -> None:
    facts = explicit_facts.read(
        "Me harias el desglose por cuenta ? o esta todo de la cuenta nomina ?", today=TODAY
    )

    assert facts.asks_account_breakdown
    assert facts.group_by is GroupBy.ACCOUNT


def test_asking_where_payroll_landed_is_an_account_breakdown() -> None:
    facts = explicit_facts.read(
        "Sabrias decirme si he cobrado mi nomina en que cuenta?", today=TODAY
    )

    assert facts.asks_account_breakdown
    assert facts.group_by is GroupBy.ACCOUNT
    assert facts.direction is Direction.INCOME


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


def test_a_monthly_average_is_not_confused_with_an_average_movement() -> None:
    facts = explicit_facts.read(
        "¿Cuál es mi gasto medio anual en supermercados por mes?", today=TODAY
    )

    assert facts.metric is Metric.PERIOD_AVERAGE
    assert facts.group_by is GroupBy.MONTH
    assert facts.window == TWELVE_MONTHS
    assert facts.names_a_category


def test_a_monthly_breakdown_of_average_movements_keeps_the_movement_divisor() -> None:
    facts = explicit_facts.read("¿Cuál es el importe medio por movimiento mensual?", today=TODAY)

    assert facts.metric is Metric.AVERAGE
    assert facts.group_by is GroupBy.MONTH


@pytest.mark.parametrize(
    "sentence",
    [
        "¿En qué mes hice más compras, julio o junio?",
        "¿Tuve más movimientos en julio o junio?",
        "Compara el número de operaciones de julio y junio",
    ],
)
def test_an_explicit_movement_quantity_is_a_count(sentence: str) -> None:
    assert explicit_facts.read(sentence).metric is Metric.COUNT


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Cuánto pago de alquiler?",
        "¿cuánto ingreso al mes?",
        "¿cuánto cargo me han hecho este mes?",
    ],
)
def test_a_singular_verb_spelled_like_a_noun_is_not_a_count(sentence: str) -> None:
    """The other side of the rule above, and it was answering the wrong question.

    Three of the nouns a count is asked with — pago, ingreso, cargo — are spelled exactly like the
    first person of the verb that asks for an amount. "¿Cuánto pago de alquiler?" came back "3
    movimientos en alquiler el mes pasado": asked how much, told how many, with a real figure of
    the wrong kind on it. It is the opening sentence of `planning/conversations.yaml` and no turn of
    the routing corpus asks it, which is why 1113 measured turns never saw it.
    """
    assert explicit_facts.read(sentence).metric is not Metric.COUNT


@pytest.mark.parametrize(
    ("sentence", "group_by"),
    [
        ("¿Cuánto suelo gastar al mes en supermercados?", GroupBy.MONTH),
        ("¿Cuál es mi gasto mensual medio en alquiler?", GroupBy.MONTH),
        ("¿Qué coste semanal medio tengo en restaurantes?", GroupBy.WEEK),
        ("¿Cuánto pago normalmente por día en transporte público?", GroupBy.DAY),
        ("¿Cuánto suelo cobrar al mes?", GroupBy.MONTH),
    ],
)
def test_natural_period_average_word_orders_are_explicit_facts(
    sentence: str, group_by: GroupBy
) -> None:
    facts = explicit_facts.read(sentence, today=TODAY)

    assert facts.metric is Metric.PERIOD_AVERAGE
    assert facts.group_by is group_by


def test_a_typical_monthly_amount_can_be_income() -> None:
    facts = explicit_facts.read("¿Cuánto suelo cobrar al mes?", today=TODAY)

    assert facts.metric is Metric.PERIOD_AVERAGE
    assert facts.direction is Direction.INCOME


def test_asking_which_category_has_the_higher_average_requests_a_breakdown() -> None:
    facts = explicit_facts.read(
        "¿Cuál tiene mayor gasto medio, supermercados o restaurantes, este año?", today=TODAY
    )

    assert facts.metric is Metric.AVERAGE
    assert facts.group_by is GroupBy.CATEGORY
    assert facts.compared_categories == ("GROCERIES", "RESTAURANTS")


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


def test_a_coordinated_category_list_asks_for_one_total() -> None:
    """The decision, in the four shapes the corpus actually writes it in.

    «entre A y B» is the one that used to go the other way, and it went there through a bare
    `\\bentre\\b` in `compares` — so a sentence Spanish reads as "A and B together" was handed to
    `readers/breakdowns` and answered as two rows.
    """
    for sentence in (
        "¿Cuánto llevo entre gasolina y transporte público este mes?",
        "¿Cuánto llevo este mes en súper, gasolina y farmacia?",
        "Me gustaría saber el importe total que he gastado en ocio y suscripciones "
        "durante este mes.",
        "¿Cuál es el importe total que he registrado para los gastos de súper, gasolina "
        "y farmacia este mes?",
    ):
        facts = explicit_facts.read(sentence, today=TODAY)

        assert facts.group_by is GroupBy.NONE, sentence
        assert facts.compared_categories == (), sentence


def test_entre_is_a_coordination_and_not_a_comparison() -> None:
    """It never reached `compares` through a comparative frame, so it no longer reaches it at all.

    Measured over every sentence this repository has written down before the row was removed: five
    sentences reached `compares` through «entre» alone and all five were combined totals, and none
    reached it through «entre» beside another alternative. The genuine comparative frames are read
    where they always were.
    """
    combined = explicit_facts.read("¿Cuánto pagué el mes pasado entre alquiler y suscripciones?")
    assert combined.group_by is GroupBy.NONE
    assert combined.compared_categories == ()

    for still_a_comparison in (
        "Compara gasolina frente a restaurantes el mes pasado",
        "Dame alquiler y súper por separado",
        "Hazme una comparación de alquiler y súper",
    ):
        facts = explicit_facts.read(still_a_comparison, today=TODAY)

        assert facts.group_by is GroupBy.CATEGORY, still_a_comparison
        assert len(facts.compared_categories) == 2, still_a_comparison


def test_a_grouping_instruction_still_outranks_the_coordinated_list() -> None:
    """The three vetoes, and the whole risk of stating a shape from an absence.

    Each of these names two or more categories and would be pinned to one total by the floor alone.
    The first spells the grouping out with a preposition and a dimension, the second asks the
    members apart without naming any dimension at all, and the third is the corner
    `plan_verifier`'s `BREAKDOWN_ANSWERED_AS_ONE_GROUP` owns.
    """
    for sentence in (
        "¿Cuánto gasté en gasolina y transporte público este mes, desglosado por categorías?",
        "¿Cuánto he gastado en ocio y suscripciones este mes por categoría?",
        "¿Cuánto llevo en gasolina y súper en cada categoría este mes?",
        "Del mes pasado, ¿cuánto fue en gasolina y cuánto en súper?",
        "Desglósame el gasto de gasolina y súper de este mes",
        "¿Cómo se reparte mi gasto entre ocio y suscripciones este mes?",
    ):
        facts = explicit_facts.read(sentence, today=TODAY)

        assert facts.group_by is not GroupBy.NONE, sentence


def test_an_enumeration_of_categories_is_not_a_grouping_instruction() -> None:
    """«en las categorías de A, B y C» lists filters; «por categorías» asks for a row each.

    The distinction is the preposition, which is why `category_breakdown` is built on
    `_ACCOUNT_BREAKDOWN`'s shape rather than on the dimension noun. Both sentences below are corpus
    paraphrases whose base asks for a single figure, so reading the noun alone as a grouping would
    break the pair — and a bare «desglose» with no dimension is the same trap from the other side,
    which is why `divides_up` and not `category_breakdown` is what catches it.
    """
    for enumeration in (
        "Necesito información sobre el total de dinero que he consumido este mes en las "
        "categorías de súper, gasolina y farmacia.",
        "¿Podría darme el resumen de los gastos que he tenido este mes en las categorías de "
        "súper, gasolina y farmacia?",
    ):
        assert explicit_facts.read(enumeration, today=TODAY).group_by is GroupBy.NONE, enumeration


def test_one_category_is_not_a_coordinated_list() -> None:
    """The floor. A single category with no grouping cue leaves the shape to the model.

    Two is where a coordination begins, and below it the only evidence for `NONE` would be silence
    — which is the thing this module refuses to read anything out of.
    """
    facts = explicit_facts.read("¿Cuánto he gastado en gasolina este mes?", today=TODAY)

    assert facts.group_by is None


def test_the_coordinated_total_overrules_an_inherited_grouping() -> None:
    """What the reading is for: a plan that arrived grouped, made to agree with the sentence."""
    facts = explicit_facts.read("¿Cuánto llevo entre gasolina y transporte público este mes?")
    grouped = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        categories=("TRANSPORT_FUEL", "TRANSPORT_PUBLIC"),
        group_by=GroupBy.CATEGORY,
    )

    repaired = explicit_facts.enforce(grouped, facts)

    assert repaired.group_by is GroupBy.NONE
    assert repaired.categories == ("TRANSPORT_FUEL", "TRANSPORT_PUBLIC")
    assert repaired.metric is grouped.metric


def test_salary_and_income_words_force_incoming_movements() -> None:
    for sentence in (
        "¿Qué nómina he recibido este año?",
        "¿Qué ingresos he tenido este mes?",
        "¿Cuánto he cobrado el mes pasado?",
    ):
        facts = explicit_facts.read(sentence, today=TODAY)
        planned = FinancialPlan(direction=Direction.ALL, period=RelativePeriod(Period.THIS_MONTH))

        assert explicit_facts.enforce(planned, facts).direction is Direction.INCOME


def test_being_charged_reads_as_money_leaving_even_though_the_verb_is_cobrar() -> None:
    """«me cobraron» matches `_INCOME` on `cobraron` and `_EXPENSE` on `me cobr`, so the reader
    used to compute both and answer None — it saw the spending and had no way to say it. The
    specific pattern is consulted first, so two characters of pronoun decide it."""
    facts = explicit_facts.read("¿Cuánto me cobraron el mes pasado?", today=TODAY)
    planned = FinancialPlan(direction=Direction.INCOME, period=RelativePeriod(Period.LAST_MONTH))

    assert facts.direction is Direction.EXPENSE
    assert explicit_facts.enforce(planned, facts).direction is Direction.EXPENSE


def test_what_i_dropped_on_something_reads_as_money_leaving() -> None:
    """«me he dejado» is ordinary Spanish for spending and the vocabulary simply lacked it, so
    neither pattern fired and the model's INCOME stood. The category question is the same gap
    read through a sentence that also names a category, and both were unsafe for one reason."""
    for sentence in (
        "¿Cuánto me he dejado este mes?",
        "¿Cuánto me he dejado en salir a comer este mes?",
    ):
        facts = explicit_facts.read(sentence, today=TODAY)
        planned = FinancialPlan(
            direction=Direction.INCOME, period=RelativePeriod(Period.THIS_MONTH)
        )

        assert facts.direction is Direction.EXPENSE
        assert explicit_facts.enforce(planned, facts).direction is Direction.EXPENSE


def test_cobrar_without_the_pronoun_is_still_money_arriving() -> None:
    """The general reading is what the specific one is carved out of, so it has to survive
    intact — otherwise «me cobraron» was fixed by breaking every payroll question."""
    for sentence in ("¿Cuánto cobré el mes pasado?", "¿Cuánto me ingresaron en junio?"):
        facts = explicit_facts.read(sentence, today=TODAY)

        assert facts.direction is Direction.INCOME


def test_an_income_question_carrying_a_spending_verb_is_still_income() -> None:
    """The risk this change creates and the reason `_LEAVING` is not `_EXPENSE`. Giving the
    reader authority over EXPENSE means it can now flip a question, and unlike the failure it
    replaces that one would show a figure. Neither of these carries a `_LEAVING` form, so the
    first reads INCOME on the income word alone and the second — which states both directions —
    reads None and leaves the model's answer where it was."""
    stated = explicit_facts.read(
        "¿Cuánto ingresé el mes pasado después de pagar el alquiler?", today=TODAY
    )
    both = explicit_facts.read(
        "¿Cuánto ingresé el mes pasado sin contar lo que gasté?", today=TODAY
    )

    assert stated.direction is Direction.INCOME
    assert both.direction is None


def test_the_expense_reading_is_not_widened_to_every_spending_word() -> None:
    """What `_LEAVING` refuses to become. «me clavaron» and «se me ha ido» plainly mean spending
    and are deliberately absent: no dataset says what they should read as, so enforcing them
    would be this module overruling the model on unmeasured vocabulary. The model gets both right
    unaided today, which is the argument for leaving them to it."""
    for sentence in ("¿Cuánto me clavaron este mes?", "¿Cuánto se me ha ido este mes?"):
        facts = explicit_facts.read(sentence, today=TODAY)

        assert facts.direction is None


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

    assert "sin extrapolar" in said
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
    assert "separadas por 16 días" in said


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


# --- a sentence that states a pair the domain cannot hold ------------------------------
#
# «gasto medio mensual por comercio» is an ordinary Spanish sentence. It asks for a monthly
# average broken down by shop — two grouping axes, where a `FinancialPlan` has one, because
# `PERIOD_AVERAGE` spends the only one on its own buckets. The sentence is not confused; the
# domain cannot express it, and `enforce` used to build the forbidden plan regardless.
#
# `enforce` must be total. It is a `T -> T` domain service with 25 call sites, twenty of them
# deterministic recognisers whose contract on failure is "return None and let the planner try".
# A refusal raised from here reaches the edge as a 422 on a sentence somebody typed.


@pytest.mark.parametrize(
    ("sentence", "metric", "group_by"),
    [
        ("gasto medio mensual por comercio", Metric.PERIOD_AVERAGE, GroupBy.COUNTERPARTY),
        ("gasto medio mensual por cada cuenta", Metric.PERIOD_AVERAGE, GroupBy.ACCOUNT),
        ("media semanal por comercio", Metric.PERIOD_AVERAGE, GroupBy.COUNTERPARTY),
        ("media diaria por cada cuenta", Metric.PERIOD_AVERAGE, GroupBy.ACCOUNT),
    ],
)
def test_a_sentence_states_a_pair_the_domain_forbids(
    sentence: str, metric: Metric, group_by: GroupBy
) -> None:
    """Both halves are read from the one sentence, and the domain refuses them together.

    The `CUMULATIVE` half of this family used to be here — «gasto acumulado por comercio» read
    `CUMULATIVE` beside `COUNTERPARTY` — and it is not a pair any more, because «acumulado» with
    no time unit after it states no series. `test_an_accumulation_by_a_dimension_is_that
    _dimensions_total` below is where those two sentences went.
    """
    facts = explicit_facts.read(sentence, today=TODAY)

    assert facts.metric is metric
    assert facts.group_by is group_by


@pytest.mark.parametrize(
    ("sentence", "group_by"),
    [
        ("gasto acumulado por comercio", GroupBy.COUNTERPARTY),
        ("gasto acumulado por cada cuenta", GroupBy.ACCOUNT),
        ("el importe acumulado por categoria", None),
    ],
)
def test_an_accumulation_by_a_dimension_is_that_dimensions_total(
    sentence: str, group_by: GroupBy | None
) -> None:
    """**The dimension survives and the invented series does not.**

    These read `CUMULATIVE` beside a non-temporal grouping, which is a cross-tabulation a plan
    cannot hold, so `_cumulative_plan` and `reshaped` both declined and the sentence fell through
    to the model. None of that was necessary: «por comercio» names a dimension and nothing here
    names a bucket, so the sentence asks for one figure per shop.

    «el importe acumulado por categoría» is the case that shows the old reading *losing* a
    grouping rather than only inventing one. Nothing in `read`'s cascade consults
    `_CATEGORY_BREAKDOWN`, so the stated dimension never reached `group_by` and the invented
    `MONTH` was returned in its place — a monthly series answering a question about categories.
    It now states no shape at all and `readers/breakdowns` claims it, which is the reader that
    knows what «por categoría» means.
    """
    facts = explicit_facts.read(sentence, today=TODAY)

    assert facts.metric is None, "no time unit is named, so no running series is stated"
    assert facts.group_by is group_by


@pytest.mark.parametrize(
    "sentence",
    [
        "gasto medio mensual por comercio",
        "gasto medio mensual por cada cuenta",
        "media semanal por comercio",
        "media diaria por cada cuenta",
        "gasto acumulado por comercio",
        "gasto acumulado por cada cuenta",
        "compara el gasto acumulado de restaurantes y supermercado",
        "¿En qué categoría gasto más al mes de media?",
    ],
)
def test_enforce_never_raises_on_a_sentence_a_person_can_type(sentence: str) -> None:
    """The plan carries one half, the sentence states the other. This used to raise."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        metric=Metric.PERIOD_AVERAGE,
        group_by=GroupBy.MONTH,
    )

    checked = explicit_facts.enforce(plan, explicit_facts.read(sentence, today=TODAY))

    # Whatever comes back is a plan the domain accepts, and never the forbidden pair.
    assert not (
        checked.metric in {Metric.PERIOD_AVERAGE, Metric.CUMULATIVE}
        and checked.group_by not in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
    )


@pytest.mark.parametrize(
    "sentence",
    ["¿y por comercio?", "¿y por cada cuenta?", "¿En qué categoría gasté más?"],
)
@pytest.mark.parametrize("inherited", [Metric.PERIOD_AVERAGE, Metric.CUMULATIVE])
def test_a_grouping_the_sentence_states_meets_an_inherited_metric(
    sentence: str, inherited: Metric
) -> None:
    """The sentence states only the grouping. The metric it cannot live beside was inherited."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        metric=inherited,
        group_by=GroupBy.MONTH,
    )

    explicit_facts.enforce(plan, explicit_facts.read(sentence, today=TODAY))


def test_a_coherent_pair_is_not_refused_by_the_order_it_is_applied_in() -> None:
    """The sentence states a valid pair and the plan carries a metric that pair replaces.

    Nothing here is contradictory: the plan the sentence asks for is grouped by category and
    counted, which the domain accepts. `enforce` raised anyway, because it applied the grouping
    while the *old* metric was still in place and the plan passed through a state neither the
    sentence nor the model ever asked for.
    """
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        metric=Metric.CUMULATIVE,
        group_by=GroupBy.MONTH,
    )
    facts = explicit_facts.read(
        "compara cuantos pagos hice en restaurantes y en supermercado", today=TODAY
    )
    assert (facts.group_by, facts.metric) == (GroupBy.CATEGORY, Metric.COUNT)

    checked = explicit_facts.enforce(plan, facts)

    assert checked.group_by is GroupBy.CATEGORY
    assert checked.metric is Metric.COUNT


def test_a_group_limit_travels_with_the_grouping_it_limits() -> None:
    """ "¿Y por mes?" over a top-5 ranking. The limit belonged to the grouping being replaced.

    `groupLimit` is a rider on `group_by` rather than a fact of its own — the domain refuses it
    beside a temporal grouping — so replacing the grouping has to carry it, exactly as the
    window's two fields already travel as a pair.
    """
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        group_by=GroupBy.MERCHANT,
        group_limit=5,
    )

    checked = explicit_facts.enforce(plan, explicit_facts.read("¿y por mes?", today=TODAY))

    assert checked.group_by is GroupBy.MONTH
    assert checked.group_limit is None


def test_a_draft_refuses_the_pair_here_rather_than_a_turn_later() -> None:
    """A draft relaxes the window and nothing else, and PERIOD_AVERAGE was missing from its rules.

    `enforce` built the pair without complaint, `_next_state` stored it on the conversation, and
    the raise arrived on the *reply* to our own clarification, where `with_window` finally checked.
    """
    with pytest.raises(InvalidValueError):
        FinancialPlanDraft(
            direction=Direction.EXPENSE,
            metric=Metric.PERIOD_AVERAGE,
            group_by=GroupBy.COUNTERPARTY,
        )

    # And so `enforce` now has something to catch, instead of handing on a poisoned draft.
    checked = explicit_facts.enforce(
        FinancialPlanDraft(direction=Direction.EXPENSE),
        explicit_facts.read("gasto medio mensual por comercio", today=TODAY),
    )
    checked.with_window(RelativePeriod(period=Period.THIS_YEAR))


@pytest.mark.parametrize("sentence", ["esos cinco", "y esos", "de esa categoría", "en esa cuenta"])
def test_a_pronoun_after_a_monthly_average_ungroups_safely(sentence: str) -> None:
    """No contradictory sentence anywhere — the pair arrives one field at a time, two turns apart.

    After "¿cuál es mi gasto medio mensual?" the plan is `PERIOD_AVERAGE` by `MONTH`. A pronoun
    reference resets the grouping to `NONE` because the sentence names none, and the average is
    inherited from the turn before. `reshaped` is what stops the reset landing on its own.
    """
    monthly_average = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        metric=Metric.PERIOD_AVERAGE,
        group_by=GroupBy.MONTH,
    )
    facts = explicit_facts.read(sentence, today=TODAY)

    reshaped = explicit_facts.reshaped(
        monthly_average,
        group_by=facts.group_by or GroupBy.NONE,
        metric=facts.metric or monthly_average.metric,
    )

    # The average survives with the buckets it needs, rather than the reset producing a
    # per-period average with no period to average over.
    assert reshaped.metric is Metric.PERIOD_AVERAGE
    assert reshaped.group_by is GroupBy.MONTH


@pytest.mark.parametrize(
    "sentence",
    [
        "gasto acumulado por comercio",
        "gasto acumulado por cada cuenta",
        "compara el gasto acumulado de restaurantes y supermercado",
    ],
)
def test_a_running_total_by_shop_is_not_a_422(sentence: str) -> None:
    """The live one. `_cumulative_plan` built the forbidden plan before `enforce` was reached.

    The capability guard refuses the `PERIOD_AVERAGE` half of this family, which is why
    «gasto medio mensual por comercio» comes back as a refusal over HTTP. It does not refuse
    "acumulado", and it runs after every deterministic recogniser anyway — so this sentence
    reached `FinancialPlan(group_by=COUNTERPARTY, metric=CUMULATIVE)` and raised out of the
    handler as `invalid_input`, which the edge maps to 422.
    """
    from finguard_orchestrator.application.queries.readers.metrics import _cumulative_plan

    assert (
        _cumulative_plan(
            AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
        )
        is None
    ), "declines instead of building a plan the domain refuses"


def test_an_exclusion_matches_a_merchant_the_plan_padded() -> None:
    """The plan's merchant name was compared unstripped and the invariant compares it stripped."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        merchants=(" Mercadona ",),
    )

    checked = explicit_facts.enforce(plan, explicit_facts.read("gastos sin Mercadona", today=TODAY))

    assert checked.merchants == ()
    assert checked.excluded_merchants == ("mercadona",)
