"""HTTP surface of the LLM layer. Routes only: shapes live in dto/, mapping in mappers/."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.use_cases.answer_message import AnswerMessage
from finguard_assistant.bootstrap.dependencies import (
    get_answer_message,
    get_catalogue,
    get_policies,
    get_provider_registry,
)
from finguard_assistant.domain.ports.model_catalogue import ModelCataloguePort
from finguard_assistant.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageRequest,
    MessageResponse,
    PolicyRequest,
)
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry
from finguard_assistant.infrastructure.mappers import llm_mapper

Policies = Annotated[PolicyStore, Depends(get_policies)]
Catalogue = Annotated[ModelCataloguePort, Depends(get_catalogue)]
Registry = Annotated[ProviderRegistry, Depends(get_provider_registry)]
Answering = Annotated[AnswerMessage, Depends(get_answer_message)]

router = APIRouter(tags=["assistant"])

# ponytail: these endpoints change how the assistant behaves and today they ask for no
# credentials. Before the service is exposed beyond localhost they have to sit behind
# OAuth with an administration scope (§12 of the vision).
admin = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
async def ready(policies: Policies, registry: Registry) -> JSONResponse:
    """Ready to serve: the active model's provider is one this build can talk to."""
    provider = policies.current().model.provider
    if provider not in registry.available():
        return JSONResponse({"status": "unavailable", "provider": provider}, status_code=503)
    return JSONResponse({"status": "ok", "provider": provider})


@router.post("/v1/messages", summary="Answer a message")
async def send_message(body: MessageRequest, answering: Answering) -> MessageResponse:
    """Answers with the active model, falling back if the catalogue says so."""
    return llm_mapper.answer_to_dto(await answering(body.prompt))


@admin.get("/llm-policy", summary="Active policy and catalogue")
async def read_policy(
    policies: Policies, catalogue: Catalogue, registry: Registry
) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(policies.current(), catalogue, registry.available())


@admin.put("/llm-policy", summary="Swap the active policy")
async def replace_policy(
    body: PolicyRequest, policies: Policies, catalogue: Catalogue, registry: Registry
) -> CatalogueResponse:
    """The alias is resolved against the catalogue before anything is rebound, so an
    unknown model never becomes active."""
    policy = llm_mapper.policy_from_dto(body, catalogue, policies.current())
    return llm_mapper.catalogue_to_dto(
        await policies.replace(policy), catalogue, registry.available()
    )


@admin.post("/llm-policy/reset", summary="Back to the startup policy")
async def reset_policy(
    policies: Policies, catalogue: Catalogue, registry: Registry
) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(await policies.reset(), catalogue, registry.available())
