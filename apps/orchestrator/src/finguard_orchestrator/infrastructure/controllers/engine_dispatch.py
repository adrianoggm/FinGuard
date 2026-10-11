"""The seam between the question routes and the LangGraph runtime.

LangGraph is the only conversational runtime. Each question route makes one call here:

- **a workflow from the retired rules coordinator is on the wire** (an older client still holding
  a payment, transfer, booking or question that coordinator opened): it is not part of the graph's
  canonical state, so it is refused with an explicit answer and never executed. A stale "sí" must
  not confirm anything;
- **the graph delegates** (an operation it has no node for): the answer says so and the canonical
  state and version stay untouched;
- **otherwise** the graph's answer goes out in the `QuestionResponse` wire shape, carrying its
  conversation id and version.

A version other than the one the graph holds is a 409 with the holder's current conversation
(`StaleDialogueError`, turned into the response by `register`). A conversation the graph holds
nothing of starts again under a new id (ruling R27).

**Every answer says what it cost**, on both routes: `X-FinGuard-Engine: model`,
`X-FinGuard-Financial-Reads` the graph's query and balance calls, `X-FinGuard-Model-Calls` the
logical calls counted by the provider wrapper (`counting_provider`) and
`X-FinGuard-Provider-Requests` the actual outbound HTTP attempts, retries and failover included.
`X-FinGuard-Models-Attempted` keeps their ordered model ids, `X-FinGuard-Models-Used` the
successful ones. With provider usage metadata, `X-FinGuard-Prompt-Tokens`,
`X-FinGuard-Completion-Tokens` and `X-FinGuard-Token-Reports` report it; missing usage is left
absent, never reported as zero.
"""

import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders

from finguard_orchestrator.application.bus.command_bus import Command, CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.engine.budgets import MAX_MODEL_CALLS
from finguard_orchestrator.application.engine.turn_commands import (
    AnswerTurnCommand,
    OwnsWorkflowQuery,
    Turn,
)
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import QueryRejectedError
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ConversationPayload,
    QuestionRequest,
    QuestionResponse,
)
from finguard_orchestrator.infrastructure.llm.counting_provider import (
    counting_calls,
    provider_request_budget,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

logger = logging.getLogger(__name__)

ENGINE_HEADER: Final = "X-FinGuard-Engine"
MODEL_CALLS_HEADER: Final = "X-FinGuard-Model-Calls"
PROVIDER_REQUESTS_HEADER: Final = "X-FinGuard-Provider-Requests"
MODELS_ATTEMPTED_HEADER: Final = "X-FinGuard-Models-Attempted"
MODELS_USED_HEADER: Final = "X-FinGuard-Models-Used"
PROMPT_TOKENS_HEADER: Final = "X-FinGuard-Prompt-Tokens"
COMPLETION_TOKENS_HEADER: Final = "X-FinGuard-Completion-Tokens"
TOKEN_REPORTS_HEADER: Final = "X-FinGuard-Token-Reports"
FINANCIAL_READS_HEADER: Final = "X-FinGuard-Financial-Reads"
PROVIDER_FAILURES_HEADER: Final = "X-FinGuard-Provider-Failures"
PROVIDER_TERMINAL_FAILURE_HEADER: Final = "X-FinGuard-Provider-Terminal-Failure"
_STREAM_ROUTE: Final = "/v1/questions/stream"
_QUESTION_ROUTES: Final = frozenset({"/v1/questions", _STREAM_ROUTE})
# The directory and the contacts are read before the sentence is: a refusal there means the turn
# was never interpreted, so it must not say it was («He entendido la pregunta…»).
_UNREAD: Final = "No he podido leer tus cuentas y tus contactos: el banco ha rechazado la consulta."


@dataclass
class _Answered:
    financial_reads: int | None = None


_answered: ContextVar[_Answered | None] = ContextVar("finguard_answered_by", default=None)
# The graph's delegations, said in words: an operation it has no node for yet.
_UNAVAILABLE: Final = {
    "CHART": "Todavía no puedo crear gráficos desde esta conversación.",
    "PAYMENT": "Los pagos aún no están disponibles en este flujo conversacional.",
    "TRANSFER": "Las transferencias aún no están disponibles en este flujo conversacional.",
    "BOOKING": "Las reservas aún no están disponibles en este flujo conversacional.",
    "UNSUPPORTED": "Todavía no puedo resolver esa operación desde esta conversación.",
}
_RETIRED_WORKFLOW: Final = (
    "Esta operación pertenece a una conversación iniciada con el flujo anterior. "
    "No puedo continuarla ni ejecutarla desde el nuevo flujo; empieza de nuevo la gestión."
)


async def try_engine(
    commands: CommandBus,
    queries: QueryBus,
    question: QuestionRequest,
    who: Caller,
    today: date,
    metric_tokens: MetricTokenCodec,
    request_id: str | None = None,
) -> QuestionResponse:
    """The graph's answer to this turn.

    A version other than the held one raises `StaleDialogueError` out of here, before anything
    ran; `register` writes the 409."""
    return await _turn(
        commands,
        queries,
        question.conversation,
        who,
        metric_tokens,
        lambda turn: AnswerTurnCommand(question.question, turn),
        today=today,
        request_id=request_id,
    )


async def _turn(
    commands: CommandBus,
    queries: QueryBus,
    echoed: ConversationPayload | None,
    who: Caller,
    metric_tokens: MetricTokenCodec,
    command_for: Callable[[Turn], Command[EngineAnswer]],
    *,
    today: date,
    request_id: str | None,
) -> QuestionResponse:
    conversation_id = echoed.conversation_id if echoed is not None else None
    version = echoed.version if echoed is not None else None
    if echoed is not None and echoed.workflow is not None:
        owns_workflow = conversation_id is not None and await queries.ask(
            OwnsWorkflowQuery(conversation_id, who.account_id, echoed.workflow)
        )
        if not owns_workflow:
            refused = AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value, text=_RETIRED_WORKFLOW
            )
            held = Conversation(conversation_id=conversation_id, version=version)
            return _on_the_wire(refused, held, who, metric_tokens)
    turn_context = Turn(
        conversation_id=conversation_id,
        version=version,
        account_id=who.account_id,
        today=today,
        reporting_timezone=who.reporting_timezone,
        request_id=request_id,
        caller_scopes=who.scopes,
    )
    try:
        with provider_request_budget(MAX_MODEL_CALLS):
            turn = await commands.dispatch(command_for(turn_context))
        if (answered := _answered.get()) is not None:
            answered.financial_reads = turn.financial_reads
    except QueryRejectedError:
        # The graph catches a bank that is down, not one that refuses: answered here, and nothing
        # was saved, so the client keeps the id and version it sent.
        unread = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value, text=_UNREAD
        )
        held = Conversation(conversation_id=conversation_id, version=version)
        return _on_the_wire(unread, held, who, metric_tokens)
    held = Conversation(conversation_id=turn.state.conversation_id, version=turn.state.version)
    if turn.result is None:
        # Never sent back through a second natural-language planner: the boundary is visible to
        # the user and canonical state and version stay untouched.
        logger.info("engine.delegated to=%s", turn.delegate_to)
        unavailable = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text=_UNAVAILABLE.get(turn.delegate_to or "", _UNAVAILABLE["UNSUPPORTED"]),
        )
        return _on_the_wire(unavailable, held, who, metric_tokens)
    return _on_the_wire(turn.result, held, who, metric_tokens)


async def try_engine_streaming(
    commands: CommandBus,
    queries: QueryBus,
    question: QuestionRequest,
    who: Caller,
    today: date,
    metric_tokens: MetricTokenCodec,
    request_id: str | None = None,
) -> StreamingResponse:
    """`try_engine` for the streaming route: one stage, then the answer, in the route's frames.
    The graph answers before the stream starts, so the headers carry exact counts."""
    answer = await try_engine(commands, queries, question, who, today, metric_tokens, request_id)

    async def frames() -> AsyncIterator[str]:
        yield _sse({"type": "stage", "name": "interpreting", "detail": None})
        yield _sse({"type": "answer", **answer.model_dump()})

    return StreamingResponse(frames(), media_type="text/event-stream")


def _on_the_wire(
    result: AskFinancialQuestionResult,
    held: Conversation,
    who: Caller,
    metric_tokens: MetricTokenCodec,
) -> QuestionResponse:
    """The engine's result in the routes' shape, with its state and authoritative version.

    Sealed for its holder like every answer the routes give (`test_the_seal_is_never_silently_
    skipped`). Graph-native visuals are verified and sealed by the graph before they arrive here.
    Keep the result's typed workflow state (for example a bank preview awaiting confirmation); the
    held value owns only the canonical id and version. Replacing the result state wholesale loses
    the payment preview between the graph and the web client, leaving prose that asks for
    confirmation without the buttons or payload needed to answer it."""
    conversation = replace(
        result.conversation,
        conversation_id=held.conversation_id,
        version=held.version,
    )
    return llm_mapper.question_to_dto(
        replace(result, conversation=conversation),
        holder_reference=who.holder_reference,
        metric_tokens=metric_tokens,
    )


def _sse(payload: Mapping[str, object]) -> str:
    """The route's own frame, copied rather than imported: the controller's helpers are private."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class _Reported:
    """Says, on every answer of the two question routes, how many model calls and bank reads the
    request made. Raw ASGI, like `RecordingTurns`, so the tally it reads is the one this
    request counted into, whatever task the route ran its calls in."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] not in _QUESTION_ROUTES:
            await self.app(scope, receive, send)
            return
        answered = _Answered()
        token = _answered.set(answered)
        try:
            with counting_calls() as calls:

                async def reported(message: Any) -> None:
                    if message["type"] == "http.response.start":
                        headers = MutableHeaders(scope=message)
                        headers.append(ENGINE_HEADER, "model")
                        headers.append(MODEL_CALLS_HEADER, str(calls.count))
                        headers.append(PROVIDER_REQUESTS_HEADER, str(calls.provider_requests))
                        headers.append(
                            MODELS_ATTEMPTED_HEADER,
                            ",".join(calls.attempted_models or []),
                        )
                        headers.append(
                            MODELS_USED_HEADER,
                            ",".join(calls.successful_models or []),
                        )
                        if calls.provider_failures:
                            headers.append(
                                PROVIDER_FAILURES_HEADER,
                                ",".join(calls.provider_failures),
                            )
                        if calls.terminal_provider_failure:
                            headers.append(
                                PROVIDER_TERMINAL_FAILURE_HEADER,
                                calls.terminal_provider_failure,
                            )
                        if calls.token_reports:
                            headers.append(PROMPT_TOKENS_HEADER, str(calls.prompt_tokens))
                            headers.append(COMPLETION_TOKENS_HEADER, str(calls.completion_tokens))
                            headers.append(TOKEN_REPORTS_HEADER, str(calls.token_reports))
                        if answered.financial_reads is not None:
                            headers.append(FINANCIAL_READS_HEADER, str(answered.financial_reads))
                    await send(message)

                await self.app(scope, receive, reported)
        finally:
            with suppress(ValueError):
                _answered.reset(token)


def register(app: FastAPI) -> None:
    """The headers on both question routes, and the 409 a stale version gets."""
    app.add_middleware(_Reported)

    @app.exception_handler(StaleDialogueError)
    async def _stale(request: Request, exc: Exception) -> JSONResponse:
        """The holder's own current conversation (ruling R20: the handler loaded it under the
        caller's account), so the client can adopt it and ask again."""
        assert isinstance(exc, StaleDialogueError)
        current = exc.current
        payload = conversation_mapper.to_payload(
            Conversation(conversation_id=current.conversation_id, version=current.version)
        )
        return JSONResponse(
            {"code": "STALE_CONVERSATION", "conversation": payload.model_dump(mode="json")},
            status_code=409,
        )
