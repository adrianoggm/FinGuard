"""Separate the subject of a turn from its relationship to the conversation."""

import re
from datetime import date
from typing import Final

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import (
    financial_relationships,
    intent_router,
    spanish,
    window_words,
)
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
_COMPARISON_WORDS: Final = re.compile(
    r"\b(?:compara\w*|comparacion|frente\s+a(?:l)?|contra|versus|vs)\b"
)


def _substantive(clause: str, intent: Intent) -> bool:
    if intent is Intent.COURTESY:
        return False
    if intent is Intent.FINANCIAL_QUERY:
        # A fragment which merely fell through is not evidence of a second request. Financial
        # vocabulary is: movements/spending/income are exactly the router's balance exclusions.
        return intent_router.asks_for_something_else(clause)
    return True


def _is_closed_relationship(clause: str, today: date) -> bool:
    if financial_relationships.asks_net_cash_flow(
        clause
    ) or financial_relationships.asks_savings_rate(clause):
        return True
    if _COMPARISON_WORDS.search(spanish.folded(clause)) is None:
        return False
    return window_words.has_compared_windows(clause, today=today)


def _request_score(clause: str, conversation: Conversation, today: date) -> int:
    """How complete one possible clause is; zero means it is not a request by itself."""
    if _is_closed_relationship(clause, today):
        return 4
    intent = intent_router.route(
        clause,
        has_previous=conversation.last_plan is not None,
        at_rest=conversation.at_rest,
    )
    if not _substantive(clause, intent):
        return 0
    if intent is Intent.FINANCIAL_QUERY:
        return 2 if window_words.has_complete_window(clause, today=today) else 1
    return 3


def _best_partition(
    sentence: str, conversation: Conversation, today: date, *, limit: int
) -> tuple[int, tuple[str, ...]]:
    cleaned = sentence.strip(" \t\r\n,¿?¡!")
    own_score = _request_score(cleaned, conversation, today)
    candidates: list[tuple[int, tuple[str, ...]]] = [(own_score, (cleaned,))] if own_score else []
    if limit > 1:
        for boundary in _BOUNDARY.finditer(sentence):
            left = sentence[: boundary.start()].strip(" \t\r\n,¿?¡!")
            right = sentence[boundary.end() :].strip(" \t\r\n,¿?¡!")
            left_score = _request_score(left, conversation, today) if left else 0
            if left_score == 0 or not right:
                continue
            right_score, right_parts = _best_partition(right, conversation, today, limit=limit - 1)
            if right_score:
                candidates.append((left_score + right_score, (left, *right_parts)))
    if not candidates:
        return 0, ()
    return max(candidates, key=lambda candidate: (candidate[0], len(candidate[1])))


def _compound_clauses(sentence: str, conversation: Conversation, today: date) -> tuple[str, ...]:
    _, clauses = _best_partition(sentence, conversation, today, limit=3)
    return clauses if len(clauses) >= 2 else ()


async def analyze(
    sentence: str,
    *,
    conversation: Conversation,
    interpreter: TurnInterpreterPort,
    today: date | None = None,
) -> TurnAnalysis:
    """Read a turn once and retain the contextual decision alongside its intent."""
    clauses = _compound_clauses(sentence, conversation, today or date.today())
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
