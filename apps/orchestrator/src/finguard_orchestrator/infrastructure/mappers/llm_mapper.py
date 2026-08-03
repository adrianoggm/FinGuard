"""Result ↔ DTO. The only place that knows both shapes.

Queries and commands answer with Results, never with domain objects, so what arrives
here is already flat. This adds the wire format and nothing else.
"""

from finguard_orchestrator.application import (
    AnswerMessageResult,
    AskFinancialQuestionResult,
    CatalogueResult,
    ModelRow,
    PolicyRow,
    SpokenPlan,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageResponse,
    ModelResponse,
    PlanPayload,
    PolicyResponse,
    QuestionResponse,
    SpokenGroupResponse,
    SpokenMovementResponse,
)


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
        plan_context=_plan_to_dto(result.plan_context) if result.plan_context else None,
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
                merchant_name=movement.merchant_name,
                category=movement.category,
            )
            for movement in result.movements
        ],
    )
