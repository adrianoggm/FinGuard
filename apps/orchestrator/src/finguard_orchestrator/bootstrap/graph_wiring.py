"""Composition of the unified conversation graph and its injected persistence ports."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.graph.graph import GraphPorts
from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.bootstrap.config import Settings
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.dialogue_store import DialogueStorePort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import TurnOperationsPort
from finguard_orchestrator.domain.ports.turn_reservations import TurnReservationsPort
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.infrastructure.graph.llm_assessor import LlmInvestigationAssessor
from finguard_orchestrator.infrastructure.llm.counting_provider import budget_calls_so_far


def graph_engine(
    settings: Settings,
    interpreter: TurnOperationsPort,
    provider: LlmProviderPort,
    banking: FinancialQueryPort,
    accounts: AccountSummaryPort,
    account_directory: AccountDirectoryPort,
    contacts: ContactDirectoryPort,
    store: DialogueStorePort,
    reservations: TurnReservationsPort,
    checkpointer: BaseCheckpointSaver[Any],
    visual_policy: VisualPlanningPolicy | None = None,
    commands: CommandBus | None = None,
) -> GraphTurnHandler:
    ports = GraphPorts(
        interpreter,
        LlmInvestigationAssessor(
            provider,
            model=settings.engine_model or settings.planner_model,
            temperature=settings.engine_temperature,
            max_output_tokens=settings.engine_max_output_tokens,
        ),
        store,
        banking,
        accounts,
        account_directory,
        contacts,
        frozenset(settings.categories),
        visual_policy=visual_policy,
        # Graph turn budgets count every hosted HTTP attempt (including 429 retry/failover), while
        # local providers still consume one unit per logical generation call.
        count_calls=budget_calls_so_far,
        commands=commands,
    )
    return GraphTurnHandler(ports, reservations, checkpointer)
