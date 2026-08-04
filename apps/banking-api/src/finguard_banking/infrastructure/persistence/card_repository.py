"""Reads the cards and what each one has been charged. Through the views and the restricted role.

The aggregation is here rather than in the view because the window needs a zone and a migration
does not know the deployment's. The view gives one row per booked charge; this decides "since".

A LEFT JOIN, so a card with no charges comes back with zero rather than not coming back. A card
missing from a card screen is worse than a card showing 0,00 €.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.models.card import Card, CardCharge
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    declare,
)

_SQL = """
    SELECT
        c.card_id,
        c.display_name,
        c.brand,
        c.kind,
        c.last_four,
        c.expires_month,
        c.expires_year,
        c.status,
        c.spending_limit_minor,
        COALESCE(SUM(h.amount_minor), 0) AS spent_minor,
        COUNT(h.card_id) AS charge_count
    FROM assistant_read.cards_v1 c
    LEFT JOIN assistant_read.card_charges_v1 h
           ON h.card_id = c.card_id
          AND (h.occurred_at AT TIME ZONE $1)::date >= $2
    GROUP BY
        c.card_id, c.display_name, c.brand, c.kind, c.last_four,
        c.expires_month, c.expires_year, c.status, c.spending_limit_minor
    -- Active first, then by brand: a blocked card belongs at the bottom of the list rather than
    -- wherever its digits happen to sort.
    ORDER BY (c.status = 'ACTIVE') DESC, c.brand, c.last_four
"""


_CHARGES_SQL = """
    SELECT transaction_id, occurred_at, description, merchant_name, category, amount_minor
    FROM assistant_read.card_charges_v1
    WHERE card_id = $1
      AND (occurred_at AT TIME ZONE $2)::date >= $3
    ORDER BY occurred_at DESC
    LIMIT $4
"""


class PostgresCardRepository:
    """Implements CardDirectoryPort over the read views."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def all_of(self, account_id: UUID, *, since: date, timezone: str) -> tuple[Card, ...]:
        async with self._pool.acquire() as connection, connection.transaction():
            # The same declaration every read makes. The views filter on it and row-level security
            # enforces it underneath, so another account's card does not exist here.
            await declare(connection, str(account_id))
            rows = await connection.fetch(_SQL, timezone, since)

        return tuple(
            Card(
                card_id=row["card_id"],
                display_name=row["display_name"],
                brand=row["brand"],
                kind=row["kind"],
                last_four=row["last_four"],
                expires_month=row["expires_month"],
                expires_year=row["expires_year"],
                status=row["status"],
                spending_limit_minor=row["spending_limit_minor"],
                # int(), because SUM over bigint is numeric in PostgreSQL and asyncpg hands that
                # back as Decimal — the same edge the payment repository already hit.
                spent_minor=int(row["spent_minor"]),
                charge_count=int(row["charge_count"]),
            )
            for row in rows
        )

    async def charges_on(
        self, account_id: UUID, card_id: UUID, *, since: date, timezone: str, limit: int
    ) -> tuple[CardCharge, ...]:
        """What was bought with one card, newest first.

        The card is named in the WHERE and the account in the declaration, and both matter: the view
        already filters on the declared account, so another holder's card id selects nothing rather
        than selecting their charges. That is the containment property, and it does not depend on
        this method being careful.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            await declare(connection, str(account_id))
            rows = await connection.fetch(_CHARGES_SQL, card_id, timezone, since, limit)

        return tuple(
            CardCharge(
                transaction_id=row["transaction_id"],
                occurred_at=row["occurred_at"],
                description=row["description"],
                merchant_name=row["merchant_name"],
                category=row["category"],
                amount_minor=int(row["amount_minor"]),
            )
            for row in rows
        )
