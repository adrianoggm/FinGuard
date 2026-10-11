"""The wire format of the payment endpoints.

snake_case, like the rest of this service's API. Banking's contract is camelCase and the client
that speaks it is the only thing that should know that.

There is no account in either body, and its absence is the point: the account comes from the
credential, so there is nowhere to say whose money you mean.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from finguard_orchestrator.domain.constants import MAX_RECIPIENT_NAME

# A reply to a confirmation is a word or two. Bounded far below a prompt because it is read by an
# allow-list of affirmations, and a paragraph is not one — nothing longer can be a yes.
MAX_REPLY_LENGTH = 200


class _Payload(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")


class PreviewPayload(_Payload):
    """The preview from the previous answer, echoed back.

    Optional, and it changes nothing about whether the payment happens — it is used to repeat the
    question when a reply is neither yes nor no. The amount that gets paid comes from the intent in
    banking's own table, never from this body: a client that could restate the figure could restate
    it differently.
    """

    recipient: str
    recipient_phone: str
    amount_minor: int
    currency: str
    balance_after_minor: int


class ConfirmPaymentRequest(_Payload):
    """The user's reply, in their own words.

    Not a boolean. A client that sent `confirmed: true` would be deciding that somebody agreed, and
    §4.2 puts that decision outside both the model and the client — the words are read here, by an
    allow-list, and "sí pero mándale 30" is not a yes.
    """

    reply: str = Field(min_length=1, max_length=MAX_REPLY_LENGTH)
    preview: PreviewPayload | None = None
    # Required by LangGraph deployments to resume the account-scoped checkpoint created by the
    # preparation request. Legacy deployments ignore it.
    workflow_id: str | None = Field(default=None, max_length=200)


class PaymentResponse(_Payload):
    """One shape for every turn: the preview, the receipt, the question, the refusal.

    `outcome` is what a client branches on, and `text` is what it shows. The figures are there so
    a client can render the payment itself rather than parse the sentence — and because a receipt
    whose number only exists inside prose cannot be checked against the ledger.
    """

    outcome: str
    text: str
    intent_id: str | None = None
    workflow_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    recipient: str | None = None
    # Which rail, and where it went. Exactly one of the two identifiers is filled and `rail` says
    # which — a Bizum names a person by phone, a transfer names the account. The account is whole
    # and unmasked: approving a transfer means recognising twenty-four characters, and four of them
    # with dots between is not something anybody can check against a note.
    rail: str | None = None
    recipient_phone: str | None = None
    recipient_iban: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    balance_after_minor: int | None = None
    expires_at: str | None = None
    transaction_id: str | None = None


class OwnTransferRequest(_Payload):
    """Money into another of the holder's own accounts.

    No recipient field, and that is the shape rather than an omission: there is nobody to name, so
    a body that could name one would be a body that can describe a payment leaving the household
    under a rail that says it does not.

    The account is an identifier the caller was shown. Whether it is theirs is banking's to decide,
    against the view that listed it — this service holds no rule about whose account is whose.
    """

    to_account_id: UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern="^[A-Z]{3}$")


class ChosenPaymentRequest(_Payload):
    """A payment built in a form: a contact picked from the list, an amount typed in a field.

    `recipient_name` and not a contact id, deliberately. Banking resolves the name against this
    account's own contacts, which is the check that makes a forged identifier useless — an id would
    have to be trusted, and a name is verified against the list the caller was allowed to see.
    """

    recipient_name: str = Field(min_length=1, max_length=MAX_RECIPIENT_NAME)
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern="^[A-Z]{3}$")
