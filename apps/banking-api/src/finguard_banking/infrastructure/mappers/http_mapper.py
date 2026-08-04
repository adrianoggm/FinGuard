"""Result ↔ DTO, plus DTO → plan. The only place that knows both shapes.

Queries answer with Results, never with domain models, so what arrives here is already
flat. What this adds is the wire format: camelCase, `from`/`to`, and the `amountMinor` +
`currency` pair the §13 contract asks for.
"""

from datetime import date
from uuid import UUID

from finguard_banking.application import (
    GetBalanceResult,
    ListCardChargesResult,
    ListCardsResult,
    ListContactsResult,
    MovementSummaryResult,
    PreparePaymentCommand,
    SummaryGroup,
    SummaryMovement,
    SummaryTrace,
)
from finguard_banking.application.queries.ranked_movement import RankedMovementResult
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.domain.value_objects.relative_period import RelativePeriod, resolve
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    CardChargeResponse,
    CardChargesResponse,
    CardResponse,
    CardsResponse,
    ContactResponse,
    ContactsResponse,
    FinancialQueryRequest,
    FinancialQueryResponse,
    GroupResponse,
    MovementResponse,
    PaymentPreviewResponse,
    PaymentReceiptResponse,
    PreparePaymentRequest,
    RankedMovementRequest,
    RankedMovementResponse,
    TraceResponse,
)


def plan_from_dto(request: FinancialQueryRequest, today: date) -> FinancialQuery:
    """Builds the plan. A relative period is resolved here, at the edge, because this is
    where the clock lives — the domain only ever sees absolute dates.

    `today` is this service's own idea of the day, used only when the caller did not send a
    reference. When it did, the caller's date and zone travel together and win: the window
    a figure is about has to be the one whoever asked was thinking of.
    """
    zone = request.period.timezone if request.period is not None else None
    if request.date_range is not None:
        date_range = DateRange(start=request.date_range.start, end=request.date_range.end)
    elif request.period is not None:
        relative = RelativePeriod(
            period=request.period.period,
            n=request.period.n,
            reference=request.period.reference,
        )
        date_range = resolve(relative, today)
    else:  # pragma: no cover - prevented by the DTO validator
        raise ValueError("missing period")

    return FinancialQuery(
        account_id=request.account_id,
        date_range=date_range,
        metric=request.metric,
        direction=request.direction,
        categories=tuple(Category(value) for value in request.categories),
        merchants=tuple(request.merchants),
        group_by=request.group_by,
        supporting_limit=request.supporting_limit,
        explain=request.explain,
        reporting_timezone=zone,
    )


def ranked_plan_from_dto(request: RankedMovementRequest, today: date) -> FinancialQuery:
    """The filters of a ranking, as a plan.

    The same window resolution as a query, reusing the same helper: a relative period becomes
    absolute here because this is where the clock lives. `metric` and `group_by` take their
    defaults — a ranking has no aggregate — and `supporting_limit` is zero, because the movement
    *is* the answer rather than evidence beside one.
    """
    return plan_from_dto(
        FinancialQueryRequest(
            account_id=request.account_id,
            direction=request.direction,
            categories=request.categories,
            merchants=request.merchants,
            date_range=request.date_range,
            period=request.period,
            supporting_limit=0,
        ),
        today,
    )


def ranked_to_dto(result: RankedMovementResult) -> RankedMovementResponse:
    return RankedMovementResponse(
        account_id=result.account_id,
        rank=result.rank,
        movement=_movement_to_dto(result.movement) if result.movement is not None else None,
    )


def balance_to_dto(result: GetBalanceResult) -> BalanceResponse:
    return BalanceResponse(
        account_id=result.account_id,
        available_minor=result.available_minor,
        booked_minor=result.booked_minor,
        currency=result.currency,
        as_of=result.as_of,
    )


def _group_to_dto(group: SummaryGroup) -> GroupResponse:
    return GroupResponse(
        key=group.key, total_minor=group.total_minor, movement_count=group.movement_count
    )


def _movement_to_dto(movement: SummaryMovement) -> MovementResponse:
    return MovementResponse(
        transaction_id=movement.transaction_id,
        occurred_at=movement.occurred_at,
        description=movement.description,
        merchant_name=movement.merchant_name,
        category=movement.category,
        category_is_custom=movement.category_is_custom,
        amount_minor=movement.amount_minor,
        direction=movement.direction,
    )


def _trace_to_dto(trace: SummaryTrace) -> TraceResponse:
    return TraceResponse(sql=trace.sql, parameters=trace.parameters, elapsed_ms=trace.elapsed_ms)


def summary_to_dto(result: MovementSummaryResult) -> FinancialQueryResponse:
    return FinancialQueryResponse(
        account_id=result.account_id,
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        groups=[_group_to_dto(group) for group in result.groups],
        supporting_movements=[_movement_to_dto(m) for m in result.movements],
        trace=_trace_to_dto(result.trace) if result.trace is not None else None,
    )


def payment_from_dto(
    body: PreparePaymentRequest, account_id: UUID, key: str
) -> PreparePaymentCommand:
    """The account comes from the path and the key from a header, so neither can be forged in the
    body. `Money` is built here: the domain never sees a loose amount/currency pair."""
    return PreparePaymentCommand(
        account_id=account_id,
        recipient_name=body.recipient_name,
        recipient_contact_id=body.recipient_contact_id,
        amount=Money(body.amount_minor, body.currency),
        idempotency_key=key,
    )


def preview_to_dto(preview: PaymentPreview) -> PaymentPreviewResponse:
    return PaymentPreviewResponse(
        intent_id=preview.intent_id,
        recipient=preview.recipient,
        recipient_phone=preview.recipient_phone,
        amount_minor=preview.amount.amount_minor,
        currency=preview.amount.currency,
        balance_before_minor=preview.balance_before.amount_minor,
        balance_after_minor=preview.balance_after.amount_minor,
        expires_at=preview.expires_at,
    )


def receipt_to_dto(receipt: PaymentReceipt) -> PaymentReceiptResponse:
    return PaymentReceiptResponse(
        intent_id=receipt.intent_id,
        transaction_id=receipt.transaction_id,
        recipient=receipt.recipient,
        recipient_phone=receipt.recipient_phone,
        amount_minor=receipt.amount.amount_minor,
        currency=receipt.amount.currency,
        balance_after_minor=receipt.balance_after.amount_minor,
        confirmed_at=receipt.confirmed_at,
    )


def contacts_to_dto(result: ListContactsResult) -> ContactsResponse:
    return ContactsResponse(
        contacts=[
            ContactResponse(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                phone=contact.phone,
            )
            for contact in result.contacts
        ]
    )


def cards_to_dto(result: ListCardsResult) -> CardsResponse:
    return CardsResponse(
        since=result.since,
        cards=[
            CardResponse(
                card_id=card.card_id,
                display_name=card.display_name,
                brand=card.brand,
                kind=card.kind,
                last_four=card.last_four,
                expires_month=card.expires_month,
                expires_year=card.expires_year,
                status=card.status,
                spending_limit_minor=card.spending_limit_minor,
                spent_minor=card.spent_minor,
                charge_count=card.charge_count,
            )
            for card in result.cards
        ],
    )


def card_charges_to_dto(result: ListCardChargesResult) -> CardChargesResponse:
    return CardChargesResponse(
        card_id=result.card_id,
        since=result.since,
        charges=[
            CardChargeResponse(
                transaction_id=charge.transaction_id,
                occurred_at=charge.occurred_at,
                description=charge.description,
                merchant_name=charge.merchant_name,
                category=charge.category,
                amount_minor=charge.amount_minor,
            )
            for charge in result.charges
        ],
    )
