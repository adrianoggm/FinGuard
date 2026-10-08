"""Grounding: names as the model wrote them, matched against the holder's catalogues.

Pure lookups. A name that matches nothing, or more than one thing, comes back as `Unknown` so the
engine can ask about it by name; it is never dropped and never guessed.
"""

from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.domain.engine.grounding import Grounds, Unknown, ground
from finguard_orchestrator.domain.engine.operations import PlanFields
from finguard_orchestrator.domain.engine.state import Missing
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange

JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))

GROUNDS = Grounds(
    categories=frozenset({"RESTAURANTS", "LEISURE", "TRANSPORT_FUEL"}),
    accounts=(
        Holding(UUID(int=11), "CURRENT", "Cuenta corriente", "EUR", 0),
        Holding(UUID(int=12), "SAVINGS", "Hucha", "EUR", 0),
        Holding(UUID(int=13), "ASSET", "Fondo indexado", "EUR", 0),
    ),
    contacts=(
        Contact(UUID(int=7), "Marta Ruiz", "600"),
        Contact(UUID(int=8), "Marta Solis Vega", "601"),
        Contact(UUID(int=9), "Javi Nuñez", "602"),
        Contact(UUID(int=10), "Aitor", "603"),
        Contact(UUID(int=14), "Aitor Pérez", "604"),
    ),
)


def test_exact_names_match_whatever_their_case_and_accents() -> None:
    said = PlanFields(categories=("restaurants",), people=("javi núñez",), accounts=("HUCHA",))

    assert ground(said, GROUNDS) == PlanFields(
        categories=("RESTAURANTS",), people=("Javi Nuñez",), accounts=("Hucha",)
    )


def test_a_category_key_matches_with_spaces_for_its_underscores() -> None:
    assert ground(PlanFields(categories=("transport fuel",)), GROUNDS) == PlanFields(
        categories=("TRANSPORT_FUEL",)
    )


@pytest.mark.parametrize(
    ("said", "found"),
    [("Javi", "Javi Nuñez"), ("marta r", "Marta Ruiz"), ("solis", "Marta Solis Vega")],
)
def test_a_name_that_begins_exactly_one_contact_matches_it(said: str, found: str) -> None:
    assert ground(PlanFields(people=(said,)), GROUNDS) == PlanFields(people=(found,))


@pytest.mark.parametrize(
    ("said", "found"), [("corriente", "Cuenta corriente"), ("fondo", "Fondo indexado")]
)
def test_the_start_of_any_word_of_an_account_name_matches_it(said: str, found: str) -> None:
    assert ground(PlanFields(accounts=(said,)), GROUNDS) == PlanFields(accounts=(found,))


def test_an_exact_name_wins_over_the_longer_names_it_begins() -> None:
    assert ground(PlanFields(people=("aitor",)), GROUNDS) == PlanFields(people=("Aitor",))


def test_a_name_that_begins_two_contacts_is_unknown_never_a_guess() -> None:
    assert ground(PlanFields(people=("Marta",)), GROUNDS) == Unknown(Missing.PERSON, "Marta")


@pytest.mark.parametrize(
    ("said", "unknown"),
    [
        (PlanFields(people=("Pepe",)), Unknown(Missing.PERSON, "Pepe")),
        (PlanFields(accounts=("Nómina",)), Unknown(Missing.ACCOUNT, "Nómina")),
        (PlanFields(categories=("TRAVEL",)), Unknown(Missing.SUBJECT, "TRAVEL")),
        (PlanFields(excluded_categories=("TRAVEL",)), Unknown(Missing.SUBJECT, "TRAVEL")),
    ],
)
def test_a_name_that_matches_nothing_is_unknown_as_written(
    said: PlanFields, unknown: Unknown
) -> None:
    assert ground(said, GROUNDS) == unknown


@pytest.mark.parametrize(
    "said", [PlanFields(people=("rta",)), PlanFields(accounts=("ente",))], ids=["person", "account"]
)
def test_a_fragment_inside_a_word_matches_nothing(said: PlanFields) -> None:
    assert isinstance(ground(said, GROUNDS), Unknown)


def test_a_blank_name_is_unknown_rather_than_the_prefix_of_everything() -> None:
    assert ground(PlanFields(accounts=("  ",)), GROUNDS) == Unknown(Missing.ACCOUNT, "  ")


def test_one_unknown_name_among_known_ones_is_still_unknown() -> None:
    said = PlanFields(people=("Marta Ruiz", "Pepe"))

    assert ground(said, GROUNDS) == Unknown(Missing.PERSON, "Pepe")


def test_the_same_name_twice_is_one_filter() -> None:
    said = PlanFields(accounts=("hucha", "Hucha"))

    assert ground(said, GROUNDS) == PlanFields(accounts=("Hucha",))


def test_fields_that_name_nothing_in_a_catalogue_pass_through_untouched() -> None:
    # Shops are free text: the ledger's merchant names are data and there is no catalogue here
    # to ground them against, so they travel as written rather than being dropped.
    said = PlanFields(
        direction=Direction.EXPENSE,
        window=JULY,
        merchants=("Mercadona",),
        excluded_merchants=("Lidl",),
        metric=Metric.COUNT,
        group_by=GroupBy.MONTH,
        limit=3,
    )

    assert ground(said, GROUNDS) == said
    assert ground(PlanFields(), GROUNDS) == PlanFields()


def test_an_empty_list_said_stays_said() -> None:
    # () is "every account", which is not the same as an account list nobody mentioned.
    assert ground(PlanFields(accounts=()), GROUNDS) == PlanFields(accounts=())
