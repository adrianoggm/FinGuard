"""HTTP surface of the LLM layer.

Routes only: shapes live in dto/, mapping in mappers/, and every route goes through a
bus — a controller never touches a handler or the policy store directly.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from finguard_assistant.application import (
    AnswerMessageQuery,
    CommandBus,
    GetLlmCatalogueQuery,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ResetLlmPolicyCommand,
)
from finguard_assistant.bootstrap.dependencies import get_command_bus, get_query_bus
from finguard_assistant.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageRequest,
    MessageResponse,
    PolicyRequest,
)
from finguard_assistant.infrastructure.mappers import llm_mapper

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]

assistant_router = APIRouter(tags=["assistant"])

# ponytail: these endpoints change how the assistant behaves and today they ask for no
# credentials. Before the service is exposed beyond localhost they have to sit behind
# OAuth with an administration scope (§12 of the vision).
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@assistant_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@assistant_router.get("/ready", summary="Readiness")
async def ready(queries: Queries) -> JSONResponse:
    """Ready to serve: the active model's provider is one this build can talk to."""
    catalogue = await queries.ask(GetLlmCatalogueQuery())
    provider = catalogue.active.model.provider
    if provider not in catalogue.available_providers:
        return JSONResponse({"status": "unavailable", "provider": provider}, status_code=503)
    return JSONResponse({"status": "ok", "provider": provider})


@assistant_router.post("/v1/messages", summary="Answer a message")
async def send_message(body: MessageRequest, queries: Queries) -> MessageResponse:
    """Answers with the active model, falling back if the catalogue says so."""
    return llm_mapper.answer_to_dto(await queries.ask(AnswerMessageQuery(body.prompt)))


@admin_router.get("/llm-policy", summary="Active policy and catalogue")
async def read_policy(queries: Queries) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(await queries.ask(GetLlmCatalogueQuery()))


@admin_router.put("/llm-policy", summary="Swap the active policy")
async def replace_policy(body: PolicyRequest, commands: Commands) -> CatalogueResponse:
    command = ReplaceLlmPolicyCommand(
        model_alias=body.model_alias,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
    )
    return llm_mapper.catalogue_to_dto(await commands.dispatch(command))


@admin_router.post("/llm-policy/reset", summary="Back to the startup policy")
async def reset_policy(commands: Commands) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(await commands.dispatch(ResetLlmPolicyCommand()))
