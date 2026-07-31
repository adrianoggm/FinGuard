"""Domain ↔ DTO. The only place that knows both shapes."""

from finguard_assistant.domain.models.answer import Answer
from finguard_assistant.domain.ports.model_catalogue import ModelCataloguePort
from finguard_assistant.domain.value_objects.llm_model import LlmModel
from finguard_assistant.domain.value_objects.llm_policy import LlmPolicy
from finguard_assistant.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageResponse,
    ModelResponse,
    PolicyRequest,
    PolicyResponse,
)


def model_to_dto(model: LlmModel) -> ModelResponse:
    return ModelResponse(alias=model.alias, provider=model.provider, model=model.model)


def policy_to_dto(policy: LlmPolicy) -> PolicyResponse:
    return PolicyResponse(
        model=model_to_dto(policy.model),
        temperature=policy.temperature,
        max_output_tokens=policy.max_output_tokens,
    )


def catalogue_to_dto(
    active: LlmPolicy, catalogue: ModelCataloguePort, providers: list[str]
) -> CatalogueResponse:
    return CatalogueResponse(
        active=policy_to_dto(active),
        fallback=catalogue.fallback.value,
        available_models=[model_to_dto(model) for model in catalogue.models()],
        available_providers=providers,
    )


def answer_to_dto(answer: Answer) -> MessageResponse:
    return MessageResponse(
        text=answer.text,
        policy=policy_to_dto(answer.policy),
        attempted=list(answer.attempts),
    )


def policy_from_dto(
    request: PolicyRequest, catalogue: ModelCataloguePort, current: LlmPolicy
) -> LlmPolicy:
    """Builds the new policy from the request.

    The alias is resolved against the catalogue — a caller cannot name a model this
    deployment does not have — and the settings it leaves out keep their current value.
    """
    return LlmPolicy(
        model=catalogue.get(request.model_alias),
        temperature=current.temperature if request.temperature is None else request.temperature,
        max_output_tokens=(
            current.max_output_tokens
            if request.max_output_tokens is None
            else request.max_output_tokens
        ),
    )
