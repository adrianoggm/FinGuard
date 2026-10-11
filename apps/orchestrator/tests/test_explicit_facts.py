"""The model may not contradict what the user wrote down.

The worst answer in the reported conversation was not a misunderstanding. It was this:

    "¿A qué personas hice bizum en los últimos 5 meses?"   → five months
    "¿Cuánto he gastado en los últimos 3 meses en alquiler?" → answered over five months

The model emitted `changed: ["categories"]`, the merge is arithmetic on that list, and the window
was therefore inherited. Every layer behaved correctly and the number was about a period nobody had
asked for — which is the failure this whole product exists to prevent, since the figure itself was
real and there was nothing to disbelieve it with.

The fix is not a wider regex. It is that the sentence outranks the model on the small set of things
it states outright, and this file is the check on that ordering: what the sentence reads as, and
the plan `enforce` makes agree with it.
"""

from datetime import date, datetime

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import explicit_facts
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

TODAY = date(2026, 8, 4)

FIVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=5)
THREE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=3)
TWELVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=12)
ALL_OF_2025 = AbsoluteRange(start=date(2025, 1, 1), end=date(2025, 12, 31))


def enforced[P: FinancialPlan | TopMovements](sentence: str, plan: P) -> P:
    """The plan a model built, made to agree with what the sentence states."""
    return explicit_facts.enforce(plan, explicit_facts.read(sentence, today=TODAY))


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
    "sentence",
    [
        # N-H1 (Wave 3 hardening, round 2): "límite" is on `merchant_words`'s closed
        # never-a-merchant list, so a tail of exactly that word names no shop -- before this fix
        # the exclusion reader claimed it anyway, and a holder was told a total was computed
        # excluding a shop called "limite" that does not exist.
        "sin límite",
        "Cuánto gasté sin límite",
        "Cuánto gasté sin límite este mes",
    ],
)
def test_the_exclusion_reader_also_consults_never_a_merchant(sentence: str) -> None:
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


def test_showing_receipts_is_recurrence_but_asking_their_total_is_not() -> None:
    assert explicit_facts.asks_recurrence("Muéstrame mis recibos")
    assert not explicit_facts.asks_recurrence("¿Cuánto he pagado de recibos domiciliados este año?")


# --- the shape the question asks for --------------------------------------------------


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


HEALTH_LAST_QUARTER = FinancialPlan(
    direction=Direction.EXPENSE, period=THREE_MONTHS, categories=("HEALTH",)
)


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
