from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class QueryGroup:
    """One slice of the result: a week, a category, a merchant..."""

    key: str
    total: Money
    movement_count: int


@dataclass(frozen=True)
class Movement:
    """A movement backing the figure. Without these the answer is not verifiable."""

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount: Money
    direction: str
    # Which of the four layers decided the category: BANK, SYSTEM, USER or NONE.
    # `category_is_custom` answers only "did the holder override it"; this says what it overrode,
    # and NONE says the movement is unfiled rather than filed under a guess.
    category_source: str = "NONE"
    # Money that stayed inside the household. Excluded from totals by default, and carried here so a
    # list can say why a 200 € line is not a purchase — ADR-009 §2.
    is_internal: bool = False
    # Whether that category is the holder's own rather than what the bank inferred. It travels with
    # the movement because a label that silently changed is a label nobody trusts twice — a screen
    # has to be able to say whose opinion it is showing. Last, and with a default, because a
    # dataclass will not take a defaulted field before a required one.
    category_is_custom: bool = False


@dataclass(frozen=True)
class QueryTrace:
    """Traceability for the report and the benchmark (vision.md §9.3)."""

    sql: str
    parameters: list[str]
    elapsed_ms: float


@dataclass(frozen=True)
class FinancialQueryResult:
    account_id: UUID
    total: Money
    movement_count: int
    groups: list[QueryGroup] = field(default_factory=list)
    supporting_movements: list[Movement] = field(default_factory=list)
    trace: QueryTrace | None = None

    @property
    def currency(self) -> str:
        return self.total.currency
