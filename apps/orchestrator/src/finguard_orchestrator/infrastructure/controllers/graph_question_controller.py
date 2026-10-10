"""Question HTTP routes owned exclusively by the LangGraph runtime.

LangGraph is the only conversational runtime; a missing graph is a 503, never a fallback.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from finguard_orchestrator.application.engine.turn_handler import ConversationTurnHandler
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import engine_dispatch
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest, QuestionResponse
from finguard_orchestrator.infrastructure.http.dependencies import (
    questions_enabled,
    reporting_today,
    required_attempt_key,
    requires,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

router = APIRouter(tags=["assistant"])

GraphCaller = Annotated[Caller, Depends(requires(Scope.QUESTIONS_READ))]
Today = Annotated[date, Depends(reporting_today)]
Attempt = Annotated[str, Depends(required_attempt_key)]


def _metric_token_codec(request: Request) -> MetricTokenCodec:
    codec = request.app.state.metric_tokens
    if not isinstance(codec, MetricTokenCodec):
        raise RuntimeError("invalid metrics token codec")
    return codec


def _the_graph(request: Request) -> ConversationTurnHandler:
    engine: ConversationTurnHandler | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="The conversation graph is not available.")
    return engine


Graph = Annotated[ConversationTurnHandler, Depends(_the_graph)]
MetricTokens = Annotated[MetricTokenCodec, Depends(_metric_token_codec)]


@router.post(
    "/v1/questions",
    summary="Answer a financial question with LangGraph",
    dependencies=[Depends(questions_enabled)],
)
async def ask(
    body: QuestionRequest,
    who: GraphCaller,
    today: Today,
    attempt: Attempt,
    metric_tokens: MetricTokens,
    engine: Graph,
) -> QuestionResponse:
    return await engine_dispatch.try_engine(
        engine, body, who, today, metric_tokens, request_id=attempt
    )


@router.post(
    "/v1/questions/stream",
    summary="Answer a financial question with LangGraph",
    dependencies=[Depends(questions_enabled)],
)
async def ask_streaming(
    body: QuestionRequest,
    who: GraphCaller,
    today: Today,
    attempt: Attempt,
    metric_tokens: MetricTokens,
    engine: Graph,
) -> StreamingResponse:
    return await engine_dispatch.try_engine_streaming(
        engine, body, who, today, metric_tokens, request_id=attempt
    )
