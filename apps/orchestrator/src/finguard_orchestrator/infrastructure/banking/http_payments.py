"""Speaks banking's payment contract. Written by hand, like the query client, for the same reason.

Generating it from banking's OpenAPI would make one service's build depend on another's and turn the
contract into a by-product of an implementation. The cost is that the shapes are stated twice; the
mitigation is that drift is loud, because banking refuses what it does not recognise.

**`refusal_for` is the part worth reading, and it is pure so it can be.** Every status banking can
answer with becomes a typed error, and the mapping is the difference between "the payment did not
happen" and "the payment may have happened":

    404 contact_not_found   nobody by that name in this account's contacts
    409 ambiguous_contact   two people answer to it; the candidates come back with the error
    409 insufficient_funds  the balance refused it. Nothing moved
    410 intent_expired      the preview is stale; a new one is needed
    409 intent_not_confirmable  already paid or cancelled. **Nothing else must be assumed here**
    5xx / transport         unknown. The confirmation may have executed, and the caller says so

The last two are why this is a function with tests rather than an `if` inside a request: getting
`intent_not_confirmable` wrong in the optimistic direction means telling somebody a payment failed
when it went through.
"""

from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.enums import Rail
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    BankingUnavailableError,
    IntentGoneError,
    PaymentRefusedError,
    RecipientHasNoAccountError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.infrastructure.banking.client import banking_client

# Banking's codes. Matched on these and never on its sentences.
CONTACT_NOT_FOUND = "contact_not_found"
# The contact exists and has no account number. 409 from banking, for the same reason an
# ambiguous name is: the request is well formed and the state is what makes it unanswerable.
CONTACT_HAS_NO_ACCOUNT = "contact_has_no_account"
AMBIGUOUS_CONTACT = "ambiguous_contact"
INSUFFICIENT_FUNDS = "insufficient_funds"
INTENT_EXPIRED = "intent_expired"
INTENT_NOT_CONFIRMABLE = "intent_not_confirmable"
INTENT_NOT_FOUND = "intent_not_found"


class HttpPayments:
    """Implements PaymentGatewayPort over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        """No defaults: these are operational numbers that belong to a deployment, and they live in
        .env.defaults beside the reason each one is what it is."""
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _payments(self, account_id: UUID) -> str:
        return f"{self._base_url}/v1/accounts/{account_id}/payments"

    async def prepare(
        self, request: PaymentRequest, *, account_id: UUID, key: str
    ) -> PaymentPreview:
        # One or the other, never both: banking refuses a body that names its recipient twice,
        # and the identifier is the one that can tell two people with the same name apart.
        # An internal transfer names no recipient at all: the destination is an account, and
        # banking refuses a body that carries both. The other two name one, and never twice — the
        # identifier is what tells two people with the same name apart.
        recipient: dict[str, object]
        if request.to_account_id is not None:
            recipient = {"toAccountId": str(request.to_account_id)}
        elif request.recipient_contact_id is not None:
            recipient = {"recipientContactId": str(request.recipient_contact_id)}
        else:
            recipient = {"recipientName": request.recipient_name}
        response = await self._post(
            self._payments(account_id),
            json={
                **recipient,
                "amountMinor": request.amount_minor,
                "currency": request.currency,
                # Always sent, never defaulted on this side. Banking defaults it to BIZUM for
                # clients written before transfers existed, and this one was not.
                "rail": request.rail.value,
            },
            headers={"Idempotency-Key": key},
        )
        return preview_from_body(_json(response))

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt:
        response = await self._post(f"{self._payments(account_id)}/{intent_id}/confirm")
        return receipt_from_body(_json(response))

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None:
        await self._post(f"{self._payments(account_id)}/{intent_id}/cancel")

    async def _post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            # Unknown, not "did not happen". For a confirmation this is the one case where the
            # caller must not claim nothing moved.
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        refusal = refusal_for(response.status_code, _body_or_empty(response))
        if refusal is not None:
            raise refusal
        return response


def refusal_for(status: int, body: dict[str, Any]) -> Exception | None:
    """The typed error for a status, or None when the answer is good.

    Pure, so the mapping every refusal depends on is testable without a server. `code` comes from
    banking's body; a 4xx without a code we know is still a refusal, and it says so rather than
    being read as one of the specific ones.
    """
    if status < 400:
        return None

    code = body.get("code")
    name = str(body.get("detail", "")) or "unknown"

    if status >= 500:
        return BankingUnavailableError(f"HTTP {status}: {name}")
    if code == AMBIGUOUS_CONTACT:
        candidates = body.get("candidates")
        names = tuple(str(c) for c in candidates) if isinstance(candidates, list) else ()
        return AmbiguousRecipientError(_named(body), names)
    if code == CONTACT_HAS_NO_ACCOUNT:
        return RecipientHasNoAccountError(_named(body))
    if code == CONTACT_NOT_FOUND:
        return RecipientNotFoundError(_named(body))
    if code == INSUFFICIENT_FUNDS:
        return PaymentRefusedError(INSUFFICIENT_FUNDS)
    if code in (INTENT_EXPIRED, INTENT_NOT_CONFIRMABLE, INTENT_NOT_FOUND):
        return IntentGoneError(str(code))
    # A 4xx we do not recognise is our request being wrong. It is a refusal — nothing moved — and it
    # is reported as one rather than as the bank being down, which would invite a retry.
    return PaymentRefusedError(f"HTTP {status}: {code or name}")


def _named(body: dict[str, Any]) -> str:
    """The name banking was asked about, taken out of its message.

    Banking does not return the name as a field, and quoting it back is what lets the assistant say
    "no tengo a Pepe en tus contactos". If it cannot be found, the sentence degrades to the whole
    detail rather than to nothing.
    """
    detail = str(body.get("detail", ""))
    if "'" in detail:
        return detail.split("'")[1]
    return detail or "esa persona"


def _body_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    body = _body_or_empty(response)
    if not body:
        raise BankingUnavailableError("unreadable answer: expected an object")
    return body


def preview_from_body(body: dict[str, Any]) -> PaymentPreview:
    """Wire shape to the assistant's own. Nothing camelCase travels past here."""
    try:
        return PaymentPreview(
            intent_id=UUID(body["intentId"]),
            recipient=body["recipient"],
            # Both optional and exactly one present, which the rail says. `get` rather than `[]`
            # for all three: a deployment of banking from before transfers answers without them,
            # and refusing to read a preview because a field is missing would break every payment
            # rather than the one feature that needs it.
            rail=Rail(body.get("rail", Rail.BIZUM.value)),
            recipient_phone=body.get("recipientPhone"),
            recipient_iban=body.get("recipientIban"),
            amount_minor=int(body["amountMinor"]),
            currency=body["currency"],
            balance_before_minor=int(body["balanceBeforeMinor"]),
            balance_after_minor=int(body["balanceAfterMinor"]),
            expires_at=_moment(body["expiresAt"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BankingUnavailableError(f"unreadable preview: {exc}") from exc


def receipt_from_body(body: dict[str, Any]) -> PaymentReceipt:
    try:
        return PaymentReceipt(
            rail=Rail(body.get("rail", Rail.BIZUM.value)),
            recipient_phone=body.get("recipientPhone"),
            recipient_iban=body.get("recipientIban"),
            intent_id=UUID(body["intentId"]),
            transaction_id=UUID(body["transactionId"]),
            recipient=body["recipient"],
            amount_minor=int(body["amountMinor"]),
            currency=body["currency"],
            balance_after_minor=int(body["balanceAfterMinor"]),
            confirmed_at=_moment(body["confirmedAt"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # A receipt we cannot read is not a payment we can report. The money moved either way, and
        # saying so honestly beats inventing the figures on it.
        raise BankingUnavailableError(f"unreadable receipt: {exc}") from exc


def _moment(written: str) -> Any:
    from datetime import datetime

    # Banking writes UTC with a Z, which fromisoformat only accepts from 3.11 — kept explicit so a
    # timestamp never silently becomes naive.
    return datetime.fromisoformat(written.replace("Z", "+00:00"))
