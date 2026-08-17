import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class Ticket:
    session_id: str
    credential: str
    token: str
    expires_at: datetime


class TicketStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], datetime] | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self._tickets: dict[str, Ticket] = {}

    def issue(self, session_id: str, credential: str) -> Ticket:
        token = secrets.token_urlsafe(32)
        ticket = Ticket(
            session_id,
            credential,
            token,
            self.clock() + timedelta(seconds=self.ttl_seconds),
        )
        self._tickets[token] = ticket
        return ticket

    def consume(self, token: str, session_id: str) -> Ticket | None:
        ticket = self._tickets.pop(token, None)
        if ticket is None or ticket.session_id != session_id or ticket.expires_at <= self.clock():
            return None
        return ticket

    def purge_expired(self) -> int:
        now = self.clock()
        expired = [token for token, ticket in self._tickets.items() if ticket.expires_at <= now]
        for token in expired:
            del self._tickets[token]
        return len(expired)

    def __len__(self) -> int:
        return len(self._tickets)

    async def cleanup_loop(self, interval_seconds: float) -> None:
        """Purge idle tickets; cancellation stops the loop at application shutdown."""
        while True:
            await asyncio.sleep(interval_seconds)
            self.purge_expired()
