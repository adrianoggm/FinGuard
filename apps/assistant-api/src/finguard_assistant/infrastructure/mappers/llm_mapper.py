"""Result ↔ DTO. The only place that knows both shapes.

Queries and commands answer with Results, never with domain objects, so what arrives
here is already flat. This adds the wire format and nothing else.
"""

from finguard_assistant.application import (
    AnswerMessageResult,
    AskFinancialQuestionResult,
    CatalogueResult,
    ModelRow,
    PolicyRow,
)
from finguard_assistant.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageResponse,
    ModelResponse,
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


def question_to_dto(result: AskFinancialQuestionResult) -> QuestionResponse:
    return QuestionResponse(
        outcome=result.outcome,
        text=result.text,
        question=result.question,
        plan=result.plan,
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
