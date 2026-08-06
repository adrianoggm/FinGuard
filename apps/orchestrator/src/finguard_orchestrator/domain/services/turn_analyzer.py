"""Separate the subject of a turn from its relationship to the conversation."""

import re
from typing import Final

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import intent_router, spanish
from finguard_orchestrator.domain.services.intent_router import Intent

# Deliberately conservative. A conjunction is only accepted as a request boundary after each side
# independently routes to a substantive request. Thus "gasolina y supermercado" remains one
# filter, while both "cuánto tengo y cuánto gasté" and two different financial questions are not
# silently reduced to one half.
_BOUNDARY: Final = re.compile(
    r"\s*(?:;|\.(?=\s|$)|\b(?:y\s+adem[aá]s|adem[aá]s|tambi[eé]n)\b|\by\b)\s*",
    re.IGNORECASE,
)
_FOLLOW_UP: Final = re.compile(
    r"^\s*(?:[¿?]?\s*)?(?:y\b|entonces\b|en\s+(?:la|el|los|las)\b)", re.IGNORECASE
)


def _substantive(clause: str, intent: Intent) -> bool:
    if intent is Intent.COURTESY:
        return False
    if intent is Intent.FINANCIAL_QUERY:
        # A fragment which merely fell through is not evidence of a second request. Financial
        # vocabulary is: movements/spending/income are exactly the router's balance exclusions.
        return intent_router.asks_for_something_else(clause)
    return True


def _compound_clauses(sentence: str, conversation: Conversation) -> tuple[str, ...]:
    pieces = tuple(piece.strip(" \t\r\n,¿?¡!") for piece in _BOUNDARY.split(sentence))
    pieces = tuple(piece for piece in pieces if piece)
    if len(pieces) < 2:
        return ()

    routed = tuple(
        (
            piece,
            intent_router.route(
                piece,
                has_previous=conversation.last_plan is not None,
                at_rest=conversation.at_rest,
            ),
        )
        for piece in pieces
    )
    requests = tuple(piece for piece, intent in routed if _substantive(piece, intent))
    if len(requests) >= 2:
        return requests
    return ()


async def analyze(
    sentence: str, *, conversation: Conversation, interpreter: TurnInterpreterPort
) -> TurnAnalysis:
    """Read a turn once and retain the contextual decision alongside its intent."""
    clauses = _compound_clauses(sentence, conversation)
    if clauses:
        return TurnAnalysis(
            intent=Intent.FINANCIAL_QUERY,
            relation=TurnRelation.COMPOUND,
            focus=TurnFocus.MULTIPLE_REQUESTS,
            clauses=clauses,
        )

    intent = await interpreter.interpret(sentence, conversation=conversation)
    if conversation.awaiting is not None or conversation.last_asked is not None:
        relation = TurnRelation.ANSWER_TO_SLOT
        focus = TurnFocus.ACTIVE_WORKFLOW
    elif intent is Intent.CORRECTION:
        relation = TurnRelation.CORRECTION
        focus = TurnFocus.LAST_ANSWER
    elif intent is Intent.EXPLAIN_PREVIOUS or (
        conversation.last_plan is not None and _FOLLOW_UP.search(spanish.folded(sentence))
    ):
        relation = TurnRelation.FOLLOW_UP
        focus = TurnFocus.LAST_ANSWER
    else:
        relation = TurnRelation.NEW_REQUEST
        focus = TurnFocus.NEW_REQUEST
    return TurnAnalysis(intent=intent, relation=relation, focus=focus)
