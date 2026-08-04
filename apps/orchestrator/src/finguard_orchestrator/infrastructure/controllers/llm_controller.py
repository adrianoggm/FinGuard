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
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from finguard_orchestrator.application import (
    AnswerMessageQuery,
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CategoriseMovementCommand,
    CommandBus,
    GetAccountSummaryQuery,
    GetBreakdownQuery,
    GetCardChargesQuery,
    GetLlmCatalogueQuery,
    GetMovementsQuery,
    ListCardsQuery,
    ListContactsQuery,
    PreparePaymentCommand,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ResetLlmPolicyCommand,
    Stage,
)
from finguard_orchestrator.application.queries.browse_account import MAX_DAYS, MAX_MOVEMENTS
from finguard_orchestrator.bootstrap.dependencies import (
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    known_categories,
    payments_available,
    questions_enabled,
    reporting_today,
    requires,
)
from finguard_orchestrator.domain.enums import GroupBy, PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import payment_router, payment_writer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    AccountSummaryResponse,
    BreakdownResponse,
    CatalogueResponse,
    CategoriesResponse,
    CategoryRequest,
    ChargesResponse,
    ContactsResponse,
    MessageRequest,
    MessageResponse,
    MovementsResponse,
    PaymentDraftPayload,
    PolicyRequest,
    QuestionRequest,
    QuestionResponse,
    SpokenContactResponse,
    WalletResponse,
)
from finguard_orchestrator.infrastructure.mappers import llm_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    draft_to_domain,
    plan_to_domain,
)

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]
Today = Annotated[date, Depends(reporting_today)]
Asking = Annotated[AskFinancialQuestionHandler, Depends(get_ask_handler)]
Who = Annotated[Caller, Depends(requires(Scope.QUESTIONS_READ))]
PaymentsOn = Annotated[bool, Depends(payments_available)]
Categories = Annotated[tuple[str, ...], Depends(known_categories)]

assistant_router = APIRouter(tags=["assistant"])

# These change which model answers every question, for everybody. They now need a scope
# nothing grants by default — a development token gets the two read permissions and not
# this one, so swapping the planner is something somebody was allowed to do rather than
# something anybody on the network can do.
#
# Still not OAuth: the credential is a development token (§12.2 asks for short-lived tokens
# from an issuer). What is real is the seam — the permission is checked, and swapping the
# issuer is a change of adapter.
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(requires(Scope.ASSISTANT_ADMIN))],
)


@assistant_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@assistant_router.get("/version", summary="Which build is answering")
async def version(request: Request) -> dict[str, str]:
    """The commit this image was built from.

    Unauthenticated and deliberately: it is a fact about the deployment, not about anybody's money,
    and the first question when a conversation misbehaves is whether the code being read is the code
    that answered. Reading it out of git at runtime is not an option — a container has no repository
    in it — so the build stamps it in.
    """
    settings = request.app.state.settings
    return {
        "service": "orchestrator",
        "sha": str(settings.build_sha),
        "ref": str(settings.build_ref),
    }


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
        # Reported, deliberately not a check. Payments are off by default, so gating readiness on
        # this would report 503 on a deployment that only answers questions — and a probe that is
        # red on a correctly configured service is a probe somebody switches off. It appears at all
        # because "payments are enabled and the endpoint 404s" is a debugging session, and a flag
        # that reaches nothing observable is a flag nobody can prove arrived.
        "payments": bool(request.app.state.payments_enabled),
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if all(checks.values()) else 503)


@assistant_router.post(
    "/v1/messages",
    summary="Answer a message",
    dependencies=[Depends(requires(Scope.ASSISTANT_USE))],
)
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


async def _as_payment(
    sentence: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    pending: PaymentDraft | None = None,
) -> QuestionResponse:
    """The payment path, reached from the chat.

    The scope is checked here rather than declared on the route, and deliberately: a route-level
    dependency would answer 403 to somebody whose credential cannot pay, and the user did not choose
    their credential. "No puedo hacer pagos" is the honest answer; a 403 is a fact about our
    configuration that they can do nothing with. Same for a deployment with payments switched off —
    503 would read as "try again later" for something that is not coming back.
    """
    if not payments_on or not who.may(Scope.PAYMENTS_PREPARE):
        return QuestionResponse(
            outcome=PlanningOutcome.UNSUPPORTED.value, text=payment_writer.cannot_pay()
        )
    answer = await commands.dispatch(
        PreparePaymentCommand(
            sentence=sentence,
            account_id=who.account_id,
            idempotency_key=str(uuid.uuid4()),
            pending=pending,
        )
    )
    return QuestionResponse(
        outcome=answer.outcome,
        text=answer.text,
        intent_id=str(answer.intent_id) if answer.intent_id else None,
        payment_draft=(
            PaymentDraftPayload(
                recipient_name=answer.draft.recipient_name,
                amount_minor=answer.draft.amount_minor,
                currency=answer.draft.currency,
                concept=answer.draft.concept,
            )
            if answer.draft
            else None
        ),
        candidates=[
            SpokenContactResponse(
                contact_id=candidate.contact_id,
                display_name=candidate.display_name,
                phone=candidate.phone,
            )
            for candidate in answer.candidates
        ],
    )


def _payment_events(
    sentence: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    pending: PaymentDraft | None = None,
) -> StreamingResponse:
    """The payment path as server-sent events: one stage, then the answer.

    Two events rather than a streamed payment pipeline, because there is nothing in between worth
    reporting — reading the sentence and asking the bank to hold an intent is one wait, not several.
    What matters is that the stage arrives first: preparing involves a model call and a call to the
    bank, and silence until the preview lands is the problem the stages exist to solve.

    The answer is produced by the same function the non-streaming route uses, so the two cannot
    answer differently. That is the property this whole change is about.
    """

    async def events() -> AsyncIterator[str]:
        yield _sse({"type": "stage", "name": "reading", "detail": None})
        answer = await _as_payment(sentence, who, commands, payments_on, pending)
        yield _sse({"type": "answer", **answer.model_dump()})

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(payload: Mapping[str, object]) -> str:
    """One JSON object per `data:` line, blank line between.

    A Mapping rather than a dict because dict is invariant in its value type: the stage payloads are
    `dict[str, str | None]`, and asking for a dict here would make each caller widen its own.
    """
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _context_of(request: QuestionRequest) -> FinancialPlan | None:
    """The follow-up context, as a plan.

    Mapped at the edge on purpose: a plan the caller made up is a bad request, and this
    is the layer that knows how to say so. Nothing further in owns a string it has to
    take on faith.
    """
    return plan_to_domain(request.previous_plan) if request.previous_plan else None


async def _continues_a_payment(
    request: QuestionRequest, who: Caller, queries: QueryBus
) -> PaymentDraft | None:
    """The payment this sentence continues, or None.

    Checked before the sentence is classified at all, because a conversation with a payment under
    way is a state machine rather than a sequence of unrelated sentences. Without it "15 euros"
    reached the financial planner: money with no payment verb is not a payment.

    The directory is fetched only when a payment is actually pending, which is rare. The prepare
    handler reads it again to resolve the recipient — one extra local call on those turns, and the
    alternative is the controller passing a resolved contact into the command, which would move the
    decision out of the layer that owns it.
    """
    if request.payment_draft is None:
        return None
    drafted = PaymentDraft(
        recipient_name=request.payment_draft.recipient_name,
        amount_minor=request.payment_draft.amount_minor,
        currency=request.payment_draft.currency,
        concept=request.payment_draft.concept,
    )
    if not drafted.missing:
        # Nothing left to fill, so there is nothing to continue: a complete payment has already
        # become a preview waiting for a yes, and that is the confirmation path.
        return None
    directory = await queries.ask(ListContactsQuery(account_id=who.account_id))
    contacts = tuple(
        Contact(uuid.UUID(row.contact_id), row.display_name, row.phone)
        for row in directory.contacts
    )
    if payment_router.continues(request.question, drafted, contacts):
        return drafted
    return None


def _pending_of(request: QuestionRequest) -> FinancialPlanDraft | None:
    """The half-built plan behind the assistant's last question, as a draft. Mapped here for
    the same reason as the plan above: what a caller sends is checked at the edge or nowhere."""
    return draft_to_domain(request.pending_draft) if request.pending_draft else None


@assistant_router.get(
    "/v1/account/summary",
    summary="The account as a screen needs it",
    dependencies=[Depends(questions_enabled)],
)
async def account_summary(who: Who, queries: Queries, today: Today) -> AccountSummaryResponse:
    """The balance and the last movements, with no model in the path.

    A bank app's home screen needs a figure before anybody has asked anything. Getting it by
    asking the assistant "¿cuál es mi saldo?" would put a language model in front of the first
    number a user reads — and would let the home screen and the conversation disagree, which is
    two balances on one phone and the worst thing to have on camera.

    No account in the path: it comes from the credential, like everywhere else here.
    """
    result = await queries.ask(
        GetAccountSummaryQuery(
            account_id=who.account_id, today=today, reporting_timezone=who.reporting_timezone
        )
    )
    return llm_mapper.summary_to_dto(result)


@assistant_router.get(
    "/v1/account/movements",
    summary="The account's movements, optionally narrowed",
    dependencies=[Depends(questions_enabled)],
)
async def account_movements(
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 90,
    limit: Annotated[int, Query(ge=1, le=MAX_MOVEMENTS)] = 50,
    category: Annotated[str | None, Query(max_length=40)] = None,
    merchant: Annotated[str | None, Query(max_length=140)] = None,
) -> MovementsResponse:
    """What the movements screen and its search field read.

    No model: the window, the filter and the limit are all in the request, so a plan built in code
    says everything there is to say. The figures come from the same compiler as the conversation's,
    which is what lets a screen and a sentence agree about the same account.
    """
    result = await queries.ask(
        GetMovementsQuery(
            account_id=who.account_id,
            today=today,
            reporting_timezone=who.reporting_timezone,
            days=days,
            limit=limit,
            category=category,
            merchant=merchant,
        )
    )
    return llm_mapper.movements_to_dto(result)


@assistant_router.get(
    "/v1/account/breakdown",
    summary="Where the money went, grouped",
    dependencies=[Depends(questions_enabled)],
)
async def account_breakdown(
    who: Who,
    queries: Queries,
    today: Today,
    by: GroupBy = GroupBy.CATEGORY,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> BreakdownResponse:
    """The finance screen's figures. `NONE` is refused: a breakdown of nothing is the total again,
    and answering it would put a one-row chart on screen that restates the number above it."""
    if by is GroupBy.NONE:
        raise InvalidValueError("a breakdown needs something to group by")
    result = await queries.ask(
        GetBreakdownQuery(
            account_id=who.account_id,
            today=today,
            reporting_timezone=who.reporting_timezone,
            days=days,
            group_by=by,
        )
    )
    return llm_mapper.breakdown_to_dto(result)


@assistant_router.get(
    "/v1/cards",
    summary="The cards this account holds",
    dependencies=[Depends(questions_enabled)],
)
async def cards(
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> WalletResponse:
    """Each card with what was charged to it in the window, and the date the window starts.

    No model: a card list is a read. The window is rolling and says so — a figure labelled as a
    month that is really thirty days is wrong on the 1st by everything that happened on the 31st.
    """
    result = await queries.ask(
        ListCardsQuery(account_id=who.account_id, since=today - timedelta(days=days))
    )
    return llm_mapper.wallet_to_dto(result)


@assistant_router.get(
    "/v1/cards/{card_id}/charges",
    summary="What was bought with one card",
    dependencies=[Depends(questions_enabled)],
)
async def card_charges(
    card_id: UUID,
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
    limit: Annotated[int, Query(ge=1, le=MAX_MOVEMENTS)] = 50,
) -> ChargesResponse:
    """The movements behind a card's total.

    A screen that states "437,92 € en 10 cargos" and cannot show the ten is asking to be trusted,
    which is the objection this product exists to answer.

    A card that is not this account's comes back with an empty list, because that is what banking
    answers: its view filters on the declared account, so the id selects nothing.
    """
    result = await queries.ask(
        GetCardChargesQuery(
            account_id=who.account_id,
            card_id=card_id,
            since=today - timedelta(days=days),
            limit=limit,
        )
    )
    return llm_mapper.charges_to_dto(result)


@assistant_router.get(
    "/v1/categories",
    summary="The categories a movement can be filed under",
    dependencies=[Depends(questions_enabled)],
)
async def categories(who: Who, known: Categories) -> CategoriesResponse:
    """The closed list, which is the honest answer to "can I invent one": not today.

    It is the same list the decoder is constrained to, so every category a screen offers is one the
    assistant can name back. `who` requires a credential rather than picking the list — the
    vocabulary is the deployment's, not the holder's.
    """
    return CategoriesResponse(categories=list(known))


@assistant_router.put(
    "/v1/movements/{transaction_id}/category",
    status_code=204,
    summary="File a movement under a category, or let the bank decide",
    dependencies=[Depends(questions_enabled), Depends(requires(Scope.MOVEMENTS_CATEGORISE))],
)
async def categorise(
    transaction_id: UUID, body: CategoryRequest, who: Who, commands: Commands
) -> Response:
    """The holder's own category, stored beside the bank's rather than over it.

    A `null` clears it and falls back to what the bank inferred. Nothing here decides whether the
    category exists: banking owns the movements and therefore the vocabulary, and a value it does
    not use comes back as a rejected request.
    """
    await commands.dispatch(
        CategoriseMovementCommand(
            account_id=who.account_id, transaction_id=transaction_id, category=body.category
        )
    )
    return Response(status_code=204)


@assistant_router.get("/v1/contacts", summary="Everybody this account can pay")
async def contacts(who: Who, queries: Queries) -> ContactsResponse:
    """The list a payment form needs.

    Behind `questions:read` rather than a payment scope: seeing who you could pay is not paying,
    and a screen that lists contacts is a screen rather than an operation.
    """
    return llm_mapper.contacts_to_dto(await queries.ask(ListContactsQuery(who.account_id)))


@assistant_router.post(
    "/v1/questions",
    summary="Answer a financial question",
    dependencies=[Depends(questions_enabled)],
)
async def ask(
    request: QuestionRequest,
    who: Who,
    queries: Queries,
    today: Today,
    commands: Commands,
    payments_on: PaymentsOn,
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

    continuing = await _continues_a_payment(request, who, queries)
    if continuing is not None:
        # A payment under way takes the reply that answers its question, before anything is
        # classified. "15 euros" is an amount because something asked for one.
        return await _as_payment(request.question, who, commands, payments_on, continuing)

    if payment_router.is_payment(request.question):
        # One chat box, two paths, and the decision is deterministic — a client cannot make it,
        # because making it is what this router is for.
        return await _as_payment(request.question, who, commands, payments_on)

    result = await queries.ask(
        AskFinancialQuestionQuery(
            sentence=request.question,
            account_id=who.account_id,
            today=today,
            previous_plan=_context_of(request),
            pending_draft=_pending_of(request),
            previous_refusal=request.previous_refusal,
        )
    )
    return llm_mapper.question_to_dto(result)


@assistant_router.post(
    "/v1/questions/stream",
    summary="Answer a question, stage by stage",
    dependencies=[Depends(questions_enabled)],
)
async def ask_streaming(
    request: QuestionRequest,
    who: Who,
    asking: Asking,
    today: Today,
    commands: Commands,
    payments_on: PaymentsOn,
    # For the contact directory, which is what resolves a recipient when a payment is being
    # continued. This route takes its own handler rather than the bus for the answer itself; the
    # directory is an ordinary query and goes through the bus like everything else.
    queries: Queries,
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
    continuing = await _continues_a_payment(request, who, queries)
    if continuing is not None:
        # Here as well as on the other route, and for the reason written below: the web client types
        # into *this* endpoint, so a payment that could only be continued on the other one was a
        # payment that could not be continued at all. The first version of this change wired the
        # routing into `/v1/questions` alone and the browser conversation still broke.
        return _payment_events(request.question, who, commands, payments_on, continuing)

    if payment_router.is_payment(request.question):
        # Routed here too, and not only on the other route. A demo that shows stages on screen uses
        # the stream, so leaving this out made the endpoint the user actually types into the one
        # that could not pay — the same sentence answering differently depending on which endpoint
        # a client happened to call.
        return _payment_events(request.question, who, commands, payments_on)

    question = AskFinancialQuestionQuery(
        sentence=request.question,
        account_id=who.account_id,
        today=today,
        previous_plan=_context_of(request),
        pending_draft=_pending_of(request),
        previous_refusal=request.previous_refusal,
    )

    async def events() -> AsyncIterator[str]:
        async for step in asking.stream(question):
            if isinstance(step, Stage):
                payload = {"type": "stage", "name": step.name, "detail": step.detail}
            else:
                payload = {"type": "answer", **llm_mapper.question_to_dto(step).model_dump()}
            yield _sse(payload)

    return StreamingResponse(events(), media_type="text/event-stream")
