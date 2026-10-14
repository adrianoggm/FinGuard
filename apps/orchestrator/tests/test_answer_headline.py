"""What the first line of an answer says.

From the reported conversation:

    "De esos gastos, ¿cuántos fueron en el supermercado?"
        → "449,62 € el mes pasado, en 8 movimientos."
    "Y de cine / ocio"
        → "44,59 € el mes pasado, en 2 movimientos."

Both figures are right and neither sentence says what it is a figure *of*. The filter that makes
the number mean something — the category, the shop — was in the plan and only visible behind "Ver la
consulta", spelled `GROCERIES`. Two answers in a row differing only in a number no one can attribute
is what makes the conversation feel poor even when it is correct.

The filter is named in Spanish, from the same vocabulary the planner's prompt uses, because
`GROCERIES` is an identifier and "supermercado" is what the user said.
"""

from dataclasses import replace
from datetime import date, datetime

from finguard_orchestrator.application.engine.executor import RankingRun
from finguard_orchestrator.application.engine.presenter import answered
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

LAST_MONTH = RelativePeriod(period=Period.LAST_MONTH)


def plan(**kwargs: object) -> FinancialPlan:
    fields: dict[str, object] = {"direction": Direction.EXPENSE, "period": LAST_MONTH}
    fields.update(kwargs)
    return FinancialPlan(**fields)  # type: ignore[arg-type]


def answer(total: int = 44_962, count: int = 8) -> QueryAnswer:
    return QueryAnswer(currency="EUR", total_minor=total, movement_count=count)


def test_the_category_is_named_in_the_headline() -> None:
    said = answer_writer.write(answer(), plan(categories=("GROCERIES",)))

    assert said == "449,62 € en supermercado el mes pasado, en 8 movimientos."


def test_two_categories_are_both_named() -> None:
    said = answer_writer.write(answer(), plan(categories=("GROCERIES", "TRANSPORT_FUEL")))

    assert "en supermercado y gasolina el mes pasado" in said


def test_three_categories_read_as_a_list() -> None:
    said = answer_writer.write(answer(), plan(categories=("GROCERIES", "TRANSPORT_FUEL", "HEALTH")))

    assert "en supermercado, gasolina y salud el mes pasado" in said


def test_the_shop_is_named_when_the_question_was_about_one() -> None:
    """A merchant is more specific than a category, so it wins the headline. Both at once would
    read as a list of two unrelated things when it is one filter narrowing another."""
    said = answer_writer.write(answer(), plan(merchants=("Mercadona",)))

    assert said == "449,62 € en Mercadona el mes pasado, en 8 movimientos."


def test_a_question_with_no_filter_is_unchanged() -> None:
    """Most questions name none, and inventing a phrase for "everything" would add words that say
    nothing — which is the failure this fixes, in the other direction."""
    said = answer_writer.write(answer(), plan())

    assert said == "449,62 € el mes pasado, en 8 movimientos."


def test_a_count_names_the_filter_too() -> None:
    said = answer_writer.write(answer(count=2), plan(categories=("LEISURE",), metric=Metric.COUNT))

    assert said == "2 movimientos en ocio el mes pasado."


def test_an_average_is_derived_from_the_exact_sum_and_count() -> None:
    said = answer_writer.write(
        answer(total=1_001, count=4),
        plan(categories=("RESTAURANTS",), metric=Metric.AVERAGE),
    )

    assert said == (
        "El importe medio por movimiento en restaurantes el mes pasado fue de 2,50 €, "
        "calculado sobre 4 movimientos."
    )


def test_an_average_rounds_half_away_from_zero_in_minor_units() -> None:
    said = answer_writer.write(answer(total=101, count=2), plan(metric=Metric.AVERAGE))

    assert "0,51 €" in said


def test_an_empty_period_has_no_per_movement_average() -> None:
    said = answer_writer.comparison_figure(answer(total=0, count=0), plan(metric=Metric.AVERAGE))

    assert said == "sin movimientos"


def test_a_monthly_average_includes_calendar_months_with_no_movements() -> None:
    answered = QueryAnswer(
        currency="EUR",
        total_minor=120_000,
        movement_count=9,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        # No row for February or May: they still belong in the six-month divisor.
        groups=(
            Group("2026-01", 30_000, 2),
            Group("2026-03", 30_000, 2),
            Group("2026-04", 30_000, 2),
            Group("2026-06", 30_000, 3),
        ),
    )

    said = answer_writer.write(
        answered,
        FinancialPlan(
            direction=Direction.EXPENSE,
            date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 6, 30)),
            categories=("GROCERIES",),
            group_by=GroupBy.MONTH,
            metric=Metric.PERIOD_AVERAGE,
        ),
    )

    assert "gasto medio por mes en supermercado" in said
    assert "200,00 €" in said
    assert "6 meses" in said


def test_a_rolling_twelve_month_average_divides_by_twelve_not_endpoint_labels() -> None:
    answered = QueryAnswer(
        currency="EUR",
        total_minor=1_200_000,
        movement_count=20,
        period_start=date(2025, 8, 6),
        period_end=date(2026, 8, 6),
    )
    monthly = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        categories=("GROCERIES",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    said = answer_writer.write(answered, monthly)

    assert "1.000,00 €" in said
    assert "12 meses" in said


def test_a_monthly_income_average_is_not_described_as_spending() -> None:
    answered = QueryAnswer(
        currency="EUR",
        total_minor=1_200_000,
        movement_count=4,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 4, 30),
    )
    salary = FinancialPlan(
        direction=Direction.INCOME,
        date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 4, 30)),
        categories=("SALARY",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    said = answer_writer.write(answered, salary)

    assert "ingreso medio por mes" in said
    assert "gasto medio" not in said


def test_a_category_comparison_shows_an_average_for_each_category() -> None:
    grouped = QueryAnswer(
        currency="EUR",
        total_minor=2_000,
        movement_count=4,
        groups=(Group("GROCERIES", 1_500, 3), Group("RESTAURANTS", 500, 1)),
    )

    said = answer_writer.write(
        grouped,
        plan(
            categories=("GROCERIES", "RESTAURANTS"),
            group_by=GroupBy.CATEGORY,
            metric=Metric.AVERAGE,
        ),
    )

    assert "supermercado: media 5,00 € (3)" in said
    assert "restaurantes: media 5,00 € (1)" in said


def test_an_empty_answer_says_what_it_looked_for() -> None:
    """ "No he encontrado movimientos el mes pasado" invites the user to believe there were none.
    Naming the filter is what lets them see the query was wrong instead."""
    said = answer_writer.write(
        QueryAnswer(currency="EUR", total_minor=0, movement_count=0),
        plan(categories=("HOUSING_RENT",)),
    )

    assert said == (
        "No he encontrado movimientos en alquiler el mes pasado. "
        "Puedes buscar sin categoría o ampliar el periodo."
    )


def test_a_category_outside_the_glossary_falls_back_to_its_identifier() -> None:
    """A deployment can carry categories nobody wrote words for. Printing the identifier is worse
    than a Spanish name and much better than inventing one."""
    said = answer_writer.write(answer(), plan(categories=("PETS",)))

    assert "en PETS el mes pasado" in said


def test_a_breakdown_keeps_its_lines() -> None:
    """The headline gains the filter; everything under it is untouched."""
    grouped = QueryAnswer(
        currency="EUR",
        total_minor=8_000,
        movement_count=2,
        groups=(Group("GROCERIES", 8_000, 2),),
    )
    said = answer_writer.write(grouped, plan(group_by=GroupBy.CATEGORY, categories=("GROCERIES",)))

    assert said.splitlines()[0].startswith("80,00 € en supermercado")


def test_a_single_account_breakdown_answers_whether_everything_came_from_it() -> None:
    grouped = QueryAnswer(
        currency="EUR",
        total_minor=713_320,
        movement_count=112,
        groups=(Group("Cuenta corriente", 713_320, 112),),
    )

    said = answer_writer.write(grouped, plan(group_by=GroupBy.ACCOUNT))

    assert said.startswith("Todos los 112 movimientos encontrados")
    assert "están en Cuenta corriente" in said


def test_one_movement_in_one_account_is_singular() -> None:
    grouped = QueryAnswer(
        currency="EUR",
        total_minor=244_287,
        movement_count=1,
        groups=(Group("Cuenta corriente", 244_287, 1),),
    )

    said = answer_writer.write(grouped, plan(group_by=GroupBy.ACCOUNT))

    assert said.startswith("El movimiento encontrado")
    assert "Todos los 1" not in said


def test_an_exclusion_outside_the_category_filter_says_the_figure_did_not_change() -> None:
    """The reported turn. "Top 3 comercios de supermercado en julio" was answered, then "sin
    contar el alquiler" came back with every figure identical under a headline that had grown
    ", sin alquiler". Rent was never inside a supermarket filter, so the exclusion could not have
    moved a cent, and the only way a reader could tell was that nothing had changed."""
    said = answer_writer.write(
        answer(), plan(categories=("GROCERIES",), excluded_categories=("HOUSING_RENT",))
    )

    assert said == (
        "449,62 € en supermercado el mes pasado, en 8 movimientos.\n"
        "No hay alquiler en supermercado, así que la cifra no cambia."
    )


def test_the_domain_names_the_exclusions_that_could_not_have_bitten() -> None:
    """The predicate the writer reads. A plan that counts only some categories cannot lose a
    movement to an exclusion naming another, and `check_filters` already refuses a category that
    is both included and excluded — so with a category filter every exclusion on it is inert."""
    filtered = plan(categories=("GROCERIES",), excluded_categories=("HOUSING_RENT", "LEISURE"))
    unfiltered = plan(excluded_categories=("HOUSING_RENT",))

    assert answer_writer.inert_exclusions(filtered) == ("HOUSING_RENT", "LEISURE")
    assert answer_writer.inert_exclusions(unfiltered) == ()
    assert answer_writer.inert_exclusions(plan(categories=("GROCERIES",))) == ()


def test_two_inert_exclusions_are_both_named_under_a_negation() -> None:
    """ "Sin alquiler y ocio" is not how Spanish enumerates under a negation, and this is read as a
    sentence rather than as the filter clause."""
    said = answer_writer.write(
        answer(),
        plan(categories=("GROCERIES",), excluded_categories=("HOUSING_RENT", "LEISURE")),
    )

    assert said.endswith("No hay alquiler ni ocio en supermercado, así que la cifra no cambia.")


def test_an_exclusion_with_no_category_filter_is_stated_and_not_explained_away() -> None:
    """The half that must not change, and the more important one. "Sin contar el alquiler" over a
    total with no category filter genuinely removes rent — 975,97 € against 1.895,97 €, 27
    movements against 28 — so the answer names the exclusion and claims nothing about the figure
    having stayed the same. A fix that made every exclusion silent would be worse than the bug."""
    said = answer_writer.write(answer(), plan(excluded_categories=("HOUSING_RENT",)))
    # A shop filter is not a category filter: nothing says a Mercadona charge cannot be filed
    # under rent, so this exclusion is not provably inert either.
    shop = answer_writer.write(
        answer(), plan(merchants=("Mercadona",), excluded_categories=("HOUSING_RENT",))
    )

    assert said == "449,62 € sin alquiler el mes pasado, en 8 movimientos."
    assert shop == "449,62 € en Mercadona, sin alquiler el mes pasado, en 8 movimientos."


def test_an_excluded_shop_is_still_named_beside_an_inert_category() -> None:
    """A category filter says nothing about which shops its movements are in, so excluding a
    merchant can still remove rows and stays in the clause."""
    said = answer_writer.write(
        answer(),
        plan(
            categories=("GROCERIES",),
            excluded_categories=("HOUSING_RENT",),
            excluded_merchants=("Mercadona",),
        ),
    )

    assert said.startswith("449,62 € en supermercado, sin Mercadona el mes pasado")
    assert said.endswith("No hay alquiler en supermercado, así que la cifra no cambia.")


def test_a_group_ranking_stops_claiming_a_filter_that_could_not_have_moved_a_row() -> None:
    """The shape that reproduced over HTTP: a ranking of shops inside one category, where three
    identical rows came back under a headline claiming the rent had been taken out."""
    ranked_shops = QueryAnswer(
        currency="EUR",
        total_minor=42_471,
        movement_count=7,
        groups=(
            Group("Carrefour", 21_828, 3),
            Group("Mercadona", 13_121, 2),
            Group("Alcampo", 7_522, 2),
        ),
    )

    said = answer_writer.write(
        ranked_shops,
        plan(
            categories=("GROCERIES",),
            excluded_categories=("HOUSING_RENT",),
            group_by=GroupBy.MERCHANT,
            group_limit=3,
        ),
    )

    assert said.splitlines()[0] == (
        "Tus 3 comercios con mayor importe total en supermercado el mes pasado son:"
    )
    assert said.splitlines()[-1] == "No hay alquiler en supermercado, así que la cifra no cambia."
    assert "  · Carrefour: 218,28 € (3)" in said


def test_a_movement_ranking_says_when_its_exclusion_could_not_have_bitten() -> None:
    """`TopMovements` carries the same two filters and reaches the same writer helper."""
    biggest = Movement(
        occurred_at=datetime(2026, 7, 14, 10, 0),
        description="Carrefour",
        amount_minor=8_940,
        direction="EXPENSE",
        merchant_name="Carrefour",
        currency="EUR",
    )
    inert = TopMovements(
        direction=Direction.EXPENSE,
        period=LAST_MONTH,
        categories=("GROCERIES",),
        excluded_categories=("HOUSING_RENT",),
    )
    real = TopMovements(
        direction=Direction.EXPENSE,
        period=LAST_MONTH,
        excluded_categories=("HOUSING_RENT",),
    )

    assert answer_writer.ranked(biggest, inert) == (
        "Tu mayor gasto en supermercado el mes pasado fue de 89,40 €: Carrefour, el 14 de julio.\n"
        "No hay alquiler en supermercado, así que la cifra no cambia."
    )
    assert answer_writer.ranked(biggest, real) == (
        "Tu mayor gasto sin alquiler el mes pasado fue de 89,40 €: Carrefour, el 14 de julio."
    )


def test_an_answer_with_no_figure_does_not_explain_a_figure_that_did_not_change() -> None:
    """Nothing was counted, so there is no "cifra" for the note to be about, and the invitation to
    widen the search is the whole answer."""
    said = answer_writer.write(
        QueryAnswer(currency="EUR", total_minor=0, movement_count=0),
        plan(categories=("GROCERIES",), excluded_categories=("HOUSING_RENT",)),
    )

    assert said == (
        "No he encontrado movimientos en supermercado el mes pasado. "
        "Puedes buscar sin categoría o ampliar el periodo."
    )


# Ruling R31. «Con Marta» said nothing about which way the money went, so «¿y ella a mí?» was
# answered with a sentence identical to the one before it, over the opposite figure.


def test_money_sent_to_a_person_says_to_whom() -> None:
    said = answer_writer.write(answer(), plan(counterparties=("Marta",)))
    assert said == "449,62 € a Marta el mes pasado, en 8 movimientos."


def test_money_received_from_a_person_says_from_whom() -> None:
    said = answer_writer.write(
        answer(), plan(direction=Direction.INCOME, counterparties=("Marta",))
    )
    assert said == "449,62 € de Marta el mes pasado, en 8 movimientos."


def test_money_both_ways_with_a_person_is_still_with_them() -> None:
    said = answer_writer.write(answer(), plan(direction=Direction.ALL, counterparties=("Marta",)))
    assert said == "449,62 € con Marta el mes pasado, en 8 movimientos."


def test_the_direction_reads_in_every_headline_that_names_the_person() -> None:
    """Every caller puts the filter right after its own noun, so a preposition is what reads in
    all of them; a participle would have to agree with «1 movimiento» and «importe medio». (A
    ranking carries no person, so it has no such line.)"""
    received = plan(direction=Direction.INCOME, counterparties=("Marta",))
    counted = answer_writer.write(answer(count=1), replace(received, metric=Metric.COUNT))
    empty = answer_writer.write(answer(total=0, count=0), received)
    averaged = answer_writer.write(answer(), replace(received, metric=Metric.AVERAGE))
    assert counted == "1 movimiento de Marta el mes pasado."
    assert empty.startswith("No he encontrado movimientos de Marta el mes pasado.")
    assert averaged.startswith("El importe medio por movimiento de Marta el mes pasado fue de")


def test_the_planners_line_is_unchanged() -> None:
    """`describe` is the line the planner reads (`prompt.py`); the person's line changed, it did
    not."""
    assert answer_writer.describe(plan(direction=Direction.INCOME, counterparties=("Marta",))) == (
        "el mes pasado, income, Marta"
    )


def test_a_requested_rank_returns_only_that_movement_and_its_amount() -> None:
    first = Movement(
        occurred_at=datetime(2026, 7, 14, 10, 0),
        description="Carrefour",
        amount_minor=8_940,
        direction="EXPENSE",
        merchant_name="Carrefour",
        currency="EUR",
    )
    second = Movement(
        occurred_at=datetime(2026, 7, 12, 10, 0),
        description="Mercadona",
        amount_minor=7_250,
        direction="EXPENSE",
        merchant_name="Mercadona",
        currency="EUR",
    )
    ranking = TopMovements(direction=Direction.EXPENSE, period=LAST_MONTH, rank=2)

    result = answered(
        RankingRun(ranking, (first, second)),
        scope=(),
        account_names={},
        reporting_timezone="Europe/Madrid",
    )

    assert result.text.startswith("El segundo fue de 72,50")
    assert "Mercadona" in result.text
    assert "los 2 mayores gastos" not in result.text
    assert result.total_minor == 7_250
    assert result.movement_count == 1
    assert [movement.merchant_name for movement in result.movements] == ["Mercadona"]


def test_a_missing_requested_rank_does_not_leak_the_previous_movement_as_its_result() -> None:
    first = Movement(
        occurred_at=datetime(2026, 7, 14, 10, 0),
        description="Carrefour",
        amount_minor=8_940,
        direction="EXPENSE",
        merchant_name="Carrefour",
        currency="EUR",
    )
    ranking = TopMovements(direction=Direction.EXPENSE, period=LAST_MONTH, rank=2)

    result = answered(
        RankingRun(ranking, (first,)),
        scope=(),
        account_names={},
        reporting_timezone="Europe/Madrid",
    )

    assert result.text.startswith("No hay el segundo movimiento")
    assert result.total_minor is None
    assert result.movement_count == 0
    assert result.movements == []
