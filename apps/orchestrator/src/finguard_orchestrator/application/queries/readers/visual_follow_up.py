"""Classify a turn against the charts in context — deterministically, changing nothing.

Three readers already exist and each recognises one kind: `interpret_visual` reads a presentation
change, `read_contextual_follow_up` reads a reshape as a patch on the previous plan, and the
drill-down lexicon below reads a question about one part of the result. The table in `classify`
is §5's: one claimant names the kind; two claimants are AMBIGUOUS; none is UNRELATED. No model, no
banking call, no state — the result is recorded and, in 4.1, nothing else.

`read_contextual_follow_up` is the undecorated body behind the cascade's own
`contextual_follow_up_plan` — this module calls the body, never the `@_recogniser`-wrapped name,
because that decorator opens a telemetry span and memoises onto the query: calling it here would
have a read-only classifier record a `RECOGNISER`/`CLAIMED` decision, and record it twice over on
the turn the cascade goes on to plan.
"""

from __future__ import annotations

import re

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers.follow_up import read_contextual_follow_up
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.infrastructure.visualization.interpreter import fold, interpret_visual

_DRILL_DOWN = re.compile(
    r"\b(por que|qué movimientos|que movimientos|qué comercio|que comercio|qué compras|que compras|"
    r"responsable|responsables|explica|ensename (los|las) (movimientos|compras|operaciones)|"
    r"muestrame (los|las) (movimientos|compras|operaciones))\b"
)


def _presentation_claims(query: AskFinancialQuestionQuery, conversation: Conversation) -> bool:
    return interpret_visual(query.sentence, conversation.visual_contexts) is not None


def _reshape_claims(query: AskFinancialQuestionQuery, analysis: TurnAnalysis) -> bool:
    if (
        analysis.relation is not TurnRelation.FOLLOW_UP
        or analysis.focus is not TurnFocus.LAST_ANSWER
    ):
        return False
    return read_contextual_follow_up(query) is not None


def _drill_down_claims(query: AskFinancialQuestionQuery, analysis: TurnAnalysis) -> bool:
    if analysis.relation is not TurnRelation.FOLLOW_UP:
        return False
    return _DRILL_DOWN.search(fold(query.sentence)) is not None


def classify(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> VisualFollowUp:
    """`conversation` is the handler's working conversation (`was`), which may differ from
    `query.conversation` after a restoration; the charts in context are read from it."""
    if not conversation.visual_contexts:
        return VisualFollowUp("UNRELATED", "none")
    presentation = _presentation_claims(query, conversation)
    # Drill-down is checked before reshape because «¿por qué agosto es tan alto?» is also a
    # FOLLOW_UP on the last answer; the lexicon is the more specific reader and wins, which is why
    # `reshape` is computed as `not drill and ...`.
    drill = _drill_down_claims(query, analysis)
    reshape = not drill and _reshape_claims(query, analysis)
    # A dict keyed by claimant name would map a plain `str` back onto the `Literal` kind and
    # claimant fields, which mypy strict rejects without a `type: ignore`; three explicit branches
    # keep every return a literal mypy can check.
    if sum((presentation, reshape, drill)) > 1:
        return VisualFollowUp("AMBIGUOUS", "both")
    if presentation:
        return VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")
    if reshape:
        return VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")
    if drill:
        return VisualFollowUp("DRILL_DOWN", "drill_down_reader")
    return VisualFollowUp("UNRELATED", "none")
