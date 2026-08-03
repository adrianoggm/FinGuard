"""The wire format of the LLM endpoints.

These are DTOs, not domain models: the domain knows nothing about camelCase, aliases
or HTTP. Keeping them apart is what stops a change in the contract from reaching the
core — before this, `LlmPolicy` was both the domain object and the request body.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from finguard_orchestrator.domain.constants import MAX_CATEGORY_NAME, MAX_PROMPT_LENGTH
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period


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


class SpokenGroupResponse(_Payload):
    key: str
    total_minor: int
    movement_count: int


class SpokenMovementResponse(_Payload):
    occurred_at: str
    description: str
    amount_minor: int
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
