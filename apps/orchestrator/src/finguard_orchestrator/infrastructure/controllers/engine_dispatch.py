"""Where a question route asks the model-led engine first, and what the engine sends back.

`ORCHESTRATOR_CONVERSATION_ENGINE=model` builds an `AnswerTurnHandler` into `app.state.engine`;
`rules` leaves it None. Each question route makes one call here before anything else, and runs
exactly as it always did whenever that call returns None:

- **the engine is off**: production, byte for byte;
- **a workflow is on the wire** (a payment, a transfer, a booking or a confirmation under way, or
  a question the rules route asked): it was opened on that route and its next turn is that
  route's, whatever its words are, so the engine is not even asked (spec decision 4, ruling R26).
  The one exception is a half-built payment the rules route has *set aside* (`suspended`) to
  answer a question: the engine answers the turns after it, and hands the draft back exactly as it
  was echoed, so it can still be resumed there;
- **the engine delegates** (`Delegate`: a payment, a transfer, a booking, a chart, or a turn it
  does not support): it saved nothing, so its focus is intact when the conversation comes back.

Every other turn is the engine's, answered with the same `QuestionResponse` wire shape. Its
`conversation` carries the engine's conversation id and version, plus what it must not drop: a
set-aside payment draft, and a gym search's sealed offers in the conversation they were sealed for.
Nothing else: the analytics memory is the engine's own, server-side, and
the rules route's would be stale beside it. A turn the
engine hands over comes back holding that id and version, because the rules route builds every
answer from the conversation it was given and the mapper carries `version` through unchanged.

A version other than the one the engine holds for this conversation is a 409 with the holder's
current conversation (the handler raises `StaleDialogueError` before anything runs; `register`
turns it into the response). A conversation the engine holds nothing of, after a restart or under
someone else's id, simply starts again under a new id (ruling R27).

**Every answer says who gave it and what it cost**, on both routes and both engines:
`X-FinGuard-Engine: rules|model` names the engine that answered the turn (a delegated turn is the
rules route's), and `X-FinGuard-Model-Calls` the model calls the request made, counted by the
provider wrapper (`counting_provider`). On the stream the headers leave before the first frame:
the engine has answered by then, so its count is exact; the rules route's planner runs inside the
stream, so a rules answer there carries no count at all rather than a partial one. The measurement
runner reads the JSON route, where both are exact.
"""

import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.constants import MAX_PROMPT_LENGTH
from finguard_orchestrator.domain.engine.state import ComparisonFocus, DialogueState, QueryFocus
from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import QueryRejectedError
from finguard_orchestrator.domain.models.conversation import Conversation, Workflow
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ConversationPayload,
    QuestionRequest,
    QuestionResponse,
)
from finguard_orchestrator.infrastructure.llm.counting_provider import counting_calls
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

logger = logging.getLogger(__name__)

ENGINE_HEADER: Final = "X-FinGuard-Engine"
MODEL_CALLS_HEADER: Final = "X-FinGuard-Model-Calls"
_STREAM_ROUTE: Final = "/v1/questions/stream"
_QUESTION_ROUTES: Final = frozenset({"/v1/questions", _STREAM_ROUTE})
# A payment the rules route set aside to answer a question: the fields that hold it, echoed back
# unchanged by an engine answer so the draft survives the turns the engine takes.
_SET_ASIDE: Final = ("workflow", "payment", "candidates", "awaiting", "suspended")
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

    A version other than the held one raises `StaleDialogueError` out of here, before anything
    ran; `register` writes the 409."""
    echoed = question.conversation
    if engine is None or (echoed is not None and _owned_by_the_rules(echoed)):
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
        return _carrying(echoed, _on_the_wire(unread, held, who, metric_tokens))
    if turn.result is None:
        logger.info("engine.delegated to=%s", turn.delegate_to)
        _answered_by("rules")
        if turn.delegate_to == "CHART":
            _hand_over_the_screen(question, turn.state)
        return None
    state = turn.state
    held = Conversation(conversation_id=state.conversation_id, version=state.version)
    return _carrying(echoed, _on_the_wire(turn.result, held, who, metric_tokens))


def _carrying(echoed: ConversationPayload | None, answer: QuestionResponse) -> QuestionResponse:
    """The engine's answer, holding what it must hand back untouched from the echoed conversation:
    a payment the rules route set aside (only that workflow reaches the engine), and the sealed
    offers of a gym search while the conversation is still the one they were sealed for (M-3)."""
    if echoed is None or answer.conversation is None:
        return answer
    carried: dict[str, object] = {}
    if echoed.workflow is not None:
        carried.update({field: getattr(echoed, field) for field in _SET_ASIDE})
    if (
        echoed.activity_offers is not None
        and answer.conversation.conversation_id == echoed.conversation_id
    ):
        carried["activity_offers"] = echoed.activity_offers
    if not carried:
        return answer
    conversation = answer.conversation.model_copy(update=carried)
    return answer.model_copy(update={"conversation": conversation})


def _hand_over_the_screen(question: QuestionRequest, state: DialogueState) -> None:
    """A chart is the rules route's to draw, of what is on screen (ruling R28), and what is on
    screen is the engine's: its focus goes into the conversation that route is about to read, as the
    plan or the pair it answered last, read from the same accounts the engine read it from. The
    rules route's chart code is not touched; only the context it is handed is."""
    focus, accounts = state.focus, state.scope.account_ids
    # The engine delegates chart rendering before changing its state. The rules route needs the
    # financial question that produced this focus, plus an explicit chart request, to execute the
    # existing bank query and renderer. Forwarding only the follow-up ("¿me lo enseñas?") makes
    # the legacy route plan it as a new unsupported question; forwarding the typed focus below
    # preserves the exact query context it should continue.
    if state.turns:
        source_question = state.turns[-1].said.strip()
        chart_request = f"{source_question} en un gráfico"
        if source_question and len(chart_request) <= MAX_PROMPT_LENGTH:
            question.question = chart_request
    screen: dict[str, object]
    if isinstance(focus, QueryFocus):
        screen = {"last_plan": conversation_mapper.plan_payload(_read_from(focus.plan, accounts))}
    elif isinstance(focus, ComparisonFocus):
        pair = focus.comparison
        screen = {
            "last_comparison_earlier": conversation_mapper.plan_payload(
                _read_from(pair.earlier, accounts)
            ),
            "last_comparison_latest": conversation_mapper.plan_payload(
                _read_from(pair.latest, accounts)
            ),
        }
    else:
        return
    echoed = question.conversation.model_dump() if question.conversation is not None else {}
    question.conversation = ConversationPayload.model_validate({**echoed, **screen})


def _read_from(
    plan: FinancialPlan | TopMovements, accounts: tuple[UUID, ...]
) -> FinancialPlan | TopMovements:
    """`plan` as the engine executed it: from the accounts selected, or every one when none is."""
    return replace(
        plan, across_accounts=not accounts, only_account_id=None, only_account_ids=accounts
    )


def _owned_by_the_rules(echoed: ConversationPayload) -> bool:
    """Whether the turn belongs to a workflow the rules route opened (ruling R26)."""
    set_aside = echoed.workflow is Workflow.PAYMENT_IN_PROGRESS and echoed.suspended
    return echoed.workflow is not None and not set_aside


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
                        # The rules route streams its planner's calls after this message leaves:
                        # no count there rather than a partial one (M-5).
                        if answered.by == "model" or scope["path"] != _STREAM_ROUTE:
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
