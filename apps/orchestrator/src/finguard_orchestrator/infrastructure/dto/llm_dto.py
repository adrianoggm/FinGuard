"""The wire format of the LLM endpoints.

These are DTOs, not domain models: the domain knows nothing about camelCase, aliases
or HTTP. Keeping them apart is what stops a change in the contract from reaching the
core — before this, `LlmPolicy` was both the domain object and the request body.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORY_LENGTH,
    MAX_PAYMENT_MINOR,
    MAX_PROMPT_LENGTH,
    MAX_RANK,
    MAX_RECIPIENT_NAME,
    MAX_SELECTED_TRANSACTIONS,
)
from finguard_orchestrator.domain.enums import (
    Action,
    Direction,
    GroupBy,
    Metric,
    Period,
    Rail,
)
from finguard_orchestrator.domain.models.conversation import Slot, Workflow
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
    excluded_merchants: list[str] = []
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM
    include_internal: bool = False
    internal_only: bool = False
    across_accounts: bool = False
    only_account_id: UUID | None = None
    only_account_ids: list[UUID] = []
    transaction_ids: list[UUID] = Field(default=[], max_length=MAX_SELECTED_TRANSACTIONS)
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
    Workflow.UNSUPPORTED_CAPABILITY: frozenset(),
    Workflow.PAYMENT_IN_PROGRESS: frozenset({Slot.AMOUNT, Slot.RECIPIENT, Slot.RECIPIENT_CHOICE}),
    # Two questions and no more: which of the holder's accounts, and how much.
    Workflow.TRANSFER_IN_PROGRESS: frozenset({Slot.AMOUNT, Slot.ACCOUNT}),
    Workflow.PAYMENT_CONFIRMATION: frozenset({Slot.CONFIRMATION}),
    None: frozenset(),
}


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
    # not prose, and the client only echoes it so a later "esa categorÃ­a" can be grounded.
    referenced_category: str | None = Field(
        default=None, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    # The plan behind the last answered question. Memory rather than a workflow, so it travels
    # alongside whatever is active.
    last_plan: PlanPayload | None = None
    prior_plan: PlanPayload | None = None
    # The validated query banking could not execute. Kept separately from `last_plan`: a failed
    # read is retryable context, not an answer a later "y este mes" should inherit from.
    retry_plan: PlanPayload | None = None
    # A proportion relates two plans. These triples are memory rather than an active workflow and
    # are all-or-nothing: one missing operand is not a calculation this service emitted.
    last_ratio_kind: RatioKind | None = None
    last_ratio_part: PlanPayload | None = None
    last_ratio_whole: PlanPayload | None = None
    pending_ratio_kind: RatioKind | None = None
    pending_ratio_part: PlanPayload | None = None
    pending_ratio_whole: PlanPayload | None = None
    # FINANCIAL_CLARIFICATION: the reading so far.
    draft: PlanPayload | None = None
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
        return self


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


class SpokenGroupResponse(_Payload):
    key: str
    total_minor: int
    movement_count: int


class SpokenMovementResponse(_Payload):
    occurred_at: str
    description: str
    # Unsigned, with the direction beside it. A client deriving the sign from the amount alone
    # showed every expense as an income, because a query filtered by EXPENSE returns positives.
    amount_minor: int
    direction: str
    merchant_name: str | None = None
    category: str | None = None


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
    total_minor: int | None = None
    movement_count: int | None = None
    groups: list[SpokenGroupResponse] = []
    movements: list[SpokenMovementResponse] = []
    # Replies the service can actually understand on the next turn. They are data rather than
    # prose so a client can render buttons without scraping the answer text.
    suggestions: list[str] = []
    # Filled only by a question about who can be paid. The identifier is what lets a client offer
    # a choice and send back *which* contact instead of a name to be interpreted a second time.
    contacts: list[SpokenContactResponse] = []
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
