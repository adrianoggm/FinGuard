"""Build the graph's investigation evidence and its bank-grounded explanation.

The StateGraph decides when an investigation may continue. This module only reconstructs the
bounded results already stored in graph state and formats the final explanation.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.transitions import Work
from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.models.query_answer import Movement
from finguard_orchestrator.domain.ports.investigation_assessor import (
    InspectedSummary,
    InvestigationSummary,
    MerchantSummary,
)
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.infrastructure.graph import investigation
from finguard_orchestrator.infrastructure.graph.graph_state import TurnGraphState
from finguard_orchestrator.infrastructure.graph.investigation import Explanation, MerchantChange


def changes(state: TurnGraphState) -> tuple[MerchantChange, ...]:
    return tuple(
        MerchantChange(name, e_total, e_count, l_total, l_count, Explanation(cause), decreased)
        for name, e_total, e_count, l_total, l_count, cause, decreased in state.get("changes", [])
    )


def inspections(state: TurnGraphState) -> tuple[investigation.Inspection, ...]:
    by_name = {change.merchant: change for change in changes(state)}
    found = []
    for merchant, amount, occurred_at in state.get("inspected", []):
        largest = None
        if amount is not None:
            largest = Movement(datetime.fromisoformat(occurred_at), "", amount, "EXPENSE")
        found.append(investigation.inspected(by_name[merchant], largest))
    return tuple(found)


def may_inspect(state: TurnGraphState) -> tuple[str, ...]:
    done = {merchant for merchant, *_ in state.get("inspected", [])}
    return tuple(
        merchant
        for merchant in investigation.worth_inspecting(changes(state))
        if merchant not in done
    )


def summary(state: TurnGraphState, candidates: tuple[str, ...]) -> InvestigationSummary:
    currency = state.get("currency", "EUR")
    inspected = []
    for found in inspections(state):
        largest = "ninguna"
        if found.largest is not None:
            amount = answer_writer.money(abs(found.largest.amount_minor), currency)
            largest = f"{amount} el {found.largest.occurred_at.date().isoformat()}"
        inspected.append(InspectedSummary(found.merchant, found.finding or "NOT_FOUND", largest))
    return InvestigationSummary(
        risen=tuple(
            MerchantSummary(
                change.merchant,
                change.explanation,
                change.earlier_count,
                change.latest_count,
            )
            for change in changes(state)
            if not change.decreased
        ),
        inspected=tuple(inspected),
        may_inspect=candidates,
    )


def investigated(
    state: TurnGraphState,
    work: Work,
    names: Mapping[UUID, str],
    account_ids: tuple[UUID, ...],
) -> AskFinancialQuestionResult:
    assert isinstance(work.plan, ComparisonPlan)
    earlier, latest = work.plan.earlier, work.plan.latest
    currency = state["currency"]
    then, now = state["totals"]
    text = investigation.answer_text(
        changes(state),
        inspections(state),
        earlier=earlier,
        latest=latest,
        earlier_total=then,
        latest_total=now,
        currency=currency,
        partial=state.get("partial", False),
    )
    text = "\n".join((text, presenter.scope_note(account_ids, names)))
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=text,
        plan_in_words=(
            f"{answer_writer.plan_in_words(earlier)} frente a "
            f"{answer_writer.plan_in_words(latest)}, por comercio"
        ),
        currency=currency,
    )
