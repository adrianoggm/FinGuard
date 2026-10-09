"""The investigation graph's composition (Task 8), loaded only when `conversation_graph=on`.

The same interpreter, store kind and banking adapters as the model engine, plus the assessor on the
planner's provider and model, the turn reservations and LangGraph's in-memory checkpointer. Every
one of those three keeps its state in this process: a restart loses checkpoints and reservations,
and two replicas would not see each other's. That is the experiment's honest scope, not a
deployment.
"""

from langgraph.checkpoint.memory import InMemorySaver

from finguard_orchestrator.application.graph.graph import GraphPorts
from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.bootstrap.config import Settings
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import TurnOperationsPort
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore
from finguard_orchestrator.infrastructure.graph.llm_assessor import LlmInvestigationAssessor
from finguard_orchestrator.infrastructure.graph.memory_turn_reservations import (
    MemoryTurnReservations,
)
from finguard_orchestrator.infrastructure.llm.counting_provider import calls_so_far


def graph_engine(
    settings: Settings,
    interpreter: TurnOperationsPort,
    provider: LlmProviderPort,
    banking: FinancialQueryPort,
    accounts: AccountSummaryPort,
    account_directory: AccountDirectoryPort,
    contacts: ContactDirectoryPort,
) -> GraphTurnHandler:
    ports = GraphPorts(
        interpreter,
        LlmInvestigationAssessor(
            provider,
            model=settings.engine_model or settings.planner_model,
            temperature=settings.engine_temperature,
            max_output_tokens=settings.engine_max_output_tokens,
        ),
        MemoryDialogueStore(),
        banking,
        accounts,
        account_directory,
        contacts,
        frozenset(settings.categories),
        count_calls=calls_so_far,
    )
    return GraphTurnHandler(ports, MemoryTurnReservations(), InMemorySaver())
