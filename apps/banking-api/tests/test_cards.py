"""The cards an account holds, and what each one was charged.

Two things are worth pinning here and one of them is a lesson already paid for.

**The window is the caller's and it is resolved in the reporting zone.** The first version of the
view grouped by month in UTC, which puts a charge made at 00:30 on the 1st in Madrid into the
previous month — the two-clocks bug, one level down. The aggregation moved to the reader, where the
zone is known, and these tests are what make that visible rather than incidental.

**A card with no charges still comes back.** A card missing from a card screen is worse than a card
showing 0,00 €, and a LEFT JOIN is the difference.
"""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.infrastructure.persistence.card_repository import PostgresCardRepository

pytestmark = pytest.mark.integration

MADRID = "Europe/Madrid"


async def add_card(
    connection: asyncpg.Connection,
    account: uuid.UUID,
    *,
    last_four: str = "4417",
    brand: str = "VISA",
    status: str = "ACTIVE",
    limit: int | None = None,
) -> uuid.UUID:
    card = uuid.uuid4()
    await connection.execute(
        "INSERT INTO cards (id, account_id, display_name, brand, kind, last_four, "
        "expires_month, expires_year, status, spending_limit_minor) "
        "VALUES ($1, $2, 'TITULAR DE PRUEBA', $3, 'DEBIT', $4, 9, 2030, $5, $6)",
        card,
        account,
        brand,
        last_four,
        status,
        limit,
    )
    return card


async def charge(
    connection: asyncpg.Connection,
    account: uuid.UUID,
    card: uuid.UUID,
    cents: int,
    when: datetime,
) -> None:
    """A booked card payment, through the lifecycle, attributed to a card."""
    counterparty = await create_account(connection)
    transaction = await record_movement(
        connection,
        account=account,
        counterparty=counterparty,
        amount_minor=-cents,
        occurred_at=when,
    )
    await connection.execute(
        "UPDATE ledger_transactions SET transaction_type = 'CARD_PAYMENT', card_id = $2 "
        "WHERE id = $1",
        transaction,
        card,
    )


def cards(connection: asyncpg.Connection) -> PostgresCardRepository:
    return PostgresCardRepository(as_pool(connection))


async def test_a_card_comes_back_with_what_it_was_charged(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    card = await add_card(connection, account)
    await charge(connection, account, card, 25_00, datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
    await charge(connection, account, card, 10_50, datetime(2026, 7, 12, 12, 0, tzinfo=UTC))

    held = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 12, 31), timezone=MADRID
    )

    assert len(held) == 1
    assert held[0].spent_minor == 35_50
    assert held[0].charge_count == 2
    assert held[0].last_four == "4417"


async def test_a_card_with_nothing_on_it_still_appears(connection: asyncpg.Connection) -> None:
    """A LEFT JOIN, and the reason: a card missing from a card screen is worse than one at zero."""
    account = await create_account(connection)
    await add_card(connection, account)

    held = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 12, 31), timezone=MADRID
    )

    assert len(held) == 1
    assert held[0].spent_minor == 0
    assert held[0].charge_count == 0


async def test_charges_before_the_window_do_not_count(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    card = await add_card(connection, account)
    await charge(connection, account, card, 99_00, datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
    await charge(connection, account, card, 11_00, datetime(2026, 7, 20, 12, 0, tzinfo=UTC))

    held = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 12, 31), timezone=MADRID
    )

    assert held[0].spent_minor == 11_00


async def test_charges_after_the_window_do_not_count_either(
    connection: asyncpg.Connection,
) -> None:
    """The mirror of the test above, and the half that did not exist.

    The SQL asked for `>= since` and nothing more, so the window had a start and no end: a charge
    booked with a date ahead of the caller's own day was inside every window that reached it. Both
    ends are the caller's now, and `until` is inclusive — the charge *on* the last day counts.
    """
    account = await create_account(connection)
    card = await add_card(connection, account)
    await charge(connection, account, card, 11_00, datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
    await charge(connection, account, card, 40_00, datetime(2026, 7, 31, 12, 0, tzinfo=UTC))
    await charge(connection, account, card, 99_00, datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

    held = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 7, 31), timezone=MADRID
    )

    assert held[0].spent_minor == 51_00
    assert held[0].charge_count == 2


async def test_the_window_is_resolved_in_the_reporting_zone(
    connection: asyncpg.Connection,
) -> None:
    """The two-clocks bug, one level down and caught by construction.

    23:30 UTC on 30 June is 01:30 on 1 July in Madrid. A window starting on 1 July must include it,
    and a view that truncated in UTC — which is what the first version of this did — would not.
    """
    account = await create_account(connection)
    card = await add_card(connection, account)
    await charge(connection, account, card, 40_00, datetime(2026, 6, 30, 23, 30, tzinfo=UTC))

    madrid = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 12, 31), timezone=MADRID
    )
    utc = await cards(connection).all_of(
        account, since=date(2026, 7, 1), until=date(2026, 12, 31), timezone="UTC"
    )

    assert madrid[0].spent_minor == 40_00, "in Madrid the charge is already July"
    assert utc[0].spent_minor == 0, "in UTC it is still June"


async def test_only_this_account_s_cards(connection: asyncpg.Connection) -> None:
    mine = await create_account(connection)
    theirs = await create_account(connection)
    await add_card(connection, mine, last_four="1111")
    await add_card(connection, theirs, last_four="2222")

    held = await cards(connection).all_of(
        mine, since=date(2026, 1, 1), until=date(2026, 12, 31), timezone=MADRID
    )

    assert [card.last_four for card in held] == ["1111"]


async def test_a_blocked_card_is_listed_last(connection: asyncpg.Connection) -> None:
    """It is still a card and it still has history. It belongs at the bottom, not missing."""
    account = await create_account(connection)
    await add_card(connection, account, last_four="9999", brand="MASTERCARD", status="BLOCKED")
    await add_card(connection, account, last_four="1111", brand="VISA")

    held = await cards(connection).all_of(
        account, since=date(2026, 1, 1), until=date(2026, 12, 31), timezone=MADRID
    )

    assert [card.status for card in held] == ["ACTIVE", "BLOCKED"]


async def test_a_limit_of_none_is_not_a_limit_of_zero(connection: asyncpg.Connection) -> None:
    """A debit card is limited by the balance. Reading that as a limit of zero would show a card
    that cannot be used at all."""
    account = await create_account(connection)
    await add_card(connection, account, last_four="1111", limit=None)
    await add_card(connection, account, last_four="2222", brand="MASTERCARD", limit=150_000)

    held = await cards(connection).all_of(
        account, since=date(2026, 1, 1), until=date(2026, 12, 31), timezone=MADRID
    )
    limits = {card.last_four: card.spending_limit_minor for card in held}

    assert limits == {"1111": None, "2222": 150_000}


async def test_a_charge_cannot_name_a_card_unless_it_is_a_card_payment(
    connection: asyncpg.Connection,
) -> None:
    """The CHECK from migration 0009. A salary that names a card would make "spent on this card"
    include money that came in."""
    account = await create_account(connection)
    card = await add_card(connection, account)
    counterparty = await create_account(connection)
    transaction = await record_movement(
        connection, account=account, counterparty=counterparty, amount_minor=200_000
    )

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE ledger_transactions SET transaction_type = 'SALARY', card_id = $2 "
            "WHERE id = $1",
            transaction,
            card,
        )
