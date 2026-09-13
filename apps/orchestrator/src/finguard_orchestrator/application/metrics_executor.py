"""Typed saved-plan execution through the same banking port as live questions."""

from collections.abc import Awaitable, Callable
from typing import Any

from finguard_orchestrator.domain.models.chart import Visualization
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.ports.chart_renderer import ChartRenderer
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


def financial_metric_executor(
    banking: FinancialQueryPort,
    render: ChartRenderer,
) -> Callable[[SavedMetricDefinition, Caller], Awaitable[dict[str, Any]]]:
    async def execute(definition: SavedMetricDefinition, caller: Caller) -> dict[str, Any]:
        if not isinstance(definition.plan, FinancialPlan):
            raise ValueError("only financial plans are enabled for saved metrics")
        plan = definition.plan
        # A stored plan is untrusted input (ADR-011 §5): written when the metric was saved,
        # replayed now by whichever credential opens it. This is the one call site of
        # `assert_may_read` that is not a tautology, and `test_saved_metrics.py` now pins it.
        #
        # It is also narrower and less coherent than it reads, and the two defects below are NOT
        # fixed here because neither can be fixed in this file. Do not "tidy" this guard by
        # widening it to the plural field: widening is what makes the first defect worse.
        #
        #  1. It refuses the holder's OWN other accounts. `Caller.account_id` is a single account
        #     (`caller.py:26-33`, "singular on purpose") while every layer beneath it isolates by
        #     *holder* (`0016_holder_scope.py`). Laura has three accounts in the seed, so a metric
        #     she saves against her "Hucha" (`only_account_id=94d556d5-...`) is saveable --
        #     `llm_mapper.py:480-499` offers a save candidate for any plan that produced a figure
        #     -- and then raises NotAuthorisedError when she reopens it. Fixing it needs the
        #     holder's real account set, i.e. the `AccountDirectoryPort` that every other
        #     echoed-id path validates against (`ask_financial_question.py:3864`, `:3694`),
        #     which is not wired into this executor.
        #  2. It is inconsistent about scope. `only_account_ids` (plural) and `across_accounts`
        #     are unchecked, and `across_accounts=True` reads *every* account the holder has --
        #     strictly more than the single account this branch refuses.
        #
        # What it does stop is a stored plan naming a stranger's account, which banking's
        # holder-scoped view would also refuse (`financial_query_repository.py:95-98` ->
        # AccountNotFoundError). Its unique contribution is refusing here rather than relying on
        # a 404 from another service.
        if plan.only_account_id is not None:
            caller.assert_may_read(plan.only_account_id)
        answer = await banking.run(
            plan,
            account_id=caller.account_id,
            today=caller.today_in(),
            reporting_timezone=caller.reporting_timezone,
        )
        value = "movement_count" if plan.metric.value == "COUNT" else "total_minor"
        counted = plan.metric.value == "COUNT"
        figure = answer.movement_count if counted else answer.total_minor
        visualization: dict[str, Any]
        if definition.chart is None:
            # A metric saved without a chart is a figure, and the figure is what reopening it shows.
            # The client already renders this shape, so nothing downstream learns a new case.
            visualization = {
                "kind": "kpi",
                "value_field": "value_minor",
                "unit": "count" if counted else "money",
                "data": [{"dimension": "total", "value_minor": figure}],
            }
        else:
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
            presented = render(Visualization(definition.chart, tuple(rows)))
            visualization = {
                "kind": "chart",
                "chart_id": "saved-metric",
                # The unit the rows are in, exactly as a live answer declares it. It was missing,
                # and the client only converts minor units to euros when it is told the rows are
                # money -- so a reopened metric drew 92000 on an axis where the chat drew 920,00 €.
                # A figure a hundred times too large is the worst thing this screen can show.
                "unit": "count" if counted else "money",
                "spec": presented["spec"],
                "data": presented["data"],
            }
        return {
            "outcome": "ANSWERED",
            # The query in words, which is what "Ver la consulta" shows. A saved metric is a stored
            # plan, so the one screen that reopens one is the screen that most needs to say which.
            "plan": answer_writer.describe(plan),
            # The evidence banking already returned with the figure. It was absent altogether, and
            # `Answer.movements` is not optional -- so the disclosure that reads it crashed the
            # whole screen rather than showing one card without movements.
            "movements": [
                {
                    "occurred_at": movement.occurred_at.isoformat(),
                    "description": movement.description,
                    "amount_minor": movement.amount_minor,
                    "direction": movement.direction,
                    "merchant_name": movement.merchant_name,
                    "category": movement.category,
                }
                for movement in answer.movements
            ],
            # Empty, and stated rather than omitted for the same reason: a client reading a field
            # the contract promises should not have to guess whether it is missing or empty.
            "suggestions": [],
            # The same writer a live question uses, on the same plan and the same answer. It used to
            # be the literal "Métrica actualizada", which said only that something had happened --
            # so a saved metric reopened without its figure, its window or its direction, and the
            # card above it could not say what the number meant. Re-executing through the path a
            # question takes (ADR-011 §5) has to include the sentence that path writes.
            "text": answer_writer.write(answer, plan),
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
            "visualization": visualization,
        }

    return execute
