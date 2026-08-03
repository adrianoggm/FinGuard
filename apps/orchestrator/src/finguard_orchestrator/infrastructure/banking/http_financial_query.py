"""Speaks the banking HTTP contract. Written by hand, on purpose.

Generating this client from banking's live OpenAPI would make one service's build
depend on another's and turn the contract into a by-product of an implementation
instead of the agreement it has to honour. The cost is that the two shapes are stated
twice and can drift; the mitigation is that drift is loud — banking rejects what it
does not recognise, and that arrives as QueryRejectedError rather than as a wrong
number.

Everything wire-shaped stops here. `to_body` is the only place that knows the contract
is camelCase and that a date range is spelled from/to.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


class HttpFinancialQuery:
    """Implements FinancialQueryPort over the banking HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        supporting_movements: int,
    ) -> None:
        """No defaults, and the absence is the design. These are operational numbers that
        belong to a deployment, so they live in .env.defaults beside the reason each one is
        what it is — not here, where changing one is a commit and reading one means reading
        Python. Required rather than optional so an adapter cannot be built with numbers
        nobody chose."""
        self._base_url = base_url.rstrip("/")
        # Public because they are configuration, and configuration that reaches an adapter
        # without being observable is configuration nobody can prove arrived.
        self.timeout_seconds = timeout_seconds
        self.supporting_movements = supporting_movements

    async def run(
        self,
        plan: FinancialPlan,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
    ) -> QueryAnswer:
        body = to_body(
            plan,
            account_id=account_id,
            today=today,
            supporting_movements=self.supporting_movements,
            reporting_timezone=reporting_timezone,
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/v1/financial-queries", json=body)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{self._base_url}: {exc}") from exc

        if response.status_code == 422:
            # The plan was well-formed to us and unacceptable to the authority. This is
            # the drift detector for a vocabulary the two services declare separately.
            raise QueryRejectedError(_detail(response))
        if 400 <= response.status_code < 500:
            # A 404 is an account that does not exist and a 400 is a body it would not
            # take. Both are our request being wrong, which reads very differently to the
            # user than the service being down — and only one of them is worth retrying.
            raise QueryRejectedError(f"HTTP {response.status_code}: {_detail(response)}")
        if response.status_code >= 500:
            raise BankingUnavailableError(f"HTTP {response.status_code}: {_detail(response)}")

        try:
            answered = response.json()
            return from_body(answered, account_id=account_id)
        except (ValueError, KeyError, TypeError) as exc:
            raise BankingUnavailableError(f"unreadable answer: {exc}") from exc

    async def ping(self) -> bool:
        """Whether the ledger answers. Every figure comes from there, so a service that
        cannot reach it is not ready however healthy its own process looks."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self._base_url}/ready")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


def to_body(
    plan: FinancialPlan,
    *,
    account_id: UUID,
    today: date,
    supporting_movements: int,
    reporting_timezone: str,
) -> dict[str, Any]:
    """Plan to request body. The only place that knows the contract is camelCase."""
    body: dict[str, Any] = {
        "accountId": str(account_id),
        "direction": plan.direction.value,
        "groupBy": plan.group_by.value,
        "metric": plan.metric.value,
        "categories": list(plan.categories),
        "supportingLimit": supporting_movements,
    }

    if plan.period is not None:
        # `reference` is what makes both services resolve the window to the same days.
        # Without it banking uses its own clock and answers a different question with a
        # perfectly real number.
        period: dict[str, Any] = {
            "period": plan.period.period.value,
            "reference": today.isoformat(),
            # The two travel together because neither describes a window alone. A date
            # without a zone asks the other service to guess the half we did not send, and
            # its guess is a deployment setting that can differ from ours.
            "timezone": reporting_timezone,
        }
        if plan.period.n is not None:
            period["n"] = plan.period.n
        body["period"] = period
    else:
        assert plan.date_range is not None  # the plan's own invariant guarantees one
        body["dateRange"] = {
            "from": plan.date_range.start.isoformat(),
            "to": plan.date_range.end.isoformat(),
        }

    return body


def from_body(body: Any, *, account_id: UUID | None = None) -> QueryAnswer:
    """Response body to the assistant's own model. Nothing wire-shaped travels past here.

    When used by the HTTP client, the expected account is mandatory context: a missing
    account identifier is not safer than a different one, because neither lets us bind
    the figures to the caller who requested them.
    """
    if not isinstance(body, dict):
        raise BankingUnavailableError(f"expected an object, got {type(body).__name__}")

    if account_id is not None:
        about = body.get("accountId")
        if about is None:
            raise BankingUnavailableError("answer does not identify its account")
        if str(about) != str(account_id):
            raise BankingUnavailableError(f"answer is about {about}, not {account_id}")

    return QueryAnswer(
        currency=body["currency"],
        total_minor=body["totalMinor"],
        movement_count=body["movementCount"],
        groups=tuple(
            Group(
                key=group["key"],
                total_minor=group["totalMinor"],
                movement_count=group["movementCount"],
            )
            for group in body.get("groups") or ()
        ),
        movements=tuple(
            Movement(
                occurred_at=_moment(movement["occurredAt"]),
                description=movement["description"],
                amount_minor=movement["amountMinor"],
                direction=movement["direction"],
                merchant_name=movement.get("merchantName"),
                category=movement.get("category"),
            )
            for movement in body.get("supportingMovements") or ()
        ),
    )


def _moment(value: str) -> datetime:
    # The contract sends UTC with a Z; fromisoformat took Z only from 3.11 onwards, and
    # being explicit costs less than remembering that.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("message") or body)[:200]
    return str(body)[:200]
