"""What the interpreter may say a turn does. Closed: a turn is one or more of these, nothing else.

These are all that crosses from the model into code. They name fields rather than restate a plan,
so a correction carries only what changed and the transition keeps the rest. That is what `None`
means throughout: not said, and therefore kept.
"""

from dataclasses import dataclass
from typing import Literal

from finguard_orchestrator.domain.constants import MAX_PAYMENT_MINOR, MAX_RECIPIENT_NAME

# `PlanFields` lives with the state, whose pending question holds one; it is named here too because
# it is what every operation carries.
from finguard_orchestrator.domain.engine.state import DialogueState, Measure, Missing, PlanFields
from finguard_orchestrator.domain.errors import InvalidValueError

__all__ = [
    "Ask",
    "Balance",
    "ChangeMeasure",
    "Clarify",
    "Compare",
    "ComparePrevious",
    "InvestigateComparison",
    "ListContacts",
    "Delegate",
    "Operation",
    "Patch",
    "PrepareBizum",
    "PrepareTransfer",
    "PrepareOwnTransfer",
    "PlanFields",
    "Reset",
    "RestorePrevious",
    "SelectOperand",
    "Smalltalk",
]


@dataclass(frozen=True)
class Ask:
    """A new question, complete in itself: nothing is inherited from what is on screen."""

    fields: PlanFields


@dataclass(frozen=True)
class Patch:
    """A correction. Only the fields `changes` names move; `target` says which plan they move on."""

    target: Literal["focus", "earlier", "latest", "pending"]
    changes: PlanFields


@dataclass(frozen=True)
class Compare:
    """Two sides measuring the same thing. `shared` applies to both and a side's own fields win;
    whatever neither names is the focus's, because «¿y comparado con junio?» is said about it."""

    shared: PlanFields
    earlier: PlanFields
    latest: PlanFields


@dataclass(frozen=True)
class ComparePrevious:
    """Compare the current rolling N-month query with the immediately preceding N months."""

    months: int

    def __post_init__(self) -> None:
        if isinstance(self.months, bool) or not 1 <= self.months <= 12:
            raise InvalidValueError("comparison months must be between 1 and 12")


@dataclass(frozen=True)
class InvestigateComparison:
    """Inspect the active comparison at a useful dimension while keeping its exact scope."""


@dataclass(frozen=True)
class ChangeMeasure:
    measure: Measure


@dataclass(frozen=True)
class SelectOperand:
    which: Literal["earlier", "latest"]


@dataclass(frozen=True)
class Reset:
    """Start again: clears what is on screen and the open question, keeps the accounts."""


@dataclass(frozen=True)
class RestorePrevious:
    """Return to a prior answered focus; steps_back is 1 for the immediately previous one."""

    steps_back: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.steps_back, bool)
            or not 1 <= self.steps_back <= DialogueState.MAX_FOCUS_HISTORY
        ):
            raise InvalidValueError(
                f"steps_back must be between 1 and {DialogueState.MAX_FOCUS_HISTORY}"
            )


@dataclass(frozen=True)
class Balance:
    accounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ListContacts:
    """Read the holder's payee directory without changing an active payment draft."""


@dataclass(frozen=True)
class Clarify:
    """`about` is a name to quote back and `candidates` the holder's entries it could be, both
    from grounding, never prose: the question itself is a template."""

    missing: Missing
    about: str | None = None
    candidates: tuple[str, ...] = ()
    draft: PlanFields = PlanFields()


@dataclass(frozen=True)
class Smalltalk:
    kind: Literal["GREETING", "THANKS", "IDENTITY", "HELP"]


@dataclass(frozen=True)
class Delegate:
    """Not the engine's turn. Money stays on the route that already guards it (spec decision 4)."""

    to: Literal["PAYMENT", "TRANSFER", "BOOKING", "CHART", "UNSUPPORTED"]


@dataclass(frozen=True)
class Visualize:
    """A first-class visual operation over the last query result or current chart."""

    target: Literal["ACTIVE_RESULT", "ACTIVE_CHART"]
    style: Literal["AUTO", "BAR", "LINE"] = "AUTO"


@dataclass(frozen=True)
class PrepareBizum:
    """Start or continue a Bizum draft; only the bank preview can reach confirmation."""

    recipient: str | None
    amount_minor: int | None

    def __post_init__(self) -> None:
        if self.recipient is not None and (
            not self.recipient.strip() or len(self.recipient) > MAX_RECIPIENT_NAME
        ):
            raise InvalidValueError("Bizum recipient name is invalid")
        if self.amount_minor is not None and (
            isinstance(self.amount_minor, bool)
            or not isinstance(self.amount_minor, int)
            or not 0 < self.amount_minor <= MAX_PAYMENT_MINOR
        ):
            raise InvalidValueError("Bizum amount must be a positive bounded integer")


@dataclass(frozen=True)
class PrepareTransfer:
    """Start or continue a bank transfer to a grounded payee.

    The operation identifies the payment rail semantically. A saved contact is resolved against
    the holder's directory; a new payee needs a name and checksum-valid IBAN written in the user's
    utterance. The deterministic payment reader independently verifies the rail before banking.
    """

    recipient: str | None
    amount_minor: int | None

    def __post_init__(self) -> None:
        if self.recipient is not None and (
            not self.recipient.strip() or len(self.recipient) > MAX_RECIPIENT_NAME
        ):
            raise InvalidValueError("transfer recipient name is invalid")
        if self.amount_minor is not None and (
            isinstance(self.amount_minor, bool)
            or not isinstance(self.amount_minor, int)
            or not 0 < self.amount_minor <= MAX_PAYMENT_MINOR
        ):
            raise InvalidValueError("transfer amount must be a positive bounded integer")


@dataclass(frozen=True)
class PrepareOwnTransfer:
    """Request a guarded transfer to one of the holder's own accounts.

    The operation carries only the destination name. The amount is read from the current sentence
    by the deterministic money parser and compared with any amount retained in canonical state.
    """

    destination_account: str | None

    def __post_init__(self) -> None:
        if self.destination_account is not None and (
            not self.destination_account.strip() or len(self.destination_account) > 256
        ):
            raise InvalidValueError("destination account name is invalid")


Operation = (
    Ask
    | Patch
    | Compare
    | ComparePrevious
    | InvestigateComparison
    | ChangeMeasure
    | SelectOperand
    | Reset
    | RestorePrevious
    | Balance
    | ListContacts
    | Clarify
    | Smalltalk
    | Delegate
    | Visualize
    | PrepareBizum
    | PrepareTransfer
    | PrepareOwnTransfer
)
