from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finguard_orchestrator.application import (
    AnswerMessageHandler,
    AnswerMessageQuery,
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CategoriseMovementCommand,
    CategoriseMovementHandler,
    CommandBus,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    GetAccountSummaryHandler,
    GetAccountSummaryQuery,
    GetBreakdownHandler,
    GetBreakdownQuery,
    GetCardChargesHandler,
    GetCardChargesQuery,
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
    GetMovementsHandler,
    GetMovementsQuery,
    ListCardsHandler,
    ListCardsQuery,
    ListContactsHandler,
    ListContactsQuery,
    PolicyStore,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.infrastructure import (
    HttpAccountSummary,
    HttpCards,
    HttpContacts,
    HttpFinancialQuery,
    HttpMovementCategories,
    HttpPayments,
    LlmPaymentReader,
    LlmQueryPlanner,
    ProviderRegistry,
    admin_router,
    assistant_router,
    payments_router,
    register_error_handlers,
)
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.security.static_token_identity import (
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
            OllamaLlmProvider.name: lambda: OllamaLlmProvider(
                base_url=settings.ollama_base_url,
                keep_alive=settings.ollama_keep_alive,
                timeout_seconds=settings.ollama_timeout_seconds,
                context_tokens=settings.ollama_context_tokens,
                seed=settings.ollama_seed,
            ),
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
    banking = HttpFinancialQuery(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
        supporting_movements=settings.supporting_movements,
    )
    asking = AskFinancialQuestionHandler(
        LlmQueryPlanner(
            planner_provider,
            model=settings.planner_model,
            temperature=settings.planner_temperature,
            max_output_tokens=settings.planner_max_output_tokens,
            categories=settings.categories,
        ),
        banking,
        # Validated here, so a rule name this build does not have stops the service
        # rather than silently leaving the rule on.
        settings.guard_policy(),
    )
    query_bus.register(AskFinancialQuestionQuery, asking)
    # The home screen's figures. Same service, no model: the plan is built in the adapter.
    # Browsing the account: the movement list, the search field and the breakdown a finance
    # screen draws. All three build their plan in code — a screen has already said everything
    # the plan needs, so a model between the tap and the query would only add a way to be wrong.
    query_bus.register(GetMovementsQuery, GetMovementsHandler(banking))
    query_bus.register(GetBreakdownQuery, GetBreakdownHandler(banking))
    # The cards, and the contacts a payment form offers. Both are reads through banking's own
    # restricted role, and neither has a model anywhere in it.
    cards = HttpCards(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
    )
    query_bus.register(ListCardsQuery, ListCardsHandler(cards))
    query_bus.register(GetCardChargesQuery, GetCardChargesHandler(cards))
    query_bus.register(
        ListContactsQuery,
        ListContactsHandler(
            HttpContacts(
                base_url=settings.banking_base_url,
                timeout_seconds=settings.banking_timeout_seconds,
            )
        ),
    )
    query_bus.register(
        GetAccountSummaryQuery,
        GetAccountSummaryHandler(
            HttpAccountSummary(
                base_url=settings.banking_base_url,
                timeout_seconds=settings.banking_timeout_seconds,
                days=settings.summary_days,
                movements=settings.summary_movements,
            )
        ),
    )

    # The payment path. Its own reader, its own prompt and its own schema: extracting who
    # and how much is a different job from planning a query, and sharing one prompt for both
    # would mean tuning either one moved the other.
    paying = PreparePaymentHandler(
        LlmPaymentReader(
            planner_provider,
            model=settings.planner_model,
            temperature=settings.planner_temperature,
            max_output_tokens=settings.planner_max_output_tokens,
        ),
        HttpPayments(
            base_url=settings.banking_base_url,
            timeout_seconds=settings.banking_timeout_seconds,
        ),
        settings.guard_policy(),
    )

    command_bus = CommandBus()
    command_bus.register(
        ReplaceLlmPolicyCommand, ReplaceLlmPolicyHandler(policies, catalogue, available)
    )
    command_bus.register(
        ResetLlmPolicyCommand, ResetLlmPolicyHandler(policies, catalogue, available)
    )
    # The holder's own category. A write, and the only one here that is not a payment.
    command_bus.register(
        CategoriseMovementCommand,
        CategoriseMovementHandler(
            HttpMovementCategories(
                base_url=settings.banking_base_url,
                timeout_seconds=settings.banking_timeout_seconds,
            )
        ),
    )
    command_bus.register(PreparePaymentCommand, paying)
    # The form path shares the gateway and skips the reader: nothing to read, nothing to ground.
    command_bus.register(
        PrepareChosenPaymentCommand,
        PrepareChosenPaymentHandler(
            HttpPayments(
                base_url=settings.banking_base_url,
                timeout_seconds=settings.banking_timeout_seconds,
            )
        ),
    )
    command_bus.register(
        ConfirmPaymentCommand,
        ConfirmPaymentHandler(
            HttpPayments(
                base_url=settings.banking_base_url,
                timeout_seconds=settings.banking_timeout_seconds,
            )
        ),
    )

    app.state.query_bus = query_bus
    app.state.command_bus = command_bus
    # Reached directly by the streaming route: a bus answers once, a stream does not.
    app.state.ask_handler = asking
    # Readiness reports on what /v1/questions needs, not on what /v1/messages needs.
    app.state.questions_enabled = settings.questions_enabled
    app.state.payments_enabled = settings.payments_enabled
    # The vocabulary a screen may offer. The same list the decoder is constrained to, so a
    # category a chip produces is one the assistant can name back.
    app.state.categories = settings.categories
    app.state.planner_probe = planner_provider
    app.state.banking_probe = banking
    # Identity, resolved per request. A development adapter today; §12.2 asks for OAuth
    # and that is a swap of this one line.
    app.state.identity = StaticTokenIdentity(settings.callers())

    # Before the routers, because a rejected preflight never reaches one. The web client cannot
    # call this service at all without it: a browser refuses a cross-origin request unless the
    # server says otherwise, which makes this the frontend's first dependency and not a detail.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        # Spelled out rather than "*": the answer to a preflight is a statement about what this
        # service accepts, and a wildcard makes that statement unfalsifiable.
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(assistant_router)
    app.include_router(payments_router)
    app.include_router(admin_router)
    register_error_handlers(app)
    return app


app = create_app()
