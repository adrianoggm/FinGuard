"""Project-wide outbound request pacing backed by the orchestrator's Postgres database."""

import asyncio
from datetime import timedelta
from typing import Any


class PostgresRequestPacer:
    """Reserve evenly spaced provider slots shared by every orchestrator using this database.

    The short transaction reserves a slot; the request itself happens after the transaction and
    never holds a database lock. A failed or cancelled request still consumes its slot, which is
    conservative for provider quotas. Without a configured pool, the provider uses its local
    pacer instead.
    """

    _LOCK_KEY = 0x46494E4755415244

    def __init__(self, *, provider: str, interval_seconds: float) -> None:
        if not provider or interval_seconds <= 0:
            raise ValueError("provider and positive pacing interval are required")
        self.provider = provider
        self.interval = timedelta(seconds=interval_seconds)
        self._pool: Any | None = None

    @property
    def active(self) -> bool:
        return self._pool is not None

    def set_pool(self, pool: Any | None) -> None:
        self._pool = pool

    async def wait_for_slot(self) -> bool:
        """Wait for one shared slot; return False when no shared database is configured."""
        pool = self._pool
        if pool is None:
            return False

        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", self._LOCK_KEY)
                now = await connection.fetchval("SELECT clock_timestamp()")
                row = await connection.fetchrow(
                    """
                    SELECT next_slot_at
                    FROM assistant.provider_request_pacer
                    WHERE provider = $1
                    FOR UPDATE
                    """,
                    self.provider,
                )
                slot = now if row is None else max(now, row["next_slot_at"])
                next_slot = slot + self.interval
                if row is None:
                    await connection.execute(
                        """
                        INSERT INTO assistant.provider_request_pacer (provider, next_slot_at)
                        VALUES ($1, $2)
                        """,
                        self.provider,
                        next_slot,
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE assistant.provider_request_pacer
                        SET next_slot_at = $2
                        WHERE provider = $1
                        """,
                        self.provider,
                        next_slot,
                    )
                delay = max(0.0, (slot - now).total_seconds())

        if delay:
            await asyncio.sleep(delay)
        return True
