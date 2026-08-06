from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    @field_validator("groq_api_key", "cloudflare_api_token", "cloudflare_account_id", mode="before")
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

    # The same floor as Groq's, for the same reason, on a different endpoint.
    # `@cf/qwen/qwen3-30b-a3b-fp8` is a reasoning model, so a budget sized for the answer is a
    # budget the thinking eats. The number is borrowed from the measurement above and is not a
    # measurement of Cloudflare — it costs nothing to be generous, because a ceiling is only
    # billed when a model reaches it.
    cloudflare_reasoning_headroom: int = 2048

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
    # As measured today, gate 1 fails on two of eight: unsafe execution is 12.7% against a
    # bar of 10%, and one invalid structure survives in the adversarial set where the bar is
    # zero. Both have identified work rather than needing a discovery.
    questions_enabled: bool
    payments_enabled: bool

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
    #   model    `Promoted`: the model decides everything except a payment, which the rules keep.
    #
    # Three values rather than two booleans because "shadow and promoted" is not a thing — a model
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
    # Still not the default. Flipping how every turn in production is read is the operator's call,
    # not a value shipped in a settings class, and `model` costs a model call on the critical path
    # of every single turn where the rules cost none.
    turn_interpreter: Literal["router", "shadow", "model"] = "router"

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
