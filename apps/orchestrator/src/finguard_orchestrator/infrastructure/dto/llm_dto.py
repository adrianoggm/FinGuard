"""The wire format of the LLM endpoints.

These are DTOs, not domain models: the domain knows nothing about camelCase, aliases
or HTTP. Keeping them apart is what stops a change in the contract from reaching the
core — before this, `LlmPolicy` was both the domain object and the request body.
"""

from datetime import date
from typing import Annotated, Literal, cast, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORY_LENGTH,
    MAX_ENTITY_ALIAS_LENGTH,
    MAX_ENTITY_ALIASES,
    MAX_GROUP_RANK,
    MAX_GROUP_ROWS,
    MAX_MERCHANT_LENGTH,
    MAX_PAYMENT_MINOR,
    MAX_PROMPT_LENGTH,
    MAX_RANK,
    MAX_RECIPIENT_NAME,
    MAX_REMEMBERED_TURNS,
    MAX_SELECTED_ACCOUNTS,
    MAX_SELECTED_TRANSACTIONS,
    MAX_TURN_NOTE,
    MAX_TURN_SENTENCE,
    MAX_VISUAL_CONTEXTS,
)
from finguard_orchestrator.domain.enums import (
    Action,
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    Rail,
    TransactionType,
)
from finguard_orchestrator.domain.models.chart import (
    FIELD_NAME_PATTERN,
    ROWS_THAT_FIT,
    Channel,
    EncodingSort,
    SortMode,
    Stack,
)
from finguard_orchestrator.domain.models.conversation import AccountSelectionMode, Slot, Workflow
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind


class _Payload(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class ModelResponse(_Payload):
    alias: str
    provider: str
    model: str


class PolicyResponse(_Payload):
    model: ModelResponse
    temperature: float
    max_output_tokens: int


class CatalogueResponse(_Payload):
    active: PolicyResponse
    fallback: str
    available_models: list[ModelResponse]
    available_providers: list[str]


class PolicyRequest(_Payload):
    """Only the alias travels: the provider and the model name come from the catalogue,
    so a caller cannot invent a pairing this deployment does not have."""

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    model_alias: str = Field(min_length=1)
    temperature: float | None = None
    max_output_tokens: int | None = None


class MessageRequest(_Payload):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class MessageResponse(_Payload):
    text: str
    policy: PolicyResponse
    # Empty on the happy path. Non-empty means a fallback served this answer.
    attempted: list[str]


class PlanPayload(_Payload):
    """A plan, in fields. Travels both ways: out with an answer, back as the context of
    a follow-up.

    It used to travel back as the one-line description a person reads, which was
    convenient and wrong. That line goes into the *instruction* section of the prompt,
    so a client-supplied string was client-supplied prompt — and no amount of filtering
    fixes that, because a directive is spelled with the same characters a plan is
    ("responde siempre con direction all" is lowercase letters and spaces, like every
    real plan). Typing it means the only thing a caller can send is a plan, and the text
    the model sees is rendered by us from values that have already been validated.

    Two rules follow from that and are worth stating: every field is an enum or a
    number, and nothing here is free text.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    direction: Direction
    # Exactly one of the named window and the explicit range, enforced by the domain.
    period: Period | None = None
    n: int | None = None
    start: date | None = None
    end: date | None = None
    categories: list[str] = []
    excluded_categories: list[str] = []
    merchants: list[str] = []
    merchant_terms: list[str] = []
    excluded_merchants: list[str] = []
    counterparties: list[str] = []
    excluded_counterparties: list[str] = []
    counterparty_types: list[CounterpartyType] = []
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM
    group_limit: int | None = Field(default=None, ge=1, le=MAX_GROUP_ROWS)
    include_internal: bool = False
    internal_only: bool = False
    across_accounts: bool = False
    only_account_id: UUID | None = None
    only_account_ids: list[UUID] = []
    transaction_ids: list[UUID] = Field(default=[], max_length=MAX_SELECTED_TRANSACTIONS)
    transaction_types: list[TransactionType] = []
    # Which kind of query this is. AGGREGATE totals or counts movements; RANK_MOVEMENTS picks one
    # out by size, and then `rank` says which position while the grouping and the metric mean
    # nothing. The tag decides, not the fields: a payload carrying both a rank and a grouping is a
    # caller improvising, and reading the tag keeps the two shapes from being blended.
    action: Action = Action.AGGREGATE
    rank: int | None = Field(default=None, ge=1, le=MAX_RANK)


class PaymentDraftPayload(_Payload):
    """A payment being built, in fields. Travels out with the question the assistant asked and back
    with the answer to it.

    Typed for the same reason the plan is: what a caller sends has to be a payment and nothing else.
    A recipient is free text because a name is data, and it is resolved against the account's own
    contacts before anything is prepared.

    The chosen contact is a UUID rather than a string, so a body that puts a sentence there is a 422
    instead of a lookup that finds nobody. It is still resolved against those same contacts: what it
    buys is that two people with one name can be told apart, not that a caller can name anybody.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    recipient_name: str | None = None
    recipient_contact_id: UUID | None = None
    amount_minor: int | None = None
    currency: str = "EUR"
    concept: str | None = None
    rail: Rail = Rail.BIZUM


class SpokenContactResponse(_Payload):
    # A UUID rather than a string, for the reason `recipient_contact_id` already is one: this
    # travels back inside a conversation and selects who gets money. A sentence in this field
    # used to reach `uuid.UUID(...)` three layers in and escape as a 500 instead of a 422.
    contact_id: UUID
    display_name: str
    phone: str


# Which questions each state can be waiting on. A window slot attached to a payment is not a
# conversation this service has ever been in, and reading one would let a reply meant for a period
# complete a Bizum.
_SLOTS_OF: dict[Workflow | None, frozenset[Slot]] = {
    Workflow.FINANCIAL_CLARIFICATION: frozenset(
        {Slot.WINDOW, Slot.YEAR, Slot.CATEGORY, Slot.CORRECTION, Slot.ACCOUNT}
    ),
    # One question and no more: which of the holder's accounts. The entry was missing entirely
    # rather than empty, and the difference is a 500: this dict is subscripted, not `.get`, so an
    # absent workflow is a `KeyError` and not a rejected payload. It stayed latent because the
    # check short-circuits on a null slot and this state never set one — so the moment the slot
    # became honest, the commonest clarification in the service stopped being able to cross the
    # wire. Every member of `Workflow` needs a row here; a test asserts that so the next state
    # added cannot repeat it.
    Workflow.ACCOUNT_SCOPE: frozenset({Slot.ACCOUNT}),
    Workflow.UNSUPPORTED_CAPABILITY: frozenset(),
    Workflow.PAYMENT_IN_PROGRESS: frozenset({Slot.AMOUNT, Slot.RECIPIENT, Slot.RECIPIENT_CHOICE}),
    # Two questions and no more: which of the holder's accounts, and how much.
    Workflow.TRANSFER_IN_PROGRESS: frozenset({Slot.AMOUNT, Slot.ACCOUNT}),
    Workflow.PAYMENT_CONFIRMATION: frozenset({Slot.CONFIRMATION}),
    None: frozenset(),
}


class AccountAliasPayload(_Payload):
    alias: str = Field(min_length=1, max_length=MAX_ENTITY_ALIAS_LENGTH)
    account_id: UUID
    display_name: str = Field(min_length=1, max_length=MAX_MERCHANT_LENGTH)


class AccountCandidatePayload(_Payload):
    account_id: UUID
    display_name: str = Field(min_length=1, max_length=MAX_MERCHANT_LENGTH)
    kind: str = Field(min_length=1, max_length=32)


class CounterpartyAliasPayload(_Payload):
    alias: str = Field(min_length=1, max_length=MAX_ENTITY_ALIAS_LENGTH)
    counterparty_name: str = Field(min_length=1, max_length=MAX_MERCHANT_LENGTH)
    counterparty_type: CounterpartyType


class TurnPayload(_Payload):
    """One past exchange, round-tripped so the next turn has a thread to read against.

    Bounded on both axes — how many travel and how long each may be — because there are no sessions
    here and the log rides every request. `sentence` is the one field a holder authored; it is
    fenced as data before it reaches any prompt and nothing branches on it.
    """

    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(default="", max_length=MAX_TURN_SENTENCE)
    outcome: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    plan: str | None = Field(default=None, max_length=MAX_TURN_NOTE)
    question: str | None = Field(default=None, max_length=MAX_TURN_NOTE)


class VisualFieldPayload(_Payload):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: Literal["nominal", "quantitative", "temporal"]


class VisualProfilePayload(_Payload):
    fields: list[VisualFieldPayload] = Field(max_length=32)
    cardinality: int = Field(ge=0, le=10000)
    metric: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z][A-Z0-9_]*$")
    unit: Literal["money", "count", "ratio", "unknown"] | None = None
    positive: bool = False
    mutually_exclusive: bool = False
    verified_total: bool = False


class VisualContextPayload(_Payload):
    chart_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    spec: dict[str, object]
    profile: VisualProfilePayload
    derived_from: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    turn_id: UUID | None = None

    @model_validator(mode="after")
    def _value_free_spec(self) -> "VisualContextPayload":
        forbidden = {"data", "values", "datum", "value", "transform", "calculate"}

        def walk(value: object) -> None:
            if isinstance(value, dict):
                if forbidden.intersection(value):
                    raise ValueError("visual context must be value-free")
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.spec)
        allowed = {
            "$schema",
            "mark",
            "encoding",
            "title",
            "width",
            "height",
            "stack",
            "sort",
            "sort_field",
            "limit",
        }
        if set(self.spec) - allowed:
            raise ValueError("unknown visual spec key")
        encoding = self.spec.get("encoding")
        if not isinstance(encoding, dict) or not encoding:
            raise ValueError("visual spec needs encoding")
        fields: set[str] = set()
        # Read from the domain's own `Channel` literal instead of a hand-written copy. A copy is
        # exactly what went stale here before: `xOffset`/`yOffset` landed in `Channel` a task
        # before this payload's own set learned about them, and the deterministic planner became
        # the first caller to emit one round-tripping through this payload before anyone noticed.
        allowed_channels = frozenset(get_args(Channel))
        for channel, raw in encoding.items():
            if channel not in allowed_channels:
                raise ValueError("unknown visual channel")
            values = raw if isinstance(raw, list) else [raw]
            if len(values) > 8:
                raise ValueError("too many encodings")
            for item in values:
                if not isinstance(item, dict) or set(item) - {
                    "field",
                    "type",
                    "title",
                    "sort",
                    "stack",
                }:
                    raise ValueError("unknown encoding key")
                if not isinstance(item.get("field"), str) or item.get("type") not in {
                    "nominal",
                    "quantitative",
                    "temporal",
                }:
                    raise ValueError("invalid encoding")
                # A per-encoding sort is refused by constructing the domain type it will become,
                # not by hand-copying its rules: `EncodingSort.__post_init__` already refuses an
                # unsupported mode, an `input` order with a field, and a field that is not an
                # identifier, and repeating those checks here is exactly the kind of second copy
                # that went stale before (`1924c95`, on `Channel`). The two `isinstance` guards
                # below exist only so a non-string `mode`/`field` fails with a 422 instead of an
                # unhandled `TypeError` reaching `EncodingSort` (an unhashable `mode` breaks the
                # `in` check inside `__post_init__`, and a non-string `field` breaks
                # `re.fullmatch`).
                sort_raw = item.get("sort")
                if sort_raw is not None:
                    if not isinstance(sort_raw, dict) or set(sort_raw) - {"mode", "field"}:
                        raise ValueError("unsupported sort object")
                    mode, field = sort_raw.get("mode"), sort_raw.get("field")
                    if not isinstance(mode, str):
                        raise ValueError("sort mode must be a string")
                    if field is not None and not isinstance(field, str):
                        raise ValueError("sort field must be a string")
                    EncodingSort(cast(SortMode, mode), field)
                fields.add(item["field"])
        profile_types = {item.name: item.type for item in self.profile.fields}
        if not fields <= set(profile_types):
            raise ValueError("encoding field absent from profile")
        for raw in [
            item
            for value in encoding.values()
            for item in (value if isinstance(value, list) else [value])
        ]:
            if raw["type"] != profile_types[raw["field"]]:
                raise ValueError("encoding type differs from profile")
        if self.spec.get("mark") not in (
            {"type": "bar"},
            {"type": "line"},
            {"type": "area"},
            {"type": "point"},
            {"type": "arc", "innerRadius": 55},
        ):
            raise ValueError("unsupported visual mark")
        # `stack`, spec-level `sort` and `sort_field` used to be read back by
        # `conversation_mapper.to_domain` with a silent `else None` on anything unrecognised --
        # a caller's typo was accepted and dropped rather than refused. They are checked against
        # the same domain sources the mapper already used to filter with (`get_args(Stack)`,
        # `get_args(SortMode)`, the field-identifier pattern) so the two never drift apart.
        stack = self.spec.get("stack")
        if stack is not None and stack not in get_args(Stack):
            raise ValueError("unsupported stack")
        sort = self.spec.get("sort")
        if sort is not None and sort not in get_args(SortMode):
            raise ValueError("unsupported sort mode")
        sort_field = self.spec.get("sort_field")
        if sort_field is not None and (
            not isinstance(sort_field, str) or not FIELD_NAME_PATTERN.fullmatch(sort_field)
        ):
            raise ValueError("sort_field must be an identifier")
        return self

    @model_validator(mode="after")
    def _derived_from_is_not_self(self) -> "VisualContextPayload":
        # `VisualContext.__post_init__` (`chart.py`) already refuses this, but a bare `ValueError`
        # raised there, reached from `conversation_mapper.to_domain`, is neither a `DomainError`
        # nor a pydantic `ValidationError` -- the two exception types `error_handlers.py` maps to a
        # 422 -- so it fell through as an unhandled 500 instead. Checked here, at the wire boundary,
        # the same collision becomes the `ValidationError` pydantic already turns into a 422, same
        # as every other refused `VisualContextPayload` field. The domain check stays: this is the
        # edge's own refusal, not a replacement for the last line of defence.
        if self.derived_from is not None and self.derived_from == self.chart_id:
            raise ValueError("derived_from cannot name the chart itself")
        return self


class ConversationPayload(_Payload):
    """Where the conversation is, in one object.

    It replaces five optional fields — the answered plan, the half-read query, the half-built
    payment, the payment awaiting a yes, and the last refusal — which between them described
    thirty-two states, most of them nonsense. `workflow` says which one this is, and the fields the
    other states would need are absent rather than null-and-meaningless.

    The client round-trips it without interpreting it. That is the point: deciding what a
    combination of nulls meant was the frontend knowing the orchestrator's semantics, and it got
    that wrong every time a new state appeared.

    Typed field by field, like the plan and for the same reason: what a caller sends back has to be
    a conversation and nothing else. The one free-text field is a recipient's name, which is data,
    and it is resolved against the account's own contacts before anything is prepared.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    # Which conversation this is, so N turns stop being N unrelated traces. Minted here on the
    # first turn and echoed back by the client like every field below it — no session, no store,
    # one more value that round-trips.
    #
    # **`extra="forbid"` is what decided the shape of this field, in both directions.**
    #
    # Forwards, it is the reason there is nothing to worry about for a client that predates it: a
    # payload arrives without `conversation_id`, `forbid` has no opinion about an *absent* optional
    # field, and the mapper mints one. That client keeps working exactly as it did, and its turns
    # go on being one trace each until it starts echoing the field back — degraded, never broken,
    # and it needs no version negotiation to cross over. The rule bites the other way round, on a
    # client that sends a field *we* do not know, which is unchanged.
    #
    # Backwards, it is the reason this had to be a declared field at all rather than a key the
    # client tacks on and we read loosely — and being declared, it had to be typed. That is the
    # half that matters, because this value makes a round trip through something we do not
    # control. **Typed `UUID`, it cannot carry anything else.** As a `str` it would be the one
    # field on this payload a client could fill with the holder's own sentence, and this service
    # would stamp it onto a span — a content leak through the exact field added to make traces
    # joinable. Pydantic refuses a non-UUID here with a 422 at the edge, before any of it reaches
    # a recorder, which makes "an opaque identifier and nothing else" mechanical rather than hoped
    # for. It is the same argument `_own_words` makes about shape, arriving at the opposite
    # conclusion about UUIDs for the opposite reason: there a UUID is refused because it would be
    # a raw *account* id, here it is required because it is the only thing that cannot be prose.
    conversation_id: UUID | None = None

    # Absent when nothing is under way.
    workflow: Workflow | None = None
    # What the last turn asked for, when it asked for anything. This is what makes "de este"
    # readable: the slot says which field the sentence is filling.
    awaiting: Slot | None = None
    # What the last turn asked for, when it left nothing open. Memory rather than a workflow, which
    # is why it has no per-state rule below: `awaiting` is constrained because a window slot on a
    # payment would let a reply meant for a period complete a Bizum, and this decides one thing —
    # whether the same question has now been asked twice.
    last_asked: Slot | None = None
    # Whether the last turn said how much money there is. Memory, like the two below, and it is
    # what makes "¿y en la hucha?" a balance question after a balance and a spending question after
    # a spending one — the same sentence, two meanings, decided by the turn before.
    answered_the_balance: bool = False
    # The category explicitly highlighted by the last answer. It is an identifier from banking,
    # not prose, and the client only echoes it so a later "esa categoría" can be grounded.
    referenced_category: str | None = Field(
        default=None, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    # A typed row reference emitted by a category, merchant or account breakdown. The key is bank
    # data, never free prose inferred by the client, and both halves are validated as one value.
    referenced_group_by: GroupBy | None = None
    referenced_group_key: str | None = Field(
        default=None, min_length=1, max_length=MAX_MERCHANT_LENGTH
    )
    # Exact evidence rows shown by the preceding answer. UUIDs are harmless selectors: banking
    # still scopes every query to the authenticated holder.
    referenced_transaction_ids: list[UUID] = Field(default=[], max_length=MAX_SELECTED_TRANSACTIONS)
    account_aliases: list["AccountAliasPayload"] = Field(default=[], max_length=MAX_ENTITY_ALIASES)
    account_selection_mode: AccountSelectionMode | None = None
    account_selection_ids: list[UUID] = Field(default=[], max_length=MAX_SELECTED_ACCOUNTS)
    counterparty_aliases: list["CounterpartyAliasPayload"] = Field(
        default=[], max_length=MAX_ENTITY_ALIASES
    )
    # The rail of the payment that just completed. It is deliberately tiny and short-lived: it
    # only grounds an immediate "y ahora otro de 14 a Aitor", never a recipient or an amount.
    recent_payment_rail: Rail | None = None
    # What happened, oldest first. Every field above it is a projection of the turn immediately
    # before, taken by whichever branch ran; this is the turn itself, and it is what lets an
    # ellipsis three deep still resolve. Bounded, because it travels on every request.
    turns: list[TurnPayload] = Field(default=[], max_length=MAX_REMEMBERED_TURNS)
    visual_contexts: list[VisualContextPayload] = Field(default=[], max_length=MAX_VISUAL_CONTEXTS)
    # The plan behind the last answered question. Memory rather than a workflow, so it travels
    # alongside whatever is active.
    last_plan: PlanPayload | None = None
    prior_plan: PlanPayload | None = None
    # The validated query banking could not execute. Kept separately from `last_plan`: a failed
    # read is retryable context, not an answer a later "y este mes" should inherit from.
    retry_plan: PlanPayload | None = None
    # A derived relationship connects two plans. These triples are memory, not an active workflow,
    # are all-or-nothing: one missing operand is not a calculation this service emitted.
    last_ratio_kind: RatioKind | None = None
    last_ratio_part: PlanPayload | None = None
    last_ratio_whole: PlanPayload | None = None
    pending_ratio_kind: RatioKind | None = None
    pending_ratio_part: PlanPayload | None = None
    pending_ratio_whole: PlanPayload | None = None
    # A comparison has two ordered operands and no independently selectable metric: both plans
    # carry the same one. The pair is all-or-nothing for the same reason as ratio memory.
    last_comparison_earlier: PlanPayload | None = None
    last_comparison_latest: PlanPayload | None = None
    # FINANCIAL_CLARIFICATION: the reading so far.
    draft: PlanPayload | None = None
    pending_plan: PlanPayload | None = None
    pending_requested_count: int | None = Field(default=None, ge=2, le=MAX_GROUP_RANK)
    pending_explicit_visualization: bool = False
    account_candidates: list[AccountCandidatePayload] = Field(
        default=[], max_length=MAX_SELECTED_ACCOUNTS
    )
    # UNSUPPORTED_CAPABILITY: which capability, so the answer can name it. The rank that used to sit
    # here left with the refusal it served — a ranking is a plan now, and `last_plan.rank` is what
    # "¿y el segundo?" patches.
    capability: str | None = Field(default=None, max_length=64)
    # PAYMENT_IN_PROGRESS: the payment so far, whether it stepped aside, and the choice offered.
    payment: PaymentDraftPayload | None = None
    suspended: bool = False
    candidates: list[SpokenContactResponse] = []
    # TRANSFER_IN_PROGRESS: the account it is going to, once one is settled, and the amount, once
    # one is said. Named apart from the payment fields so the validator can keep the two states from
    # borrowing each other's — a transfer has nobody to name and a payment has no account.
    transfer_to: UUID | None = None
    transfer_amount_minor: int | None = Field(default=None, ge=1, le=MAX_PAYMENT_MINOR)
    # PAYMENT_CONFIRMATION: the intent the bank is holding.
    #
    # A UUID, not a string. It was a string that `uuid.UUID(...)` parsed inside the mapper, so
    # "not-a-uuid" escaped as a ValueError from a layer whose only honest answer is 422.
    intent_id: UUID | None = None
    recipient: str | None = Field(default=None, max_length=MAX_RECIPIENT_NAME)
    amount_minor: int | None = Field(default=None, ge=1, le=MAX_PAYMENT_MINOR)
    # What the amount is denominated in. It was missing, so the domain's default put every
    # confirmation back together as euros: a preview prepared in USD came back from one round trip
    # as the same number of euros, silently.
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    concept: str | None = None
    # Which rail the preview is for, and the account when it is a transfer. Absent means BIZUM,
    # so a client written before transfers keeps round-tripping a state it does not understand.
    rail: Rail | None = None
    recipient_iban: str | None = Field(default=None, max_length=42)

    @model_validator(mode="after")
    def _fields_match_the_workflow(self) -> "ConversationPayload":
        """A state the client sends has to be one this service can issue.

        The mapper used to reinterpret what it could not read: a PAYMENT_CONFIRMATION with no intent
        became idle, a PAYMENT_IN_PROGRESS with no payment became an empty draft, and a window slot
        could arrive attached to a payment. Reinterpreting a state nobody could have been in is a
        guess about money, and the caller never learns their state was discarded.

        `extra="forbid"` above rejects fields we do not know. This rejects combinations we do know
        and cannot mean anything — which is the larger half.

        ponytail: one validator rather than five payload classes behind a discriminated union. It
        buys the 422s and the impossible states; what it does not buy is OpenAPI documenting each
        state's own shape. Split it when a second consumer has to generate a client from the schema.
        """
        required: dict[Workflow | None, tuple[str, ...]] = {
            Workflow.FINANCIAL_CLARIFICATION: ("draft",),
            Workflow.ACCOUNT_SCOPE: ("pending_plan", "account_candidates"),
            Workflow.UNSUPPORTED_CAPABILITY: ("capability",),
            Workflow.PAYMENT_IN_PROGRESS: ("payment",),
            # Neither field is required: the state exists precisely while one of them is missing.
            Workflow.TRANSFER_IN_PROGRESS: (),
            Workflow.PAYMENT_CONFIRMATION: ("intent_id", "recipient", "amount_minor"),
            None: (),
        }
        # Which fields belong to which state. Anything set outside its own state is a caller
        # improvising a combination this service never sends.
        owned: dict[Workflow | None, frozenset[str]] = {
            Workflow.FINANCIAL_CLARIFICATION: frozenset({"draft"}),
            Workflow.ACCOUNT_SCOPE: frozenset(
                {
                    "pending_plan",
                    "pending_requested_count",
                    "pending_explicit_visualization",
                    "account_candidates",
                }
            ),
            Workflow.UNSUPPORTED_CAPABILITY: frozenset({"capability"}),
            Workflow.PAYMENT_IN_PROGRESS: frozenset({"payment", "candidates", "suspended"}),
            Workflow.TRANSFER_IN_PROGRESS: frozenset({"transfer_to", "transfer_amount_minor"}),
            Workflow.PAYMENT_CONFIRMATION: frozenset(
                {
                    "intent_id",
                    "recipient",
                    "amount_minor",
                    "currency",
                    "concept",
                    "rail",
                    "recipient_iban",
                }
            ),
            None: frozenset(),
        }
        every = frozenset().union(*owned.values())

        for name in required[self.workflow]:
            if not getattr(self, name):
                raise ValueError(f"{self.workflow} needs {name}")
        for name in every - owned[self.workflow]:
            if getattr(self, name):
                raise ValueError(f"{name} does not belong to {self.workflow or 'an idle'} state")

        if self.pending_requested_count is not None:
            if self.pending_plan is None or self.pending_plan.action is not Action.RANK_MOVEMENTS:
                raise ValueError("pendingRequestedCount requires a movement-ranking plan")

        if self.account_selection_mode is None and self.account_selection_ids:
            raise ValueError("accountSelectionIds require accountSelectionMode")
        if self.account_selection_mode is AccountSelectionMode.ALL and self.account_selection_ids:
            raise ValueError("ALL account selection cannot contain ids")
        if (
            self.account_selection_mode is AccountSelectionMode.SELECTED
            and not self.account_selection_ids
        ):
            raise ValueError("SELECTED account selection needs ids")

        if self.awaiting is not None and self.awaiting not in _SLOTS_OF[self.workflow]:
            raise ValueError(f"{self.workflow or 'an idle state'} never asks for {self.awaiting}")
        for prefix in ("last_ratio", "pending_ratio"):
            values = (
                getattr(self, f"{prefix}_kind"),
                getattr(self, f"{prefix}_part"),
                getattr(self, f"{prefix}_whole"),
            )
            if any(value is not None for value in values) and any(
                value is None for value in values
            ):
                raise ValueError(f"{prefix} needs its kind and both operands")
        comparison = (self.last_comparison_earlier, self.last_comparison_latest)
        if any(value is not None for value in comparison) and any(
            value is None for value in comparison
        ):
            raise ValueError("last_comparison needs both ordered operands")
        group_reference = (self.referenced_group_by, self.referenced_group_key)
        if any(value is not None for value in group_reference) and any(
            value is None for value in group_reference
        ):
            raise ValueError("a referenced group needs both its dimension and key")
        if self.referenced_group_by not in {
            None,
            GroupBy.CATEGORY,
            GroupBy.MERCHANT,
            GroupBy.COUNTERPARTY,
            GroupBy.ACCOUNT,
        }:
            raise ValueError(
                "only category, merchant, counterparty and account rows can be referenced"
            )
        if len(set(self.referenced_transaction_ids)) != len(self.referenced_transaction_ids):
            raise ValueError("referenced transaction ids must be unique")
        return self


class MetricSourcePayload(_Payload):
    """Signed save authority for one chart already present in the conversation."""

    chart_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    definition_token: str = Field(min_length=1, max_length=20000)


class QuestionRequest(_Payload):
    """A financial question in natural language.

    There is no account here, and its absence is the point. It used to travel in the
    body, which meant any caller could read any account; it now comes from the
    authenticated identity, so there is nowhere left to say whose money you mean.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    # Free text, because a question is free text: it can only be delimited, never
    # parsed. The plan below is the opposite case and gets the opposite treatment.
    question: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    # Echo back `conversation` from the previous answer. One object rather than the five fields
    # it replaced — the answered plan, the half-read query, the half-built payment, the payment
    # awaiting a yes, and the last refusal — because a client should store state, not interpret it.
    #
    # Absent on the first turn, and absent means a fresh conversation rather than an error.
    conversation: ConversationPayload | None = None
    channel: Literal["TEXT", "VOICE"] = "TEXT"
    # Circumstances of this turn, not facts about the data -- see `PresentationContext`. Both are
    # optional so an old client that has never heard of either keeps working; `extra="forbid"`
    # above is what makes that safe rather than a loophole, because a viewport nobody declared
    # (`"WATCH"`) is still rejected rather than silently accepted. Validated here and carried all
    # the way to `floor.plan(profile, presentation)` -- but `DeterministicVisualPlanner.plan()`
    # still does not read `presentation` (its own docstring says so); a later wave's model planner
    # is what consumes `max_visible_rows`.
    viewport: Literal["MOBILE", "DESKTOP"] = "DESKTOP"
    max_visible_rows: int = Field(default=ROWS_THAT_FIT, ge=1, le=100)
    metric_sources: list[MetricSourcePayload] = Field(default=[], max_length=MAX_VISUAL_CONTEXTS)


class SpokenGroupResponse(_Payload):
    key: str
    total_minor: int
    movement_count: int
    value_minor: int | None = None


class SpokenMovementResponse(_Payload):
    occurred_at: str
    description: str
    # Unsigned, with the direction beside it. A client deriving the sign from the amount alone
    # showed every expense as an income, because a query filtered by EXPENSE returns positives.
    amount_minor: int
    direction: str
    merchant_name: str | None = None
    category: str | None = None
    transaction_type: str | None = None
    counterparty_name: str | None = None
    counterparty_type: str = "UNKNOWN"
    receipt_category: str | None = None


class ChartVisualizationResponse(_Payload):
    kind: Literal["chart"] = "chart"
    chart_id: str
    # Rows remain in minor units at the API boundary. The client uses this semantic unit only
    # when presenting axes/tooltips, so persisted metric definitions never contain floats.
    unit: Literal["money", "count", "ratio"] = "money"
    spec: dict[str, object]
    data: list[dict[str, object]]
    # How many rows the chart draws, when it draws fewer than it was given. The number is already
    # inside `spec` as a rank filter, but reading it back out of a Vega transform string is not a
    # contract -- and the client needs it to say, under the chart, that it is showing a sample. Null
    # means every row is drawn, which is also the only case where saying nothing is honest.
    limit: int | None = None


class KpiVisualizationResponse(_Payload):
    kind: Literal["kpi"] = "kpi"
    value_field: Literal["value_minor"] = "value_minor"
    unit: Literal["money", "count"]
    data: list[dict[str, object]]


class VisualizationDerivation(_Payload):
    source_chart_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    chart_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    spec: dict[str, object]
    # Rows the derived plot draws, for the same reason the chart carries one -- and here it is
    # load-bearing rather than convenient. A derivation is merged onto the source it names, so
    # without its own value the source's limit survives a change that lifted it: "Ver todos" would
    # draw all eighteen rows under a caveat still claiming eight.
    limit: int | None = None

    @model_validator(mode="after")
    def _strict_spec(self) -> "VisualizationDerivation":
        if self.chart_id == self.source_chart_id:
            raise ValueError("a visualization derivation needs a new chart id")
        if set(self.spec) - {
            "$schema",
            "mark",
            "encoding",
            "title",
            "width",
            "height",
            "transform",
        }:
            raise ValueError("unknown visualization update key")
        mark = self.spec.get("mark")
        if not isinstance(mark, dict) or mark.get("type") not in {
            "bar",
            "line",
            "area",
            "point",
            "arc",
        }:
            raise ValueError("invalid visualization derivation mark")
        if not isinstance(self.spec.get("encoding"), dict):
            raise ValueError("visualization derivation needs encoding")
        if _contains_chart_values(self.spec):
            raise ValueError("visualization derivations must not contain data or values")
        return self


def _contains_chart_values(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"data", "values"} or _contains_chart_values(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_chart_values(item) for item in value)
    return False


VisualizationResponse = Annotated[
    ChartVisualizationResponse | KpiVisualizationResponse,
    Field(discriminator="kind"),
]


class SaveCandidate(_Payload):
    """Authority to keep this answer as a metric, and the chart it was drawn as if it was one.

    ``chart_id`` is nullable because a figure without a chart is still a metric. It is also what a
    visual follow-up matches against, so a null one simply never matches -- which is right, since
    there is no chart to restyle.
    """

    definition_token: str
    chart_id: str | None = None


class QuestionResponse(_Payload):
    """The answer, and everything needed to disbelieve it.

    `plan` is the query that was actually run and `movements` are the entries behind
    the figure. Both are part of the answer rather than a debug extra: a number from a
    pipeline with a language model in it should never have to be taken on trust.
    """

    outcome: str
    text: str
    question: str | None = None
    # Present only when the sentence was a payment: what a client echoes back to confirm it. The
    # chat endpoint answers both kinds of sentence, so the shape has to admit both.
    intent_id: str | None = None
    plan: str | None = None
    # Where the conversation is after this turn, echoed back verbatim on the next one. `plan`
    # above is the same query in words, for a person to read.
    conversation: ConversationPayload | None = None
    currency: str | None = None
    # The figure this answer's own `text` leads with, and `None` when it has no single one. Never a
    # signed difference: those are `difference_minor`.
    total_minor: int | None = None
    # The signed change between two compared figures, on an answer that compares two. Its presence
    # is also what says the rows in `groups` are the two *operands* of that difference rather than
    # *parts* of `total_minor` -- so a screen drawing a bar as a share of the total knows not to.
    # `AskFinancialQuestionResult.difference_minor` carries the argument and the measurement.
    difference_minor: int | None = None
    movement_count: int | None = None
    groups: list[SpokenGroupResponse] = []
    movements: list[SpokenMovementResponse] = []
    visualization: VisualizationResponse | None = None
    visualization_derivation: VisualizationDerivation | None = None
    # Replies the service can actually understand on the next turn. They are data rather than
    # prose so a client can render buttons without scraping the answer text.
    suggestions: list[str] = []
    # Filled only by a question about who can be paid. The identifier is what lets a client offer
    # a choice and send back *which* contact instead of a name to be interpreted a second time.
    contacts: list[SpokenContactResponse] = []
    # Independent read-only requests asked in one turn. Each part keeps its own plan and evidence;
    # only the parent carries the conversation left behind after all parts have run.
    parts: list["QuestionResponse"] = []
    save_candidate: SaveCandidate | None = None
    # The half-built payment, the refused capability and the recipient choice all live on
    # `conversation` now: they are states of one conversation rather than four independent fields
    # a client had to combine.


class HoldingPayload(_Payload):
    """One of the holder's accounts.

    `kind` is `CURRENT`, `SAVINGS` or `ASSET`, and it is the field a screen must read before adding
    anything up: on an `ASSET` the balance is what was paid, not what it is worth, so a total
    across all three without a word about it reports a purchase price as somebody's money.

    `displayName` is nullable because the holder may not have named the account. A screen falls
    back to the kind — never to the reference, which is a string nobody recognises.
    """

    account_id: str
    kind: str
    display_name: str | None
    currency: str
    booked_minor: int


class SummaryMovementResponse(_Payload):
    occurred_at: str
    description: str
    # Signed: negative is money out. A screen that shows absolute values makes a charge and a
    # payment in look the same, which is the one thing a movement list exists to distinguish.
    amount_minor: int
    merchant_name: str | None = None
    category: str | None = None
    # Only the movement list fills these: it is the one screen that can refile a movement, so it
    # needs the ledger's identifier to do it and the flag to say whose category it is showing.
    transaction_id: str | None = None
    category_is_custom: bool = False
    # Which layer decided the category — BANK, SYSTEM, USER, NONE — and whether the money stayed
    # between the holder's own accounts. A movement list needs both to label a row honestly: a
    # transfer to the piggy bank drawn like a purchase is spending that never happened.
    category_source: str = "NONE"
    is_internal: bool = False


class AccountSummaryResponse(_Payload):
    """The home screen's figures. No plan, no outcome and no model: a fixed question, answered from
    the ledger through a plan built in code."""

    currency: str
    booked_minor: int
    available_minor: int
    as_of: str
    movements: list[SummaryMovementResponse] = []
    # Added rather than replacing anything, so a client written before there were several accounts
    # keeps reading the same figures: the balance above is still the declared account's, and this
    # is a field it can ignore until somebody decides to show it.
    holdings: list[HoldingPayload] = []


class ContactPayload(_Payload):
    contact_id: str
    display_name: str
    phone: str


class ContactsResponse(_Payload):
    """Everybody this account can pay. The whole list, because the name somebody is looking for is
    exactly the one a page limit would cut."""

    contacts: list[ContactPayload] = []


class MovementsResponse(_Payload):
    currency: str
    total_minor: int
    movement_count: int
    movements: list[SummaryMovementResponse] = []


class GroupPayload(_Payload):
    key: str
    total_minor: int
    movement_count: int


class BreakdownResponse(_Payload):
    """Where the money went, grouped. `total_minor` is the figure the groups add up to, so a screen
    can draw a bar as a share of it without doing arithmetic the ledger did not do."""

    currency: str
    total_minor: int
    movement_count: int
    groups: list[GroupPayload] = []


class CardPayload(_Payload):
    """A card as a screen needs it. Four digits, never a number.

    `expires` is already "09/28": how it is written on the card, assembled once rather than in every
    place it is shown.
    """

    card_id: str
    display_name: str
    brand: str
    kind: str
    last_four: str
    expires: str
    status: str
    # null means the balance is the limit, which is a debit card — not a limit of zero.
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


class WalletResponse(_Payload):
    """`since` travels with the figures: a total whose window is implicit is a total somebody
    eventually labels as a month."""

    since: str
    cards: list[CardPayload] = []


class ChargePayload(_Payload):
    """One charge on a card. Unsigned: every row is money out, and the screen writes the minus."""

    transaction_id: str
    occurred_at: str
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int


class ChargesResponse(_Payload):
    card_id: str
    since: str
    charges: list[ChargePayload] = []


class CategoryRequest(_Payload):
    """What a movement should be filed under. `null` lets the bank decide again.

    Bounded and shape-checked here; whether the value is a category this ledger uses is banking's
    answer, because the vocabulary belongs to whoever owns the movements.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    category: str | None = Field(default=None, max_length=MAX_CATEGORY_LENGTH)


class CategoriesResponse(_Payload):
    """The categories a screen may offer. The same closed list the planner is constrained to, so a
    category the assistant could not name back is not one a chip can produce."""

    categories: list[str] = []
