"""Asking for one movement by size, and saying which one it was.

    "¿Cuál fue el gasto más caro del año en gasolina?"
    "Y el segundo."

The capability the assistant refused until now. Refusing was right — `Metric` is SUM or COUNT, so a
total in place of a maximum is a real number about a different question — and this is what replaces
the refusal rather than making it politer.

A separate value object rather than a wider `FinancialPlan`, for the reason banking's side already
records: a maximum is one row with an identity, a date and a shop. It shares the filters and shares
nothing else. `group_by` and `metric` are absent because they mean nothing here, and a field with no
meaning is a field something will eventually set.

The rank is what makes the follow-up cheap: "y el segundo" changes one number and re-derives nothing
about the question.
"""

from datetime import datetime

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.query_answer import Movement
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import MAX_RANK, TopMovements

THIS_YEAR = RelativePeriod(period=Period.THIS_YEAR)


def ranking(**kwargs: object) -> TopMovements:
    fields: dict[str, object] = {"direction": Direction.EXPENSE, "period": THIS_YEAR}
    fields.update(kwargs)
    return TopMovements(**fields)  # type: ignore[arg-type]


CEPSA = Movement(
    occurred_at=datetime(2026, 3, 14, 10, 0),
    description="Repostaje",
    amount_minor=8_240,
    direction="EXPENSE",
    merchant_name="Cepsa",
    category="TRANSPORT_FUEL",
    # A ranked movement always carries one: it *is* the answer, so it has to say what its amount
    # is denominated in. A fixture without it now renders a bare figure, which is the point.
    currency="EUR",
)


# --- the plan ------------------------------------------------------------------------


def test_a_ranking_needs_exactly_one_window() -> None:
    """The same invariant as a plan, and for the same reason: two windows are two questions, and
    which one wins would depend on the reader."""
    with pytest.raises(InvalidValueError):
        TopMovements(direction=Direction.EXPENSE)


def test_the_first_is_the_largest() -> None:
    assert ranking().rank == 1


@pytest.mark.parametrize("rank", [0, -1, MAX_RANK + 1])
def test_a_rank_outside_its_bounds_is_refused(rank: int) -> None:
    """It becomes an OFFSET in banking. "El gasto número cuatro mil" is a scan, not a question, and
    zero is not a position at all."""
    with pytest.raises(InvalidValueError):
        ranking(rank=rank)


def test_the_next_one_down_is_the_same_question() -> None:
    """What "y el segundo" costs: one field. Nothing about the window, the direction or the
    category is read again, which is what makes it robust to how the follow-up is phrased."""
    second = ranking(categories=("TRANSPORT_FUEL",)).next_rank()

    assert second.rank == 2
    assert second.categories == ("TRANSPORT_FUEL",)
    assert second.period == THIS_YEAR


def test_the_filters_are_checked_like_a_plan_s() -> None:
    """Same checks, shared: a ranking that accepted a category a plan refuses would be a way past
    the plan's invariants."""
    with pytest.raises(InvalidValueError):
        ranking(categories=("no válida",))


# --- the sentence --------------------------------------------------------------------


def test_the_largest_is_named_with_its_shop_and_its_date() -> None:
    """A figure alone would send the user to the movement list to work out which charge it was."""
    said = answer_writer.ranked(CEPSA, ranking(categories=("TRANSPORT_FUEL",)))

    assert said == (
        "Tu mayor gasto en gasolina en lo que va de año fue de 82,40 €: Cepsa, el 14 de marzo."
    )


def test_the_second_reads_as_a_follow_up() -> None:
    """It answers "y el segundo", so it does not repeat the whole question — the user is looking at
    the previous answer."""
    said = answer_writer.ranked(CEPSA, ranking(categories=("TRANSPORT_FUEL",), rank=2))

    assert said == "El segundo fue de 82,40 €: Cepsa, el 14 de marzo."


def test_a_position_with_no_ordinal_is_said_as_a_number() -> None:
    """Spanish ordinals stop being useful somewhere around the tenth. "El número 12" is what a
    person would say, and inventing "duodécimo" would read as a machine trying too hard."""
    said = answer_writer.ranked(CEPSA, ranking(rank=12))

    assert said.startswith("El número 12 fue de 82,40 €")


def test_no_filter_leaves_the_sentence_general() -> None:
    said = answer_writer.ranked(CEPSA, ranking())

    assert said.startswith("Tu mayor gasto en lo que va de año fue de 82,40 €")


def test_income_is_not_called_a_gasto() -> None:
    """ "Tu mayor gasto" for a salary would be wrong in the one word that matters."""
    salary = Movement(
        occurred_at=datetime(2026, 7, 28, 9, 0),
        description="Nómina",
        amount_minor=244_287,
        direction="INCOME",
        merchant_name="Innovasur SL",
        category="SALARY",
    )

    said = answer_writer.ranked(salary, ranking(direction=Direction.INCOME))

    assert "mayor ingreso" in said
    assert "gasto" not in said


def test_a_movement_with_no_shop_falls_back_to_its_description() -> None:
    """Not every movement has a merchant. Naming nothing would leave "fue de 82,40 €:" hanging."""
    unnamed = Movement(
        occurred_at=datetime(2026, 3, 14, 10, 0),
        description="Transferencia recibida",
        amount_minor=8_240,
        direction="EXPENSE",
    )

    assert "Transferencia recibida" in answer_writer.ranked(unnamed, ranking())


def test_nothing_at_that_position_says_so() -> None:
    """ "El quinto" of three movements has no answer, and the honest sentence says which position it
    could not find rather than reporting zero."""
    said = answer_writer.ranked(None, ranking(rank=5))

    assert "no hay" in said.lower()
    assert "0,00" not in said
