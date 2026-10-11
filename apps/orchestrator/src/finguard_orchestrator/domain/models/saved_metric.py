"""The small persisted aggregate used by the private ``Mis métricas`` feature.

Only a typed plan and a value-free chart are persisted.  Execution results are deliberately
absent from this model: opening a metric must execute the plan again against banking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import ChartSpec
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

SavedPlan = FinancialPlan


@dataclass(frozen=True)
class SavedMetricDefinition:
    """A question worth asking again, and optionally the chart that drew its answer.

    ``chart`` is nullable because the chart is presentation and the plan is the metric. A single
    figure -- "cuanto he gastado este mes" -- is the most obvious thing anybody would keep, and it
    was the one thing that could not be kept: the save offer was gated on there being a chart to
    hang it from, and one row is a number rather than a chart.
    """

    plan: SavedPlan
    chart: ChartSpec | None = None


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
            raise InvalidValueError("holder_id is required")
        if not self.title.strip() or len(self.title) > 80:
            raise InvalidValueError("metric title must contain 1..80 characters")
        if self.position < 0:
            raise InvalidValueError("metric position cannot be negative")
        if self.version < 1:
            raise InvalidValueError("metric version must be positive")

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
