"""One sentence, one reading.

A turn handed down with a reading carries it once. A query that names an intent and an analysis
that disagree is two readings of one sentence, and three answers to one question is how "mueve
200 € a la hucha" once paid the wrong person, so the query refuses to be built that way.
"""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.services.intent_router import Intent

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)


def test_query_rejects_discordant_legacy_intent_and_analysis():
    with pytest.raises(InvalidValueError):
        AskFinancialQuestionQuery(
            sentence="¿por qué?",
            account_id=ACCOUNT,
            today=TODAY,
            intent=Intent.FINANCIAL_QUERY,
            analysis=TurnAnalysis(
                Intent.EXPLAIN_PREVIOUS,
                TurnRelation.FOLLOW_UP,
                TurnFocus.LAST_ANSWER,
            ),
        )
