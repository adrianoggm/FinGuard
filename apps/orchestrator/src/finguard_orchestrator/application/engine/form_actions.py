"""A payment built in a form, as a turn of the same conversation the chat has.

A form has nothing to interpret: the holder picked a contact or an account and typed an amount. So
an action skips the model and hands the graph the operation the interpreter would have produced,
with a canonical sentence written from the same fields. The graph's payment node grounds the amount
and the rail in that sentence with the deterministic readers it uses for every chat payment, asks
banking for the preview through the guarded commands and keeps it in canonical state, so a form and
the chat share one preview, one workflow id and one confirmation.

Answering a preview is not an operation at all: it is the holder's yes or no, read by the same
allow-list as a typed «sí», against the preview already held for that conversation.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import (
    Operation,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
)
from finguard_orchestrator.domain.errors import InvalidValueError


@dataclass(frozen=True)
class PayContact:
    """A contact picked from the list and an amount; banking resolves the name again."""

    recipient_name: str
    amount_minor: int
    rail: Literal["BIZUM", "TRANSFER"] = "BIZUM"

    def __post_init__(self) -> None:
        if not self.recipient_name.strip():
            raise InvalidValueError("a payment needs a recipient")
        if isinstance(self.amount_minor, bool) or self.amount_minor <= 0:
            raise InvalidValueError("a payment needs a positive amount")


@dataclass(frozen=True)
class MoveBetweenAccounts:
    """Money into another of the holder's own accounts, by the id the directory listed."""

    to_account_id: UUID
    amount_minor: int

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or self.amount_minor <= 0:
            raise InvalidValueError("a transfer needs a positive amount")


@dataclass(frozen=True)
class AnswerPreview:
    """The holder's answer to the preview held for this conversation."""

    confirm: bool


FormAction = PayContact | MoveBetweenAccounts | AnswerPreview


def own_transfer_turn(destination: str, amount_minor: int) -> tuple[str, tuple[Operation, ...]]:
    """The canonical sentence and operation of a transfer to the account named `destination`."""
    return f"Traspasa {euros(amount_minor)} a {destination}", (PrepareOwnTransfer(destination),)


def as_turn(action: PayContact) -> tuple[str, tuple[Operation, ...]]:
    """The canonical sentence and the operation a contact payment stands for."""
    amount = euros(action.amount_minor)
    if action.rail == "TRANSFER":
        return (
            f"Transferencia de {amount} a {action.recipient_name}",
            (PrepareTransfer(action.recipient_name, action.amount_minor),),
        )
    return (
        f"Bizum de {amount} a {action.recipient_name}",
        (PrepareBizum(action.recipient_name, action.amount_minor),),
    )


def reply_to(action: AnswerPreview) -> str:
    """The yes or no the confirmation allow-list reads; nothing else reaches it."""
    return "sí" if action.confirm else "no"


def euros(minor: int) -> str:
    """`123456` as «1.234,56 €», the way the deterministic money reader expects it."""
    whole, cents = divmod(minor, 100)
    return f"{whole:,}".replace(",", ".") + f",{cents:02d} €"
