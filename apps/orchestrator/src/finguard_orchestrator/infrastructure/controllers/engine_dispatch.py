"""Where a question route asks the model-led engine first, and what the engine sends back.

`ORCHESTRATOR_CONVERSATION_ENGINE=model` builds an `AnswerTurnHandler` into `app.state.engine`;
`rules` leaves it None. Each question route makes one call here before anything else, and runs
exactly as it always did whenever that call returns None:

- **the engine is off**: production, byte for byte;
- **a money workflow is on the wire** (a payment, a transfer, a booking or a confirmation under
  way): that turn belongs to the state machine that guards it, whatever its words are, and the
  engine is not even asked (spec decision 4);
- **the engine delegates** (`Delegate`: a payment, a transfer, a booking, a chart, or a turn it
  does not support): it saved nothing, so its focus is intact when the conversation comes back.

Every other turn is the engine's, answered with the same `QuestionResponse` wire shape. Its
`conversation` carries the engine's conversation id and version and nothing else: the analytics
memory is the engine's own, server-side, and the rules route's would be stale beside it. A turn the
engine hands over comes back holding that id and version, because the rules route builds every
answer from the conversation it was given and the mapper carries `version` through unchanged.

A version the engine no longer holds is a 409 with the holder's current conversation (the handler
raises `StaleDialogueError` before anything runs; `register` turns it into the response).

**Every answer says who gave it and what it cost**, on both routes and both engines:
`X-FinGuard-Engine: rules|model` names the engine that answered the turn (a delegated turn is the
rules route's), and `X-FinGuard-Model-Calls` the model calls the request made, counted by the
provider wrapper (`counting_provider`). On the stream the headers leave before the first frame:
exact for the engine, which answers before streaming, and for the rules route only up to that
point, since its planner runs inside the stream. The measurement runner reads the JSON route.
"""

import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import QueryRejectedError
from finguard_orchestrator.domain.models.conversation import Conversation, Workflow
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest, QuestionResponse
from finguard_orchestrator.infrastructure.llm.counting_provider import counting_calls
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

logger = logging.getLogger(__name__)

ENGINE_HEADER: Final = "X-FinGuard-Engine"
MODEL_CALLS_HEADER: Final = "X-FinGuard-Model-Calls"
_QUESTION_ROUTES: Final = frozenset({"/v1/questions", "/v1/questions/stream"})
# The workflows whose next turn belongs to the rules route: money, and the booking that may cost it.
_MONEY: Final = frozenset(
    {
        Workflow.PAYMENT_IN_PROGRESS,
        Workflow.TRANSFER_IN_PROGRESS,
        Workflow.PAYMENT_CONFIRMATION,
        Workflow.BOOKING_CONFIRMATION,
    }
)
# The directory and the contacts are read before the sentence is: a refusal there means the turn
# was never interpreted, so it must not say it was («He entendido la pregunta…»).
_UNREAD: Final = "No he podido leer tus cuentas y tus contactos: el banco ha rechazado la consulta."


def _the_engine(request: Request) -> AnswerTurnHandler | None:
    engine: AnswerTurnHandler | None = request.app.state.engine
    return engine


# The route parameter. None, the default, is the engine switched off, which is also what a test
# calling a route function directly gets.
Engine = Annotated[AnswerTurnHandler | None, Depends(_the_engine)]


@dataclass
class _Answered:
    by: Literal["rules", "model"] = "rules"


_answered: ContextVar[_Answered | None] = ContextVar("finguard_answered_by", default=None)


def _answered_by(engine: Literal["rules", "model"]) -> None:
    if (answered := _answered.get()) is not None:
        answered.by = engine


async def try_engine(
    engine: AnswerTurnHandler | None,
    question: QuestionRequest,
    who: Caller,
    today: date,
    metric_tokens: MetricTokenCodec,
) -> QuestionResponse | None:
    """The engine's answer to this turn, or None for the rules route to answer it.

    A version the engine no longer holds raises `StaleDialogueError` out of here, before anything
    ran; `register` writes the 409."""
    echoed = question.conversation
    if engine is None or (echoed is not None and echoed.workflow in _MONEY):
        return None
    conversation_id = echoed.conversation_id if echoed is not None else None
    version = echoed.version if echoed is not None else None
    # Whatever the engine answers, refuses or fails with is the engine's, until it hands over.
    _answered_by("model")
    try:
        turn = await engine.answer(
            question.question,
            conversation_id=conversation_id,
            version=version,
            account_id=who.account_id,
            today=today,
            reporting_timezone=who.reporting_timezone,
        )
    except QueryRejectedError:
        # The handler catches a bank that is down, not one that refuses: answered here, and
        # nothing was saved, so the client keeps the id and version it sent.
        unread = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value, text=_UNREAD
        )
        held = Conversation(conversation_id=conversation_id, version=version)
        return _on_the_wire(unread, held, who, metric_tokens)
    if turn.result is None:
        logger.info("engine.delegated to=%s", turn.delegate_to)
        _answered_by("rules")
        return None
    state = turn.state
    held = Conversation(conversation_id=state.conversation_id, version=state.version)
    return _on_the_wire(turn.result, held, who, metric_tokens)


async def try_engine_streaming(
    engine: AnswerTurnHandler | None,
    question: QuestionRequest,
    who: Caller,
    today: date,
    metric_tokens: MetricTokenCodec,
) -> StreamingResponse | None:
    """`try_engine` for the streaming route: one stage, then the answer, in the route's frames.

    The engine answers before the stream starts, because a turn it delegates has to be streamed
    by the rules route instead, and that cannot be known until it has answered."""
    answer = await try_engine(engine, question, who, today, metric_tokens)
    if answer is None:
        return None

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
    """The engine's result in the routes' shape, holding the engine's conversation and no other.

    Sealed for its holder like every answer the routes give (`test_the_seal_is_never_silently_
    skipped`): the engine draws no chart today, so nothing is minted, and the day it does its
    charts are born sealed rather than refused at their first restyle."""
    return llm_mapper.question_to_dto(
        replace(result, conversation=held),
        holder_reference=who.holder_reference,
        metric_tokens=metric_tokens,
    )


def _sse(payload: Mapping[str, object]) -> str:
    """The route's own frame, copied rather than imported: the controller's helpers are private."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class _Reported:
    """Says, on every answer of the two question routes, which engine answered and how many model
    calls the request made. Raw ASGI, like `RecordingTurns`, so the tally it reads is the one this
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
                        headers.append(ENGINE_HEADER, answered.by)
                        headers.append(MODEL_CALLS_HEADER, str(calls.count))
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
