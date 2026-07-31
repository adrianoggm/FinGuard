from fastapi import Request

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.use_cases.answer_message import AnswerMessage
from finguard_assistant.domain.ports.model_catalogue import ModelCataloguePort
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry


def get_policies(request: Request) -> PolicyStore:
    policies: PolicyStore = request.app.state.policies
    return policies


def get_catalogue(request: Request) -> ModelCataloguePort:
    catalogue: ModelCataloguePort = request.app.state.catalogue
    return catalogue


def get_provider_registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


def get_answer_message(request: Request) -> AnswerMessage:
    # The use case receives the registry as a function: it never learns which providers
    # this build happens to carry.
    return AnswerMessage(
        get_policies(request),
        get_catalogue(request),
        get_provider_registry(request).get,
    )
