"""The conversation, between the domain and the wire.

Both directions in one module because they have to agree: the client sends back what it was given,
so anything this drops on the way out is state the next turn does not have. A round-trip test is the
only honest way to check that, and it lives beside this.

Coming *in*, everything is input. The payload crossed the network and a caller can send anything, so
each piece is rebuilt through the domain type that validates it — a payment of nothing, a category
that is not one, a rank of forty thousand are all refused at the edge with a 422 rather than three
layers in with an apology.
"""

from dataclasses import fields as dataclass_fields
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

from finguard_orchestrator.domain.enums import Action
from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    EncodingSort,
    FieldOrder,
    VisualContext,
    VisualField,
)
from finguard_orchestrator.domain.models.conversation import (
    AccountScopeClarification,
    ActiveWorkflow,
    ActivityOffers,
    BookingConfirmation,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    TransferInProgress,
    UnsupportedCapability,
    workflow_of,
)
from finguard_orchestrator.domain.models.expert_agents import ActivitySlot
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ConversationPayload,
    PaymentDraftPayload,
    PlanPayload,
    SpokenContactResponse,
    VisualContextPayload,
)
from finguard_orchestrator.infrastructure.mappers.plan_mapper import (
    plan_to_domain,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

# `VisualField.order`'s own dataclass default (`chart.py`), read rather than copied: a literal
# `"NONE"` here would be the second copy that goes stale the moment that default changes -- the
# same "second copy" this module's own reconstruction comment already warns against for `role`.
_DEFAULT_ORDER: FieldOrder = cast(
    FieldOrder, next(f.default for f in dataclass_fields(VisualField) if f.name == "order")
)


def plan_payload(plan: FinancialPlan | FinancialPlanDraft | TopMovements) -> PlanPayload:
    """A plan or a half-built one, in fields.

    One function for both because the payload is the same shape and the difference is exactly what
    the payload already allows: a draft has no window, and `period` and the range are optional.
    """
    common = PlanPayload(
        direction=plan.direction,
        period=plan.period.period if plan.period else None,
        n=plan.period.n if plan.period else None,
        start=plan.date_range.start if plan.date_range else None,
        end=plan.date_range.end if plan.date_range else None,
        categories=list(plan.categories),
        excluded_categories=list(getattr(plan, "excluded_categories", ())),
        merchants=list(plan.merchants),
        merchant_terms=list(getattr(plan, "merchant_terms", ())),
        excluded_merchants=list(getattr(plan, "excluded_merchants", ())),
        counterparties=list(getattr(plan, "counterparties", ())),
        excluded_counterparties=list(getattr(plan, "excluded_counterparties", ())),
        counterparty_types=list(getattr(plan, "counterparty_types", ())),
        include_internal=getattr(plan, "include_internal", False),
        internal_only=getattr(plan, "internal_only", False),
        across_accounts=getattr(plan, "across_accounts", False),
        only_account_id=getattr(plan, "only_account_id", None),
        only_account_ids=list(getattr(plan, "only_account_ids", ())),
        transaction_ids=list(getattr(plan, "transaction_ids", ())),
        transaction_types=list(getattr(plan, "transaction_types", ())),
        group_limit=getattr(plan, "group_limit", None),
    )
    if isinstance(plan, TopMovements):
        # No grouping and no metric: the payload keeps their defaults rather than inventing values a
        # caller could echo back into an aggregate.
        return common.model_copy(update={"action": Action.RANK_MOVEMENTS, "rank": plan.rank})
    return common.model_copy(
        update={
            "group_by": plan.group_by,
            "metric": plan.metric,
            "action": Action.AGGREGATE,
        }
    )


def _encoding_sort_payload(sort: EncodingSort) -> dict[str, object]:
    return {"mode": sort.mode, **({"field": sort.field} if sort.field is not None else {})}


def _context_spec_payload(spec: ChartSpec) -> dict[str, object]:
    # I1 (fix wave): the shape is defined once, here, rather than folded into the loop below --
    # one encoding on a channel is a bare dict, more than one is a list, and which shape a
    # channel gets must not depend on how many happened to arrive before it: the old accumulator
    # decided that inline, one encoding at a time, and a third `tooltip` both dropped the first
    # two and left the shape itself unstable across a round trip (a lone survivor serialised as a
    # list on the first pass, a dict on the second) -- exactly the case `_context_payload`'s own
    # docstring promises never drifts. `tooltip` is the only channel that can repeat
    # (`ChartSpec.__post_init__`'s uniqueness check excludes it by name), but the grouping below
    # does not assume that: it groups by channel first and only then asks how many landed there.
    grouped: dict[str, list[tuple[str, str, dict[str, object]]]] = {}
    for item in spec.encodings:
        value: dict[str, object] = {
            "field": item.field,
            "type": item.type,
            **({"title": item.title} if item.title else {}),
            **({"sort": _encoding_sort_payload(item.sort)} if item.sort is not None else {}),
        }
        grouped.setdefault(item.channel, []).append((item.field, item.type, value))
    encoding: dict[str, object] = {}
    for channel, entries in grouped.items():
        if len(entries) == 1:
            encoding[channel] = entries[0][2]
        else:
            # Ordered by (field, type) rather than by arrival order, so the same *set* of
            # encodings always serialises to the same list whichever order the domain handed
            # them in -- a stability the old accumulator's append-in-arrival-order never promised
            # and never needed to, because it never round-tripped correctly at three or more.
            encoding[channel] = [
                value for _, _, value in sorted(entries, key=lambda entry: (entry[0], entry[1]))
            ]
    return {
        "mark": {"type": "arc", "innerRadius": 55} if spec.mark == "donut" else {"type": spec.mark},
        "encoding": encoding,
        **({"title": spec.title} if spec.title else {}),
        **({"stack": spec.stack} if spec.stack else {}),
        **({"sort": spec.sort} if spec.sort else {}),
        **({"sort_field": spec.sort_field} if spec.sort_field else {}),
        **({"limit": spec.limit} if spec.limit is not None else {}),
    }


def _context_payload(context: VisualContext) -> dict[str, object]:
    """One visual context's wire shape, shared by `to_payload` and `seal_context`.

    Factored out so the dict a seal is computed over (`seal_context`, through
    `VisualContextPayload.model_validate` and `context_semantics`) and the dict `to_payload`
    actually emits are built by the exact same code -- never two copies that could drift.
    """
    return {
        "chart_id": context.chart_id,
        "derived_from": context.derived_from,
        "turn_id": context.turn_id,
        "spec": _context_spec_payload(context.spec),
        "profile": {
            "fields": [
                {
                    "name": field.name,
                    "type": field.type,
                    "role": field.role,
                    "order": field.order,
                    # Task 2 (Wave 3 hardening, A-6): the field's own count, not the dataset-level
                    # one below -- a series and its dimension can have different cardinalities, and
                    # collapsing both onto one number is exactly the defect this key exists to end.
                    "cardinality": field.cardinality,
                }
                for field in context.profile.fields
            ],
            # Kept for an older client's payload, which predates per-field cardinality above: the
            # nominal dimension's count, what `_limit` and the donut rule both key off of when a
            # field's own number is absent. It is 0 whenever the chart's dimension is temporal
            # rather than nominal -- read that as "not a donut's business", never as "no rows": the
            # rows are still all there.
            "cardinality": next(
                (
                    field.cardinality
                    for field in context.profile.role("dimension")
                    if field.type == "nominal"
                ),
                0,
            ),
            "metric": context.profile.metric,
            "unit": context.profile.unit,
            "positive": context.profile.positive,
            "mutually_exclusive": context.profile.mutually_exclusive,
            "verified_total": context.profile.verified_total,
            "comparison": context.profile.comparison,
            "plan_limited": context.profile.plan_limited,
            # Task 2 (Wave 3 hardening, U-4/C-5): sealed with the rest -- a later turn's
            # explanation reads `measure_metric()`, which needs `plan_metric` to have survived.
            "row_count": context.profile.row_count,
            "plan_metric": context.profile.plan_metric,
        },
        "token": context.token,
        # Wave 4.3: the labels the chart drew, sealed with the rest -- `context_semantics` is
        # `model_dump(exclude token)`, so these ride the same digest with no change there.
        "labels": list(context.labels),
        "series_labels": list(context.series_labels),
        "ranked": context.ranked,
        # The fix wave: the window the chart's rows cover and a digest of the plan that drew them
        # -- sealed the same way, for the same reason, riding the same digest.
        "window_start": context.window.start if context.window is not None else None,
        "window_end": context.window.end if context.window is not None else None,
        "plan_digest": context.plan_digest,
        # Task 1 (Wave 4.5): descriptive provenance, sealed the same way and for the same reason
        # the fix wave's own two facts just above are -- riding the same digest, no change there.
        "source": context.source,
        "fallback_reason": context.fallback_reason,
        # Task 2 (Wave 3 hardening, U-5): sealed with the rest, for the same reason `plan_digest`
        # above is -- `_nouns_of` (`visual_explanation.py`) reads it on a later turn.
        "group_by": context.group_by,
        # Task 2 (Wave 3 hardening, A-2): sealed with the rest -- `read_drill_down` reads it on a
        # later turn to refuse narrowing a chart whose plan it never actually drew.
        "metric_definition_borrowed": context.metric_definition_borrowed,
    }


def context_semantics(payload: VisualContextPayload) -> dict[str, object]:
    """What a seal covers: the whole wire context, minus the seal itself.

    The one definition, read both when a context is sealed (`seal_context`, over the context this
    process is about to sign) and when one is verified (`to_domain`'s `verify`, over the context a
    caller echoed back) -- so the digest at creation and the digest at verification are always the
    same pydantic-normalised dict, never two hand-picked field lists that could drift apart.

    Unchanged by the fix wave's `window_start`/`window_end`/`plan_digest`: `model_dump` walks every
    field the payload declares, so the three are covered by construction the moment they exist on
    `VisualContextPayload` -- there is no allowlist here to extend.
    """
    return payload.model_dump(mode="json", exclude={"token"})


def seal_context(
    context: VisualContext,
    conversation_id: UUID,
    position: int,
    holder_reference: str,
    codec: MetricTokenCodec,
) -> VisualContext:
    """`context`, sealed for `holder_reference` at `position` in `conversation_id`'s own list: same
    wire shape `to_payload` would emit, signed.

    Serialised through `_context_payload` -- the same dict `to_payload` builds -- and validated
    through `VisualContextPayload.model_validate` before it is sealed, so the digest covers the
    pydantic-normalised shape a caller's echoed payload will also be read through at verification,
    never the domain object's own, unvalidated, representation of itself.

    A-1 (Wave 3 hardening): `conversation_id` and `position` are folded into the token alongside
    the content digest, never into `context_semantics` itself -- they describe *where* this
    context lives, not *what* it is, and the content digest must stay the same value whether a
    context is sealed once or resealed ten times as its neighbours come and go. `trusted`/
    `trust_reason` are stamped `True`/`"ok"` explicitly rather than left as whatever `context`
    already carried: this function is also `reseal_visual_contexts`' own tool for restoring trust
    to a survivor after an eviction shifted its position, and the context it is given there is
    already trusted -- stamping it here rather than trusting the caller keeps that invariant true
    by construction instead of by every caller remembering it.
    """
    payload = VisualContextPayload.model_validate(_context_payload(context))
    token = codec.seal_context(
        holder_reference, conversation_id, position, context_semantics(payload)
    )
    return replace(context, token=token, trusted=True, trust_reason="ok")


def reseal_visual_contexts(
    conversation: Conversation, holder_reference: str, codec: MetricTokenCodec
) -> Conversation:
    """`conversation`, with every already-trusted visual context resealed at its current index.

    A-1 (Wave 3 hardening), the design decision the task names explicitly: a seal now covers a
    context's *position*, and `Conversation.with_visual_context` can evict the oldest context when
    the cap is reached, which shifts every survivor's index down by one. A seal minted at the old
    index would then fail its own position check on the very next turn -- not because anything was
    replayed, reordered or tampered with, but because the list it was sealed against no longer
    exists. The orchestrator holds the signing key and already re-emits the whole list every turn
    (`to_payload`), so re-sealing here is free of any new trust the client would have to be asked
    to extend: the invariant a seal states is "this is context N of the list I, the server, last
    emitted" -- never "context N of the list you first saw it in".

    Only contexts already `trusted` are resealed; an untrusted one (a failed replay, a tampered
    payload, a stale `c1.` token) is left exactly as `to_domain` produced it -- resealing it would
    manufacture trust this function has no business granting.

    `conversation.conversation_id is None` mints one here rather than skipping the seal --
    `_identified`'s own rule applied a second time ("an absent identifier is a first turn"), for a
    caller that reaches this function without ever having gone through `_conversation_of`/
    `to_domain` at all (every production request has, so this is a fallback for exactly one thing:
    a test, or a future caller, that builds an `AskFinancialQuestionResult` with the module-level
    `IDLE` conversation directly). Minting here rather than leaving the chart unsealed keeps the
    invariant this whole task exists for -- every trusted chart carries a seal -- true even for a
    caller that forgot to mint an id first, at the cost of nothing: the id this function mints is
    stamped onto the very `Conversation` it returns, so it travels forward exactly as if it had
    been minted earlier.
    """
    if not conversation.visual_contexts:
        return conversation
    conversation_id = conversation.conversation_id or uuid4()
    resealed = tuple(
        seal_context(context, conversation_id, position, holder_reference, codec)
        if context.trusted
        else context
        for position, context in enumerate(conversation.visual_contexts)
    )
    return replace(conversation, conversation_id=conversation_id, visual_contexts=resealed)


# --- the offers of the last activity search (A2A expert agents, spec §5.3) ---------------------


def _offers_payload(offers: ActivityOffers | None) -> dict[str, object] | None:
    """The offers' wire shape. Each slot carries its agent's shown name too, so a client listing
    them needs nothing else; the seal covers both copies, and the payload refuses a disagreement."""
    if offers is None:
        return None
    return {
        "slots": [_slot_payload(slot, offers.name_of(slot.agent_id)) for slot in offers.slots],
        "agent_names": [
            {"agent_id": agent_id, "name": name} for agent_id, name in offers.agent_names
        ],
        "contexts": [
            {"agent_id": agent_id, "context_id": context_id}
            for agent_id, context_id in offers.contexts
        ],
        "token": offers.token,
    }


def query_to_domain(payload: PlanPayload) -> FinancialPlan | TopMovements:
    """The last answered question, whichever kind it was.

    The tag decides, not the fields: a payload with a rank *and* a grouping is a caller
    improvising, and the tag keeps the two shapes from blending into something neither service
    issues.
    """
    if payload.action is Action.RANK_MOVEMENTS:
        absolute = payload.start is not None and payload.end is not None
        return TopMovements(
            direction=payload.direction,
            period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
            date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
            categories=tuple(payload.categories),
            excluded_categories=tuple(payload.excluded_categories),
            merchants=tuple(payload.merchants),
            merchant_terms=tuple(payload.merchant_terms),
            excluded_merchants=tuple(payload.excluded_merchants),
            across_accounts=payload.across_accounts,
            only_account_id=payload.only_account_id,
            only_account_ids=tuple(payload.only_account_ids),
            rank=payload.rank or 1,
        )
    return plan_to_domain(payload)


def _slot_payload(slot: ActivitySlot, agent_name: str) -> dict[str, object]:
    return {
        "agent_id": slot.agent_id,
        "agent_name": agent_name,
        "activity": slot.activity,
        "start": slot.start,
        "end": slot.end,
        "free_places": slot.free_places,
        "slot_ref": slot.slot_ref,
    }


def to_payload(conversation: Conversation) -> ConversationPayload:
    """What the client stores and sends back.

    Everything the next turn needs is here, and nothing else: a client that reads this to decide
    what to keep is the design this replaced.

    Built in one call rather than a base plus `model_copy` patches, because the payload validates
    itself now: a half-filled PAYMENT_CONFIRMATION is refused, and constructing one on the way to
    filling it would make this function raise on its own correct output.
    """
    return ConversationPayload.model_validate(
        {
            # The half of the round trip that makes it one. Without this line the identifier is
            # minted on every turn and every turn is still its own trace, which is the state this
            # change exists to end — and nothing would fail, because nothing downstream reads it.
            "conversation_id": conversation.conversation_id,
            # The engine's, carried unchanged: the rules engine neither reads nor moves it.
            "version": conversation.version,
            "workflow": workflow_of(conversation.active),
            "awaiting": conversation.awaiting,
            "visual_contexts": [
                _context_payload(context) for context in conversation.visual_contexts
            ],
            "activity_offers": _offers_payload(conversation.activity_offers),
            "last_plan": plan_payload(conversation.last_plan) if conversation.last_plan else None,
            **_active_payload(conversation.active),
        }
    )


def _active_payload(active: ActiveWorkflow | None) -> dict[str, Any]:
    """The fields that belong to the active state, and only those."""
    if isinstance(active, FinancialClarification):
        return {"draft": plan_payload(active.draft)}
    if isinstance(active, AccountScopeClarification):
        return {
            "pending_plan": plan_payload(active.pending),
            "pending_requested_count": active.requested_count,
            "pending_explicit_visualization": active.explicit_visualization,
            "account_candidates": [
                {
                    "account_id": candidate.account_id,
                    "display_name": candidate.display_name,
                    "kind": candidate.kind,
                }
                for candidate in active.candidates
            ],
        }
    if isinstance(active, UnsupportedCapability):
        return {"capability": active.capability}
    if isinstance(active, PaymentInProgress):
        return {
            "payment": PaymentDraftPayload(
                recipient_name=active.draft.recipient_name,
                recipient_contact_id=active.draft.recipient_contact_id,
                amount_minor=active.draft.amount_minor,
                currency=active.draft.currency,
                concept=active.draft.concept,
                rail=active.draft.rail,
            ),
            "suspended": active.suspended,
            "candidates": [
                SpokenContactResponse(
                    contact_id=contact.contact_id,
                    display_name=contact.display_name,
                    phone=contact.phone,
                )
                for contact in active.candidates
            ],
        }
    if isinstance(active, TransferInProgress):
        return {
            "transfer_to": active.to_account_id,
            "transfer_amount_minor": active.amount_minor,
        }
    if isinstance(active, PaymentConfirmation):
        return {
            "intent_id": active.intent_id,
            "recipient": active.recipient,
            "amount_minor": active.amount_minor,
            # Sent as well as read. Dropping it here was the whole of the round-trip loss: the
            # payload had nowhere to put a currency, so the far side rebuilt the default.
            "currency": active.currency,
            "concept": active.concept,
            "rail": active.rail,
            "recipient_iban": active.recipient_iban,
        }
    if isinstance(active, BookingConfirmation):
        return {
            "booking_id": active.booking_id,
            # The slot carries its gym's name too, as the offers' slots do; the payload refuses a
            # slot and a booking that name the gym differently.
            "booking_slot": _slot_payload(active.slot, active.agent_name),
            "booking_agent_name": active.agent_name,
            "booking_context_id": active.context_id,
        }
    return {}
