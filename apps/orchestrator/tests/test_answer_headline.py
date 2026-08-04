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

from datetime import datetime

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

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


def test_an_empty_answer_says_what_it_looked_for() -> None:
    """ "No he encontrado movimientos el mes pasado" invites the user to believe there were none.
    Naming the filter is what lets them see the query was wrong instead."""
    said = answer_writer.write(
        QueryAnswer(currency="EUR", total_minor=0, movement_count=0),
        plan(categories=("HOUSING_RENT",)),
    )

    assert said == "No he encontrado movimientos en alquiler el mes pasado."


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


def test_the_movements_behind_it_are_still_the_evidence() -> None:
    """Unrelated to the headline, and asserted here so a change to one is not read as licence to
    change the other."""
    with_movements = QueryAnswer(
        currency="EUR",
        total_minor=2_211,
        movement_count=1,
        movements=(
            Movement(
                occurred_at=datetime(2026, 7, 28, 10, 0),
                description="Cafe Central",
                amount_minor=2_211,
                direction="EXPENSE",
                merchant_name="Cafe Central",
                category="RESTAURANTS",
            ),
        ),
    )

    assert answer_writer.cite(with_movements) != []
