"""Shared process, catalogue, and one-shot message routes.

This module contains no conversational language interpretation. The question-turn routes live in
the graph controller or the explicitly selected legacy controller.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ResetLlmPolicyCommand,
)
from finguard_orchestrator.application.queries.answer_message import AnswerMessageQuery
from finguard_orchestrator.application.queries.get_llm_catalogue import GetLlmCatalogueQuery
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageRequest,
    MessageResponse,
    PolicyRequest,
)
from finguard_orchestrator.infrastructure.http.dependencies import (
    get_command_bus,
    get_query_bus,
    requires,
)
from finguard_orchestrator.infrastructure.mappers import llm_mapper

assistant_router = APIRouter(tags=["assistant"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(requires(Scope.ASSISTANT_ADMIN))],
)

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]


@assistant_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@assistant_router.get("/version", summary="Which build is answering")
async def version(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "service": "orchestrator",
        "sha": str(settings.build_sha),
        "ref": str(settings.build_ref),
        "source_sha256": str(settings.build_source_sha256),
    }


@assistant_router.get("/ready", summary="Readiness")
async def ready(request: Request, queries: Queries) -> JSONResponse:
    """Report the financial runtime separately from the one-shot message catalogue."""
    catalogue = await queries.ask(GetLlmCatalogueQuery())
    provider = catalogue.active.model.provider
    settings = request.app.state.settings
    # The graph is built by the lifespan once PostgreSQL is open; until then it is missing.
    graph_ready = getattr(request.app.state, "engine", None) is not None
    persistence_probe = getattr(request.app.state, "persistence_probe", None)
    checks = {
        "questions_enabled": bool(request.app.state.questions_enabled),
        "catalogue_provider": provider in catalogue.available_providers,
        "planner": await request.app.state.planner_probe.ping(),
        "banking": await request.app.state.banking_probe.ping(),
        "conversation_runtime": graph_ready,
        "persistence": await persistence_probe.ping() if persistence_probe is not None else False,
    }
    body: dict[str, object] = {
        "status": "ok" if all(checks.values()) else "unavailable",
        "provider": provider,
        "financial_path": {
            # Kept in the shape the evaluation runner and the browser checks read; LangGraph is
            # the only runtime, so only `active_runtime` and `authoritative` can vary.
            "engine": "model",
            "graph": "on",
            "active_runtime": "langgraph" if graph_ready else "missing",
            "authoritative": graph_ready,
            "provider": settings.planner_provider,
            "model": settings.engine_model or settings.planner_model,
        },
        "payments": bool(request.app.state.payments_enabled),
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if all(checks.values()) else 503)


@assistant_router.post(
    "/v1/messages",
    summary="Answer a message",
    dependencies=[Depends(requires(Scope.ASSISTANT_USE))],
)
async def send_message(body: MessageRequest, queries: Queries) -> MessageResponse:
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
