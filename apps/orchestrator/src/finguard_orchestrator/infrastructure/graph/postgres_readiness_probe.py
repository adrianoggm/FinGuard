"""Readiness checks for the durable parts of the LangGraph conversation runtime."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_REQUIRED_TABLES = (
    "assistant.conversation_state",
    "assistant.conversation_turn",
    # Match LangGraph's unqualified table names so a deployment-provided search_path is respected.
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
_PROBE_TIMEOUT_SECONDS = 2.0


class PostgresGraphReadinessProbe:
    """Report whether the graph's canonical store and checkpoint tables remain reachable."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def ping(self) -> bool:
        try:
            return await asyncio.wait_for(self._query(), timeout=_PROBE_TIMEOUT_SECONDS)
        except Exception as failed:
            logger.warning("graph.persistence_probe_failed error=%s", type(failed).__name__)
            return False

    async def _query(self) -> bool:
        async with self._pool.acquire() as connection:
            for table in _REQUIRED_TABLES:
                # These identifiers are fixed above, never supplied by a request. LIMIT 0 checks
                # table resolution and SELECT permission without reading customer rows.
                await connection.fetch(f"SELECT 1 FROM {table} LIMIT 0")
        return True
