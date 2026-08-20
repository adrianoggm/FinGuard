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

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Spoken
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.card import Card, Wallet
from finguard_orchestrator.domain.services.intent_router import Intent, route

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 6)

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


class Cards:
    def __init__(self, wallet: Wallet | None = WALLET) -> None:
        self._wallet = wallet
        self.asked = 0

    async def all_of(self, account_id: uuid.UUID, **kwargs: object) -> Wallet:
        self.asked += 1
        if self._wallet is None:
            raise BankingUnavailableError("the ledger is not there")
        return self._wallet

    async def charges_on(self, account_id: uuid.UUID, card_id: uuid.UUID, **kwargs: object):
        return ()


class UnusedPlanner:
    """A card question that reaches this is the defect, so reaching it is the failure."""

    async def plan(self, sentence: str, **kwargs: object) -> object:
        raise AssertionError(f"the planner was asked about {sentence!r}")


class UnusedLedger:
    async def run(self, plan: object, **kwargs: object) -> object:
        raise AssertionError("a plan was run for a question about cards")


class NoAccounts:
    async def of(self, account_id: uuid.UUID, **kwargs: object) -> object:
        raise AssertionError("the balance was read")


class NoContacts:
    async def all_of(self, account_id: uuid.UUID) -> tuple[()]:
        return ()


async def ask(sentence: str, cards: Cards | None = None):
    handler = AskFinancialQuestionHandler(
        UnusedPlanner(),  # type: ignore[arg-type]
        UnusedLedger(),  # type: ignore[arg-type]
        accounts=NoAccounts(),  # type: ignore[arg-type]
        contacts=NoContacts(),  # type: ignore[arg-type]
        cards=cards or Cards(),  # type: ignore[arg-type]
    )
    return await handler.handle(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
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


async def test_the_wallet_is_answered_without_a_model() -> None:
    """The planner raises if it is reached, so this passing is the whole point."""
    said = await ask("¿qué tarjetas tengo?")

    assert said.outcome == Spoken.CARDS.value
    assert "Visa Clásica" in said.text and "Visa Oro" in said.text


async def test_the_kind_is_said_in_the_words_people_filter_by() -> None:
    """ "De crédito" is what the question asks for, and VISA is not an answer to it."""
    said = await ask("¿qué tarjetas tengo de crédito?")

    assert "débito" in said.text and "crédito" in said.text


async def test_each_card_carries_what_it_has_been_charged() -> None:
    """The second reported question, answered by the same turn as the first. It used to come back
    with the account total — 868,97 € across 21 movements, none of which was a card."""
    said = await ask("¿cuánto he gastado con cada tarjeta este mes?")

    assert "868,97" in said.text and "21 cargos" in said.text
    assert "120,50" in said.text and "3 cargos" in said.text


async def test_an_empty_wallet_says_so_rather_than_refusing() -> None:
    said = await ask("¿qué tarjetas tengo?", Cards(Wallet(since=date(2026, 7, 7))))

    assert "No tienes tarjetas" in said.text


async def test_a_bank_that_cannot_answer_is_not_a_refusal_about_the_product() -> None:
    """An outage and "this service does not do cards" read completely differently to somebody
    deciding whether to try again."""
    said = await ask("¿qué tarjetas tengo?", Cards(None))

    assert "Vuelve a intentarlo" in said.text
    assert "no puedo" not in said.text.lower()
