"""The wire format of the LLM endpoints.

These are DTOs, not domain models: the domain knows nothing about camelCase, aliases
or HTTP. Keeping them apart is what stops a change in the contract from reaching the
core — before this, `LlmPolicy` was both the domain object and the request body.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from finguard_orchestrator.domain.constants import MAX_CATEGORY_NAME, MAX_PROMPT_LENGTH
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.conversation import MAX_RANK, Slot, Workflow
from finguard_orchestrator.domain.value_objects.payment_request import (
    MAX_PAYMENT_MINOR,
    MAX_RECIPIENT_NAME,
)


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
    merchants: list[str] = []
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM


class PaymentDraftPayload(_Payload):
    """A payment being built, in fields. Travels out with the question the assistant asked and back
    with the answer to it.

    Typed for the same reason the plan is: what a caller sends has to be a payment and nothing else.
    A recipient is free text because a name is data, and it is resolved against the account's own
    contacts before anything is prepared.
    """

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    recipient_name: str | None = None
    amount_minor: int | None = None
    currency: str = "EUR"
    concept: str | None = None


class SpokenContactResponse(_Payload):
    contact_id: str
    display_name: str
    phone: str


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
    # The plan behind the last answered question. Memory rather than a workflow, so it travels
    # alongside whatever is active.
    last_plan: PlanPayload | None = None
    # FINANCIAL_CLARIFICATION and UNSUPPORTED_CAPABILITY: the reading so far.
    draft: PlanPayload | None = None
    # UNSUPPORTED_CAPABILITY: which capability, and which position was asked about. The rank is why
    # "¿y el segundo?" can be a patch of one field rather than a sentence to be read again.
    capability: str | None = Field(default=None, max_length=64)
    rank: int | None = Field(default=None, ge=1, le=MAX_RANK)
    # PAYMENT_IN_PROGRESS: the payment so far, whether it stepped aside, and the choice offered.
    payment: PaymentDraftPayload | None = None
    suspended: bool = False
    candidates: list[SpokenContactResponse] = []
    # PAYMENT_CONFIRMATION: the intent the bank is holding.
    intent_id: str | None = None
    recipient: str | None = Field(default=None, max_length=MAX_RECIPIENT_NAME)
    amount_minor: int | None = Field(default=None, ge=1, le=MAX_PAYMENT_MINOR)


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
    # Echo back `plan_context` from the previous answer to ask a follow-up. Optional,
    # and the caller owns it: the service keeps no conversation.
    previous_plan: PlanPayload | None = None
    # Echo back `pending_context` to answer the question the assistant asked. Same shape with
    # no window, because that is what it is: the reading its question interrupted. Sending it
    # is how "este año" completes a plan instead of becoming a new question.
    pending_draft: PlanPayload | None = None
    # And the payment being built, if one is. Sending it is how "15 euros" finishes a bizum
    # instead of being classified as a question about movements — which is what it looks like on
    # its own, money with no verb.
    payment_draft: PaymentDraftPayload | None = None
    # Echo back `refused_capability` so the turn after a refusal can be about it.
    previous_refusal: str | None = Field(default=None, max_length=64)


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
    # The same plan as `plan`, in fields. `plan` is for a person to read; this is what
    # comes back as `previous_plan` on the next question.
    plan_context: PlanPayload | None = None
    # Present with a question back, absent otherwise. What a client echoes as `pending_draft` so
    # the reply finishes the plan the question was about.
    pending_context: PlanPayload | None = None
    currency: str | None = None
    total_minor: int | None = None
    movement_count: int | None = None
    groups: list[SpokenGroupResponse] = []
    movements: list[SpokenMovementResponse] = []
    # Filled only by a question about who can be paid. The identifier is what lets a client offer
    # a choice and send back *which* contact instead of a name to be interpreted a second time.
    contacts: list[SpokenContactResponse] = []
    # Present while a payment is still being built: what a client echoes as `payment_draft` so the
    # next reply completes it. Absent once there is an intent to confirm, or once it is refused.
    payment_draft: PaymentDraftPayload | None = None
    # On an UNSUPPORTED answer: which capability is missing, by name. Echoed back as
    # `previous_refusal` so a bare "¿y el segundo?" gets the same refusal rather than a question
    # about a period it can never use.
    refused_capability: str | None = None
    # Present when a recipient names several contacts, so the client can offer the choice with
    # identifiers rather than asking the user to retype a name.
    candidates: list[SpokenContactResponse] = []


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


class AccountSummaryResponse(_Payload):
    """The home screen's figures. No plan, no outcome and no model: a fixed question, answered from
    the ledger through a plan built in code."""

    currency: str
    booked_minor: int
    available_minor: int
    as_of: str
    movements: list[SummaryMovementResponse] = []


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

    category: str | None = Field(default=None, max_length=MAX_CATEGORY_NAME)


class CategoriesResponse(_Payload):
    """The categories a screen may offer. The same closed list the planner is constrained to, so a
    category the assistant could not name back is not one a chip can produce."""

    categories: list[str] = []
