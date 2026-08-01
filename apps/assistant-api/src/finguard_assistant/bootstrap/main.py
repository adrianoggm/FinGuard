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
from finguard_assistant.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_assistant.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_assistant.infrastructure.security.static_token_identity import (
    StaticTokenIdentity,
)

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
    # The registry builds providers with no arguments; the one that needs configuration
    # gets it here, in the only place allowed to know what the deployment looks like.
    providers = ProviderRegistry(
        {
            EchoLlmProvider.name: EchoLlmProvider,
            OllamaLlmProvider.name: lambda: OllamaLlmProvider(base_url=settings.ollama_base_url),
        }
    )
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
    planner_provider = providers.get(OllamaLlmProvider.name)
    banking = HttpFinancialQuery(base_url=settings.banking_base_url)
    asking = AskFinancialQuestionHandler(
        LlmQueryPlanner(
            planner_provider,
            model=settings.planner_model,
            temperature=0.0,
            max_output_tokens=384,
            categories=settings.categories,
        ),
        banking,
    )
    query_bus.register(AskFinancialQuestionQuery, asking)

    command_bus = CommandBus()
    command_bus.register(
        ReplaceLlmPolicyCommand, ReplaceLlmPolicyHandler(policies, catalogue, available)
    )
    command_bus.register(
        ResetLlmPolicyCommand, ResetLlmPolicyHandler(policies, catalogue, available)
    )

    app.state.query_bus = query_bus
    app.state.command_bus = command_bus
    # Reached directly by the streaming route: a bus answers once, a stream does not.
    app.state.ask_handler = asking
    # Readiness reports on what /v1/questions needs, not on what /v1/messages needs.
    app.state.questions_enabled = settings.questions_enabled
    app.state.planner_probe = planner_provider
    app.state.banking_probe = banking
    # Identity, resolved per request. A development adapter today; §12.2 asks for OAuth
    # and that is a swap of this one line.
    app.state.identity = StaticTokenIdentity(settings.callers())

    app.include_router(assistant_router)
    app.include_router(admin_router)
    register_error_handlers(app)
    return app


app = create_app()
