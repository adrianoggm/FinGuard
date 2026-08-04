"""Result ↔ DTO. The only place that knows both shapes.

Queries and commands answer with Results, never with domain objects, so what arrives
here is already flat. This adds the wire format and nothing else.
"""

from finguard_orchestrator.application import (
    AccountSummaryResult,
    AnswerMessageResult,
    AskFinancialQuestionResult,
    BreakdownResult,
    CatalogueResult,
    ChargesResult,
    ListContactsResult,
    ModelRow,
    MovementsResult,
    PolicyRow,
    SpokenPlan,
    WalletResult,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    AccountSummaryResponse,
    BreakdownResponse,
    CardPayload,
    CatalogueResponse,
    ChargePayload,
    ChargesResponse,
    ContactPayload,
    ContactsResponse,
    GroupPayload,
    MessageResponse,
    ModelResponse,
    MovementsResponse,
    PlanPayload,
    PolicyResponse,
    QuestionResponse,
    SpokenContactResponse,
    SpokenGroupResponse,
    SpokenMovementResponse,
    SummaryMovementResponse,
    WalletResponse,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper


def _model_to_dto(row: ModelRow) -> ModelResponse:
    return ModelResponse(alias=row.alias, provider=row.provider, model=row.model)


def _policy_to_dto(row: PolicyRow) -> PolicyResponse:
    return PolicyResponse(
        model=_model_to_dto(row.model),
        temperature=row.temperature,
        max_output_tokens=row.max_output_tokens,
    )


def catalogue_to_dto(result: CatalogueResult) -> CatalogueResponse:
    return CatalogueResponse(
        active=_policy_to_dto(result.active),
        fallback=result.fallback,
        available_models=[_model_to_dto(row) for row in result.available_models],
        available_providers=result.available_providers,
    )


def answer_to_dto(result: AnswerMessageResult) -> MessageResponse:
    return MessageResponse(
        text=result.text,
        policy=PolicyResponse(
            model=ModelResponse(
                alias=result.model_alias, provider=result.provider, model=result.model
            ),
            temperature=result.temperature,
            max_output_tokens=result.max_output_tokens,
        ),
        attempted=list(result.attempted),
    )


def plan_to_domain(payload: PlanPayload) -> FinancialPlan:
    """The echoed plan, rebuilt as a plan.

    Pydantic has already refused anything that is not one of the vocabulary's values;
    the domain now refuses the combinations that are individually valid and jointly
    wrong — both ends of a range, neither window, `n` on a period that has no count.
    An InvalidValueError from here is a 422, which is the honest answer: what came back
    is not something this service ever handed out.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlan(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        merchants=tuple(payload.merchants),
        group_by=payload.group_by,
        metric=payload.metric,
    )


def draft_to_domain(payload: PlanPayload) -> FinancialPlanDraft:
    """The echoed draft, rebuilt as a draft.

    The same payload as a plan and a different type on the way out: a draft is allowed to
    carry no window, which is the one invariant a plan will not relax. Everything else is
    checked identically — Pydantic has already refused values outside the vocabulary, and
    two windows at once is as wrong here as it is there.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlanDraft(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        merchants=tuple(payload.merchants),
        group_by=payload.group_by,
        metric=payload.metric,
    )


def _plan_to_dto(plan: SpokenPlan) -> PlanPayload:
    return PlanPayload.model_validate(
        {
            "direction": plan.direction,
            "period": plan.period,
            "n": plan.n,
            "start": plan.start,
            "end": plan.end,
            "categories": plan.categories,
            "merchants": plan.merchants,
            "group_by": plan.group_by,
            "metric": plan.metric,
        }
    )


def question_to_dto(result: AskFinancialQuestionResult) -> QuestionResponse:
    return QuestionResponse(
        outcome=result.outcome,
        text=result.text,
        question=result.question,
        plan=result.plan,
        conversation=conversation_mapper.to_payload(result.conversation),
        contacts=[
            SpokenContactResponse(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                phone=contact.phone,
            )
            for contact in result.contacts
        ],
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        groups=[
            SpokenGroupResponse(
                key=group.key,
                total_minor=group.total_minor,
                movement_count=group.movement_count,
            )
            for group in result.groups
        ],
        movements=[
            SpokenMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                direction=movement.direction,
                merchant_name=movement.merchant_name,
                category=movement.category,
            )
            for movement in result.movements
        ],
    )


def summary_to_dto(result: AccountSummaryResult) -> AccountSummaryResponse:
    return AccountSummaryResponse(
        currency=result.currency,
        booked_minor=result.booked_minor,
        available_minor=result.available_minor,
        as_of=result.as_of,
        movements=[
            SummaryMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                merchant_name=movement.merchant_name,
                category=movement.category,
            )
            for movement in result.movements
        ],
    )


def contacts_to_dto(result: ListContactsResult) -> ContactsResponse:
    return ContactsResponse(
        contacts=[
            ContactPayload(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                phone=contact.phone,
            )
            for contact in result.contacts
        ]
    )


def movements_to_dto(result: MovementsResult) -> MovementsResponse:
    return MovementsResponse(
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        movements=[
            SummaryMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                merchant_name=movement.merchant_name,
                category=movement.category,
                transaction_id=movement.transaction_id,
                category_is_custom=movement.category_is_custom,
            )
            for movement in result.movements
        ],
    )


def breakdown_to_dto(result: BreakdownResult) -> BreakdownResponse:
    return BreakdownResponse(
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        groups=[
            GroupPayload(
                key=group.key,
                total_minor=group.total_minor,
                movement_count=group.movement_count,
            )
            for group in result.groups
        ],
    )


def wallet_to_dto(result: WalletResult) -> WalletResponse:
    return WalletResponse(
        since=result.since,
        cards=[
            CardPayload(
                card_id=card.card_id,
                display_name=card.display_name,
                brand=card.brand,
                kind=card.kind,
                last_four=card.last_four,
                expires=card.expires,
                status=card.status,
                spending_limit_minor=card.spending_limit_minor,
                spent_minor=card.spent_minor,
                charge_count=card.charge_count,
            )
            for card in result.cards
        ],
    )


def charges_to_dto(result: ChargesResult) -> ChargesResponse:
    return ChargesResponse(
        card_id=result.card_id,
        since=result.since,
        charges=[
            ChargePayload(
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
