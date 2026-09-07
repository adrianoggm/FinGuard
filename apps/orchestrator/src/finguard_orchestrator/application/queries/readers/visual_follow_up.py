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
from dataclasses import replace
from typing import cast, get_args

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers.follow_up import read_contextual_follow_up
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import Claimant, VisualFollowUp
from finguard_orchestrator.domain.services.visual_interpreter import (
    VisualIntent,
    fold,
    interpret_visual,
)
from finguard_orchestrator.observability import Step, Verdict, record

# The input is `fold()`ed before this searches it, so an accented alternative (`qué`, an accented
# `ensename`) never matches and none is listed here. `por que` is qualified to the shapes §3.3
# actually names — unqualified, it read «¿Por qué no me funciona la app?» as a drill-down just as
# readily as «¿Por qué agosto es tan alto?» — and neither `explica` nor a bare `responsable`
# survived for the same reason: each claimed an ordinary question that named none of this turn's
# chart.
_DRILL_DOWN = re.compile(
    r"\bpor que\b.*\b(?:tan|tanto|alto|alta|bajo|baja|sube|subio|subido|pico)\b|"
    r"\bque movimientos\b|\bque comercio\b|"
    r"\b(?:ensename|muestrame) (?:los|las) (?:movimientos|compras|operaciones)\b"
)


def _visual_intent(
    query: AskFinancialQuestionQuery, conversation: Conversation
) -> VisualIntent | None:
    """The interpreter's one reading of this turn's presentation request, if any.

    Called exactly once: `classify` reads both whether a presentation change was asked
    (`intent is not None`) and whether the turn is a mixed one — a data change and a format change
    asked together, which `interpret_visual` already recognises as its own refusal — off this same
    `VisualIntent`, rather than asking the interpreter twice for two different facts about it.
    """
    return interpret_visual(
        query.sentence, conversation.visual_contexts, voice=query.presentation.channel == "VOICE"
    )


def _reshape_claims(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> bool:
    if (
        analysis.relation is not TurnRelation.FOLLOW_UP
        or analysis.focus is not TurnFocus.LAST_ANSWER
    ):
        return False
    # `conversation`, not `query.conversation`: the two can differ after a restoration (see
    # `classify`'s docstring), and `read_contextual_follow_up` resolves its focus from
    # `query.conversation` internally, so it is asked with a copy of the query that carries the
    # same working conversation `_visual_intent` already reads directly.
    return read_contextual_follow_up(replace(query, conversation=conversation)) is not None


def _drill_down_claims(query: AskFinancialQuestionQuery, analysis: TurnAnalysis) -> bool:
    if (
        analysis.relation is not TurnRelation.FOLLOW_UP
        or analysis.focus is not TurnFocus.LAST_ANSWER
    ):
        return False
    return _DRILL_DOWN.search(fold(query.sentence)) is not None


_CLAIMANT_NAMES = ("presentation", "reshape", "drill")


def _two_claimant_pair(presentation: bool, reshape: bool, drill: bool) -> Claimant:
    """Name the two claimants that agree, in the fixed order presentation, reshape, drill. The
    pair string is built from whichever two names are true and checked against `get_args(Claimant)`
    rather than hand-typed here, so `Claimant` stays the one place the three pairs are spelled
    out."""
    names = [
        name
        for name, claimed in zip(_CLAIMANT_NAMES, (presentation, reshape, drill), strict=True)
        if claimed
    ]
    pair = "+".join(names)
    assert pair in get_args(Claimant), f"not a two-claimant pair: {pair!r}"
    return cast(Claimant, pair)


# The interpreter's own mixed refusal, named once (M10/D-4.2-6): a data change and a format
# change asked in the same turn is presentation+reshape, at both sites below that read it.
_MIXED_PAIR = _two_claimant_pair(True, True, False)


def classify(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> VisualFollowUp:
    """`conversation` is the handler's working conversation (`was`), which may differ from
    `query.conversation` after a restoration; the charts in context are read from it, and both
    `_reshape_claims` and `_visual_intent` are asked about it rather than about
    `query.conversation`."""
    if not conversation.visual_contexts:
        return VisualFollowUp("UNRELATED", "none")
    intent = _visual_intent(query, conversation)
    if intent is not None and intent.mixed:
        # The interpreter has already read two claimants in this one turn — a data change (reshape)
        # and a format change (presentation) asked together — which is exactly what AMBIGUOUS
        # exists for; the table below is never reached.
        return VisualFollowUp("AMBIGUOUS", _MIXED_PAIR)
    presentation = intent is not None
    # Drill-down is checked before reshape because «¿por qué agosto es tan alto?» is also a
    # FOLLOW_UP on the last answer; the lexicon is the more specific reader and wins, which is why
    # `reshape` is computed as `not drill and ...`.
    drill = _drill_down_claims(query, analysis)
    reshape = not drill and _reshape_claims(query, analysis, conversation)
    # A dict keyed by claimant name would map a plain `str` back onto the `Literal` kind and
    # claimant fields, which mypy strict rejects without a `type: ignore`; three explicit branches
    # keep every return a literal mypy can check.
    if sum((presentation, reshape, drill)) > 1:
        return VisualFollowUp("AMBIGUOUS", _two_claimant_pair(presentation, reshape, drill))
    if presentation:
        return VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")
    if reshape:
        return VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")
    if drill:
        return VisualFollowUp("DRILL_DOWN", "drill_down_reader")
    return VisualFollowUp("UNRELATED", "none")


def classify_claimed_visual(intent: VisualIntent, sentence: str) -> VisualFollowUp:
    """The short-circuit's own row: interpret_visual has already claimed, so the turn is about the
    chart by construction and the drill lexicon needs no FOLLOW_UP gate to be safe. The
    interpreter's own mixed refusal is presentation+reshape (a data change and a format change
    asked together); the drill lexicon matching too is presentation+drill."""
    if intent.mixed:
        return VisualFollowUp("AMBIGUOUS", _MIXED_PAIR)
    if _DRILL_DOWN.search(fold(sentence)):
        return VisualFollowUp("AMBIGUOUS", _two_claimant_pair(True, False, True))
    return VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")


def record_visual_follow_up(follow_up: VisualFollowUp) -> None:
    """One Decision on the existing record; the kind and claimant are service vocabulary.

    The one place `Step.VISUAL_FOLLOW_UP` is written, so both call sites that classify a turn's
    relation to the chart it follows — the handler, for the turn that reaches it, and the
    controller's visual short-circuit, for the turn that never does — record it identically.
    """
    record(
        Step.VISUAL_FOLLOW_UP,
        Verdict.AGREED,
        changed=False,
        field="visual_follow_up",
        now=follow_up.kind,
        detail=follow_up.claimant,
    )
