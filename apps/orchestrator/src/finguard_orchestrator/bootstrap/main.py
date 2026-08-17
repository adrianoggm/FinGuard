from collections.abc import Callable

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
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services.turn_interpreters import (
    Deterministic,
    Hybrid,
    Promoted,
    Shadowed,
)
from finguard_orchestrator.infrastructure import (
    HttpAccounts,
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
from finguard_orchestrator.infrastructure.interpreting.llm_turn_interpreter import (
    LlmTurnInterpreter,
)
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import (
    CLOUDFLARE,
    cloudflare_workers_ai,
)
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)
from finguard_orchestrator.infrastructure.request_log import register_request_logging
from finguard_orchestrator.infrastructure.security.static_token_identity import (
    StaticTokenIdentity,
)
from finguard_orchestrator.observability import configure_logging

# The vendor behind the OpenAI-compatible adapter. A name and a base URL are the whole of
# what distinguishes one from another, which is why the adapter is not named after this one.
# `CLOUDFLARE` is the second such vendor and is imported rather than spelled here, because that
# one needs a builder to assemble its URLs and the name belongs beside them.
GROQ = "groq"

DESCRIPTION = """
Conversational orchestrator of FinGuard.

The model that answers is chosen by the **active policy**, which can be swapped at
runtime over `/admin/llm-policy` without restarting. Which models exist, which provider
serves each one and what happens when one fails come from the catalogue file, not from
code.
"""


def create_app() -> FastAPI:
    """Composition root: the only place that wires concrete implementations."""
    # First, before anything can want to say something. Uvicorn has already configured its own
    # loggers by now and this takes them over deliberately — two formats interleaved in one stream
    # is a stream nothing can parse.
    logging_settings = get_settings()
    configure_logging(level=logging_settings.log_level, as_json=logging_settings.log_json)

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
    builders: dict[str, Callable[[], LlmProviderPort]] = {
        EchoLlmProvider.name: EchoLlmProvider,
        OllamaLlmProvider.name: lambda: OllamaLlmProvider(
            base_url=settings.ollama_base_url,
            keep_alive=settings.ollama_keep_alive,
            timeout_seconds=settings.ollama_timeout_seconds,
            context_tokens=settings.ollama_context_tokens,
            seed=settings.ollama_seed,
            think=settings.ollama_think,
        ),
    }
    if (groq_key := settings.groq_api_key) is not None:
        # Registered only when a key exists, so an unconfigured deployment cannot reach a
        # provider that would 401 every request — and `providers.available()`, which /admin
        # reports and the catalogue is validated against, tells the truth about this build.
        #
        # The one place the secret is unwrapped. Everywhere else it is a `SecretStr` whose repr
        # is `**********`, which is what keeps a stray log line from being a live credential.
        builders[GROQ] = lambda: OpenAiCompatibleProvider(
            name=GROQ,
            api_key=groq_key.get_secret_value(),
            base_url=settings.groq_base_url,
            timeout_seconds=settings.groq_timeout_seconds,
            reasoning_headroom=settings.groq_reasoning_headroom,
        )
    if (cf_token := settings.cloudflare_api_token) is not None and (
        cf_account := settings.cloudflare_account_id
    ) is not None:
        # Both halves or nothing, which is the same rule as Groq's above with one more way to
        # break: the account id is not a credential, it is the path segment every request is
        # posted to, so a deployment holding only the token would register a provider whose URL
        # has a hole in it. Neither half alone is a provider.
        builders[CLOUDFLARE] = lambda: cloudflare_workers_ai(
            api_token=cf_token.get_secret_value(),
            account_id=cf_account,
            api_base=settings.cloudflare_api_base,
            timeout_seconds=settings.cloudflare_timeout_seconds,
            reasoning_headroom=settings.cloudflare_reasoning_headroom,
        )
    providers = ProviderRegistry(builders)
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
    # Configured rather than hardcoded, which is what makes "try a bigger model" a setting and
    # not a patch. An unknown or unconfigured name is a startup error from the registry, and
    # that is the right moment for it: the alternative is a service that starts and then fails
    # every financial question.
    planner_provider = providers.get(settings.planner_provider)
    banking = HttpFinancialQuery(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
        supporting_movements=settings.supporting_movements,
    )
    # And one for both readers of the address book: the payment form and the conversation. The
    # same argument as the balance — two lists of contacts that could disagree is worse than one
    # that is occasionally stale.
    contacts = HttpContacts(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
    )
    # One adapter for both readers of the balance. The home screen and the conversation showing
    # different figures would be worse than either being wrong, so there is one way to ask.
    accounts = HttpAccountSummary(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
        days=settings.summary_days,
        movements=settings.summary_movements,
    )
    account_directory = HttpAccounts(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
    )
    # How a turn is read, chosen once. The rules always exist — under `shadow` they still decide
    # and under `model` they keep the payments and catch the outages — so this builds one and then
    # decides what stands in front of it. See `Shadowed`, `Hybrid` and `Promoted`.
    rules: TurnInterpreterPort = Deterministic()
    interpreter = rules
    if settings.turn_interpreter != "router":
        reading = LlmTurnInterpreter(planner_provider, model=settings.planner_model)
        if settings.turn_interpreter == "shadow":
            interpreter = Shadowed(rules, reading)
        elif settings.turn_interpreter == "hybrid":
            interpreter = Hybrid(reading, rules)
        else:
            interpreter = Promoted(reading, rules)

    # One adapter for both readers of the wallet, like the balance and the address book. The chat
    # answering "cuánto llevo con cada tarjeta" from a different window than the screen shows would
    # be two figures on one phone, which is the failure those two were unified to avoid.
    cards = HttpCards(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
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
        # "¿Cuánto dinero tengo?" and "¿a quién puedo hacer un bizum?" are answered from these,
        # with no model in the path.
        accounts=accounts,
        account_directory=account_directory,
        contacts=contacts,
        cards=cards,
        read_attempts=settings.banking_read_attempts,
        # Only used when a caller did not classify the turn itself. Both HTTP routes do, so on the
        # live path this is unreachable — it is here so the handler and the edge cannot end up
        # reading a sentence two different ways when somebody adds a third caller.
        interpreter=interpreter,
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
    query_bus.register(ListCardsQuery, ListCardsHandler(cards))
    query_bus.register(GetCardChargesQuery, GetCardChargesHandler(cards))
    query_bus.register(
        ListContactsQuery,
        ListContactsHandler(contacts),
    )
    query_bus.register(
        GetAccountSummaryQuery,
        GetAccountSummaryHandler(accounts),
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
        # The same directory the conversation and the payment form read. A recipient is resolved
        # against it without a model, which is what lets "Marta Solis Vega" answer "¿a quién?".
        contacts=contacts,
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
    # Money between the holder's own accounts. Same gateway, same preview, same confirmation route
    # — the only thing that differs is that there is nobody to resolve.
    command_bus.register(
        PrepareOwnTransferCommand,
        PrepareOwnTransferHandler(
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
    # For /version. The whole Settings object rather than the two strings, because the next thing
    # somebody wants from a running service is always another one of these.
    app.state.settings = settings
    app.state.planner_probe = planner_provider
    # Read by both question routes, which classify the turn once and carry the answer down.
    app.state.turn_interpreter = interpreter
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
        #
        # `Idempotency-Key` is the one that has to be remembered here. A header the routes read and
        # CORS does not list is not a header that gets ignored — the browser refuses the preflight
        # and never sends the request at all, so adding it to the client took the whole chat down
        # while every route still answered `curl` perfectly. A wildcard would have hidden that, and
        # would hide the next one too.
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    # Outermost, so it sees the status a handler's exception turns into and the ones CORS answers
    # by itself. Every request accounted for is the property; a decorator per route would account
    # for the ones somebody remembered.
    register_request_logging(app)

    app.include_router(assistant_router)
    app.include_router(payments_router)
    app.include_router(admin_router)
    register_error_handlers(app)
    return app


app = create_app()
