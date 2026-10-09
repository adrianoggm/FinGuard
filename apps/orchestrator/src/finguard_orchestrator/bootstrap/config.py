from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_orchestrator.domain.models.conversation import AccountScopeDefault
from finguard_orchestrator.domain.models.visual_plan import VisualMode
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.guard_policy import GuardPolicy
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure import TomlModelCatalogue

# The values live in .env.defaults, versioned and free of secrets, beside the reason each
# one is what it is. `.env` is loaded after it and wins, which is where a token table or a
# real credential goes — that file is gitignored.
#
# Anchored to the package rather than the working directory. `env_file="..."` resolves
# against the CWD, so a script run from the repository root would silently read no
# configuration at all and fall back to whatever the field defaults happened to be. There
# are no field defaults for the operational numbers, so it would fail loudly instead — but
# failing loudly for the wrong reason still costs somebody an afternoon.
_HERE: Final = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Service configuration.

    No operational number appears in this file. Each one is declared here with its type and
    read from .env.defaults, so the value and its justification sit together in one place
    that is not code, and changing a timeout is a configuration change rather than a commit.

    The fields below have no defaults on purpose. A service that invents its own timeouts is
    a service nobody configured, and the failure mode of a missing configuration file should
    be a refusal to start rather than a set of numbers somebody has to go and discover.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_file=(_HERE / ".env.defaults", _HERE / ".env"),
        extra="ignore",
        protected_namespaces=(),
    )

    # Path to the model catalogue. Which models exist, which provider serves each one
    # and what happens when one fails are configuration, not code (see
    # infrastructure/config/toml_model_catalogue.py). Unset means the built-in echo
    # catalogue, so the service starts with no file at all.
    llm_catalogue_file: Path | None = None

    # Where the ledger lives. Localhost by default: development credentials never leave
    # 127.0.0.1 (rule 13), and this service is useless without banking anyway.
    banking_base_url: str

    # How long to wait for the ledger, and how many movements to carry back as evidence.
    # Both were constants inside the adapter, which made them parameters and not
    # configuration — a slower machine needed a commit.
    banking_timeout_seconds: float
    # How many times a *read* against banking is attempted before the failure becomes visible
    # state. Reads have no side effects, so one immediate retry recovers a short transport
    # interruption; a second failure lets the user decide whether to try again.
    #
    # Operational policy, so it lives here rather than beside the code that uses it. Commands
    # deliberately do not retry at all: their outcome may be indeterminate after a timeout, and
    # this number must never be able to reach them.
    banking_read_attempts: int = 2
    supporting_movements: int

    # Where the local runtime listens. Configurable because inside a container 127.0.0.1
    # is the container: compose points this at host.docker.internal so the service can
    # reach the daemon on the machine, which is where the GPU is.
    ollama_base_url: str

    # The operational numbers of local inference. Each default is a measurement rather
    # than a preference and the adapter's module docstring says which — `num_ctx: 4096`
    # took P50 from 5.28s to 2.58s by keeping the model resident, and the pinned seed
    # removed a three-point swing between identical runs. They are configurable because
    # the next GPU will want different ones, not because these were guesses.
    ollama_keep_alive: str
    ollama_timeout_seconds: float
    ollama_context_tokens: int
    ollama_seed: int
    ollama_think: bool | Literal["low", "medium", "high"]

    # --- hosted inference (Groq), for comparing against a bigger model ----------------
    #
    # The key, and the only field in this class that is a secret. `SecretStr` so a stray log
    # line, a `repr` or a `/version`-style dump prints `**********` instead of a live
    # credential — the characters come out at exactly one call site, the composition root.
    #
    # **None means the provider does not exist.** Not "exists and fails at the first request":
    # a provider nobody configured cannot be built, which is the same rule that took Ollama out
    # of the registry's zero-argument builders. It also means the whole feature is off by
    # default, and turning it on is a deliberate act by whoever holds the key.
    #
    # It belongs in `.env`, which is gitignored, and never in `.env.defaults`, which is not.
    # Compose reads that same file, so there is one place for it whichever way this is started.
    groq_api_key: SecretStr | None = None

    # --- hosted inference (Cloudflare Workers AI) -------------------------------------
    #
    # A second registration of the same protocol rather than a second adapter — see
    # infrastructure/llm/cloudflare_workers_ai.py, which is where the reasoning lives and
    # where the three things a live token still has to settle are written down.
    #
    # Two fields, and the provider exists only when **both** are set. The token is the secret.
    # The account id is not one, but it is equally personal — it names whose Cloudflare account
    # is billed for every question asked — so it belongs in `.env` with the token and never as a
    # value in this file or in .env.defaults. Half a credential is worse than none: a missing
    # account id interpolates into `/accounts//ai/v1`, which is a 404 that reads like an outage.
    cloudflare_api_token: SecretStr | None = None
    cloudflare_account_id: str | None = None

    # --- expert agents (A2A) -------------------------------------------------------------
    #
    # Where the A2A gateway listens (apps/a2a-gateway). **Without it the capability does not
    # exist**: no agent is asked and the turn says no external agents are configured. Fail closed,
    # like the visual planner staying `deterministic` by default. Blank is absent -- see
    # `_a_blank_key_is_no_key`.
    a2a_gateway_url: str | None = None
    # The gateway's shared bearer token, and the key under which each gym's pairwise `memberRef` is
    # derived (spec §5.4). Secrets, so `SecretStr` and `.env` only, never `.env.defaults`.
    a2a_gateway_token: SecretStr | None = None
    a2a_pairwise_key: SecretStr | None = None
    # How long one agent has to answer, covering the send and its normalisation. No code default,
    # like every operational number here; the value and its reason are in .env.defaults.
    a2a_agent_budget_seconds: float = Field(gt=0)

    @field_validator(
        "groq_api_key",
        "cloudflare_api_token",
        "cloudflare_account_id",
        "visual_planner_provider",
        "visual_planner_model",
        "visual_planner_temperature",
        "visual_planner_max_output_tokens",
        "engine_model",
        "a2a_gateway_url",
        "a2a_gateway_token",
        "a2a_pairwise_key",
        mode="before",
    )
    @classmethod
    def _a_blank_key_is_no_key(cls, value: object) -> object:
        """`ORCHESTRATOR_XAI_API_KEY=` means the provider does not exist, not that it has a
        blank credential.

        An empty string is what a half-filled `.env` line produces, and what compose passes when
        a `${VAR:-}` interpolation finds nothing. Taken literally it builds a provider whose every
        request is a 401 — the exact failure the "no key, no provider" rule exists to prevent,
        arriving by the one route that rule does not cover.

        The account id gets the same treatment because it fails the same way from one line lower:
        blank, it is not a rejected credential but a URL with a hole in it.

        The visual planner's own settings share the treatment for the same reason one level up:
        `ORCHESTRATOR_VISUAL_PLANNER_MODEL=` is what an unset override looks like on disk, and
        taken literally it would fail float/int coercion instead of falling back to the planner's.
        The engine's model shares it for the same reason: blank is "the planner's model".

        The expert agents' three share it: `ORCHESTRATOR_A2A_GATEWAY_URL=` is how .env.defaults
        says the capability does not exist, and a blank token or pairwise key is no key at all.
        """
        return None if isinstance(value, str) and not value.strip() else value

    # Defaults rather than required fields, unlike every operational number above, because
    # these have a right answer that does not belong to a deployment: the endpoint is the
    # vendor's and the timeout is a ceiling for "something is wrong". A deployment that never
    # uses the provider should not have to state them.
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 60.0

    # The floor this puts under a caller's token budget when the endpoint is a hosted one.
    #
    # A reasoning model generates its thinking first and bills it against the same ceiling, so a
    # budget sized for the answer truncates the reasoning and the request fails with a message
    # about JSON rather than about tokens. Measured against openai/gpt-oss-120b with this
    # service's own prompts: reading a turn generated 224 tokens for a ten-token answer, and
    # planning a query generated 698-907 for a plan of about 120. The callers ask for 32 and 384.
    #
    # A ceiling and not a reservation — you pay for what is generated — so this is generous on
    # purpose. It buys nothing on a non-reasoning model and it is the difference between working
    # and not on a reasoning one.
    groq_reasoning_headroom: int = 2048

    # The API root the account path is hung off. A default for the same reason Groq's base URL
    # is one — the endpoint is the vendor's, not the deployment's — and a field at all so a
    # proxy or a recorded fixture can stand in front of it without a commit.
    cloudflare_api_base: str = "https://api.cloudflare.com/client/v4"
    cloudflare_timeout_seconds: float = 60.0

    # A larger floor than Groq's, for the same reason, on a different endpoint.
    # `@cf/qwen/qwen3-30b-a3b-fp8` is a reasoning model, so a budget sized for the answer is a
    # budget the thinking eats. Measured on the planning prompt: one exclusion case consumed all
    # 2048 completion tokens as reasoning and returned null content with finish_reason `length`;
    # 4096 completed the same constrained plan. A higher ceiling is billed only when generated.
    cloudflare_reasoning_headroom: int = 4096

    # Which provider turns a sentence into a plan, and reads a turn.
    #
    # It was `ollama` in code at the composition root, which made "try a bigger model" a patch
    # rather than a setting. The catalogue beside it chooses who *answers* /v1/messages; this
    # chooses who *plans*, and they stay separate for the reason they always have — planning is
    # measured its own way and one knob for both would hide which one moved.
    planner_provider: str = "ollama"

    # Planner sampling. These were literals at the composition root, which is the same
    # defect one layer up. Temperature 0 is deliberate: the planner is not being creative,
    # and a plan that varies between identical questions cannot be measured.
    planner_temperature: float
    planner_max_output_tokens: int

    # Development identity, as "token:holder:account_uuid" entries. Not the design —
    # §12.2 asks for OAuth 2.0 with short-lived tokens — but the seam is real: the
    # account comes from the credential, so a caller cannot name someone else's.
    #
    # Set through ORCHESTRATOR_DEV_TOKENS. It has no default on purpose: a service that
    # ships with a built-in credential ships with a credential everyone knows.
    dev_tokens: tuple[str, ...] = ()

    # The financial endpoints, off unless a deployment turns them on.
    #
    # Not caution for its own sake. Both open risks produce a confident, plausible, wrong
    # figure rather than an error, which is the one failure mode nobody downstream catches.
    # Documenting a risk is not controlling it; a flag is, and it makes turning this on a
    # decision somebody takes rather than a default nobody chose.
    #
    # The bar is now written down and signed, in docs/next.md — two gates, because a scripted
    # demo over seeded data and somebody's real balance are not the same risk. Zero for what
    # deterministic code can guarantee, a number for what a model decides.
    #
    # Re-measured 2026-08-28: gate 1 fails on **one** of eight, and it is not the one this
    # comment used to name. `invalid structure` is 0 on all five sets in every run, so that half
    # closed and nobody noticed for three weeks. Unsafe execution is 11.4% on the held-out set
    # against a bar of 10% — it was 7.6% on 2026-08-03, and the 158 planning-path commits in
    # between are where it moved, not the conversational fixes of the day it was re-measured.
    #
    # So a deployment turning this on today is turning on a surface whose own gate does not pass.
    # compose.yaml does exactly that, deliberately, over seeded data on loopback. Anywhere else
    # that is a decision somebody has to take out loud.
    questions_enabled: bool
    payments_enabled: bool
    metrics_enabled: bool = False
    metrics_signing_keys: tuple[str, ...] = ()
    metrics_signing_active: str = "default"
    metrics_max_saved: int = Field(default=50, ge=1, le=50)
    database_url: str | None = None

    # The commit this image was built from, stamped in by the build rather than read from git at
    # runtime — a container has no repository in it. "unknown" is honest for a build that did not
    # pass one, and it is what a local `uvicorn` run reports.
    build_sha: str = "unknown"
    build_ref: str = "unknown"

    # The home screen's window and movement count. Product decisions about what a bank app shows
    # when it opens, so configuration rather than constants in the adapter that uses them.
    summary_days: int = Field(ge=1, le=366)
    summary_movements: int = Field(ge=1, le=50)

    # Browser origins allowed to call this service. Exact origins, never a wildcard: the credential
    # is in a header, and `*` with credentials lets any page a user visits spend their session.
    web_origins: tuple[str, ...]

    # How loud, and in which shape. Operational policy, so configuration — a developer wants lines
    # they can read in a terminal and a deployment wants objects something can index. What is *in*
    # a record is not configurable and never should be: see the redaction note in observability.py.
    log_level: str = "INFO"
    log_json: bool = True

    # Whether anything is exported, and to where.
    #
    # **False by default, and that default is load-bearing.** A test run and a developer's laptop
    # must not need a collector, so the whole SDK stays unbuilt unless a deployment says otherwise
    # — `just app` does not pay for the observability profile either, which is the same decision
    # one layer out.
    #
    # What this does *not* switch off is the recording. The decisions of a turn are recorded
    # whether or not anybody is listening, and only the export asks this question; see the module
    # docstrings of `observability/decisions.py` and `observability/otel.py`, which both argue it
    # at length because it is the property six green-but-dead instruments in the last week were
    # missing. If a flag here ever starts deciding whether a call site executes, that is the
    # seventh.
    #
    # These two have defaults, unlike every operational number above, for the reason the vendor
    # base URLs do: there is nothing about them a deployment has to state in order to be correct.
    # Off is a complete configuration.
    otel_enabled: bool = False

    # The collector's OTLP/HTTP root, without a signal path — `observability/otel.py` appends
    # `/v1/traces` and `/v1/metrics`, because the HTTP exporter posts to an explicit endpoint
    # verbatim and a base URL handed straight to it is a 404 on every export that nothing outside
    # the SDK's own logs would show.
    #
    # HTTP and not gRPC, and 4318 and not 4317: the collector opens both and says why — inside
    # compose the orchestrator reaches it by service name, but `just serve-orchestrator` runs it on
    # the host, and OTLP/HTTP is the protocol that survives every proxy in between. Loopback, since
    # that is where compose publishes it (rule 13). A compose deployment overrides this with
    # http://otel-collector:4318.
    otel_endpoint: str = "http://127.0.0.1:4318"

    # Whether the deterministic cascade's path is exported as a span per reader consulted.
    #
    # **True, and it is only ever asked once `otel_enabled` is already true.** The compound default
    # is therefore "nothing at all on a laptop or in a test run, and the whole path the moment
    # somebody turns tracing on" — which is right, because seeing which readers the reasoning fell
    # through and which called which is the thing they turned it on for. A default of False would
    # be the fourth flag in a row whose off position is the one everybody has, and this design
    # document's own lesson is that the configuration that makes instrumentation run is the
    # configuration nobody has.
    #
    # What it costs when it is on: measured, not guessed. An ordinary spending question exports 29
    # spans with this true and 7 with it false — one `turn`, six decisions, and twenty-two
    # consultations, being the nineteen readers the cascade asks plus the three it reaches through
    # another. A compound question pays that whole cost once per clause, and a clause list is
    # bounded at three. `tests/test_otel.py` asserts the nineteen, so a change to the cascade that
    # doubles it arrives as a failing number rather than as a Tempo bill.
    #
    # What it does *not* switch off is the recording. `decisions.consulting` builds the tree
    # unconditionally, exactly like `record`, so a turn is still assertable with tracing off; this
    # decides only whether the tree is drawn. If this flag ever starts deciding whether a call site
    # executes, that is the seventh green-but-dead instrument.
    otel_recogniser_spans: bool = True

    # The model that turns a sentence into a plan. Separate from the catalogue on
    # purpose: the catalogue chooses who *answers*, and planning is a different job with
    # a different measurement — scripts/evaluate_planning.py is what picks this value.
    #
    # Eleven models were measured and this one is fourth on accuracy. It is here because
    # the reason to prefer the others stopped being a model property.
    #
    # qwen3.5:9b was chosen for `unsupported` — 3/5 held out against this one's 1/5, the
    # worst open defect in the phase. The capability guard now refuses those before a
    # model is consulted, deterministically and identically whichever model is
    # configured, so that 2-case advantage is gone and 6.4 points of accuracy are what
    # remains on one side.
    #
    # On the other side is the only property here that is not a percentage: 4.7 GB fits
    # an 8 GB card whole and 6.6 GB does not, which puts 14% of the layers on the CPU on
    # every single request. Measured under offload before, a 4B ran slower than a 7B.
    #
    # So this is the fast, fully resident model, and the accuracy gap is now a known
    # cost rather than a risk — the guard and the verifier catch what it gets wrong in
    # the directions that matter. Revisit when the GPU changes or when the gap shows up
    # in a group the guard does not cover. See evaluation/planning/RESULTS.md.
    planner_model: str

    # Deterministic rules to switch off, by name. Empty means every rule is on, which is
    # the only sane default: a guard that is off by accident reads like a control while
    # enforcing nothing.
    #
    # It exists so each rule's worth can be measured rather than argued. Every one of them
    # refuses something, so every one can be wrong in the expensive direction — turning a
    # question the pipeline answers into a dead end — and "is this rule earning its place?"
    # deserves a number. `scripts/evaluate_planning.py --ablate` produces that number by
    # replaying each policy over one recorded set of model answers.
    #
    # A name this build does not have is a startup error, not a shrug: the quiet outcome
    # is an operator who believes a rule is off, measures with it on, and concludes it is
    # worthless.
    disabled_guards: tuple[str, ...] = ()

    # Who reads a turn: the rules, the rules with a model watching, or the model.
    #
    #   router   the deterministic rules alone. Today's behaviour, and the default.
    #   shadow   both. The rules still decide and the model's opinion becomes a log line, which is
    #            the number that has to exist before anybody argues about the third value. Costs a
    #            model call per turn and changes no answer.
    #   hybrid   the rules keep every turn they recognise; the model may widen only their catch-all
    #            and may never create a payment or own-account transfer.
    #   model    `Promoted`: the model decides everything except a payment, which the rules keep.
    #
    # Named modes rather than booleans because "shadow and promoted" is not a thing — a model
    # cannot both be watched and be the authority, and a pair of switches would let somebody
    # configure that and get one of them silently ignored.
    #
    # The case for `model` is measured and it is lopsided. On the 879-turn corpus the rules score
    # 100 % by construction — their answers *are* its labels — so that corpus can never show them
    # failing, and the model's 91.5 % on it is a disagreement rate with the rules rather than an
    # error rate. On 96 sentences written by something that had never seen this vocabulary, the
    # rules reach 37 and `Promoted` reaches 86. See test_the_same_question_said_differently.py.
    #
    # 86 and not the 88 the model scores alone, because nobody can run the model alone: two of the
    # sentences it read as payments are refused by the rule above. Both are worth the refusal —
    # "puedo hacerlo por bizum, ¿verdad?" and "¿cómo lo hago?" ask how the thing works rather than
    # instructing anybody to send money — so the two points are the rule doing its job, not its
    # price.
    #
    # **And then the other side was measured, and `model` is not ready.** Replaying all 133
    # episodes through `Promoted` — 900 turns, the tool each one reached — costs 35 failures, and
    # two of them move money:
    #
    #   * "Déjalo" is read as manners, so the transfer it was cancelling stays open, and the "500"
    #     in the next turn lands inside it. Two episodes, and it is the failure this service exists
    #     to make impossible.
    #   * "¿Qué traspaso al fondo?" and "mueve 200 € a la cuenta de la empresa" — a question and an
    #     account the holder does not have — both come back as transfers. `Promoted` refuses to let
    #     a model start a PAYMENT and says nothing about OWN_TRANSFER, which is the same decision
    #     one rail over. A hole in the rule, found by measuring rather than by reading it.
    #   * The short balance vocabulary regresses: "saldo", "dinero disponible" and "¿cuánto tenía?"
    #     stop reaching the balance, and "cuánto he gastado con cada tarjeta" stops reaching the
    #     wallet and answers with the account total — the exact defect the wallet intent fixed.
    #
    # The other 23 are one shape: the model over-specifies when the reading depends on the turn
    # before. "¿Y en la hucha?" after a question about spending is a narrowed *query*, and the
    # model calls it a balance. That is the interpreter's prompt, not the arrangement, and it is
    # where the work goes before this is worth revisiting.
    #
    # So the honest summary is that neither reader dominates. The rules lose to phrasing and the
    # model loses to context, and the corpus and the drift set each only measure one of those.
    #
    # Still not the default, and now for a second reason. Flipping how every turn in production is
    # read is the operator's call rather than a value shipped in a settings class; and on today's
    # numbers there is no case for flipping it.
    turn_interpreter: Literal["router", "shadow", "hybrid", "model"] = "router"

    # Which engine leads an analytics conversation (spec 2026-10-09, "LLM conversation engine").
    #
    #   rules   the handler every turn has run on: readers, a turn arbiter, a planner. What ships.
    #   model   the phase-1 prototype: a model turns each turn into typed operations, pure code
    #           applies them to a versioned state held server-side, and money turns (payments,
    #           transfers, bookings, confirmations) still go to the rules route, which guards them.
    #
    # `rules` by default because `model` is a prototype under measurement, not a promotion: its
    # store lives in one process and forgets every conversation on a restart, and whether it beats
    # the rules is what Task 7 of the plan measures. With `rules` nothing of the engine is built and
    # both question routes answer byte for byte as they did before it existed.
    conversation_engine: Literal["rules", "model"] = "rules"
    # The engine's model, on the planner's provider (`planner_provider`). None means the planner's
    # own model, so pointing the planner at a hosted provider moves the engine with it.
    engine_model: str | None = None
    # The interpreter's answer: one to four operations. 600 keeps prompt and answer inside the
    # local model's 4096-token window (the prompt is held to ~2,800 tokens).
    engine_max_output_tokens: int = Field(default=600, gt=0)
    # Zero for the planner's reason: an interpretation that varies between identical turns cannot
    # be measured.
    engine_temperature: float = 0.0

    # What a spending or income question means when it names no account and the conversation has
    # chosen none.
    #
    #   all   every account the holder has, which is what «¿cuánto gasté?» means to whoever asks
    #         it — and the answer says so («Consultado en todas tus cuentas.»), unless the person
    #         asked for every account in those words.
    #   ask   «¿En qué cuenta quieres consultarlo?» first, as every thread began until 2026-10-07.
    #
    # `all` since spec §3.5 (conversation state, slice 1). Live that day, «¿Cuánto llevo gastado en
    # Spotify?» was asked which account and then which period before any figure: two questions
    # about things the person had an ordinary meaning for. `ask` keeps that question one variable
    # away for a deployment that wants it. Neither touches an account the sentence names, a choice
    # the person already made in the conversation, a balance (every account, listed) or a payment
    # (the credential's own account).
    account_scope_default: AccountScopeDefault = "all"

    # The visual planner's ladder, the same four rungs the turn interpreter climbs. `shadow` runs
    # the model beside the floor and shows the floor, which is how the baseline comparison is
    # collected before anything is risked. Promotion is a measured decision (docs/next.md), not a
    # default: this stays `deterministic` until three runs of `just evaluate-visual` say otherwise.
    visual_planner: VisualMode = "deterministic"
    # Spent before the floor answers, so it is added latency on every turn the model runs.
    # `6.0` is a guess, not a measurement: Task 9's runbook
    # (docs/superpowers/plans/2026-09-05-visual-planner-shadow.md) has never been run, so no
    # `evaluation/visual/last-run.json` exists to set this from (M-4, M-5). And the method the
    # runbook prescribes is right-censored by construction -- a run made *at* budget B records
    # every timed-out call's `latency_ms` as B, so its own P95 can never exceed B and "set the
    # budget from the P95 of a run at this budget" would silently ratify whatever B was already
    # guessed. Generous here so a future `shadow` run measures the model rather than the budget.
    # `gt=0`: `VisualPlanningPolicy.__init__` refuses a non-positive budget with a bare
    # `ValueError` and no field name, so an operator's `0` is caught here, at load, with a named
    # validation error, rather than during `create_app()`.
    visual_planner_budget_seconds: float = Field(default=6.0, gt=0)

    # The visual planner's own provider, model and sampling. `None` means "the financial
    # planner's", which is what shipped and what `deterministic` mode never reads. Naming one
    # here lets "try a bigger chart model" move nothing else: the financial planner's model was
    # chosen against its own evaluation (evaluation/planning/RESULTS.md), and one knob for both
    # would hide which one moved. A blank value in the environment is "unset" -- see
    # `_a_blank_key_is_no_key`.
    visual_planner_provider: str | None = None
    visual_planner_model: str | None = None
    visual_planner_temperature: float | None = None
    visual_planner_max_output_tokens: int | None = None

    def visual_planner_sampling(self) -> tuple[float, int]:
        """Temperature and token limit for the visual planner: its own when set, else the planner's.

        `is None`, not `or`: an explicit temperature of 0.0 is a setting, not an absence.
        """
        temperature = (
            self.planner_temperature
            if self.visual_planner_temperature is None
            else self.visual_planner_temperature
        )
        tokens = (
            self.planner_max_output_tokens
            if self.visual_planner_max_output_tokens is None
            else self.visual_planner_max_output_tokens
        )
        return temperature, tokens

    # The categories that exist in the data. They belong to banking; until there is a
    # legitimate place to read them from, they are stated here and drift shows up as a
    # rejected plan rather than as a wrong figure.
    categories: tuple[str, ...] = (
        "GROCERIES",
        "TRANSPORT_FUEL",
        "RESTAURANTS",
        "TRANSPORT_PUBLIC",
        "HEALTH",
        "LEISURE",
        "SUBSCRIPTIONS",
        "HOUSING_RENT",
        "SALARY",
        "TRANSFER_BIZUM",
        "INTEREST",
    )

    def callers(self) -> dict[str, Caller]:
        """Parses the token table. Fails loudly on a malformed entry rather than
        skipping it: a silently dropped token is a caller who cannot log in and an
        afternoon spent looking at the wrong layer.

        The zone is the optional fourth field. Optional because most holders share the
        deployment's, present because "hoy" belongs to whoever is asking — a holder in the
        Canaries has a different one for an hour every night, and being told about the wrong
        day is a real figure answering a question nobody asked.
        """
        table: dict[str, Caller] = {}
        for entry in self.dev_tokens:
            # maxsplit, because a scope name contains a colon: "questions:read" would
            # otherwise be split into two fields and the entry rejected as malformed. The
            # scopes are last precisely so they can keep their own separator.
            parts = entry.split(":", 4)
            if len(parts) not in (3, 4, 5):
                raise ValueError(
                    f"a token entry is token:holder:account_uuid[:timezone[:scopes]], got {entry!r}"
                )
            token, holder, account = parts[:3]
            zone = parts[3] if len(parts) > 3 and parts[3] else "Europe/Madrid"
            # Comma-separated, because the entry is already colon-delimited. Absent means
            # the two read permissions, which is what a development credential needs and
            # deliberately not the admin one: swapping the model that answers every
            # question for everybody has to be granted on purpose.
            granted = (
                parts[4].split(",")
                if len(parts) == 5 and parts[4]
                else [
                    Scope.QUESTIONS_READ.value,
                    Scope.METRICS_READ.value,
                    Scope.METRICS_WRITE.value,
                    Scope.ASSISTANT_USE.value,
                    # Relabelling your own movement moves no money and is undone by another
                    # request. A development credential that can read the account can file it.
                    Scope.MOVEMENTS_CATEGORISE.value,
                ]
            )
            if token in table:
                # Two entries sharing a secret is a typo, and the quiet outcome is the
                # dangerous one: the token would resolve to whichever account came last.
                # Configuration errors often end up in CI and container logs. The
                # operator already has the table in front of them; repeating even a
                # prefix of the shared secret there only creates another copy to leak.
                raise ValueError("a development token is configured twice")
            table[token] = Caller(
                holder_reference=holder,
                account_id=UUID(account),
                reporting_timezone=zone,
                scopes=frozenset(granted),
            )
        return table

    def guard_policy(self) -> GuardPolicy:
        """Validates the names as it builds it, so a typo fails at startup."""
        return GuardPolicy(disabled=frozenset(self.disabled_guards))

    def catalogue(self) -> TomlModelCatalogue:
        if self.llm_catalogue_file is None:
            return TomlModelCatalogue.built_in()
        return TomlModelCatalogue.from_file(self.llm_catalogue_file)


@lru_cache
def get_settings() -> Settings:
    """Every field without a default is supplied by .env.defaults, which mypy cannot see —
    the pydantic plugin reads the class and not the environment, so it asks for arguments
    the environment provides. The alternative is giving the fields defaults, which is the
    thing this change removed."""
    return Settings()  # type: ignore[call-arg]
