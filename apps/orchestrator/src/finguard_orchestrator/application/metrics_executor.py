"""Typed saved-plan execution through the same banking port as live questions."""

from collections.abc import Awaitable, Callable
from typing import Any

from finguard_orchestrator.domain.models.chart import Visualization
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def financial_metric_executor(
    banking: FinancialQueryPort,
) -> Callable[[SavedMetricDefinition, Caller], Awaitable[dict[str, Any]]]:
    async def execute(definition: SavedMetricDefinition, caller: Caller) -> dict[str, Any]:
        if not isinstance(definition.plan, FinancialPlan):
            raise ValueError("only financial plans are enabled for saved metrics")
        plan = definition.plan
        if plan.only_account_id is not None:
            caller.assert_may_read(plan.only_account_id)
        answer = await banking.run(
            plan,
            account_id=caller.account_id,
            today=caller.today_in(),
            reporting_timezone=caller.reporting_timezone,
        )
        value = "movement_count" if plan.metric.value == "COUNT" else "total_minor"
        available_fields = {"dimension", "key", "value_minor", "total_minor", "movement_count"}
        if not definition.chart.fields() <= available_fields:
            raise ValueError("saved chart references unavailable banking fields")
        rows = []
        for group in answer.groups:
            source = {
                "dimension": group.key,
                "key": group.key,
                "value_minor": getattr(group, value),
                "total_minor": group.total_minor,
                "movement_count": group.movement_count,
            }
            rows.append(
                {field: source[field] for field in definition.chart.fields() if field in source}
            )
        presented = present(Visualization(definition.chart, tuple(rows)))
        return {
            "outcome": "ANSWERED",
            "text": "Métrica actualizada",
            "total_minor": answer.total_minor,
            "movement_count": answer.movement_count,
            "groups": [
                {
                    "key": group.key,
                    "total_minor": group.total_minor,
                    "movement_count": group.movement_count,
                }
                for group in answer.groups
            ],
            "currency": answer.currency,
            "visualization": {
                "kind": "chart",
                "chart_id": "saved-metric",
                "spec": presented["spec"],
                "data": presented["data"],
            },
        }

    return execute
