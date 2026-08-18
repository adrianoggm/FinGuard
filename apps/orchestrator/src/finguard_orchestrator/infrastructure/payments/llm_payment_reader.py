"""Turns a sentence into a payment reading using one model, once.

Everything that can go wrong becomes an outcome rather than an exception, like the query planner:
the caller has to say something to the user whatever happened.

**Nothing here normalises, and on this path that rule earns its keep twice over.** A field of the
wrong type is INVALID_STRUCTURE, never a best-effort reading — a `"2000"` read as 2000 is a figure
this layer invented, and the next thing that happens to a figure this layer invented is somebody
being asked to confirm it.

The grounding check runs here rather than in the caller, so an ungrounded reading never leaves. A
caller cannot forget a check it has no way to skip.
"""

import json
from dataclasses import replace
from typing import Any, Final

from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError, ProviderFailedError
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.services import payment_grounding, payment_words
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.infrastructure.payments.payment_prompt import build_prompt
from finguard_orchestrator.infrastructure.payments.payment_schema import PAYMENT_SCHEMA

# What to ask when the model chose to ask and did not say what. Spanish because it reaches the user.
#
# Supplying the words of a question commits to nothing; supplying an amount would commit to a
# payment. That asymmetry is why this fallback exists and why there is no equivalent for a figure.
FALLBACK_QUESTION: Final = "¿Cuánto quieres enviar, y a quién?"


# What to ask when the model read a payment and left one slot empty. Spanish, and naming the slot:
# "¿de cuánto?" tells the user what to type and "no lo he entendido" does not.
MISSING_AMOUNT: Final = "¿De cuánto quieres hacer el bizum?"
MISSING_RECIPIENT: Final = "¿A quién quieres enviarle el dinero?"


class _MalformedError(Exception):
    """The answer did not match the schema it was constrained to. Internal: it becomes
    INVALID_STRUCTURE before anything else sees it."""


class LlmPaymentReader:
    """Implements PaymentReaderPort against a single model."""

    def __init__(
        self,
        provider: LlmProviderPort,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    async def read(self, sentence: str, *, policy: GuardPolicy = EVERY_RULE) -> PaymentReading:
        try:
            answer = await self._provider.complete(
                build_prompt(sentence),
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=PAYMENT_SCHEMA,
            )
        except ProviderFailedError as exc:
            return PaymentReading.provider_error(str(exc))

        return _read(answer, sentence=sentence, policy=policy)


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise _MalformedError(f"{key} must be a string, got {type(value).__name__}")
    return value


def _count(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    # bool is an int in Python and never an amount. Excluded explicitly so `true` cannot arrive
    # as one cent.
    if not isinstance(value, int) or isinstance(value, bool):
        raise _MalformedError(f"{key} must be an integer, got {type(value).__name__}")
    return value


def _read(answer: str, *, sentence: str, policy: GuardPolicy) -> PaymentReading:
    try:
        body = json.loads(answer)
    except ValueError as exc:
        # Under constrained decoding this is unreachable. Counting it is how we find out if the
        # constraint ever stops holding, instead of assuming it always did.
        return PaymentReading.invalid_structure(f"not JSON: {exc}")

    if not isinstance(body, dict):
        return PaymentReading.invalid_structure(f"expected an object, got {type(body).__name__}")

    try:
        outcome = PlanningOutcome(_text(body, "outcome"))
    except _MalformedError as exc:
        return PaymentReading.invalid_structure(str(exc))
    except ValueError:
        return PaymentReading.invalid_structure(f"unknown outcome: {body.get('outcome')!r}")

    match outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            try:
                question = _text(body, "question").strip()
            except _MalformedError as exc:
                return PaymentReading.invalid_structure(str(exc))
            drafted = _draft(body)
            written = payment_words.amount_in(sentence)
            if drafted is not None and drafted.amount_minor is None and written is not None:
                drafted = replace(drafted, amount_minor=written)
                request = drafted.into_request()
                if request is not None:
                    return _grounded(request, sentence=sentence, policy=policy)
                question = MISSING_RECIPIENT
            return PaymentReading.needs_clarification(question or FALLBACK_QUESTION, drafted)

        case PlanningOutcome.UNSUPPORTED:
            return PaymentReading.unsupported("the sentence is not a payment this assistant makes")

        case PlanningOutcome.PLANNED:
            return _build_request(body, sentence=sentence, policy=policy)

        case _:
            # The three remaining outcomes describe things that happen TO a model; it cannot
            # report them about itself, and the schema does not offer them.
            return PaymentReading.invalid_structure(f"{outcome} is not the model's to choose")


def _draft(body: dict[str, Any]) -> PaymentDraft | None:
    """What the model had read by the time it decided to ask.

    The schema requires every field on every outcome, so a clarification already carries whatever it
    understood — usually the recipient, when the amount is what it is asking about. That
    was dropped, which is why answering "15 euros" started a new conversation.

    Nothing here can fail the clarification: an unreadable draft becomes no draft, because
    a question back is useful on its own and the model did nothing wrong by asking.
    """
    try:
        return PaymentDraft(
            recipient_name=payment_words.without_trailing_filler(_text(body, "recipient")) or None,
            amount_minor=_count(body, "amountMinor") or None,
            concept=(body.get("concept") or "").strip() or None,
        )
    except (_MalformedError, InvalidValueError):
        return None


def _build_request(body: dict[str, Any], *, sentence: str, policy: GuardPolicy) -> PaymentReading:
    try:
        recipient, currency = _text(body, "recipient"), _text(body, "currency")
        amount_minor = _count(body, "amountMinor")
    except _MalformedError as exc:
        return PaymentReading.invalid_structure(str(exc))

    if not recipient.strip() or amount_minor <= 0:
        # It chose PLANNED and left a slot empty. That is a question rather than a bad payment: the
        # sentinel means "the sentence did not say", and building a PaymentRequest from it used to
        # raise and come back as "no lo he entendido" — about a sentence that was understood except
        # for one field.
        drafted = _draft(body)
        written = payment_words.amount_in(sentence)
        if drafted is not None and drafted.amount_minor is None and written is not None:
            drafted = replace(drafted, amount_minor=written)
            request = drafted.into_request()
            if request is not None:
                return _grounded(request, sentence=sentence, policy=policy)
        missing_amount = amount_minor <= 0
        return PaymentReading.needs_clarification(
            MISSING_AMOUNT if missing_amount and written is None else MISSING_RECIPIENT, drafted
        )

    try:
        request = PaymentRequest(
            recipient_name=payment_words.without_trailing_filler(recipient),
            amount_minor=amount_minor,
            currency=currency,
            concept=(body.get("concept") or "").strip() or None,
        )
    except InvalidValueError as exc:
        # A payment of nothing, a negative one, an absurd one, or nobody to pay. Well-formed JSON
        # carrying a value the domain will not take — and none of it reaches a confirmation prompt.
        return PaymentReading.invalid_domain(str(exc))

    return _grounded(request, sentence=sentence, policy=policy)


def _grounded(request: PaymentRequest, *, sentence: str, policy: GuardPolicy) -> PaymentReading:
    """A complete extraction still has to equal the words before it may leave this reader."""
    ungrounded = payment_grounding.ungrounded_reason(sentence, request, policy)
    if ungrounded is not None:
        # The reading is well formed and does not correspond to the sentence. `reason` is the
        # rule's name so the evaluation can count which one, rather than a sentence about it.
        return PaymentReading.invalid_domain(ungrounded)

    return PaymentReading.read(request)
