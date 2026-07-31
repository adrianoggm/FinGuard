from fastapi import FastAPI

from finguard_assistant.application.bus.command_bus import CommandBus
from finguard_assistant.application.bus.query_bus import QueryBus
from finguard_assistant.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.queries.answer_message import (
    AnswerMessageHandler,
    AnswerMessageQuery,
)
from finguard_assistant.application.queries.get_llm_catalogue import (
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
)
from finguard_assistant.bootstrap.config import get_settings
from finguard_assistant.infrastructure import error_handlers
from finguard_assistant.infrastructure.controllers.llm_controller import admin, router
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry

DESCRIPTION = """
Conversational orchestrator of FinGuard.

The model that answers is chosen by the **active policy**, which can be swapped at
runtime over `/admin/llm-policy` without restarting. Which models exist, which provider
serves each one and what happens when one fails come from the catalogue file, not from
code.
"""


def create_app() -> FastAPI:
    """Composition root: the only place that wires concrete implementations."""
    app = FastAPI(
        title="FinGuard Assistant API",
        version="0.1.0",
        description=DESCRIPTION,
        summary="Conversational orchestrator",
        openapi_tags=[
            {"name": "assistant", "description": "Conversation with the assistant."},
            {"name": "admin", "description": "Runtime configuration. Needs auth before exposure."},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
    )

    catalogue = get_settings().catalogue()
    providers = ProviderRegistry()
    # The active policy is created at startup and lives in the application state: the
    # service is deliberately not stateless, its configuration changes at runtime.
    policies = PolicyStore(catalogue, catalogue.default_policy())
    available = providers.available()

    query_bus = QueryBus()
    query_bus.register(AnswerMessageQuery, AnswerMessageHandler(policies, catalogue, providers.get))
    query_bus.register(GetLlmCatalogueQuery, GetLlmCatalogueHandler(policies, catalogue, available))

    command_bus = CommandBus()
    command_bus.register(
        ReplaceLlmPolicyCommand, ReplaceLlmPolicyHandler(policies, catalogue, available)
    )
    command_bus.register(
        ResetLlmPolicyCommand, ResetLlmPolicyHandler(policies, catalogue, available)
    )

    app.state.query_bus = query_bus
    app.state.command_bus = command_bus

    app.include_router(router)
    app.include_router(admin)
    error_handlers.register(app)
    return app


app = create_app()
