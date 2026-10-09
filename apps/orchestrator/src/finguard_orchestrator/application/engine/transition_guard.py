"""Shared safety boundary for applying typed operations to canonical dialogue state."""

import logging
from collections.abc import Sequence
from datetime import date

from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import Clarify, Operation
from finguard_orchestrator.domain.engine.state import DialogueState, Missing
from finguard_orchestrator.domain.engine.transitions import (
    InvariantViolationError,
    Transition,
    apply,
)

logger = logging.getLogger(__name__)


def apply_guarded(
    state: DialogueState, ops: Sequence[Operation], today: date, grounds: Grounds
) -> Transition:
    """Apply the validated transition; turn an invariant violation into a safe clarification."""
    try:
        return apply(state, ops, today=today, grounds=grounds)
    except InvariantViolationError as broken:
        logger.error("engine.invariant_violation detail=%s", broken)
        return Transition(state, None, Clarify(Missing.SUBJECT))
