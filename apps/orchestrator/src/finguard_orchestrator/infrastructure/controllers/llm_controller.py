"""HTTP surface of the LLM layer.

Routes only: shapes live in dto/, mapping in mappers/, and a controller never touches
the policy store directly.

Every route goes through a bus except the streaming one. A bus is one message in, one
answer out; a stream is a different shape, and widening `ask` to return an async
iterator would make every query pay for the one that streams. That route takes its
handler directly and says so, which is a smaller lie than a bus that sometimes answers
once and sometimes many times.
"""

import json
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from finguard_orchestrator.application import (
    AnswerMessageQuery,
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    GetLlmCatalogueQuery,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ResetLlmPolicyCommand,
    Stage,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    questions_enabled,
    reporting_today,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    CatalogueResponse,
    MessageRequest,
    MessageResponse,
    PolicyRequest,
    QuestionRequest,
    QuestionResponse,
)
from finguard_orchestrator.infrastructure.mappers import llm_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import plan_to_domain

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]
Today = Annotated[date, Depends(reporting_today)]
Asking = Annotated[AskFinancialQuestionHandler, Depends(get_ask_handler)]
Who = Annotated[Caller, Depends(authenticated_caller)]

assistant_router = APIRouter(tags=["assistant"])

# ponytail: these endpoints change how the assistant behaves and today they ask for no
# credentials. Before the service is exposed beyond localhost they have to sit behind
# OAuth with an administration scope (§12 of the vision).
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@assistant_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@assistant_router.get("/ready", summary="Readiness")
async def ready(request: Request, queries: Queries) -> JSONResponse:
    """Ready to answer *financial questions*, which is what this service is for.

    It used to check only that the catalogue's active provider was registered. The
    catalogue chooses who answers /v1/messages; the planner is wired to Ollama and every
    figure comes from banking. With Ollama pointed at a dead port the answer was
    200 ok provider echo while every financial question would have failed — a readiness
    check that reports on a dependency the important endpoint does not use.
    """
    catalogue = await queries.ask(GetLlmCatalogueQuery())
    provider = catalogue.active.model.provider

    checks = {
        "questions_enabled": bool(request.app.state.questions_enabled),
        "catalogue_provider": provider in catalogue.available_providers,
        "planner": await request.app.state.planner_probe.ping(),
        "banking": await request.app.state.banking_probe.ping(),
    }
    body: dict[str, object] = {
        "status": "ok" if all(checks.values()) else "unavailable",
        "provider": provider,
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if all(checks.values()) else 503)


@assistant_router.post("/v1/messages", summary="Answer a message")
async def send_message(body: MessageRequest, queries: Queries) -> MessageResponse:
    """Answers with the active model, falling back if the catalogue says so."""
    return llm_mapper.answer_to_dto(await queries.ask(AnswerMessageQuery(body.prompt)))


@admin_router.get("/llm-policy", summary="Active policy and catalogue")
async def read_policy(queries: Queries) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(await queries.ask(GetLlmCatalogueQuery()))


@admin_router.put("/llm-policy", summary="Swap the active policy")
async def replace_policy(body: PolicyRequest, commands: Commands) -> CatalogueResponse:
    command = ReplaceLlmPolicyCommand(
        model_alias=body.model_alias,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
    )
    return llm_mapper.catalogue_to_dto(await commands.dispatch(command))


@admin_router.post("/llm-policy/reset", summary="Back to the startup policy")
async def reset_policy(commands: Commands) -> CatalogueResponse:
    return llm_mapper.catalogue_to_dto(await commands.dispatch(ResetLlmPolicyCommand()))


def _context_of(request: QuestionRequest) -> FinancialPlan | None:
    """The follow-up context, as a plan.

    Mapped at the edge on purpose: a plan the caller made up is a bad request, and this
    is the layer that knows how to say so. Nothing further in owns a string it has to
    take on faith.
    """
    return plan_to_domain(request.previous_plan) if request.previous_plan else None


@assistant_router.post(
    "/v1/questions",
    summary="Answer a financial question",
    dependencies=[Depends(questions_enabled)],
)
async def ask(
    request: QuestionRequest, who: Who, queries: Queries, today: Today
) -> QuestionResponse:
    """Natural language in, a figure with its evidence out.

    The answer carries the plan that produced it and the movements behind it. That is
    not debug output: a language model chose the question, so an answer nobody can check
    is an answer nobody should trust.

    A question the assistant will not guess at comes back with outcome
    NEEDS_CLARIFICATION and a question of its own, which is a turn in the conversation
    rather than an error.
    """
    who.assert_may_read(who.account_id)
    result = await queries.ask(
        AskFinancialQuestionQuery(
            sentence=request.question,
            account_id=who.account_id,
            today=today,
            previous_plan=_context_of(request),
        )
    )
    return llm_mapper.question_to_dto(result)


@assistant_router.post(
    "/v1/questions/stream",
    summary="Answer a question, stage by stage",
    dependencies=[Depends(questions_enabled)],
)
async def ask_streaming(
    request: QuestionRequest, who: Who, asking: Asking, today: Today
) -> Response:
    """The same answer, emitted as it is produced.

    Not token-by-token: the answer text comes from a template and exists all at once
    the moment the figures land, so streaming its characters would be an animation
    rather than progress. What takes the time is the model deciding what to ask, and
    that is what the stages report.

    The order matters as much as the timing. `planned` arrives before any figure, so a
    plan that misread the question can be seen for what it is while that still helps —
    rather than arriving as a footnote under a number the user has already read.
    """
    question = AskFinancialQuestionQuery(
        sentence=request.question,
        account_id=who.account_id,
        today=today,
        previous_plan=_context_of(request),
    )

    async def events() -> AsyncIterator[str]:
        async for step in asking.stream(question):
            if isinstance(step, Stage):
                payload = {"type": "stage", "name": step.name, "detail": step.detail}
            else:
                payload = {"type": "answer", **llm_mapper.question_to_dto(step).model_dump()}
            # Server-sent events: one JSON object per "data:" line, blank line between.
            body = json.dumps(payload, ensure_ascii=False, default=str)
            yield f"data: {body}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
