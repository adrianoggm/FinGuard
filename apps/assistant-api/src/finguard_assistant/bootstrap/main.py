from fastapi import FastAPI

from finguard_assistant.application import (
    AnswerMessageHandler,
    AnswerMessageQuery,
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
    PolicyStore,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_assistant.bootstrap.config import get_settings
from finguard_assistant.infrastructure import (
    HttpFinancialQuery,
    LlmQueryPlanner,
    ProviderRegistry,
    admin_router,
    assistant_router,
    register_error_handlers,
)
from finguard_assistant.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider

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

    settings = get_settings()
    catalogue = settings.catalogue()
    providers = ProviderRegistry()
    # The active policy is created at startup and lives in the application state: the
    # service is deliberately not stateless, its configuration changes at runtime.
    policies = PolicyStore(catalogue, catalogue.default_policy())
    available = providers.available()

    query_bus = QueryBus()
    query_bus.register(AnswerMessageQuery, AnswerMessageHandler(policies, catalogue, providers.get))
    query_bus.register(GetLlmCatalogueQuery, GetLlmCatalogueHandler(policies, catalogue, available))

    # The planning path. It does not go through the LLM policy on purpose: the policy
    # chooses who *answers*, and planning is a different job measured a different way
    # (scripts/evaluate_planning.py). One knob per decision beats one knob for both.
    query_bus.register(
        AskFinancialQuestionQuery,
        AskFinancialQuestionHandler(
            LlmQueryPlanner(
                providers.get(OllamaLlmProvider.name),
                model=settings.planner_model,
                temperature=0.0,
                max_output_tokens=384,
                categories=settings.categories,
            ),
            HttpFinancialQuery(base_url=settings.banking_base_url),
        ),
    )

    command_bus = CommandBus()
    command_bus.register(
        ReplaceLlmPolicyCommand, ReplaceLlmPolicyHandler(policies, catalogue, available)
    )
    command_bus.register(
        ResetLlmPolicyCommand, ResetLlmPolicyHandler(policies, catalogue, available)
    )

    app.state.query_bus = query_bus
    app.state.command_bus = command_bus

    app.include_router(assistant_router)
    app.include_router(admin_router)
    register_error_handlers(app)
    return app


app = create_app()
