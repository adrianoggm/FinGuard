"""Writes the audit trail. One row per attempt, and the row cannot be edited afterwards.

Migration 0008 refuses UPDATE and DELETE with a trigger, so this adapter has one method and no way
to correct itself. That is the point: the service is what the trail is evidence about.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from finguard_banking.domain.models.audit_event import AuditEvent

_INSERT = """
    INSERT INTO audit_events (account_id, command, outcome, detail)
    VALUES ($1, $2, $3, $4::jsonb)
"""


class PostgresAuditLog:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def record(self, event: AuditEvent) -> None:
        # `default=str` because the detail is whatever the command carried: UUIDs, dates, a Money
        # that a dataclass walk turned into a dict. An audit row is worth more than the exactness
        # of its JSON types, so nothing here refuses to serialise.
        detail = json.dumps(event.detail, default=str, ensure_ascii=False)
        async with self._pool.acquire() as connection:
            await connection.execute(
                _INSERT, event.account_id, event.command, event.outcome, detail
            )
