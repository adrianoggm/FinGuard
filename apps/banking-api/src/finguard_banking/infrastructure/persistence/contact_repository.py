"""Reads the contact list. Through the view and the restricted role, like every other read.

Resolution is by prefix and case-insensitive, because a person types "Marta" and the list says
"Marta Ruiz Gomez", and a match that needs the exact stored spelling would answer "no such
contact" to a real name.

The interesting behaviour is what it does with more than one match: it refuses. A single
`LIMIT 1` here would be the most expensive line in the codebase — the one place where guessing
moves somebody else's money.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.errors import AmbiguousContactError, ContactNotFoundError
from finguard_banking.domain.models.contact import Contact
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    _DECLARE_ACCOUNT,
)

# Two candidates are enough to know it is ambiguous, but the user needs the names to choose, so
# a handful are fetched. More than a handful is not a list somebody reads — it means the name
# was not a name.
_MAX_CANDIDATES = 5

_LIST_SQL = """
    SELECT contact_id, display_name, phone
    FROM assistant_read.contacts_v1
    ORDER BY display_name
"""

_RESOLVE_SQL = f"""
    SELECT contact_id, display_name, phone
    FROM assistant_read.contacts_v1
    WHERE lower(display_name) LIKE lower($1) || '%'
    ORDER BY display_name
    LIMIT {_MAX_CANDIDATES}
"""


class PostgresContactRepository:
    """Implements ContactDirectoryPort over the read view."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def all_of(self, account_id: UUID) -> tuple[Contact, ...]:
        """Everybody this account can pay, in the order a list is read.

        No limit. A contact list is bounded by how many people somebody knows, and a truncated one
        is worse than a long one: the name that is missing is the one they were looking for.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(_DECLARE_ACCOUNT, str(account_id))
            rows = await connection.fetch(_LIST_SQL)
        return tuple(
            Contact(
                contact_id=row["contact_id"],
                display_name=row["display_name"],
                phone=row["phone"],
            )
            for row in rows
        )

    async def resolve(self, account_id: UUID, name: str) -> Contact:
        async with self._pool.acquire() as connection, connection.transaction():
            # The same declaration every read makes. The view filters on it and row-level
            # security enforces it underneath, so a contact of another account is not "not
            # authorised" — it does not exist, and a caller cannot learn otherwise.
            await connection.execute(_DECLARE_ACCOUNT, str(account_id))
            rows = await connection.fetch(_RESOLVE_SQL, name.strip())

        if not rows:
            raise ContactNotFoundError(name)
        if len(rows) > 1:
            raise AmbiguousContactError(name, tuple(row["display_name"] for row in rows))

        row = rows[0]
        return Contact(
            contact_id=row["contact_id"],
            display_name=row["display_name"],
            phone=row["phone"],
        )
