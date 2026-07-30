from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class QueryGroup:
    """Un tramo del resultado: una semana, una categoría, un comercio..."""

    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class Movement:
    """Movimiento que sustenta el cálculo. Sin esto la respuesta no es verificable."""

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
    direction: str


@dataclass(frozen=True)
class QueryTrace:
    """Traza para la memoria y el benchmark (vision.md §9.3)."""

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
