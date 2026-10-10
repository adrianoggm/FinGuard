"""The cards were a screen the conversation could not open.

Reported from the demo, two turns apart:

    "¿Qué tarjetas tengo de crédito?"     "Eso no puedo calcularlo todavía."
    "¿Cuánto he gastado con cada tarjeta?" "868,97 € en el último mes, en 21 movimientos."

The first is a refusal about data this service holds — `ListCardsQuery` is registered on the bus
and the wallet screen draws it. The second is worse: 868,97 € is the *account* total, and it
arrived wearing the label of an answer about cards, with a movement list underneath that has no
card in it. A real figure answering a different question.

Neither is the planner being weak. A `FinancialPlan` has no card dimension at all, so the nearest
thing it *could* express was the account total — and an approximation with a euro sign on it is the
failure this service is built around not having. The same shape as LIST_CONTACTS, and fixed the
same way: a question with an answer in this repository does not go to a model.

One intent answers both, because both were always one query: `Card` carries `spent_minor` and
`charge_count` beside the name and the kind, so "which cards" and "how much on each" differ only in
which column the reader cares about.
"""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.domain.models.card import Card, Wallet
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.services.intent_router import Intent, route

WALLET = Wallet(
    since=date(2026, 7, 7),
    cards=(
        Card(
            card_id=uuid.UUID(int=1),
            display_name="Visa Clásica",
            brand="VISA",
            kind="DEBIT",
            last_four="4242",
            expires_month=5,
            expires_year=2029,
            status="ACTIVE",
            spending_limit_minor=None,
            spent_minor=86897,
            charge_count=21,
        ),
        Card(
            card_id=uuid.UUID(int=2),
            display_name="Visa Oro",
            brand="VISA",
            kind="CREDIT",
            last_four="7788",
            expires_month=1,
            expires_year=2028,
            status="ACTIVE",
            spending_limit_minor=200000,
            spent_minor=12050,
            charge_count=3,
        ),
    ),
)


# --- which sentences are about the wallet ------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Que tarjetas tengo de credito ?",
        "¿qué tarjetas tengo?",
        "¿cuántas tarjetas tengo?",
        "mis tarjetas",
        "cuanto he gastado con cada tarjeta en el ultimo mes",
        "el gasto por tarjeta",
    ],
)
def test_these_are_about_the_wallet(said: str) -> None:
    assert route(said, has_previous=False, at_rest=True) is Intent.LIST_CARDS


@pytest.mark.parametrize(
    "said",
    [
        # One purchase that happens to mention how it was paid. The wallet is not the subject, and
        # a plan expresses this perfectly well.
        "¿cuánto gasté con la tarjeta en Mercadona?",
        "¿cuánto gasté el mes pasado?",
        # The address book, which has its own intent and its own answer.
        "¿qué contactos tengo?",
    ],
)
def test_these_are_not(said: str) -> None:
    assert route(said, has_previous=False, at_rest=True) is not Intent.LIST_CARDS


# --- and what the answer says --------------------------------------------------------------


def test_the_wallet_names_every_card() -> None:
    said = answer_writer.cards(WALLET)

    assert "Visa Clásica" in said and "Visa Oro" in said


def test_the_kind_is_said_in_the_words_people_filter_by() -> None:
    """ "De crédito" is what the question asks for, and VISA is not an answer to it."""
    said = answer_writer.cards(WALLET)

    assert "débito" in said and "crédito" in said


def test_each_card_carries_what_it_has_been_charged() -> None:
    """The second reported question, answered by the same sentence as the first. It used to come
    back with the account total — 868,97 € across 21 movements, none of which was a card."""
    said = answer_writer.cards(WALLET)

    assert "868,97" in said and "21 cargos" in said
    assert "120,50" in said and "3 cargos" in said


def test_an_empty_wallet_says_so_rather_than_refusing() -> None:
    assert "No tienes tarjetas" in answer_writer.cards(Wallet(since=date(2026, 7, 7)))
