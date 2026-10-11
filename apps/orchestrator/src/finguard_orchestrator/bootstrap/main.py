from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.categorise import (
    CategoriseMovementCommand,
    CategoriseMovementHandler,
)
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
)
from finguard_orchestrator.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_orchestrator.application.engine.turn_commands import (
    AnswerTurnCommand,
    AnswerTurnHandler,
    OwnsWorkflowHandler,
    OwnsWorkflowQuery,
)
from finguard_orchestrator.application.engine.turn_handler import ConversationTurnHandler
from finguard_orchestrator.application.metrics_executor import financial_metric_executor
from finguard_orchestrator.application.policy_store import PolicyStore
from finguard_orchestrator.application.queries.answer_message import (
    AnswerMessageHandler,
    AnswerMessageQuery,
)
from finguard_orchestrator.application.queries.browse_account import (
    GetBreakdownHandler,
    GetBreakdownQuery,
    GetMovementsHandler,
    GetMovementsQuery,
)
from finguard_orchestrator.application.queries.get_account_summary import (
    GetAccountSummaryHandler,
    GetAccountSummaryQuery,
)
from finguard_orchestrator.application.queries.get_llm_catalogue import (
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
)
from finguard_orchestrator.application.queries.list_cards import (
    GetCardChargesHandler,
    GetCardChargesQuery,
    ListCardsHandler,
    ListCardsQuery,
)
from finguard_orchestrator.application.queries.list_contacts import (
    ListContactsHandler,
    ListContactsQuery,
)
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.bootstrap.graph_wiring import graph_engine
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.infrastructure.banking.http_account_summary import HttpAccountSummary
from finguard_orchestrator.infrastructure.banking.http_accounts import HttpAccounts
from finguard_orchestrator.infrastructure.banking.http_cards import HttpCards
from finguard_orchestrator.infrastructure.banking.http_contacts import HttpContacts
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery
from finguard_orchestrator.infrastructure.banking.http_movement_categories import (
    HttpMovementCategories,
)
from finguard_orchestrator.infrastructure.banking.http_payments import HttpPayments
from finguard_orchestrator.infrastructure.controllers import engine_dispatch
from finguard_orchestrator.infrastructure.controllers.account_controller import (
    router as account_router,
)
from finguard_orchestrator.infrastructure.controllers.graph_question_controller import (
    router as graph_question_router,
)
from finguard_orchestrator.infrastructure.controllers.runtime_controller import (
    admin_router,
    assistant_router,
)
from finguard_orchestrator.infrastructure.engine.llm_turn_operations import LlmTurnOperations
from finguard_orchestrator.infrastructure.error_handlers import register as register_error_handlers
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import (
    CLOUDFLARE,
    cloudflare_workers_ai,
)
from finguard_orchestrator.infrastructure.llm.counting_provider import (
    CountingProvider,
)
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.llm.gemini import GEMINI, GeminiProvider
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)
from finguard_orchestrator.infrastructure.llm.postgres_request_pacer import (
    PostgresRequestPacer,
)
from finguard_orchestrator.infrastructure.llm.provider_registry import ProviderRegistry
from finguard_orchestrator.infrastructure.metrics.memory_repository import (
    MemorySavedMetricRepository,
)
from finguard_orchestrator.infrastructure.metrics.postgres_repository import (
    PostgresSavedMetricRepository,
)
from finguard_orchestrator.infrastructure.metrics.router import router as metrics_router
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import LlmVisualPlanner
from finguard_orchestrator.infrastructure.request_log import register_request_logging
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.security.static_token_identity import (
    StaticTokenIdentity,
)
from finguard_orchestrator.infrastructure.telemetry.otel import (
    configure_tracing,
    register_turn_recording,
)
from finguard_orchestrator.infrastructure.visualization.vega_lite import present
from finguard_orchestrator.observability.logging import configure_logging

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

logger = logging.getLogger(__name__)

# I4 (fix wave): rule 13's own vocabulary -- the loopback addresses a development credential is
# allowed to sit on. `urlsplit(...).hostname` strips brackets from an IPv6 literal, so `[::1]`
# and `::1` compare equal here without a second form of the same address to maintain.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_origin(origin: str) -> bool:
    hostname = urlsplit(origin).hostname
    return hostname is not None and hostname.lower() in _LOOPBACK_HOSTS


def _counted(build: Callable[[], LlmProviderPort]) -> Callable[[], LlmProviderPort]:
    """`build`, with what it builds counted per request (`counting_provider`)."""
    return lambda: CountingProvider(build())


def create_app() -> FastAPI:
    """Composition root: the only place that wires concrete implementations."""
    # First, before anything can want to say something. Uvicorn has already configured its own
    # loggers by now and this takes them over deliberately — two formats interleaved in one stream
    # is a stream nothing can parse.
    logging_settings = get_settings()
    configure_logging(level=logging_settings.log_level, as_json=logging_settings.log_json)
    graph_factory: Callable[[object, object, object], ConversationTurnHandler] | None = None

    @asynccontextmanager
    async def lifespan(service: FastAPI) -> AsyncIterator[None]:
        pool = None
        if (
            settings.metrics_enabled or settings.gemini_api_key is not None or graph_factory
        ) and settings.database_url:
            import asyncpg

            pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        if gemini_pacer is not None:
            gemini_pacer.set_pool(pool)
        if settings.metrics_enabled and pool is not None:
            service.state.metrics_repository = PostgresSavedMetricRepository(
                pool, settings.metrics_max_saved
            )
        saver_context = None
        saver_open = False
        try:
            if graph_factory is not None:
                if not settings.database_url:
                    raise ValueError("the conversation graph requires DATABASE_URL")
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                from finguard_orchestrator.infrastructure.engine.postgres_dialogue_store import (
                    PostgresDialogueStore,
                )
                from finguard_orchestrator.infrastructure.graph.postgres_readiness_probe import (
                    PostgresGraphReadinessProbe,
                )
                from finguard_orchestrator.infrastructure.graph.postgres_turn_reservations import (
                    PostgresTurnReservations,
                )

                # LangGraph owns checkpoint recovery; application state retains its separate
                # versioned CAS and request reservation records in the assistant schema.
                postgres_url = settings.database_url.replace(
                    "postgresql+asyncpg://", "postgresql://", 1
                )
                saver_context = AsyncPostgresSaver.from_conn_string(postgres_url)
                checkpointer = await saver_context.__aenter__()
                saver_open = True
                await checkpointer.setup()
                service.state.persistence_probe = PostgresGraphReadinessProbe(pool)
                dialogue_store = PostgresDialogueStore(pool)
                graph_runtime = graph_factory(
                    dialogue_store, PostgresTurnReservations(pool), checkpointer
                )
                service.state.engine = graph_runtime
            yield
        finally:
            if saver_context is not None and saver_open:
                await saver_context.__aexit__(None, None, None)
            if pool is not None:
                if gemini_pacer is not None:
                    gemini_pacer.set_pool(None)
                await pool.close()

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
        lifespan=lifespan,
    )

    settings = get_settings()
    gemini_pacer = PostgresRequestPacer(
        provider=GEMINI,
        interval_seconds=settings.gemini_min_request_interval_seconds,
    )
    if sys.platform == "win32":
        # psycopg's async connection requires selector-based IO on native Windows. Uvicorn
        # creates its loop after importing this module, so choose it here, before startup. A loop
        # already running (a test building the app) keeps its own: it never opens the lifespan.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if settings.metrics_enabled and (
        not settings.database_url or not settings.metrics_signing_keys
    ):
        raise ValueError("metrics_enabled requires DATABASE_URL and METRICS_SIGNING_KEYS")
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required: the conversation graph persists in PostgreSQL")
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
    if (gemini_key := settings.gemini_api_key) is not None:
        # Gemini uses Google's OpenAI-compatible endpoint. Its large nested operation schema is
        # projected to a shallow wire schema by GeminiProvider; LlmTurnOperations then validates
        # the complete response against the canonical Pydantic schema before execution.
        builders[GEMINI] = lambda: GeminiProvider(
            api_key=gemini_key.get_secret_value(),
            base_url=settings.gemini_base_url,
            timeout_seconds=settings.gemini_timeout_seconds,
            min_request_interval_seconds=settings.gemini_min_request_interval_seconds,
            reasoning_headroom=settings.gemini_reasoning_headroom,
            fallback_models=settings.gemini_fallback_models,
            shared_request_pacer=gemini_pacer,
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
    # Every model call this service makes goes through one of these, so wrapping them here counts
    # them all, whichever engine or reader made them: the `X-FinGuard-Model-Calls` header.
    providers = ProviderRegistry({name: _counted(build) for name, build in builders.items()})
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
    # One adapter for both readers of the wallet, like the balance and the address book. The chat
    # answering "cuánto llevo con cada tarjeta" from a different window than the screen shows would
    # be two figures on one phone, which is the failure those two were unified to avoid.
    cards = HttpCards(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
    )
    visual_model: VisualPlannerPort | None = None
    if settings.visual_planner != "deterministic":
        temperature, max_output_tokens = settings.visual_planner_sampling()
        visual_model = LlmVisualPlanner(
            providers.get(settings.visual_planner_provider or settings.planner_provider),
            model=settings.visual_planner_model or settings.planner_model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    visual_policy = VisualPlanningPolicy(
        model=visual_model,
        fallback=DeterministicVisualPlanner(),
        verifier=verify,
        mode=settings.visual_planner,
        budget_seconds=settings.visual_planner_budget_seconds,
    )
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

    # Conversational turns go through the buses like every other use case. The graph is built in
    # the lifespan, after this, so its handlers reach it through the application state and answer
    # 503 until it is there.
    def the_graph() -> ConversationTurnHandler | None:
        graph: ConversationTurnHandler | None = app.state.engine
        return graph

    command_bus.register(AnswerTurnCommand, AnswerTurnHandler(the_graph))
    query_bus.register(OwnsWorkflowQuery, OwnsWorkflowHandler(the_graph))
    app.state.query_bus = query_bus
    app.state.command_bus = command_bus
    app.state.metrics_executor = financial_metric_executor(banking, present)
    # Readiness reports on what /v1/questions needs, not on what /v1/messages needs.
    app.state.questions_enabled = settings.questions_enabled
    app.state.payments_enabled = settings.payments_enabled
    app.state.metrics_enabled = settings.metrics_enabled
    # The vocabulary a screen may offer. The same list the decoder is constrained to, so a
    # category a chip produces is one the assistant can name back.
    app.state.categories = settings.categories
    # For /version. The whole Settings object rather than the two strings, because the next thing
    # somebody wants from a running service is always another one of these.
    app.state.settings = settings
    app.state.planner_probe = planner_provider
    app.state.banking_probe = banking
    # Identity, resolved per request. A development adapter today; §12.2 asks for OAuth
    # and that is a swap of this one line.
    app.state.identity = StaticTokenIdentity(settings.callers())
    # Disabled metrics keep an in-memory adapter for unit tests. Enabled deployments replace it
    # during lifespan with the assistant-owned PostgreSQL adapter above.
    app.state.metrics_repository = MemorySavedMetricRepository(settings.metrics_max_saved)
    configured_metric_keys: dict[str, bytes] = {}
    for entry in settings.metrics_signing_keys:
        key_name, separator, key_value = entry.partition(":")
        if separator:
            import base64

            configured_metric_keys[key_name] = base64.urlsafe_b64decode(key_value + "=")
    if settings.metrics_enabled and not configured_metric_keys:
        raise ValueError("METRICS_SIGNING_KEYS contains no valid signing keys")
    # I4 (fix wave): the case the guard above never covered. Task 3 made the same codec seal
    # every visual context unconditionally, not only save candidates, so a deployment with
    # metrics *off* and no signing keys is still running the whole trust boundary on the
    # fallback key committed in this repository -- forgeable by whoever reads the source. No new
    # setting: `web_origins` is already required (`config.py`) and already distinguishes `just
    # app` (loopback only) from a real deployment, which is the same thing this refusal needs to
    # know. Loopback-only or no origins at all still just warns, below.
    if not configured_metric_keys and any(
        not _is_loopback_origin(origin) for origin in settings.web_origins
    ):
        raise ValueError(
            "METRICS_SIGNING_KEYS is required to serve a non-loopback origin: the visual seal "
            "would run on the development key"
        )
    # Task 3: the same codec now seals every visual context, not only save candidates -- a
    # deployment running on the fallback key is one whose signed contexts anybody who reads this
    # log line already knows how to forge, so it says so once, at the moment the choice was made.
    if not configured_metric_keys:
        logger.warning(
            "visual seals and metric tokens use the development key: set "
            "ORCHESTRATOR_METRICS_SIGNING_KEYS"
        )
    app.state.metric_tokens = MetricTokenCodec(
        configured_metric_keys or {"default": b"test-only-disabled-metrics-key-32bytes"},
        settings.metrics_signing_active if configured_metric_keys else "default",
    )

    # LangGraph is the only conversational runtime. The typed operation interpreter reads each
    # turn; the graph owns sequencing, durable state and execution. Its PostgreSQL store,
    # reservations and checkpointer exist only once the lifespan has opened the pool, so the
    # engine is built there from this factory.
    operations = LlmTurnOperations(
        planner_provider,
        model=settings.engine_model or settings.planner_model,
        temperature=settings.engine_temperature,
        max_output_tokens=settings.engine_max_output_tokens,
        categories={key: category_words.name_of(key) for key in settings.categories},
    )

    def build_graph(
        store: object, reservations: object, checkpointer: object
    ) -> ConversationTurnHandler:
        return graph_engine(
            settings,
            operations,
            planner_provider,
            banking,
            accounts,
            account_directory,
            contacts,
            store,  # type: ignore[arg-type]
            reservations,  # type: ignore[arg-type]
            checkpointer,  # type: ignore[arg-type]
            visual_policy,
            command_bus,
        )

    graph_factory = build_graph
    app.state.engine = None
    # The visual ladder the graph is built with, readable where the wiring is asserted.
    app.state.visual_policy = visual_policy

    # Before the routers, because a rejected preflight never reaches one. The web client cannot
    # call this service at all without it: a browser refuses a cross-origin request unless the
    # server says otherwise, which makes this the frontend's first dependency and not a detail.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Spelled out rather than "*": the answer to a preflight is a statement about what this
        # service accepts, and a wildcard makes that statement unfalsifiable.
        #
        # `Idempotency-Key` is the one that has to be remembered here. A header the routes read and
        # CORS does not list is not a header that gets ignored — the browser refuses the preflight
        # and never sends the request at all, so adding it to the client took the whole chat down
        # while every route still answered `curl` perfectly. A wildcard would have hidden that, and
        # would hide the next one too.
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match"],
    )

    # Outermost, so it sees the status a handler's exception turns into and the ones CORS answers
    # by itself. Every request accounted for is the property; a decorator per route would account
    # for the ones somebody remembered.
    register_request_logging(app)

    # The two halves of the observability adapter, and the order between them is the whole of the
    # wiring. **Recording is unconditional; exporting is optional** — the middleware that opens a
    # turn around every request is added whatever the settings say, so the decisions of a live turn
    # are recorded and assertable with nothing listening, and `configure_tracing` returns having
    # done nothing at all when `otel_enabled` is false, which is the default.
    #
    # The order between these two does not matter, which is worth stating because it looks as
    # though it should: the FastAPI instrumentation replaces the app's `build_middleware_stack`
    # rather than adding a middleware, so its inbound span wraps every user middleware whichever
    # was registered first, and every `turn` span is a child of the request that produced it.
    register_turn_recording(app)
    configure_tracing(app, settings=settings)
    # Which engine answered and how many model calls it cost, on both question routes, and the
    # 409 a stale engine version gets.
    engine_dispatch.register(app)

    app.include_router(assistant_router)
    app.include_router(account_router)
    app.include_router(graph_question_router)
    app.include_router(admin_router)
    app.include_router(metrics_router)
    register_error_handlers(app)
    return app


app = create_app()
