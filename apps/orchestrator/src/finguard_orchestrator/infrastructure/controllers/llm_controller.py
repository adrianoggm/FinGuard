"""HTTP surface of the LLM layer.

Routes only: shapes live in dto/, mapping in mappers/, and a controller never touches
the policy store directly.

Every route goes through a bus except the streaming one. A bus is one message in, one
answer out; a stream is a different shape, and widening `ask` to return an async
iterator would make every query pay for the one that streams. That route takes its
handler directly and says so, which is a smaller lie than a bus that sometimes answers
once and sometimes many times.
"""

import functools
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from finguard_orchestrator.application import (
    AnswerMessageQuery,
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    CategoriseMovementCommand,
    CommandBus,
    ConfirmPaymentCommand,
    GetAccountSummaryQuery,
    GetBreakdownQuery,
    GetCardChargesQuery,
    GetLlmCatalogueQuery,
    GetMovementsQuery,
    ListCardsQuery,
    ListContactsQuery,
    PaymentAnswer,
    PrepareOwnTransferCommand,
    PreparePaymentCommand,
    QueryBus,
    ReplaceLlmPolicyCommand,
    ResetLlmPolicyCommand,
    Stage,
)
from finguard_orchestrator.application.queries.ask_financial_question import (
    record_context_untrusted,
    record_visual_decision,
    record_visual_decision_refused,
)
from finguard_orchestrator.application.queries.browse_account import MAX_DAYS, MAX_MOVEMENTS
from finguard_orchestrator.application.queries.readers.visual_follow_up import (
    classify_claimed_visual,
    record_visual_follow_up,
)
from finguard_orchestrator.bootstrap.dependencies import (
    attempt_key,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    known_categories,
    metrics_available,
    payments_available,
    questions_enabled,
    reporting_today,
    requires,
    turn_interpreter,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    PaymentOutcome,
    PlanningOutcome,
    Rail,
    Spoken,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    ContextTrust,
    PresentationContext,
    VisualContext,
)
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    ActiveWorkflow,
    Conversation,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    slot_for,
)
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.models.visual_follow_up import Claimant
from finguard_orchestrator.domain.models.visual_plan import (
    Rejection,
    RejectionRule,
    canonical,
    draft_of,
)
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import (
    confirmation,
    intent_router,
    payment_router,
    payment_words,
    payment_writer,
    turn_analyzer,
    window_words,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.visual_explanation import (
    WhyAsked,
    asks_why,
    explain_chart,
)
from finguard_orchestrator.domain.services.visual_interpreter import (
    VisualIntent,
    apply_visual_intent,
    interpret_visual,
    resolve_target,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import (
    _COMPARISON_LIMIT_REASON,
    verify,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
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
    PolicyRequest,
    QuestionRequest,
    QuestionResponse,
    SaveCandidate,
    VisualizationDerivation,
    WalletResponse,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.metrics.codec import CodecError
from finguard_orchestrator.infrastructure.security.metric_tokens import (
    MetricTokenCodec,
    MetricTokenError,
)
from finguard_orchestrator.infrastructure.visualization.vega_lite import present_spec

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]
Today = Annotated[date, Depends(reporting_today)]
Asking = Annotated[AskFinancialQuestionHandler, Depends(get_ask_handler)]
Who = Annotated[Caller, Depends(requires(Scope.QUESTIONS_READ))]
PaymentsOn = Annotated[bool, Depends(payments_available)]
# The caller's name for this attempt. The chat prepares payments too, so a resent question that
# happened to be "hazle un bizum de 20 a Marta" must not leave two intents. See `attempt_key`.
Attempt = Annotated[str, Depends(attempt_key)]
# How a turn is read. Asked once per turn, at the top of the route, and carried from there — the
# three decisions below used to ask separately, which was three chances to disagree while it was
# free and would be three model calls once it is not.
Reads = Annotated[TurnInterpreterPort, Depends(turn_interpreter)]
Categories = Annotated[tuple[str, ...], Depends(known_categories)]


def _metric_token_codec(request: Request) -> MetricTokenCodec:
    codec = request.app.state.metric_tokens
    if not isinstance(codec, MetricTokenCodec):
        raise RuntimeError("invalid metrics token codec")
    return codec


MetricTokens = Annotated[MetricTokenCodec, Depends(_metric_token_codec)]
MetricsOn = Annotated[bool, Depends(metrics_available)]


def _saved_visual_source(
    request: QuestionRequest,
    intent: VisualIntent,
    conversation: Conversation,
    who: Caller,
    metric_tokens: MetricTokenCodec,
    metrics_on: bool,
) -> SavedMetricDefinition | None:
    if not metrics_on or request.channel != "TEXT" or intent.target is None:
        return None
    offered = next(
        (item for item in request.metric_sources if item.chart_id == intent.target),
        None,
    )
    context = conversation.visual_context(intent.target)
    if offered is None or context is None:
        return None
    # Task 3: a valid metric token says nothing about whether the *chart* it would restyle is
    # still one this holder can vouch for. An untrusted context refuses a save-candidate restyle
    # the same way it refuses a plain one -- refuse, never repair.
    if not context.trusted:
        return None
    # I3 (fix wave): the token also has to name this chart. `intent.target` is never `None` here
    # (checked above), so a token minted before this change -- one that carries no chart at all --
    # can no longer be used as a source: it is the holder's own token, over the holder's own
    # chart, but pairing it with *this* chart would sign a saved metric that draws that token's
    # plan over a profile nobody verified for it. Refuse, never repair.
    try:
        definition = metric_tokens.verify(
            offered.definition_token, who.holder_reference, chart_id=intent.target
        )
    except (MetricTokenError, CodecError):
        return None
    # A metric saved as a figure has no chart, so there is nothing here for a restyle to trust.
    if definition.chart is None:
        return None
    if not definition.chart.fields() <= context.profile.field_names():
        return None
    return definition


def _derived_context(
    trusted: VisualContext,
    spec: ChartSpec,
    chart_id: str,
    *,
    decision: VisualDecision | None = None,
) -> VisualContext:
    """The restyled chart's own context: a new id and turn, the source's profile and provenance.

    A named, pure function rather than an inline construction, so the copy of `source_plan` and
    `evidence_ids` from `trusted` -- Task 4's own addition -- can be driven directly with an
    in-process `VisualContext` that still carries them, instead of only through the endpoint, where
    every `trusted_context` reaching this point already came from a wire round trip that emptied
    both fields before the copy ever ran.

    `decision` defaults to `None` -- a merged patch the verifier refuses never reaches this
    function at all, and every other caller before Task 3 had no decision to pass either -- and is
    turn-local like `VisualContext.decision` itself: a verified patch's own `VisualDecision`, when
    there is one, so a restyle is a `patch` source the same way a fresh chart is
    `llm`/`deterministic`.

    `derived_from=trusted.chart_id` names the chart on screen, not the chart `spec` came from
    (M12): on a saved-metric restyle `trusted` is `replace(context, spec=source.chart)`, so
    `trusted.chart_id` is still the conversation's own displayed chart while `spec` began as the
    saved metric's. Correct -- the restyle *is* of the chart on screen -- but read the link as
    "this is a restyle of that chart", never as "this spec derives from that spec".

    No `seal` parameter (A-1, Wave 3 hardening: it moved out from here). A seal covers a context's
    *position*, which this function -- building one context in isolation -- cannot know until its
    caller appends it to the conversation; `_visual_only_response` reseals the whole conversation
    once, after `with_visual_context`, the same way `_remember_visual_context` now does.

    `labels`/`series_labels`/`ranked` are copied from `trusted` too (Task 1, Wave 4.3): a restyle
    shows the same data, so the labels a reader can resolve «agosto» or «la barra más alta»
    against are the source's own, unchanged by the shape the chart is redrawn in.

    `window`/`plan_digest` are copied from `trusted` too (the fix wave): a restyle shows the same
    data of the same plan, so the window it drew and the plan it drew from travel unchanged --
    exactly the reasoning the two lines above already state for the labels.
    """
    context = VisualContext(
        chart_id,
        spec,
        trusted.profile,
        decision=decision,
        source_plan=trusted.source_plan,
        evidence_ids=trusted.evidence_ids,
        derived_from=trusted.chart_id,
        turn_id=uuid.uuid4(),
        labels=trusted.labels,
        series_labels=trusted.series_labels,
        ranked=trusted.ranked,
        window=trusted.window,
        plan_digest=trusted.plan_digest,
        # Task 1 (Wave 4.5): from this restyle's own decision (the patch's, `source="patch"`) --
        # `None` only when there is no decision at all, the same guard `decision` above already
        # needs for the docstring's own reasoning.
        source=decision.source if decision is not None else None,
        fallback_reason=decision.fallback_reason if decision is not None else None,
        # Task 2 (Wave 3 hardening, U-5): copied from `trusted` too, the same reasoning
        # `labels`/`window`/`plan_digest` above already give -- a restyle shows the same data, so
        # its grouping is the source chart's own, unchanged by the shape it is redrawn in.
        group_by=trusted.group_by,
        # Task 2 (Wave 3 hardening, A-2): copied from `trusted` too, for the same reason -- a
        # restyle of a borrowed-plan chart is still a chart whose plan it never drew.
        metric_definition_borrowed=trusted.metric_definition_borrowed,
    )
    return context


# §5's question, for the two AMBIGUOUS pairs the short-circuit's own `classify_claimed_visual`
# can actually return: the interpreter's mixed refusal (a format change and a data change asked
# together) and the drill lexicon matching a sentence `interpret_visual` already claimed (I-1).
# `reshape+drill` cannot reach the short-circuit -- `interpret_visual` never claimed there -- so
# it has no text here; `test_every_ambiguous_claimant_classify_claimed_visual_can_return_has_a_text`
# checks that against the function's own outputs rather than against this dict's keys, so a
# claimant this dict cannot answer fails a test instead of a `KeyError` in production.
_ASK: dict[Claimant, str] = {
    "presentation+reshape": (
        "¿Quieres cambiar sólo la visualización, o volver a calcular los datos?"
    ),
    "presentation+drill": (
        "¿Quieres cambiar sólo la visualización, o ver los movimientos detrás de esa parte "
        "del gráfico?"
    ),
}

# I1 (fix wave): one Spanish clause per `Rejection.rule`, in the controller's own voice, so a
# refusal tells the holder *what about the chart* refused the change rather than handing them the
# verifier's internal name for the rule that caught it -- the rule token stays in the record's
# `detail` (`record_visual_decision_refused`), never in this text. Keyed by `RejectionRule` itself
# rather than by the nine the verifier constructs today, so `merge` (I3's own name for the older
# refusal path, below) reads the same way. `test_every_rejection_rule_has_a_spanish_clause` checks
# this dict's keys against `get_args(RejectionRule)` rather than the other way round, so a rule
# the closed set gains and this dict does not would fail a test instead of `KeyError`ing on a
# holder's own turn.
_REFUSAL_TEXT: dict[RejectionRule, str] = {
    "structure": "el cambio no forma un gráfico completo",
    "fields": "el cambio nombra un campo que este gráfico no tiene",
    "types": "el cambio pone un campo en un papel que no le corresponde",
    "matrix": "ese canal no admite ese campo",
    "mark": "ese tipo de gráfico no encaja con estos datos",
    "donut_claims": "un donut necesita partes de un total verificado",
    "grouped": "una comparación conserva sus series, sin apilarlas",
    "limit": "este gráfico no admite recortar filas",
    "order": "este eje no admite ese orden",
    "merge": "el cambio pedido no encaja con los campos del gráfico",
}


def _refusal_response(
    rejection: Rejection, conversation: Conversation, *, chart_id: str | None = None
) -> QuestionResponse:
    """The one refusal sentence, in the holder's language, whichever path named the rule.

    A-3 (Wave 3 hardening): `rule == "limit"` covers two different claims -- "this chart cannot
    be ranked at all" and "a comparison keeps both its periods" -- and `_REFUSAL_TEXT["limit"]`
    only speaks the first. `_COMPARISON_LIMIT_REASON` is the verifier's own name for the second;
    matched against `rejection.reason` (never re-derived from the spec here) so this dict stays
    the single source for every other rule's clause.

    D-H-1 (from T5, gateway G-6): `chart_id` is the target this refusal is about -- always
    resolved by the time either caller reaches this function -- so the gateway can stamp identity
    onto a turn that drew no chart of its own.
    """
    clause = (
        "una comparación necesita sus dos periodos"
        if rejection.reason == _COMPARISON_LIMIT_REASON
        else _REFUSAL_TEXT[rejection.rule]
    )
    return QuestionResponse(
        outcome="VISUAL_INCOMPATIBLE",
        text=f"No puedo aplicar ese cambio a este gráfico: {clause}; el anterior se mantiene.",
        conversation=conversation_mapper.to_payload(conversation),
        about_chart_id=chart_id,
    )


# A-1 (Wave 3 hardening): two failure populations, two sentences. `"conversation"` and `"position"`
# are a *genuine* seal, minted by this very process for this very holder, that simply names a
# different conversation or a different place in this one -- another conversation's chart replayed
# here, or this conversation's own list reordered, duplicated, or shifted by a dropped neighbour --
# and the honest, specific thing to say is that the chart is not this conversation's. `"holder"`,
# `"content"` and `"format"` are the three shapes where the holder check itself already failed or
# the token cannot be trusted at all (a different holder's seal, a changed payload, a malformed or
# pre-hardening token) -- a holder cannot fix any of those by naming the right conversation, so the
# older, more general tamper sentence stays for them.
_UNTRUSTED_TEXT: dict[ContextTrust, str] = {
    "holder": "No reconozco ese gráfico; pide uno nuevo y podré cambiarlo.",
    "conversation": "Ese gráfico no es de esta conversación; pide uno nuevo y podré cambiarlo.",
    "position": "Ese gráfico no es de esta conversación; pide uno nuevo y podré cambiarlo.",
    "content": "No reconozco ese gráfico; pide uno nuevo y podré cambiarlo.",
    "format": "No reconozco ese gráfico; pide uno nuevo y podré cambiarlo.",
}


def _untrusted_text(context: VisualContext) -> str:
    """The `VISUAL_UNTRUSTED` sentence for `context`, chosen by why its seal failed."""
    return _UNTRUSTED_TEXT[context.trust_reason]


def _explanation_response(why: WhyAsked, conversation: Conversation) -> QuestionResponse:
    """R4/R5/R6: «¿Por qué este gráfico?» answered from the chart itself -- zero ledger calls, zero
    model calls, the conversation unchanged. `context` is resolved exactly as a restyle resolves
    one (`resolve_target`, the same helper `interpret_visual` uses), and the two shapes it can
    miss on -- no such chart, an untrusted echo -- are `_visual_only_response`'s own two shapes,
    verbatim: a holder should not learn two different sentences for the same failure depending on
    whether the turn asked to change the chart or asked why it looks the way it does.
    """
    # `resolve_target` returns `None` here only for "el segundo gráfico" with no second chart --
    # `conversation.visual_contexts` is non-empty (the caller's own gate) so `resolve_target`
    # otherwise always names a real chart_id. `visual_context(None)` means something else
    # entirely (its own default: "the last chart"), so `None` is answered here directly rather
    # than handed to it.
    target = resolve_target(why.target, conversation.visual_contexts)
    context = conversation.visual_context(target) if target is not None else None
    if context is None:
        # M-8 (fix wave): this is the *explanation* path -- a holder asking why a chart looks the
        # way it does, never asking to change one. The restyle path below reuses this same
        # outcome for a different question and keeps its own "quieres cambiar" wording; this text
        # serves both without claiming a change was ever asked for.
        return QuestionResponse(
            outcome="VISUAL_NEEDS_TARGET",
            text="Indica de qué gráfico hablas.",
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=target,  # `target` is None here -- the one shape that misses
        )
    if not context.trusted:
        return QuestionResponse(
            outcome="VISUAL_UNTRUSTED",
            text=_untrusted_text(context),
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=target,
        )
    return QuestionResponse(
        outcome="VISUAL_EXPLAINED",
        text=explain_chart(context).as_text(),
        explanation=llm_mapper.explanation_of(context),
        conversation=conversation_mapper.to_payload(conversation),
        about_chart_id=target,
    )


def _visual_only_response(
    intent: VisualIntent,
    conversation: Conversation,
    *,
    source: SavedMetricDefinition | None = None,
    holder_reference: str | None = None,
    metric_tokens: MetricTokenCodec | None = None,
    seal: Callable[[Conversation], Conversation] | None = None,
) -> QuestionResponse:
    if intent.explanation:
        return QuestionResponse(
            outcome="VISUAL_INCOMPATIBLE",
            text=intent.explanation,
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=intent.target,
        )
    context = conversation.visual_context(intent.target)
    if context is None or intent.patch is None:
        return QuestionResponse(
            outcome="VISUAL_NEEDS_TARGET",
            text="Indica qué gráfico quieres cambiar.",
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=None,  # D-H-1: no chart was resolved -- that is the question itself
        )
    # Task 3: refuse, never repair. An echoed context whose seal did not check out
    # (`conversation_mapper.to_domain`'s own `verify`) is drawn and can still be navigated, but it
    # is not a legitimate target for a restyle -- before `_saved_visual_source`'s own refusal even
    # matters, before any merge or verify, and before a single ledger call.
    if not context.trusted:
        return QuestionResponse(
            outcome="VISUAL_UNTRUSTED",
            text=_untrusted_text(context),
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=context.chart_id,
        )
    # `_saved_visual_source` only ever offers a definition that has a chart; this states it rather
    # than assuming it, so a chart-less metric falls back to the conversation's own context. A
    # `replace` rather than a fresh three-positional `VisualContext` (D-4.1-1): the saved metric
    # replaces only the spec, and the rest of `context` -- its decision, source plan, evidence,
    # provenance -- is provenance a three-positional rebuild would silently drop.
    trusted_context = (
        replace(context, spec=source.chart)
        if source is not None and source.chart is not None
        else context
    )
    try:
        updated = apply_visual_intent(intent, trusted_context)
    except ValueError as exc:
        # I3: this path used to record nothing and name no rule, so the per-rule rejection rate
        # `record_visual_decision_refused` promises was read against the wrong denominator --
        # every refusal `merge_encoding_patch`/`apply_visual_intent` raise before a merged spec
        # ever reaches `verify` below was invisible to it. `"merge"` is `RejectionRule`'s own name
        # for this path; the exception's English (`str(exc)`) goes into the record's `reason`,
        # never into the text the holder reads.
        merge_rejection = Rejection("merge", str(exc))
        record_visual_decision_refused(trusted_context, merge_rejection)
        return _refusal_response(merge_rejection, conversation, chart_id=context.chart_id)
    # The domain verifies the merged chart exactly as it would a model's proposal (`draft_of`
    # names what the merged spec names). It refuses, it never repairs: a refusal here leaves the
    # previous chart untouched and names which rule it broke, and nothing below repairs it either.
    #
    # `verify` is a gate here, nothing more: `draft_of`/`_structure` carry no titles, so what it
    # returns on acceptance is `updated.spec` minus its chart title and every encoding title. What
    # is shown, stored in the derived context, and offered for saving is `updated.spec` itself --
    # the merged chart, titles included -- at every site below; `refusal` is read only for its
    # `Rejection`, never held onto past the `isinstance` check.
    refusal = verify(draft_of(updated.spec), trusted_context.profile)
    if isinstance(refusal, Rejection):
        record_visual_decision_refused(trusted_context, refusal)
        return _refusal_response(refusal, conversation, chart_id=context.chart_id)
    # F2 (fix wave 2): a verified patch is not automatically a *different* chart. `canonical()`
    # is the same equality `changed_from_baseline` already trusts, so a restyle whose merged spec
    # draws identically to what is already on screen answers here, before anything is minted --
    # no new `chart_id`, no derived context, no `patch` decision row (the `VISUAL_FOLLOW_UP` row
    # recorded upstream is this turn's whole audit trail), zero ledger calls.
    #
    # Judged against `context.spec` -- what is actually drawn -- and not `trusted_context.spec`
    # (N1, round 1 re-review). The two differ exactly when a verified saved-metric definition
    # replaces the trusted spec (`trusted_context = replace(context, spec=source.chart)` above):
    # comparing against the *saved* chart there answered "ya está así" for a restyle that was a
    # no-op only relative to a chart nobody was looking at, and silently dropped the save
    # candidate the same request would otherwise have minted. With no saved source,
    # `trusted_context is context` and this is unchanged.
    if canonical(updated.spec) == canonical(context.spec):
        # B-7 (Wave 3 hardening): the chart did not change, so neither do its chips -- the same
        # affordance the holder is already looking at, offered again rather than dropped.
        return QuestionResponse(
            outcome="VISUAL_UNCHANGED",
            text="El gráfico ya está así; lo dejo como está.",
            conversation=conversation_mapper.to_payload(conversation),
            about_chart_id=context.chart_id,
            suggestions=llm_mapper._chart_suggestions(context),
        )
    rendered_spec = present_spec(updated.spec)
    decision = VisualDecision(
        spec=updated.spec,
        profile=trusted_context.profile,
        source="patch",
        accepted=True,
        valid=True,
    )
    # I2 (fix wave): `decision` is turn-local (§4) -- a context that crossed the wire always has
    # `decision is None`, even though a decision produced the chart on screen. `None` says that
    # honestly; `record_visual_decision`'s own docstring is why this is not "deterministic".
    was = trusted_context.decision.source if trusted_context.decision is not None else None
    record_visual_decision(decision, was=was)
    chart_id = "chart-" + uuid.uuid4().hex[:16]
    derived_context = _derived_context(trusted_context, updated.spec, chart_id, decision=decision)
    next_conversation = conversation.with_visual_context(derived_context)
    # A-1 (Wave 3 hardening): sealed here, after the append -- never before it -- because a seal
    # covers the context's position and `with_visual_context` can evict the oldest context once
    # `MAX_VISUAL_CONTEXTS` is reached, shifting every survivor down by one. `seal` (bound to
    # `conversation_mapper.reseal_visual_contexts`) reseals every already-trusted context at its
    # now-final index, this restyle's own new one included, and leaves any untrusted context alone.
    if seal is not None:
        next_conversation = seal(next_conversation)
    candidate = None
    if source is not None and holder_reference is not None and metric_tokens is not None:
        candidate = SaveCandidate(
            chart_id=chart_id,
            definition_token=metric_tokens.issue(
                holder_reference,
                SavedMetricDefinition(source.plan, updated.spec),
                chart_id=chart_id,
            ),
        )
    # `spoken` stays unset (Task 3): a restyle never runs `question_to_dto`, so it never computes
    # one, and a voice client speaks this `text` instead -- the same sentence a screen would show.
    # `explanation` IS set (Task 3, Wave 4.5): the derived context is a chart too, and R4 says
    # every chart explains itself regardless of which path drew it.
    return QuestionResponse(
        outcome="VISUAL_UPDATED",
        text="He creado una nueva versión del gráfico.",
        conversation=conversation_mapper.to_payload(next_conversation),
        visualization_derivation=VisualizationDerivation(
            source_chart_id=context.chart_id,
            chart_id=chart_id,
            spec=rendered_spec,
            limit=updated.spec.limit,
        ),
        save_candidate=candidate,
        explanation=llm_mapper.explanation_of(next_conversation.visual_contexts[-1]),
        # B-7 (Wave 3 hardening): the same `_chart_suggestions` -> `_chip_verifies` filter the
        # first chart's own chips went through (`llm_mapper.question_to_dto`), run over the new
        # chart -- the restyle just used is an affordance too, not one that disappears once taken.
        suggestions=llm_mapper._chart_suggestions(derived_context),
    )


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


def _is_an_order_to_pay(read_as: Intent, sentence: str, was: Conversation) -> bool:
    """Whether this sentence starts a payment, given how the turn was read.

    It used to ask `payment_router.is_payment` directly, which is the *narrower* question: is there
    a payment order in these words. The interpreter adds the one case where the answer is no anyway
    — a sentence asking who can be paid — and that rule was written because "¿qué contactos tengo
    para mandar bizum?" was read as an order and answered by asking who to pay.

    The rule lived only in the handler, and this check runs before the handler is reached. So it
    worked for the phrasings that say "contactos" and not for "¿a quién puedo hacerle un bizum?",
    which is the same question reaching the same wrong answer by the same route.

    The reading is now taken as an argument rather than computed, because two copies of "is this a
    payment" is how the first one came to be incomplete — and because it costs a model call once
    the interpreter is a model.

    An open question takes precedence over a payment word, unless the sentence carries an amount
    **or instructs**. "Pues bizum", answering "¿de qué comercio o categoría?", started a payment and
    asked who to send it to — a word offered as the answer to a question, read as an order. But the
    amount alone was too narrow an escape: reported, a holder wrote "que le hagas el puto bizum" and
    then "quiero que hagas un bizum" with a clarification open, and both reached the planner and
    came back "¿de qué periodo?". A bare noun has no verb; those two are instructions. This stays
    here rather than moving into the interpreter: it is not about what the sentence means, it is
    about which of two open subjects owns the turn.

    **A reading of FINANCIAL_QUERY does not settle it.** That intent is the catch-all — the prompt
    tells the model to answer it whenever unsure — so a deterministic reader requiring a payment
    verb *and* money is the better authority there, and this is the doctrine `explicit_facts` rests
    on: the model decides what is ambiguous, and an order to move money is not. Every other reading
    is a positive choice and is left alone. LIST_CONTACTS for "¿a quién puedo hacerle un bizum?" is
    the case this function was written to protect, and overriding that would undo it.
    """
    if read_as is not Intent.PAYMENT and not (
        read_as is Intent.FINANCIAL_QUERY and payment_router.is_payment(sentence)
    ):
        return False
    asked = was.awaiting or was.last_asked
    if asked not in _FINANCIAL_SLOTS:
        return True
    # An open question keeps only the turn that could be its answer, and that set is closed: a rail
    # noun with an article or a particle around it. Everything else instructs.
    #
    # This used to enumerate the instructions instead, which is the open set — every form missed
    # ("que le hagas", "hagamos") became a turn the question swallowed. Inverting it means a form
    # nobody thought of now falls on the side of being an order, which is the side where the holder
    # gets a preview they can decline rather than a question they did not ask.
    return not payment_words.is_only_a_rail_word(sentence)


# The slots a financial question leaves open. A reply while one of these is waiting belongs to it.
_FINANCIAL_SLOTS: frozenset[Slot | None] = frozenset(
    {Slot.WINDOW, Slot.YEAR, Slot.CATEGORY, Slot.CORRECTION, Slot.ACCOUNT}
)


async def _as_payment(
    sentence: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    was: Conversation = IDLE,
    pending: PaymentDraft | None = None,
    *,
    key: str,
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
            outcome=PlanningOutcome.UNSUPPORTED.value,
            text=payment_writer.cannot_pay(),
            # Nothing was started, so nothing is left open — but the conversation before it is not
            # a casualty of a payment this deployment cannot make.
            conversation=conversation_mapper.to_payload(was),
        )
    answer = await commands.dispatch(
        PreparePaymentCommand(
            sentence=sentence,
            account_id=who.account_id,
            idempotency_key=key,
            pending=pending,
        )
    )
    return QuestionResponse(
        outcome=answer.outcome,
        text=answer.text,
        intent_id=str(answer.intent_id) if answer.intent_id else None,
        # Starting a payment is choosing a different operation. Do not carry an older, invisible
        # failed-query retry through it and let a later bare "reintenta" revive stale context.
        conversation=conversation_mapper.to_payload(
            was.without_retry().with_active(_payment_state(answer))
        ),
    )


def _answers_a_transfer_preview(reply: str) -> bool:
    """Whether this sentence answers a transfer preview: yes or no, and nothing else.

    Deliberately narrower than what a Bizum preview accepts. A Bizum has a revision path because
    re-stating a recipient is tedious and getting one wrong is expensive; a transfer has two fields
    and saying the whole thing again — "mueve 1000 a la hucha" — is one short sentence that already
    produces a new preview. So there is no third case to get wrong here.

    Reusing the Bizum rule was tried and is what the first version did. It read "gracias" and
    "perfecto" as answers, because a concept reader finds a concept in almost any words and the yes
    vocabulary contains the polite ones — so a pleasantry moved money. And it read "pasa 100 € del
    fondo a la hucha" as *changing the recipient* of the preview, which came back "no tengo a Fondo
    indexado en tus contactos": a transfer instruction answered as a failed payment to a person who
    is an account.
    """
    return confirmation.is_confirmation(reply) or confirmation.is_refusal(reply)


async def _abandons_a_transfer_preview(
    reply: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    active: PaymentConfirmation,
) -> bool:
    """Give up an internal-transfer preview the turn is not about, and say whether it was given up.

    The intent is cancelled at the bank rather than merely forgotten here. A held intent nobody
    answered is a thing that expires on its own, and leaving one behind while telling the user about
    their balance would mean the next `sí` — to some later question entirely — could still find it.

    Only when this caller could have confirmed it. Otherwise the preview is left alone and the
    ordinary confirmation path answers "no puedo hacer pagos", which is honest and does not
    quietly discard something staged.
    """
    if active.rail is not Rail.INTERNAL or _answers_a_transfer_preview(reply):
        return False
    if not payments_on or not who.may(Scope.PAYMENTS_CONFIRM):
        return False
    await commands.dispatch(
        ConfirmPaymentCommand(reply="no", account_id=who.account_id, intent_id=active.intent_id)
    )
    return True


async def _as_own_transfer(
    result: AskFinancialQuestionResult,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    *,
    key: str,
) -> QuestionResponse:
    """Turn a fully-read transfer between the holder's own accounts into a preview to confirm.

    The read path resolved which account and how much, and then told the holder to go and say it
    again on a screen. A Bizum asked in the same box comes back as a preview and a yes, and there
    is no reason for a transfer to be the one operation that cannot be finished where it was
    started — the confirmation is what protects it, and the confirmation is right here.

    It keeps the preview and the explicit yes even though the money never leaves the household,
    exactly like the form route: that step guards against a mistake rather than against a
    misreading, and 150 € in the wrong direction between two of your own accounts is a mistake worth
    being asked about.

    The scope is checked here and not on the route, for the reason `_as_payment` checks it here:
    a 403 is a fact about our configuration that the holder can do nothing with. Without it they get
    the sentence naming the screen, which is what they got before this existed.
    """
    if not payments_on or not who.may(Scope.PAYMENTS_PREPARE) or result.transfer_to is None:
        return llm_mapper.question_to_dto(result)
    answer = await commands.dispatch(
        PrepareOwnTransferCommand(
            account_id=who.account_id,
            to_account_id=uuid.UUID(result.transfer_to),
            amount_minor=result.total_minor or 0,
            currency=result.currency or "EUR",
            idempotency_key=key,
        )
    )
    return QuestionResponse(
        outcome=answer.outcome,
        text=answer.text,
        intent_id=str(answer.intent_id) if answer.intent_id else None,
        conversation=conversation_mapper.to_payload(
            result.conversation.with_active(_payment_state(answer))
        ),
    )


def _payment_state(answer: PaymentAnswer) -> ActiveWorkflow | None:
    """Where a payment turn leaves the conversation.

    Three outcomes and three states: an intent the bank is holding is a confirmation waiting for a
    yes; a half-built payment is a payment in progress, with the slot it needs and the choice it can
    offer; anything else — sent, refused, cancelled, not understood — leaves nothing open.

    Deriving it here rather than in the payment handler keeps the command's answer about the payment
    and this about the conversation. The handler already says which of the three happened.
    """
    if (
        answer.intent_id is not None
        and answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
        # All three, not "whatever arrived plus a default". `recipient or ""` and
        # `amount_minor or 0` used to build a confirmation for nobody of nothing, which the wire
        # then refused as a payment of zero — a state that describes no preview and can only be
        # confirmed blind. A preview the handler could not describe leaves nothing open here; the
        # intent still travels on the response for the standalone confirmation endpoint.
        and answer.recipient
        and answer.amount_minor
    ):
        return PaymentConfirmation(
            intent_id=answer.intent_id,
            recipient=answer.recipient,
            amount_minor=answer.amount_minor,
            currency=answer.currency or "EUR",
            concept=answer.concept,
            rail=Rail(answer.rail),
            recipient_iban=answer.recipient_iban,
        )
    if answer.draft is None:
        return None

    drafted = PaymentDraft(
        recipient_name=answer.draft.recipient_name,
        recipient_contact_id=(
            uuid.UUID(answer.draft.recipient_contact_id)
            if answer.draft.recipient_contact_id
            else None
        ),
        amount_minor=answer.draft.amount_minor,
        currency=answer.draft.currency,
        concept=answer.draft.concept,
        rail=Rail(answer.draft.rail),
    )
    candidates = tuple(
        Contact(uuid.UUID(row.contact_id), row.display_name, row.phone) for row in answer.candidates
    )
    return PaymentInProgress(
        draft=drafted,
        # A choice is a different question from a field to type, and the client renders them
        # differently: buttons against a text box.
        awaiting=Slot.RECIPIENT_CHOICE if candidates else slot_for(drafted.missing),
        candidates=candidates,
    )


def _payment_events(
    sentence: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    was: Conversation = IDLE,
    pending: PaymentDraft | None = None,
    *,
    key: str,
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
        answer = await _as_payment(sentence, who, commands, payments_on, was, pending, key=key)
        yield _sse({"type": "answer", **answer.model_dump()})

    return StreamingResponse(events(), media_type="text/event-stream")


async def _as_confirmation(
    reply: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    was: Conversation,
    active: PaymentConfirmation,
    *,
    key: str,
) -> QuestionResponse:
    """Answer the payment preview that is active in this chat.

    The standalone payment endpoint deliberately has no conversation state. The chat does, and the
    response must close that state after a terminal answer; otherwise the client keeps offering the
    same already-consumed intent. An unclear reply is the sole outcome that leaves the preview open.
    """
    if not payments_on or not who.may(Scope.PAYMENTS_CONFIRM):
        return QuestionResponse(
            outcome=PaymentOutcome.UNSUPPORTED.value,
            text=payment_writer.cannot_pay(),
            conversation=conversation_mapper.to_payload(was),
        )

    revises_amount = payment_words.amount_in(reply) is not None
    revises_concept = payment_words.concept_in(reply) is not None or payment_words.clears_concept(
        reply
    )
    revises_recipient = payment_words.changes_recipient(reply)
    if revises_amount or revises_concept or revises_recipient:
        # A correction is not a confirmation and never mutates an intent already held by banking.
        # Cancel that offer, rebuild a draft from the facts the user kept and prepare a new preview.
        # The second explicit yes is mandatory whichever field changed.
        cancelled = await commands.dispatch(
            ConfirmPaymentCommand(
                reply="no",
                account_id=who.account_id,
                intent_id=active.intent_id,
            )
        )
        if cancelled.outcome != PaymentOutcome.CANCELLED.value:
            still_waiting = cancelled.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
            conversation = was if still_waiting else was.with_active(None)
            return QuestionResponse(
                outcome=cancelled.outcome,
                text=cancelled.text,
                intent_id=str(cancelled.intent_id) if cancelled.intent_id else None,
                conversation=conversation_mapper.to_payload(conversation),
                currency=cancelled.currency,
            )
        replacement = PaymentDraft(
            # A named recipient correction reopens this slot. The payment handler resolves the
            # name in the reply against the account's contact directory and exposes ambiguity.
            recipient_name=None if revises_recipient else active.recipient,
            amount_minor=active.amount_minor,
            currency=active.currency,
            # Clearing is an explicit value. A new written concept is merged from `reply` by the
            # same deterministic continuation reader used while a payment is being built.
            concept=None if payment_words.clears_concept(reply) else active.concept,
        )
        revised = await _as_payment(
            reply,
            who,
            commands,
            payments_on,
            was.with_active(None),
            replacement,
            key=key,
        )
        return revised.model_copy(
            update={"text": f"He descartado el Bizum anterior. {revised.text}"}
        )

    answer = await commands.dispatch(
        ConfirmPaymentCommand(
            reply=reply,
            account_id=who.account_id,
            intent_id=active.intent_id,
        )
    )
    # A transport failure during confirmation is indeterminate: banking may have executed it. Keep
    # this same intent available for a safe retry instead of letting the user create a duplicate.
    still_waiting = answer.outcome in {
        PaymentOutcome.AWAITING_CONFIRMATION.value,
        PaymentOutcome.BANKING_UNAVAILABLE.value,
    }
    conversation = (
        was
        if still_waiting
        else (
            was.paid(active.rail)
            if answer.outcome == PaymentOutcome.PAID.value
            else was.with_active(None)
        )
    )
    return QuestionResponse(
        outcome=answer.outcome,
        # The chat state retains the material facts even though the standalone confirmation
        # endpoint has no full preview. Repeat them after an unclear answer: asking somebody to
        # confirm an unnamed payment is neither useful nor a sound confirmation experience.
        text=(
            payment_writer.unclear_pending(active.recipient, active.amount_minor, active.currency)
            if answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
            else answer.text
        ),
        intent_id=str(answer.intent_id) if answer.intent_id else None,
        conversation=conversation_mapper.to_payload(conversation),
        currency=answer.currency,
    )


def _confirmation_events(
    reply: str,
    who: Caller,
    commands: CommandBus,
    payments_on: bool,
    was: Conversation,
    active: PaymentConfirmation,
    *,
    key: str,
) -> StreamingResponse:
    """The streaming shape of a confirmation: one answer, with no invented planning stage."""

    async def events() -> AsyncIterator[str]:
        answer = await _as_confirmation(reply, who, commands, payments_on, was, active, key=key)
        yield _sse({"type": "answer", **answer.model_dump()})

    return StreamingResponse(events(), media_type="text/event-stream")


def _open_payment_reply(
    sentence: str, was: Conversation, active: PaymentInProgress
) -> QuestionResponse | None:
    """Handle replies that control a draft without sending them to a financial planner.

    A draft has not reached banking yet, so abandoning it is only a state transition. Uncertainty
    keeps it open and repeats the missing field with context; everything else continues through the
    ordinary routing rules, including contact questions and genuine changes of subject.
    """
    if confirmation.is_refusal(sentence):
        return QuestionResponse(
            outcome=PaymentOutcome.CANCELLED.value,
            text=payment_writer.cancelled(),
            conversation=conversation_mapper.to_payload(was.with_active(None)),
        )
    if payment_router.is_uncertain(sentence):
        return QuestionResponse(
            outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
            text=payment_writer.unsure(active.draft),
            conversation=conversation_mapper.to_payload(was),
        )
    return None


async def _paused_payment_reply(
    sentence: str,
    was: Conversation,
    active: PaymentInProgress,
    who: Caller,
    queries: QueryBus,
) -> QuestionResponse | None:
    """Stop a slot value from silently reviving a payment after another conversation.

    The same amount or contact would have completed an active draft. Once suspended, it becomes an
    ambiguous turn: it may have been written about the intervening subject. The payment stays intact
    and the answer names the explicit instruction that resumes it.
    """
    if (
        not active.suspended
        or payment_router.resumes(sentence)
        or payment_router.is_payment(sentence)
    ):
        return None
    directory = await queries.ask(ListContactsQuery(account_id=who.account_id))
    contacts = tuple(
        Contact(uuid.UUID(row.contact_id), row.display_name, row.phone)
        for row in directory.contacts
    )
    if not payment_router.continues(sentence, active.draft, contacts):
        return None
    return QuestionResponse(
        outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
        text=payment_writer.paused(active.draft),
        conversation=conversation_mapper.to_payload(was),
    )


def _open_payment_events(answer: QuestionResponse) -> StreamingResponse:
    """The streaming representation of an immediate draft-control answer."""

    async def events() -> AsyncIterator[str]:
        yield _sse({"type": "answer", **answer.model_dump()})

    return StreamingResponse(events(), media_type="text/event-stream")


def _compound_reply(clauses: tuple[str, ...], was: Conversation) -> QuestionResponse:
    """Ask the holder to choose instead of silently executing one half of their turn."""
    return QuestionResponse(
        outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
        text="Has pedido varias cosas. ¿Con cuál empezamos?",
        question="¿Con cuál empezamos?",
        suggestions=list(clauses),
        conversation=conversation_mapper.to_payload(was),
    )


_SAFE_COMPOUND_READS = frozenset({Intent.FINANCIAL_QUERY, Intent.ACCOUNT_BALANCE})
_COMPOUND_SUCCESS = frozenset({PlanningOutcome.PLANNED.value, Spoken.BALANCE.value})


def _compound_read_order(
    clauses: tuple[str, ...], was: Conversation, today: date
) -> tuple[tuple[int, Intent], ...] | None:
    """Safe read-only clauses, with one explicit financial window executed first when needed."""
    if not 2 <= len(clauses) <= 3:
        return None
    intents = tuple(
        intent_router.route(
            clause,
            has_previous=was.last_plan is not None,
            at_rest=was.at_rest,
        )
        for clause in clauses
    )
    if any(intent not in _SAFE_COMPOUND_READS for intent in intents):
        return None

    financial = tuple(
        index for index, intent in enumerate(intents) if intent is Intent.FINANCIAL_QUERY
    )
    explicit = tuple(
        index
        for index in financial
        if window_words.has_complete_window(clauses[index], today=today)
    )
    shareable = tuple(
        index
        for index in financial
        if not window_words.has_compared_windows(clauses[index], today=today)
        and window_words.window_in(clauses[index], today=today) is not None
    )
    if financial and was.last_plan is None and not explicit:
        return None
    if (
        was.last_plan is None
        and any(index not in explicit for index in financial)
        and not shareable
    ):
        # A comparison owns two windows; choosing either as the period of an adjacent abbreviated
        # query would be an invented interpretation.
        return None

    order = list(range(len(clauses)))
    if was.last_plan is None and financial and financial[0] not in explicit:
        seed = shareable[0]
        order.remove(seed)
        order.insert(0, seed)
    return tuple((index, intents[index]) for index in order)


def _window_only_context(answered: Conversation) -> Conversation | None:
    """The shared modifier from a seed clause, without any of that clause's filters."""
    plan = answered.last_plan
    if plan is None:
        return None
    return Conversation(
        last_plan=FinancialPlan(
            direction=Direction.ALL,
            period=plan.period,
            date_range=plan.date_range,
        ),
        account_aliases=answered.account_aliases,
        counterparty_aliases=answered.counterparty_aliases,
        visual_contexts=answered.visual_contexts,
        # Filters are what this strips. The thread is not a filter, and a clause read without it is
        # the blindness the log exists to end.
        turns=answered.turns,
    )


async def _compound_read_reply(
    clauses: tuple[str, ...],
    was: Conversation,
    *,
    who: Caller,
    today: date,
    queries: QueryBus,
    presentation: PresentationContext,
    metric_tokens: MetricTokenCodec,
) -> QuestionResponse | None:
    """Execute bounded read-only clauses and retain a separately auditable answer for each."""
    ordered = _compound_read_order(clauses, was, today)
    if ordered is None:
        return None

    current = was
    shared_window: Conversation | None = None
    parts: list[QuestionResponse | None] = [None] * len(clauses)
    conversations: list[Conversation | None] = [None] * len(clauses)
    visual_contexts_by_index: list[tuple[VisualContext, ...]] = [()] * len(clauses)
    final_outcome = "COMPOUND"
    final_question: str | None = None
    final_suggestions: list[str] = []
    for index, intent in ordered:
        has_explicit_window = intent is Intent.FINANCIAL_QUERY and window_words.has_complete_window(
            clauses[index], today=today
        )
        if intent is Intent.ACCOUNT_BALANCE:
            # A balance is an aside. It uses the accumulated conversation so a relationship
            # answered immediately before it remains available to the next follow-up.
            clause_context = current
        else:
            clause_context = was if has_explicit_window or shared_window is None else shared_window
        result = await queries.ask(
            AskFinancialQuestionQuery(
                sentence=clauses[index],
                account_id=who.account_id,
                today=today,
                reporting_timezone=who.reporting_timezone,
                presentation=presentation,
                conversation=clause_context,
                intent=intent,
            )
        )
        current = result.conversation
        conversations[index] = current
        # Fix round 2 (Task 3, C1): a compound clause draws a chart the same as any other turn,
        # and it needs the same seal or its own restyle refuses it the very next turn --
        # `offer_saving` stays `False`, since a compound part has never offered a save candidate.
        rendered = llm_mapper.question_to_dto(
            result, holder_reference=who.holder_reference, metric_tokens=metric_tokens
        )
        if rendered.conversation is not None:
            # A-1 (Wave 3 hardening): `verify=` now real, where I2 (above this edit) argued none
            # was needed. That argument held only as long as nothing here ever re-signed a
            # context -- `to_domain`'s default relaunders every context to `trusted=True`, but a
            # context that came in untrusted was also tokenless, and I2's own `reseal_visual_
            # contexts` did not exist yet to mint it a fresh one. It exists now (this function's
            # own final merge calls it, below), and it reseals whatever `trusted` says here --
            # so an unguarded relaunder would let a tampered context carried into a compound turn
            # walk out with a brand-new, genuinely valid seal. Verifying for real keeps a
            # relaundered-but-tokenless context `trusted=False` exactly as `_conversation_of`
            # already found it, while a context this very clause just minted (a real token,
            # sealed two lines above by `question_to_dto`'s own `seal`) verifies `"ok"` and stays
            # trusted -- both for the reason that actually holds now, not the one that used to.
            rendered_conversation = conversation_mapper.to_domain(
                rendered.conversation,
                verify=lambda token, conversation_id, position, semantics: (
                    metric_tokens.verify_context(
                        token, who.holder_reference, conversation_id, position, semantics
                    )
                ),
            )
            previous_visual_ids = {
                context.chart_id for context in result.conversation.visual_contexts
            }
            current = replace(current, visual_contexts=rendered_conversation.visual_contexts)
            conversations[index] = current
            visual_contexts_by_index[index] = tuple(
                context
                for context in rendered_conversation.visual_contexts
                if context.chart_id not in previous_visual_ids
            )
        parts[index] = rendered.model_copy(update={"conversation": None, "parts": []})
        if rendered.outcome not in _COMPOUND_SUCCESS:
            final_outcome = rendered.outcome
            final_question = rendered.question
            final_suggestions = rendered.suggestions
            break
        if has_explicit_window and shared_window is None:
            shared_window = _window_only_context(current)

    completed_with_indexes = tuple(
        (index, part) for index, part in enumerate(parts, start=1) if part is not None
    )
    completed = [part for _, part in completed_with_indexes]
    completed_conversations = tuple(
        (index, conversation)
        for index, conversation in enumerate(conversations)
        if conversation is not None
    )
    if completed_conversations:
        # Conversation follows the clause the user mentioned last, not the internal execution
        # order (which may have moved a trailing shared window to the front).
        current = completed_conversations[-1][1]
    merged = current
    for index in range(len(clauses)):
        for context in visual_contexts_by_index[index]:
            merged = merged.with_visual_context(context)
    # A-1 (Wave 3 hardening): each clause's own chart was sealed against that clause's own,
    # smaller conversation (`question_to_dto` inside the loop above) -- a position in a list that
    # is not this compound answer's final, merged one. Resealing once here, the same tool
    # `question_to_dto` and a restyle both reseal with, is what makes the very next turn's restyle
    # of a compound chart verify at all, rather than refusing every one of them as a position
    # mismatch nobody replayed or reordered.
    merged = conversation_mapper.reseal_visual_contexts(merged, who.holder_reference, metric_tokens)
    current = merged
    text = "\n".join(f"{index}. {part.text}" for index, part in completed_with_indexes)
    return QuestionResponse(
        outcome=final_outcome,
        text=text,
        question=final_question,
        conversation=conversation_mapper.to_payload(current),
        suggestions=final_suggestions,
        parts=completed,
    )


def _sse(payload: Mapping[str, object]) -> str:
    """One JSON object per `data:` line, blank line between.

    A Mapping rather than a dict because dict is invariant in its value type: the stage payloads are
    `dict[str, str | None]`, and asking for a dict here would make each caller widen its own.
    """
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _conversation_of(
    request: QuestionRequest, who: Caller, metric_tokens: MetricTokenCodec
) -> Conversation:
    """Where the conversation was, as a conversation.

    Mapped at the edge on purpose: a state the caller made up is a bad request, and this is the
    layer that knows how to say so. Nothing further in owns a value it has to take on faith — the
    handler receives typed states whose invariants have already run.

    Task 3: the one production call of `to_domain` bound to `verify=...` -- every echoed visual
    context is checked against `who.holder_reference` and this process's own `metric_tokens` codec
    right here, at the one HTTP boundary a caller's own claim about the conversation crosses. A
    context whose seal does not check out comes back `trusted=False` (`to_domain`'s own default
    otherwise), and everything downstream refuses it as a semantic input rather than repairing or
    silently dropping it.

    A-1 (Wave 3 hardening): the closure now also binds each context's conversation id and its
    index in `request.conversation`'s own echoed list -- `to_domain` computes the id once
    (`_identified`) and threads it through to every context it verifies, so a context replayed
    from a different conversation, reordered, duplicated, or shifted by a dropped neighbour fails
    here on the fact that actually changed, not on content alone. Every context `to_domain` marks
    untrusted is logged once, by reason, before this function returns -- `record_context_untrusted`
    reads `trust_reason` off each one, which is exactly why verification happens here rather than
    lazily, the first time some later branch happens to read `trusted`.
    """
    conversation = conversation_mapper.to_domain(
        request.conversation,
        verify=lambda token, conversation_id, position, semantics: metric_tokens.verify_context(
            token, who.holder_reference, conversation_id, position, semantics
        ),
    )
    for context in conversation.visual_contexts:
        if not context.trusted:
            record_context_untrusted(context)
    return conversation


def _presentation_of(request: QuestionRequest) -> PresentationContext:
    """Translate the validated HTTP circumstances into the application value object once."""
    return PresentationContext(
        channel=request.channel,
        viewport=request.viewport,
        max_visible_rows=request.max_visible_rows,
    )


async def _continues_a_payment(
    read_as: Intent, was: Conversation, question: str, who: Caller, queries: QueryBus
) -> PaymentDraft | None:
    """The payment this sentence continues, or None.

    Checked before the sentence's *meaning* decides anything, because a conversation with a payment
    under way is a state machine rather than a sequence of unrelated sentences. Without it "15
    euros" reached the financial planner: money with no payment verb is not a payment.

    The directory is fetched only when a payment is actually under way, which is rare. The prepare
    handler reads it again to resolve the recipient — one extra local call on those turns, and the
    alternative is the controller passing a resolved contact into the command, which would move the
    decision out of the layer that owns it.
    """
    active = was.active
    if not isinstance(active, PaymentInProgress):
        return None
    if read_as is Intent.OWN_TRANSFER:
        # An instruction to move money between the holder's own accounts, not a reply to the Bizum.
        # "Mueve 200 euros a la hucha" carries a figure, and a half-built payment missing its amount
        # took it — so a sentence naming one of the holder's own accounts completed a payment to
        # somebody else. Both halves of a transfer have to be present for the router to say so, and
        # neither of them belongs to a Bizum.
        return None
    drafted = active.draft
    if active.suspended:
        # A value for the missing slot is intercepted above and gets a reminder. Only an explicit
        # resumption reaches preparation, carrying the old draft so none of its settled fields
        # vanish.
        return drafted if payment_router.resumes(question) else None
    if drafted.recipient_contact_id is not None and drafted.recipient_name is None:
        # A contact the user tapped and this service has not applied yet. It fills the recipient
        # slot, so the draft below looks complete — and "complete means a preview already exists"
        # sent the tap to the financial planner, which answered a button with "eso no puedo
        # calcularlo todavía". The preview is exactly what this turn is asking for.
        return drafted
    if not drafted.missing:
        # Nothing left to fill, so there is nothing to continue: a complete payment has already
        # become a preview waiting for a yes, and that is the confirmation path.
        return None
    directory = await queries.ask(ListContactsQuery(account_id=who.account_id))
    contacts = tuple(
        Contact(uuid.UUID(row.contact_id), row.display_name, row.phone)
        for row in directory.contacts
    )
    if payment_router.continues(question, drafted, contacts):
        return drafted
    return None


async def _repeats_a_recent_payment(
    was: Conversation, question: str, who: Caller, queries: QueryBus
) -> PaymentDraft | None:
    """Build the empty draft an immediate ``otro`` fills, after grounding its contact."""
    if was.recent_payment_rail is None:
        return None
    directory = await queries.ask(ListContactsQuery(account_id=who.account_id))
    contacts = tuple(
        Contact(uuid.UUID(row.contact_id), row.display_name, row.phone)
        for row in directory.contacts
    )
    if not payment_router.repeats(question, contacts):
        return None
    return PaymentDraft(rail=was.recent_payment_rail)


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
    include_internal: Annotated[bool, Query()] = False,
    account: Annotated[UUID | None, Query()] = None,
    across_accounts: Annotated[bool, Query()] = False,
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
            include_internal=include_internal,
            only_account_id=account,
            across_accounts=across_accounts,
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

    `days` counts days with both ends inside, so a week is `since` six days back and not seven. It
    subtracted the whole count and left the caller's own day in the window as well, which made every
    span one longer than its label; banking had the same arithmetic and the two agreed with each
    other while both disagreed with what they said.
    """
    result = await queries.ask(
        ListCardsQuery(
            account_id=who.account_id,
            since=today - timedelta(days=days - 1),
            today=today,
            reporting_timezone=who.reporting_timezone,
        )
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
            since=today - timedelta(days=days - 1),
            today=today,
            reporting_timezone=who.reporting_timezone,
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
    attempt: Attempt,
    reads: Reads,
    metric_tokens: MetricTokens,
    metrics_on: MetricsOn,
) -> QuestionResponse:
    """Natural language in, a figure with its evidence out.

    The answer carries the plan that produced it and the movements behind it. That is
    not debug output: a language model chose the question, so an answer nobody can check
    is an answer nobody should trust.

    A question the assistant will not guess at comes back with outcome
    NEEDS_CLARIFICATION and a question of its own, which is a turn in the conversation
    rather than an error.

    There is no authorisation check here, and its absence is deliberate. This path has one
    account -- the credential's -- because `QuestionRequest` has no account field and forbids
    extras, so there is no second account to compare it against. The line that used to stand
    here read `who.assert_may_read(who.account_id)`: a value compared with itself, which
    cannot fail under any implementation of `may_read`. `Caller.may_read`'s own docstring
    names that failure mode -- "an unused authorisation check is worse than none: it reads
    like a control while enforcing nothing" -- so the inert line is gone rather than kept as
    reassurance. What replaces it is a test on the thing that actually holds the guarantee:
    `test_the_question_path_has_no_account_to_authorise` fails the day the request grows a
    field that could carry one.
    """
    key = attempt
    was = _conversation_of(request, who, metric_tokens)
    # Task 3: built once per request, unconditionally -- restyling a chart is not a metrics
    # feature, so a derived context is sealed for its holder the same turn it is minted regardless
    # of whether saved metrics are enabled.
    seal = functools.partial(
        conversation_mapper.reseal_visual_contexts,
        holder_reference=who.holder_reference,
        codec=metric_tokens,
    )
    presentation = _presentation_of(request)

    # m8: targeting reads every echoed context, trusted or not -- «el gráfico» resolves against
    # `was.visual_contexts` as a whole, not the trusted subset, because the newest one is what a
    # holder means by it and the gate below has not run yet. A client that appends a forged
    # context as the newest chart makes «el gráfico» resolve onto the forgery instead of the
    # holder's real one, and the turn then refuses at the trust gate -- self-inflicted (a client
    # can only harm its own conversation this way) and the safe direction: refusing beats
    # silently resolving onto whichever context happens to still be trusted.
    # R5: claimed before `interpret_visual` -- «¿por qué este gráfico?» is a question about the
    # chart, not a request to change it, and the two grammars do not overlap (`asks_why` excludes
    # every `_FINANCIAL`/`_VISUAL` sentence `interpret_visual` would otherwise read). Gated on
    # there being a chart at all: with nothing charted the recogniser has nothing to explain and
    # this falls through unchanged, to whatever answered the sentence before this recogniser
    # existed -- not an oversight, R5's own rule for an empty conversation.
    why = asks_why(request.question)
    if why is not None and was.visual_contexts:
        # `classify_claimed_visual`/`record_visual_follow_up`: no `VisualFollowUpKind` fits an
        # explanation (`PRESENTATION_ONLY`, `ANALYTICAL_RESHAPE`, `DRILL_DOWN`, `UNRELATED`,
        # `AMBIGUOUS`) -- it neither changes the chart nor asks about one part of it -- and adding
        # one is a domain vocabulary change Task 5/6 would have to chase through every ratchet.
        # Nothing is recorded here; the outcome itself (`VISUAL_EXPLAINED`) is the record.
        return _explanation_response(why, was)

    if isinstance(was.active, PaymentConfirmation):
        # A preview owns the next turn, whatever its words are. In particular, "sí pero 30" is
        # not a new instruction: the deterministic confirmation reader leaves the preview open.
        #
        # A transfer between the holder's own accounts is the exception, and it is the ordinary case
        # that made it one: move money, then look at the balance. Stubbornness there answered
        # "¿cuánto tengo en la hucha?" with the pending transfer, and every turn after it too.
        previewed = was.active
        if await _abandons_a_transfer_preview(
            request.question, who, commands, payments_on, previewed
        ):
            was = was.with_active(None)
        else:
            return await _as_confirmation(
                request.question, who, commands, payments_on, was, previewed, key=key
            )

    if isinstance(was.active, PaymentInProgress):
        paused = await _paused_payment_reply(request.question, was, was.active, who, queries)
        if paused is not None:
            return paused
        immediate = _open_payment_reply(request.question, was, was.active)
        if immediate is not None:
            return immediate

    # C-1/C-6/C-3 (Wave 3 hardening): moved below the two payment branches above -- a preview or an
    # open payment owns its own reply, whatever words it contains, and a restyle answering it left
    # the preview open with nothing the holder said ever reaching `_as_confirmation`/the paused-
    # payment readers. Gated on `was.visual_contexts` the same way `asks_why` already is (`if
    # visual is not None and was.visual_contexts:` -- not just `if visual is not None:`):
    # `interpret_visual` can return a non-`None` intent with no chart at all (the mixed-turn
    # refusal fires on financial + style words alone, and `_VISUAL_ALL`/`show_every_row` fires on
    # a bare "todos"), and both used to be answered `VISUAL_AMBIGUOUS`/`VISUAL_INCOMPATIBLE` -- a
    # question about, or a refusal of, a chart the holder never mentioned -- instead of falling
    # through to the planner or the clarification the sentence was actually answering.
    visual = interpret_visual(request.question, was.visual_contexts)
    if visual is not None and was.visual_contexts:
        follow_up = classify_claimed_visual(visual, request.question)
        record_visual_follow_up(follow_up)
        if follow_up.kind == "AMBIGUOUS":
            # §5's question, not a guess: two readers agree on this turn and only the holder
            # knows which one they meant, so nothing is merged and no ledger is ever asked.
            return QuestionResponse(
                outcome="VISUAL_AMBIGUOUS",
                text=_ASK[follow_up.claimant],
                conversation=conversation_mapper.to_payload(was),
                about_chart_id=visual.target,
            )
        source = _saved_visual_source(request, visual, was, who, metric_tokens, metrics_on)
        # D-3b-6 (fix wave): unconditional -- `_visual_only_response`'s own save-candidate branch
        # already requires `source is not None` (see its `if source is not None and holder_
        # reference is not None and metric_tokens is not None:` below), so passing `None` here
        # whenever there is no source was a second, redundant gate on the same decision, and
        # Minor 5 is what it costs: an AST allowlist that checks only the keyword's *name*, never
        # its value, cannot see through a call site that always "passes" `metric_tokens=` while
        # the value itself is `None`.
        return _visual_only_response(
            visual,
            was,
            source=source,
            holder_reference=who.holder_reference,
            metric_tokens=metric_tokens,
            seal=seal,
        )

    repeated = await _repeats_a_recent_payment(was, request.question, who, queries)
    if repeated is not None:
        return await _as_payment(
            request.question,
            who,
            commands,
            payments_on,
            was.without_recent_payment(),
            repeated,
            key=key,
        )
    if was.recent_payment_rail is not None:
        # The referent is intentionally one turn wide. An unrelated sentence cannot revive a
        # payment several questions later merely because it contains the word "otro".
        was = was.without_recent_payment()

    # One reading, before any of the three decisions that need one. It is asked after the
    # confirmation and open-payment branches above on purpose: those turns belong to a state
    # machine whatever the words mean, and paying to classify them would be paying for nothing.
    analysis = await turn_analyzer.analyze(
        request.question, conversation=was, interpreter=reads, today=today
    )
    if analysis.is_compound:
        compound = await _compound_read_reply(
            analysis.clauses,
            was,
            who=who,
            today=today,
            queries=queries,
            presentation=presentation,
            metric_tokens=metric_tokens,
        )
        return compound or _compound_reply(analysis.clauses, was)
    read_as = analysis.intent

    continuing = await _continues_a_payment(read_as, was, request.question, who, queries)
    if continuing is not None:
        # A payment under way takes the reply that answers its question, before anything is
        # classified. "15 euros" is an amount because something asked for one.
        return await _as_payment(
            request.question, who, commands, payments_on, was, continuing, key=key
        )

    if _is_an_order_to_pay(read_as, request.question, was):
        # One chat box, two paths, and the decision is deterministic — a client cannot make it,
        # because making it is what this router is for.
        return await _as_payment(request.question, who, commands, payments_on, was, key=key)

    result = await queries.ask(
        AskFinancialQuestionQuery(
            sentence=request.question,
            account_id=who.account_id,
            today=today,
            # The holder's zone, exactly like every screen endpoint above. Left out, the chat
            # answered a Canarian holder about a Madrid day: between 00:00 and 01:00 peninsular
            # the two are different dates, and the movements screen and the conversation would
            # disagree about "hoy" on the same phone.
            reporting_timezone=who.reporting_timezone,
            presentation=presentation,
            conversation=was,
            # The reading this route already made. Handed down rather than made again — see the
            # field. Everything above decided *not* to pay on the strength of it, and the handler
            # deciding otherwise from a second reading is the disagreement this removes.
            analysis=analysis,
        )
    )
    if result.outcome == Spoken.TRANSFER_READY.value:
        return await _as_own_transfer(result, who, commands, payments_on, key=key)
    # Fix round 1 (Task 3): the seal is unconditional -- who.holder_reference and metric_tokens are
    # always real here (the codec always exists on app.state) -- and only the save-candidate offer
    # keeps the routes' own pre-Task-3 gate, now passed by name instead of silently riding along.
    return llm_mapper.question_to_dto(
        result,
        holder_reference=who.holder_reference,
        metric_tokens=metric_tokens,
        offer_saving=metrics_on and request.channel == "TEXT",
        spoken_for_voice=request.channel == "VOICE",
    )


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
    attempt: Attempt,
    reads: Reads,
    metric_tokens: MetricTokens,
    metrics_on: MetricsOn,
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
    key = attempt
    was = _conversation_of(request, who, metric_tokens)
    # Task 3: built once per request, unconditionally -- see the same line on the question route.
    seal = functools.partial(
        conversation_mapper.reseal_visual_contexts,
        holder_reference=who.holder_reference,
        codec=metric_tokens,
    )
    presentation = _presentation_of(request)

    # R5: the same short-circuit as the question route, before `interpret_visual` -- see its own
    # comment there. Streaming yields the identical single `answer` frame the restyle branch below
    # does: the answer text exists all at once, so there is nothing to stage.
    why = asks_why(request.question)
    if why is not None and was.visual_contexts:
        answer = _explanation_response(why, was)

        async def explanation_events() -> AsyncIterator[str]:
            yield _sse({"type": "answer", **answer.model_dump()})

        return StreamingResponse(explanation_events(), media_type="text/event-stream")

    if isinstance(was.active, PaymentConfirmation):
        # Here too, and for the reason every payment branch is duplicated: the browser types into
        # this endpoint, so a rule that only held on the other one is a rule the conversation never
        # got.
        previewed = was.active
        if await _abandons_a_transfer_preview(
            request.question, who, commands, payments_on, previewed
        ):
            was = was.with_active(None)
        else:
            return _confirmation_events(
                request.question, who, commands, payments_on, was, previewed, key=key
            )

    if isinstance(was.active, PaymentInProgress):
        paused = await _paused_payment_reply(request.question, was, was.active, who, queries)
        if paused is not None:
            return _open_payment_events(paused)
        immediate = _open_payment_reply(request.question, was, was.active)
        if immediate is not None:
            return _open_payment_events(immediate)

    # C-1/C-6/C-3 (Wave 3 hardening): moved below the two payment branches above, and gated on
    # `was.visual_contexts` -- see the same block's comment on the question route.
    visual = interpret_visual(request.question, was.visual_contexts)
    if visual is not None and was.visual_contexts:
        follow_up = classify_claimed_visual(visual, request.question)
        record_visual_follow_up(follow_up)
        if follow_up.kind == "AMBIGUOUS":
            answer = QuestionResponse(
                outcome="VISUAL_AMBIGUOUS",
                text=_ASK[follow_up.claimant],
                conversation=conversation_mapper.to_payload(was),
                about_chart_id=visual.target,
            )
        else:
            source = _saved_visual_source(request, visual, was, who, metric_tokens, metrics_on)
            # D-3b-6 (fix wave): unconditional -- see the same line on the question route.
            answer = _visual_only_response(
                visual,
                was,
                source=source,
                holder_reference=who.holder_reference,
                metric_tokens=metric_tokens,
                seal=seal,
            )

        async def visual_events() -> AsyncIterator[str]:
            yield _sse({"type": "answer", **answer.model_dump()})

        return StreamingResponse(visual_events(), media_type="text/event-stream")

    repeated = await _repeats_a_recent_payment(was, request.question, who, queries)
    if repeated is not None:
        return _payment_events(
            request.question,
            who,
            commands,
            payments_on,
            was.without_recent_payment(),
            repeated,
            key=key,
        )
    if was.recent_payment_rail is not None:
        was = was.without_recent_payment()

    analysis = await turn_analyzer.analyze(
        request.question, conversation=was, interpreter=reads, today=today
    )
    if analysis.is_compound:
        compound = await _compound_read_reply(
            analysis.clauses,
            was,
            who=who,
            today=today,
            queries=queries,
            presentation=presentation,
            metric_tokens=metric_tokens,
        )
        return _open_payment_events(compound or _compound_reply(analysis.clauses, was))
    read_as = analysis.intent

    continuing = await _continues_a_payment(read_as, was, request.question, who, queries)
    if continuing is not None:
        # Here as well as on the other route, and for the reason written below: the web client types
        # into *this* endpoint, so a payment that could only be continued on the other one was a
        # payment that could not be continued at all. The first version of this change wired the
        # routing into `/v1/questions` alone and the browser conversation still broke.
        return _payment_events(
            request.question, who, commands, payments_on, was, continuing, key=key
        )

    if _is_an_order_to_pay(read_as, request.question, was):
        # Routed here too, and not only on the other route. A demo that shows stages on screen uses
        # the stream, so leaving this out made the endpoint the user actually types into the one
        # that could not pay — the same sentence answering differently depending on which endpoint
        # a client happened to call.
        return _payment_events(request.question, who, commands, payments_on, was, key=key)

    question = AskFinancialQuestionQuery(
        sentence=request.question,
        account_id=who.account_id,
        today=today,
        # Here as well, and for the reason the whole streaming path exists twice: the browser
        # types into *this* endpoint, so a zone that only reached the other one was a zone the
        # conversation never used.
        reporting_timezone=who.reporting_timezone,
        presentation=presentation,
        conversation=was,
        analysis=analysis,
    )

    async def events() -> AsyncIterator[str]:
        async for step in asking.stream(question):
            if isinstance(step, Stage):
                payload = {"type": "stage", "name": step.name, "detail": step.detail}
            elif step.outcome == Spoken.TRANSFER_READY.value:
                # Staged here as well as on the other route, for the reason the payment branches
                # above are: the browser types into *this* endpoint, so a transfer that could only
                # be confirmed on the other one could not be confirmed at all.
                staged = await _as_own_transfer(step, who, commands, payments_on, key=key)
                payload = {"type": "answer", **staged.model_dump()}
            else:
                # Fix round 1 (Task 3): see the same line on the question route -- the seal is
                # unconditional, only the save-candidate offer keeps the metrics/channel gate.
                payload = {
                    "type": "answer",
                    **llm_mapper.question_to_dto(
                        step,
                        holder_reference=who.holder_reference,
                        metric_tokens=metric_tokens,
                        offer_saving=metrics_on and request.channel == "TEXT",
                        spoken_for_voice=request.channel == "VOICE",
                    ).model_dump(),
                }
            yield _sse(payload)

    return StreamingResponse(events(), media_type="text/event-stream")
