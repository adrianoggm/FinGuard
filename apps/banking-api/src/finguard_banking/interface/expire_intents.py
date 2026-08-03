"""Closes the payment intents nobody answered.

    python -m finguard_banking.interface.expire_intents

The first thing in `interface/`: an entry point that is neither HTTP nor a repository. Nothing
schedules it yet, and that is deliberate — `confirm` refuses an intent past its expiry on its own,
without trusting this to have run. What this fixes is the data: `EXPIRED` exists in the state
machine and nothing else ever writes it.

It prints how many it closed, because a sweep that says nothing gives no way to tell "there was
nothing to do" from "it did not run".
"""

import asyncio
from typing import Any

import asyncpg

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.infrastructure import PostgresPaymentRepository


async def sweep() -> int:
    settings = get_settings()
    pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=1
    )
    try:
        return await PostgresPaymentRepository(
            pool,
            clearing_reference=settings.clearing_account_reference,
            validity_seconds=settings.payment_validity_seconds,
        ).expire_stale()
    finally:
        await pool.close()


def main() -> None:
    print(f"expired intents: {asyncio.run(sweep())}")


if __name__ == "__main__":
    main()
