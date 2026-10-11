"""The conversation's HTTP routes: a question, its stream, and a screen's typed action.

Each dispatches a turn command on the bus; the LangGraph runtime behind it is the only
conversational runtime, and a missing graph is a 503 (`GraphUnavailableError`), never a fallback.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import engine_dispatch
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest, QuestionResponse
from finguard_orchestrator.infrastructure.http.dependencies import (
    get_command_bus,
    get_query_bus,
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


Commands = Annotated[CommandBus, Depends(get_command_bus)]
Queries = Annotated[QueryBus, Depends(get_query_bus)]
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
    commands: Commands,
    queries: Queries,
) -> QuestionResponse:
    return await engine_dispatch.try_engine(
        commands, queries, body, who, today, metric_tokens, request_id=attempt
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
    commands: Commands,
    queries: Queries,
) -> StreamingResponse:
    return await engine_dispatch.try_engine_streaming(
        commands, queries, body, who, today, metric_tokens, request_id=attempt
    )
