"""What the interpreter may say a turn does. Closed: a turn is one or more of these, nothing else.

These are all that crosses from the model into code. They name fields rather than restate a plan,
so a correction carries only what changed and the transition keeps the rest. That is what `None`
means throughout: not said, and therefore kept.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORIES,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_PAYMENT_MINOR,
    MAX_RECIPIENT_NAME,
    MAX_SELECTED_ACCOUNTS,
    MAX_SUPPORTING_MOVEMENTS,
)

# `PlanFields` lives with the state, whose pending question holds one; it is named here too because
# it is what every operation carries.
from finguard_orchestrator.domain.engine.state import DialogueState, Measure, Missing, PlanFields
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

__all__ = [
    "Ask",
    "Balance",
    "ChangeMeasure",
    "Clarify",
    "Compare",
    "ComparePrevious",
    "InvestigateComparison",
    "ListContacts",
    "ListCategories",
    "ListMovements",
    "PatchMovementList",
    "AskCorrection",
    "Delegate",
    "Operation",
    "Patch",
    "PrepareBizum",
    "PrepareTransfer",
    "PrepareOwnTransfer",
    "PlanFields",
    "ResumeFocus",
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
    """A correction. Only named fields move; task_id identifies a suspended draft when needed."""

    target: Literal["focus", "earlier", "latest", "pending", "suspended"]
    changes: PlanFields
    task_id: str | None = None

    def __post_init__(self) -> None:
        if self.target == "suspended":
            try:
                valid_id = self.task_id is not None and UUID(hex=self.task_id).hex == self.task_id
            except (TypeError, ValueError):
                valid_id = False
            if not valid_id:
                raise InvalidValueError("resuming a suspended task requires its valid task_id")
        elif self.task_id is not None:
            raise InvalidValueError("task_id is only valid when resuming a suspended task")


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
class ResumeFocus:
    """Resume one named answered task from canonical focus history."""

    task_id: str

    def __post_init__(self) -> None:
        try:
            valid_id = UUID(hex=self.task_id).hex == self.task_id
        except (TypeError, ValueError):
            valid_id = False
        if not valid_id:
            raise InvalidValueError("resuming an answered task requires its valid task_id")


@dataclass(frozen=True)
class Balance:
    accounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ListContacts:
    """Read the holder's payee directory without changing an active payment draft."""


@dataclass(frozen=True)
class ListCategories:
    """Read FinGuard's supported spending categories without changing the current query."""


@dataclass(frozen=True)
class ListMovements:
    """Start a ledger-row query or continue its saved page sequence."""

    # A generic request for "movements" includes both income and expenses. The planner must
    # narrow the list only when the user names one direction.
    direction: Direction = Direction.ALL
    accounts: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    merchant_terms: tuple[str, ...] = ()
    window: RelativePeriod | AbsoluteRange | None = None
    page_size: int | None = None
    continue_current: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            raise InvalidValueError("movement list direction is unsupported")
        if self.window is not None and not isinstance(self.window, (RelativePeriod, AbsoluteRange)):
            raise InvalidValueError("movement list window is unsupported")
        if self.page_size is not None and (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= MAX_SUPPORTING_MOVEMENTS
        ):
            raise InvalidValueError("movement page size must be between 1 and 100")
        filters = (*self.accounts, *self.categories, *self.merchant_terms)
        if any(not isinstance(item, str) or not item.strip() for item in filters):
            raise InvalidValueError("movement list filters cannot be empty strings")
        if len(self.accounts) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError("movement list has too many selected accounts")
        if len(self.categories) > MAX_CATEGORIES or len(self.merchant_terms) > MAX_MERCHANTS:
            raise InvalidValueError("movement list has too many filters")
        if any(len(item) > MAX_MERCHANT_LENGTH for item in (*self.accounts, *self.merchant_terms)):
            raise InvalidValueError("movement list name filters are too long")
        if self.continue_current and (
            self.accounts or self.categories or self.merchant_terms or self.window is not None
        ):
            raise InvalidValueError("continuing a movement list cannot change its filters")


@dataclass(frozen=True)
class PatchMovementList:
    """Change only supplied fields on the visible ledger list; empty lists clear filters."""

    direction: Direction | None = None
    accounts: tuple[str, ...] | None = None
    categories: tuple[str, ...] | None = None
    merchant_terms: tuple[str, ...] | None = None
    window: RelativePeriod | AbsoluteRange | None = None
    clear_window: bool = False
    page_size: int | None = None

    def __post_init__(self) -> None:
        if self.direction is not None and not isinstance(self.direction, Direction):
            raise InvalidValueError("movement list direction is unsupported")
        if self.window is not None and not isinstance(self.window, (RelativePeriod, AbsoluteRange)):
            raise InvalidValueError("movement list window is unsupported")
        if self.page_size is not None and (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= MAX_SUPPORTING_MOVEMENTS
        ):
            raise InvalidValueError("movement page size must be between 1 and 100")
        filters = (*(self.accounts or ()), *(self.categories or ()), *(self.merchant_terms or ()))
        if any(not isinstance(item, str) or not item.strip() for item in filters):
            raise InvalidValueError("movement list filters cannot be empty strings")
        if len(self.accounts or ()) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError("movement list has too many selected accounts")
        if (
            len(self.categories or ()) > MAX_CATEGORIES
            or len(self.merchant_terms or ()) > MAX_MERCHANTS
        ):
            raise InvalidValueError("movement list has too many filters")
        if any(
            len(item) > MAX_MERCHANT_LENGTH
            for item in (*(self.accounts or ()), *(self.merchant_terms or ()))
        ):
            raise InvalidValueError("movement list name filters are too long")
        if self.window is not None and self.clear_window:
            raise InvalidValueError("movement list patch cannot set and clear its window")
        if not isinstance(self.clear_window, bool):
            raise InvalidValueError("clear_window must be a boolean")
        if (
            all(
                value is None
                for value in (
                    self.direction,
                    self.accounts,
                    self.categories,
                    self.merchant_terms,
                    self.window,
                    self.page_size,
                )
            )
            and not self.clear_window
        ):
            raise InvalidValueError("movement list patch must change at least one field")


@dataclass(frozen=True)
class AskCorrection:
    """Ask what the user meant to correct while preserving the current focus and open payment."""


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
    | ResumeFocus
    | RestorePrevious
    | Balance
    | ListContacts
    | ListCategories
    | ListMovements
    | PatchMovementList
    | AskCorrection
    | Clarify
    | Smalltalk
    | Delegate
    | Visualize
    | PrepareBizum
    | PrepareTransfer
    | PrepareOwnTransfer
)
