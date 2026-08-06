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
from typing import Any, Final
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


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
        movements: int | None = None,
    ) -> QueryAnswer:
        body = to_body(
            plan,
            account_id=account_id,
            today=today,
            # The caller's count when it gave one, the deployment's otherwise. Banking clamps it to
            # its own ceiling either way, so this cannot ask for half the ledger.
            supporting_movements=movements if movements is not None else self.supporting_movements,
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

    async def ranked(
        self,
        ranking: TopMovements,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
    ) -> Movement | None:
        """The nth largest movement, from banking's own ranking endpoint.

        The same error ladder as `run`, deliberately: a 422 is our plan being refused by the
        authority, a 4xx is our request being wrong, a 5xx is the service being down. Three
        different things to say to a user, and collapsing them is how "no puedo ahora mismo" ends
        up covering a bug of ours.
        """
        body = ranked_body(
            ranking, account_id=account_id, today=today, reporting_timezone=reporting_timezone
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/v1/financial-queries/ranked", json=body
                )
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{self._base_url}: {exc}") from exc

        if response.status_code == 422:
            raise QueryRejectedError(_detail(response))
        if 400 <= response.status_code < 500:
            raise QueryRejectedError(f"HTTP {response.status_code}: {_detail(response)}")
        if response.status_code >= 500:
            raise BankingUnavailableError(f"HTTP {response.status_code}: {_detail(response)}")

        try:
            answered = response.json()
            found = answered.get("movement")
            if not found:
                return None
            # The currency belongs to the answer rather than the row, so it is attached here. The
            # assistant used to write "EUR" beside every ranked movement because nothing carried it.
            return movement_from_body(found, currency=answered.get("currency"))
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


# Every field `RankedMovementRequest` declares, and the only ones sent. Written out rather than
# derived, because deriving it would mean importing banking — the duplication between these two
# services is deliberate and the drift is supposed to surface as a 422. What is new is that it now
# surfaces in a test instead of in somebody's conversation.
RANKED_FIELDS: Final = frozenset(
    {
        "accountId",
        "direction",
        "categories",
        "merchants",
        "dateRange",
        "period",
        "reportingTimezone",
        "rank",
    }
)


def ranked_body(
    ranking: TopMovements,
    *,
    account_id: UUID,
    today: date,
    reporting_timezone: str,
) -> dict[str, Any]:
    """Ranking to request body.

    It reuses `to_body` for the filters and the window — the same fields, resolved the same way,
    including the reference date that makes both services agree on which days a period covers — and
    then keeps only what the ranking endpoint accepts. Reusing is still right: a second copy of the
    window logic is a second chance for the two services to disagree.

    **An allow-list, and the deny-list it replaced is why.** This used to pop three names with a
    comment calling them "the three the ranking endpoint does not take". `to_body` then grew
    `includeInternal` and `acrossAccounts`, nobody came back here, and banking — which forbids
    extra inputs — started 422ing every ranking. The user saw "la consulta que he construido no es
    válida" for "¿cuál es el gasto más caro que he realizado?", which is a question this service
    added a whole endpoint to answer.

    Nothing caught it: the corpus drives a stubbed bank, so the one thing being got wrong was the
    real contract. That is item 5 of docs/next.md arriving as promised — "drift arrives as a 422 at
    runtime, which is better than a wrong number and still an error found by a user."

    A deny-list is wrong here for a structural reason rather than a stylistic one: it has to be
    updated when *the other* function changes, and nothing links the two. An allow-list is wrong
    only when this endpoint changes, which is the change a reader is already looking at.
    """
    body = to_body(
        FinancialPlan(
            direction=ranking.direction,
            period=ranking.period,
            date_range=ranking.date_range,
            categories=ranking.categories,
            merchants=ranking.merchants,
        ),
        account_id=account_id,
        today=today,
        supporting_movements=0,
        reporting_timezone=reporting_timezone,
    )
    body["rank"] = ranking.rank
    return {name: value for name, value in body.items() if name in RANKED_FIELDS}


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
        "merchants": list(plan.merchants),
        "supportingLimit": supporting_movements,
        "includeInternal": plan.include_internal,
        "acrossAccounts": plan.across_accounts,
        **({"onlyAccountId": str(plan.only_account_id)} if plan.only_account_id else {}),
        **(
            {"onlyAccountIds": [str(account_id) for account_id in plan.only_account_ids]}
            if plan.only_account_ids
            else {}
        ),
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
        period_start=(date.fromisoformat(body["periodStart"]) if body.get("periodStart") else None),
        period_end=date.fromisoformat(body["periodEnd"]) if body.get("periodEnd") else None,
        groups=tuple(
            Group(
                key=group["key"],
                total_minor=group["totalMinor"],
                movement_count=group["movementCount"],
            )
            for group in body.get("groups") or ()
        ),
        movements=tuple(
            movement_from_body(movement) for movement in body.get("supportingMovements") or ()
        ),
    )


def movement_from_body(movement: Any, *, currency: str | None = None) -> Movement:
    """One movement, from banking's camelCase.

    Shared by the supporting movements of an aggregate and the single row of a ranking. Two copies
    would drift the first time a field is added to one of them.

    `currency` is the ranking's: there the movement *is* the answer, so it has to carry what its
    amount is denominated in. A supporting movement leaves it absent, because the aggregate beside
    it already says so once.
    """
    return Movement(
        occurred_at=_moment(movement["occurredAt"]),
        description=movement["description"],
        amount_minor=movement["amountMinor"],
        direction=movement["direction"],
        merchant_name=movement.get("merchantName"),
        transaction_id=movement.get("transactionId"),
        category_is_custom=bool(movement.get("categoryIsCustom", False)),
        category_source=str(movement.get("categorySource") or "NONE"),
        is_internal=bool(movement.get("isInternal", False)),
        category=movement.get("category"),
        currency=currency,
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
