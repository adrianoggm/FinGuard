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
    category_words,
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
# Task 1 (Wave 4.2b): a follow-up focused on the last answer, but only inside the
# `conversation.last_plan is not None` branch below -- never a new-request shape on its own.
#
# The bare "ahora" also reads a discourse marker as a reshape: "Ahora bien, ¿cuánto tengo?" is
# FOLLOW_UP here whenever a plan is in view (D-4.2b-1, dropped as a fix). It is provably inert --
# `relation` is consulted in exactly two places, `visual_follow_up.py`'s classifier and its drill
# lexicon, both gated on a chart in context and both requiring a *second* claimant before anything
# changes, and the balance route above reads `intent`, never `relation`
# (`tests/test_balance_answer.py::test_ahora_bien_still_answers_the_balance_over_a_charted_
# conversation` pins the answer unchanged). Excluding "ahora bien" and its siblings would add a
# second vocabulary of discourse markers to maintain for zero behaviour change.
_RESHAPE_FOLLOW_UP: Final = re.compile(
    r"^\s*(?:"
    r"ahora\b"  # "Ahora compáralo por meses."
    r"|(?:compara|separa|desglosa|agrupa|reparte|divide|distingue)"
    r"(?:lo|la|los|las|melo|mela|melos|melas)\b"  # "Desglósalo por comercio.", "Sepáralo en..."
    r")",
    re.IGNORECASE,
)
# A split imperative alone is not enough -- "Separa la gasolina." names one category, which is a
# filter question and stays a NEW_REQUEST as today; `_splits_two_or_more_categories` is what tells
# the two shapes apart.
_SPLIT_IMPERATIVE: Final = re.compile(
    r"^\s*(?:separa|desglosa|divide|distingue|reparte)\b", re.IGNORECASE
)


def _splits_two_or_more_categories(sentence: str, folded: str) -> bool:
    """A split imperative ("Separa transporte público y gasolina.") reshapes the last answer only
    once it names two or more precise categories; one name ("Separa la gasolina.") is a filter
    question and is read exactly as today."""
    if _SPLIT_IMPERATIVE.search(folded) is None:
        return False
    return len(category_words.precisely_named_in(sentence)) >= 2


def _is_reshape_follow_up(sentence: str, folded: str) -> bool:
    """The three Wave 4.2b shapes: «ahora…», a reshape/compare verb carrying a clitic pronoun, and
    a split imperative naming two or more precise categories."""
    return _RESHAPE_FOLLOW_UP.search(folded) is not None or _splits_two_or_more_categories(
        sentence, folded
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
    # Folded once (M-5, Wave 4.2b fix wave): `spanish.folded` is not memoised, and the two
    # FOLLOW_UP checks below both used to fold the same sentence again on their own.
    folded = spanish.folded(sentence)
    if conversation.awaiting is not None or conversation.last_asked is not None:
        relation = TurnRelation.ANSWER_TO_SLOT
        focus = TurnFocus.ACTIVE_WORKFLOW
    elif intent is Intent.CORRECTION:
        relation = TurnRelation.CORRECTION
        focus = TurnFocus.LAST_ANSWER
    elif intent is Intent.EXPLAIN_PREVIOUS or (
        conversation.last_plan is not None
        and (_FOLLOW_UP.search(folded) is not None or _is_reshape_follow_up(sentence, folded))
    ):
        relation = TurnRelation.FOLLOW_UP
        focus = TurnFocus.LAST_ANSWER
    else:
        relation = TurnRelation.NEW_REQUEST
        focus = TurnFocus.NEW_REQUEST
    return TurnAnalysis(intent=intent, relation=relation, focus=focus)
