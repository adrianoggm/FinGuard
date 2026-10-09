"""The result contract shared by the model-led turn runner and its LangGraph replacement."""

from dataclasses import dataclass

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.state import DialogueState


@dataclass(frozen=True)
class EngineAnswer:
    """`result` is None exactly when `delegate_to` names the flow that owns this turn."""

    result: AskFinancialQuestionResult | None
    state: DialogueState
    delegate_to: str | None
    model_calls: int
    stages_ms: dict[str, int]
