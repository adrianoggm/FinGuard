from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class QueryGroup:
    """One slice of the result: a week, a category, a merchant..."""

    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class Movement:
    """A movement backing the figure. Without these the answer is not verifiable."""

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
    direction: str


@dataclass(frozen=True)
class QueryTrace:
    """Traceability for the report and the benchmark (vision.md §9.3)."""

    sql: str
    parameters: list[str]
    elapsed_ms: float


@dataclass(frozen=True)
class FinancialQueryResult:
    account_id: UUID
    currency: str
    total_minor: int
    movement_count: int
    groups: list[QueryGroup] = field(default_factory=list)
    supporting_movements: list[Movement] = field(default_factory=list)
    trace: QueryTrace | None = None
