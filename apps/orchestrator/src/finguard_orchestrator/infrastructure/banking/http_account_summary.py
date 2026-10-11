"""The home screen's figures, from banking, with no model anywhere in the path.

Two calls: the balance endpoint, and the ordinary financial-query endpoint with a plan **built here
in code** — last N days, every direction, no grouping. Banking validates it like any other plan, so
this path gets the same protection as the conversation without asking anybody to phrase a question.

That is the whole reason this exists rather than the screen asking "¿cuál es mi saldo?": a language
model in front of the first number somebody reads is one thing, and the home screen disagreeing with
the chat about the balance is a worse one. Two balances on one phone is the failure that looks worst
on camera.

The window and the movement count are configuration. They are product decisions about what a home
screen shows, not facts about the ledger.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.banking.client import banking_client
from finguard_orchestrator.infrastructure.banking.http_accounts import HttpAccounts
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery


class HttpAccountSummary(AccountSummaryPort):
    """Implements AccountSummaryPort over banking's HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        days: int,
        movements: int,
    ) -> None:
        """No defaults, like the other adapters: these are operational numbers and they live in
        .env.defaults beside the reason each one is what it is."""
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.days = days
        self.movements = movements
        # Composed rather than duplicated: the movements come back through the same client, the
        # same contract and the same error translation as every other figure — configured with this
        # screen's own count instead of the conversation's.
        self._queries = HttpFinancialQuery(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            supporting_movements=movements,
        )
        self._accounts = HttpAccounts(base_url=base_url, timeout_seconds=timeout_seconds)

    async def of(self, account_id: UUID, *, today: date, reporting_timezone: str) -> AccountSummary:
        balance = await self._balance(account_id)
        recent = await self._queries.run(
            # Built here, so nothing can talk it into a different window. `ALL` because a home
            # screen shows what happened, not only what was spent.
            FinancialPlan(
                direction=Direction.ALL,
                period=RelativePeriod(period=Period.LAST_N_DAYS, n=self.days),
                group_by=GroupBy.NONE,
                metric=Metric.SUM,
            ),
            account_id=account_id,
            today=today,
            reporting_timezone=reporting_timezone,
        )
        return AccountSummary(
            currency=balance["currency"],
            booked_minor=balance["bookedMinor"],
            available_minor=balance["availableMinor"],
            as_of=balance["asOf"],
            movements=recent.movements,
            # The holder's other accounts, beside the declared one's figures rather than replacing
            # them. A client written before this keeps reading exactly what it read: the balance at
            # the top is still the account the credential names, and `holdings` is a field it can
            # ignore. Migrating a home screen is then a decision somebody makes rather than a
            # number that changed under it.
            holdings=await self._accounts.all_of(account_id),
        )

    async def _balance(self, account_id: UUID) -> dict[str, Any]:
        url = f"{self._base_url}/v1/accounts/{account_id}/balance"
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code != 200:
            # Including a 404. An account the credential names and banking does not have is not a
            # balance of zero, and answering with one would be inventing a figure.
            raise BankingUnavailableError(f"HTTP {response.status_code} reading the balance")

        try:
            body = response.json()
            return {
                "currency": str(body["currency"]),
                "bookedMinor": int(body["bookedMinor"]),
                "availableMinor": int(body["availableMinor"]),
                "asOf": _moment(str(body["asOf"])),
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise BankingUnavailableError(f"unreadable balance: {exc}") from exc


def _moment(written: str) -> datetime:
    """Banking writes UTC with a Z. Kept explicit so a timestamp never silently becomes naive."""
    return datetime.fromisoformat(written.replace("Z", "+00:00"))
