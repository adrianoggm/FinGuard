"""The small persisted aggregate used by the private ``Mis métricas`` feature.

Only a typed plan and a value-free chart are persisted.  Execution results are deliberately
absent from this model: opening a metric must execute the plan again against banking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from finguard_orchestrator.domain.models.chart import ChartSpec
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

SavedPlan = FinancialPlan


@dataclass(frozen=True)
class SavedMetricDefinition:
    plan: SavedPlan
    chart: ChartSpec


@dataclass(frozen=True)
class SavedMetric:
    holder_id: str
    title: str
    definition: SavedMetricDefinition
    position: int = 0
    version: int = 1
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.holder_id or len(self.holder_id) > 255:
            raise ValueError("holder_id is required")
        if not self.title.strip() or len(self.title) > 80:
            raise ValueError("metric title must contain 1..80 characters")
        if self.position < 0:
            raise ValueError("metric position cannot be negative")
        if self.version < 1:
            raise ValueError("metric version must be positive")

    @classmethod
    def new(
        cls,
        holder_id: str,
        title: str,
        definition: SavedMetricDefinition,
        position: int = 0,
    ) -> "SavedMetric":
        return cls(
            holder_id=holder_id,
            title=title,
            definition=definition,
            position=position,
            id=uuid4(),
        )
