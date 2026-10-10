"""The conversation, between the domain and the wire.

Both directions in one module because they have to agree: the client sends back what it was given,
so anything this drops on the way out is state the next turn does not have. A round-trip test is the
only honest way to check that, and it lives beside this.

Coming *in*, everything is input. The payload crossed the network and a caller can send anything, so
each piece is rebuilt through the domain type that validates it — a payment of nothing, a category
that is not one, a rank of forty thousand are all refused at the edge with a 422 rather than three
layers in with an apology.
"""

from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from finguard_orchestrator.domain.enums import Action, GroupBy, Rail
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import (
    Channel,
    ChartEncoding,
    ChartSpec,
    ContextTrust,
    EncodingSort,
    FieldOrder,
    SortMode,
    Stack,
    VisualContext,
    VisualField,
    VisualProfile,
)
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    AccountAlias,
    AccountCandidate,
    AccountScopeClarification,
    AccountSelection,
    ActiveWorkflow,
    ActivityOffers,
    BookingConfirmation,
    Conversation,
    CounterpartyAlias,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    TransferInProgress,
    Turn,
    UnsupportedCapability,
    Workflow,
    workflow_of,
)
from finguard_orchestrator.domain.models.expert_agents import ActivitySlot
from finguard_orchestrator.domain.models.result_reference import (
    ResultReference,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ActivityOffersPayload,
    ActivitySlotPayload,
    ConversationPayload,
    PaymentDraftPayload,
    PlanPayload,
    SpokenContactResponse,
    VisualContextPayload,
)
from finguard_orchestrator.infrastructure.mappers.plan_mapper import (
    draft_to_domain,
    plan_to_domain,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.observability import name_the_conversation

# D-3b-4 (fix wave): named once, used by both `_visual_contexts` and `to_domain` below -- the two
# signatures have to move together by definition (one is the closure the other threads through to
# it), and a hand-written copy at each site is exactly the kind of pair this module's other
# comments already warn drifts apart.
#
# A-1 (Wave 3 hardening): the closure now takes the conversation id and the context's position in
# the echoed list, and returns *why* rather than whether -- `context_semantics` is still the last
# argument, unchanged, so only the caller (`llm_controller._conversation_of`) had to grow the two
# new arguments it already has both of by the time it builds this closure.
type ContextVerifier = Callable[[str | None, UUID, int, dict[str, object]], ContextTrust]
# The offers' own, bound by the controller the same way: token, conversation id, what the seal
# covers. No position -- a conversation holds one set of offers (`MetricTokenCodec.seal_offers`).
type OffersVerifier = Callable[[str | None, UUID, dict[str, object]], ContextTrust]

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


def _encoding_sort_from_payload(raw: object) -> EncodingSort | None:
    """One encoding's `sort`, trusting the DTO to have already refused anything invalid.

    `VisualContextPayload._value_free_spec` now constructs `EncodingSort` itself and lets its
    `ValueError` become a 422 before this ever runs, so a payload that reaches `to_domain` has a
    `sort` that is either absent (`raw is None`) or exactly the dict `EncodingSort` accepts. This
    used to filter instead of trust -- catching that same `ValueError` and returning `None` -- so
    a caller's `{"mode": "sideways"}` silently became "no sort" instead of a refusal. A
    `ValueError` reaching here now is not a caller's mistake: it is the DTO's guard and the domain
    rule it enforces coming apart, and it is left to propagate rather than caught and hidden.
    """
    if raw is None:
        return None
    assert isinstance(raw, dict)
    field = raw.get("field")
    return EncodingSort(cast(SortMode, raw["mode"]), field if isinstance(field, str) else None)


def _visual_contexts(
    payload: ConversationPayload | None,
    *,
    conversation_id: UUID,
    verify: ContextVerifier | None = None,
) -> tuple[VisualContext, ...]:
    if payload is None:
        return ()
    result: list[VisualContext] = []
    for position, item in enumerate(payload.visual_contexts):
        raw = item.spec
        mark = raw.get("mark")
        if not isinstance(mark, dict) or mark.get("type") not in {
            "bar",
            "line",
            "area",
            "point",
            "arc",
        }:
            raise ValueError("unsupported visual mark")
        mark_name = cast(
            Literal["line", "area", "bar", "point", "donut"],
            "donut" if mark.get("type") == "arc" else mark["type"],
        )
        raw_encoding = raw.get("encoding")
        if not isinstance(raw_encoding, dict):
            raise ValueError("visual context needs encoding")
        encodings: list[ChartEncoding] = []
        for channel, value in raw_encoding.items():
            values = value if isinstance(value, list) else [value]
            for encoded in values:
                if not isinstance(encoded, dict) or not isinstance(encoded.get("field"), str):
                    raise ValueError("invalid visual encoding")
                encodings.append(
                    ChartEncoding(
                        cast(Channel, channel),
                        encoded["field"],
                        cast(
                            Literal["nominal", "quantitative", "temporal"],
                            encoded.get("type", "nominal"),
                        ),
                        encoded.get("title") if isinstance(encoded.get("title"), str) else None,
                        _encoding_sort_from_payload(encoded.get("sort")),
                    )
                )
        # The profile travels whole enough for the verifier now: `role` and `order` are on the
        # wire per field, and `comparison`/`plan_limited` are on the wire per profile, so `verify`
        # judges a round-tripped chart -- Task 3's restyle path -- on the same facts the floor saw
        # when it built it. The fallback below (`role` guessed from `type`, `order` defaulted to
        # `_DEFAULT_ORDER` -- `VisualField.order`'s own dataclass default, read rather than
        # hard-coded) is for a conversation an older client still holds, whose payload predates
        # these two keys.
        #
        # I4 (fix wave), decided rather than left implicit: this validates; it does not restore
        # what base itself could do. `_DEFAULT_ORDER` is `"NONE"`, and the verifier's own `order`
        # rule reads that as "nothing may sort this field" -- so a sort restyle asked of an
        # old-shape chart is refused exactly as it was at base, before this task existed. That is
        # not a regression this fallback owes anyone: the turn still completes rather than 500,
        # and the window closes on its own the moment the conversation's next chart is built --
        # every new chart carries the full profile, so a sort restyle resumes working from there,
        # never from this one. Recovering `order` from the spec's own axis sort
        # (`_encoding_sort_from_payload` already reads one back) was judged not worth the code for
        # a window one turn wide; `test_an_older_payload_validates_but_a_sort_restyle_waits_for_
        # the_next_chart` (`test_visualization_stage1.py`) pins both halves of this paragraph.
        #
        # Task 2 (Wave 3 hardening, A-6/U-4/C-5): `cardinality` now travels per field
        # (`VisualFieldPayload.cardinality`), so the true dimension, a series, or an old payload's
        # series misread as one each keep their own count -- falling back to the one dataset-level
        # number (`item.profile.cardinality`, the nominal dimension's) only for a field an older
        # client's payload predates this key for, the same one-turn-wide window `role`/`order`'s
        # own fallback above already describes. `row_count` and `plan_metric` now round-trip too:
        # neither is authority -- `valid_for`/`merge_encoding_patch`/`verify` read neither -- but
        # `measure_metric()` (`chart.py`) is the one thing that does, and a later turn's
        # explanation calling it on a zeroed `plan_metric` is exactly A-7's defect reopened through
        # this wire. The floor never runs on a round-tripped profile regardless -- it runs on one
        # built fresh from the dataset in the same turn.
        profile = VisualProfile(
            fields=tuple(
                VisualField(
                    field.name,
                    field.type,
                    field.role
                    if field.role is not None
                    else ("measure" if field.type == "quantitative" else "dimension"),
                    field.cardinality
                    if field.cardinality is not None
                    else item.profile.cardinality,
                    field.order if field.order is not None else _DEFAULT_ORDER,
                )
                for field in item.profile.fields
            ),
            metric=item.profile.metric or "SUM",
            unit=item.profile.unit or "unknown",
            positive=item.profile.positive,
            mutually_exclusive=item.profile.mutually_exclusive,
            verified_total=item.profile.verified_total,
            comparison=item.profile.comparison,
            plan_limited=item.profile.plan_limited,
            row_count=item.profile.row_count,
            plan_metric=item.profile.plan_metric,
        )
        title_value = raw.get("title")
        title = title_value if isinstance(title_value, str) else None
        sort_field_value = raw.get("sort_field")
        sort_field = sort_field_value if isinstance(sort_field_value, str) else None
        limit_value = raw.get("limit")
        limit = limit_value if isinstance(limit_value, int) else None
        # `stack` and `sort` used to be filtered here -- an unrecognised value silently became
        # `None` instead of being refused. `VisualContextPayload._value_free_spec` is the guard
        # now (`stack in get_args(Stack)`, `sort in get_args(SortMode)`), so what reaches this
        # point is already either absent or a member of the domain's own set; this only tells
        # "absent" from "present" and casts, it does not filter.
        raw_stack = raw.get("stack")
        raw_sort = raw.get("sort")
        spec = ChartSpec(
            mark=mark_name,
            encodings=tuple(encodings),
            title=title,
            stack=cast(Stack, raw_stack) if raw_stack is not None else None,
            sort=cast(SortMode, raw_sort) if raw_sort is not None else None,
            sort_field=sort_field,
            limit=limit,
        )
        # No verifier: exactly today's behaviour -- every context this process ever built without
        # Task 3's holder-bound check in front of it is trusted, on the wire or freshly minted the
        # same way. With one, a context is trusted only through a seal that actually checks out;
        # an unverified token is dropped rather than echoed back as if it still meant something.
        #
        # A-1 (Wave 3 hardening): checked against THIS conversation's id and THIS context's index
        # in the list the caller just echoed -- never the position it may have been sealed at,
        # which is exactly what lets a replay from another conversation, a reorder, a duplicate or
        # a dropped-then-shifted context all fail here instead of passing as `True` the way a bare
        # holder-and-content seal did.
        trust_reason: ContextTrust = (
            verify(item.token, conversation_id, position, context_semantics(item))
            if verify is not None
            else "ok"
        )
        trusted = trust_reason == "ok"
        # Both ends or neither -- `VisualContextPayload`'s own validator already refuses a payload
        # with just one, so `item.window_end` is never `None` here when `item.window_start` is not.
        window = (
            AbsoluteRange(item.window_start, item.window_end)
            if item.window_start is not None and item.window_end is not None
            else None
        )
        result.append(
            VisualContext(
                item.chart_id,
                spec,
                profile,
                derived_from=item.derived_from,
                turn_id=item.turn_id,
                token=item.token if trusted else None,
                trusted=trusted,
                trust_reason=trust_reason,
                labels=tuple(item.labels),
                series_labels=tuple(item.series_labels),
                ranked=item.ranked,
                window=window,
                plan_digest=item.plan_digest,
                source=item.source,
                fallback_reason=item.fallback_reason,
                group_by=item.group_by,
                metric_definition_borrowed=item.metric_definition_borrowed,
            )
        )
    return tuple(result)


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


def offers_semantics(payload: ActivityOffersPayload) -> dict[str, object]:
    """What an offers seal covers: the whole wire offers, minus the seal itself -- read at sealing
    and at verification alike, like `context_semantics`, so the two digests cannot drift."""
    return payload.model_dump(mode="json", exclude={"token"})


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


def _offers(
    payload: ConversationPayload,
    *,
    conversation_id: UUID,
    verify_offers: OffersVerifier | None = None,
) -> ActivityOffers | None:
    """The echoed offers, trusted only through a seal that checks out for this conversation.

    No verifier is today's behaviour for every other echoed memory here, and the one the tests use
    for a plain round trip: trusted. With one (`llm_controller._conversation_of`), a seal that does
    not verify leaves the offers readable and unusable -- `trusted=False` and no token, so the next
    turn cannot carry a seal forward that never held (§5.3: «vuelve a pedirla»).
    """
    item = payload.activity_offers
    if item is None:
        return None
    trust: ContextTrust = (
        verify_offers(item.token, conversation_id, offers_semantics(item))
        if verify_offers is not None
        else "ok"
    )
    trusted = trust == "ok"
    return ActivityOffers(
        slots=tuple(_slot(slot) for slot in item.slots),
        agent_names=tuple((name.agent_id, name.name) for name in item.agent_names),
        contexts=tuple((context.agent_id, context.context_id) for context in item.contexts),
        trusted=trusted,
        token=item.token if trusted else None,
    )


def seal_offers_payload(
    payload: ConversationPayload,
    conversation_id: UUID,
    holder_reference: str,
    codec: MetricTokenCodec,
) -> ConversationPayload:
    """`payload`, with its offers sealed for `holder_reference` in `conversation_id`.

    Over the payload rather than the domain object, like `seal_context` seals the shape a caller's
    echo will be verified through: `offers_semantics` of this exact, validated dict. A payload with
    no offers is returned as it is.
    """
    offers = payload.activity_offers
    if offers is None:
        return payload
    token = codec.seal_offers(holder_reference, conversation_id, offers_semantics(offers))
    return payload.model_copy(
        update={"activity_offers": offers.model_copy(update={"token": token})}
    )


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


def _identified(payload: ConversationPayload | None) -> UUID:
    """Which conversation this is: the one the caller echoed back, or a new one.

    **Minted here because this is the only moment a wire payload becomes a conversation.** There is
    no session and no store, and this does not invent one: the identifier is a value the client
    round-trips exactly like the workflow and the plans, and the mapper's existing job is already
    to fill in the state the client did not send — an absent payload is an idle conversation, and
    an absent identifier is a first turn.

    **`uuid4` and nothing cleverer, and that is the privacy rule rather than laziness.** An
    identifier derived from anything — the sentence, the holder, the first plan — would be a
    function of what somebody typed, and this value ends up on a span attribute where a label is
    forever. Random is the only mint with no input to leak. It also means two conversations cannot
    collide into one trace by having started the same way, which a hash of the opening turn would.

    Telling the turn is the second half and belongs in the same breath: a conversation that were
    identified here and named somewhere else would be two places to forget.
    """
    known = payload.conversation_id if payload is not None else None
    identifier = known or uuid4()
    name_the_conversation(str(identifier))
    return identifier


def to_domain(
    payload: ConversationPayload | None,
    *,
    verify: ContextVerifier | None = None,
    verify_offers: OffersVerifier | None = None,
) -> Conversation:
    """The conversation a caller echoed back.

    An absent payload is an idle conversation rather than an error: the first turn has none, and a
    client that forgets to send one gets a fresh conversation instead of a rejection. It gets a
    fresh *identifier* on the same terms and for the same reason — see `_identified`.

    A payload whose `workflow` does not match the fields it carries — PAYMENT_CONFIRMATION with no
    intent, a clarification with no draft — never reaches this function: the payload refuses itself
    and the caller gets a 422. It used to become idle here, which was quieter and worse. A state
    nobody could have been in is a client bug or an attempt, and silently reinterpreting it as "no
    conversation" tells whoever sent it nothing while discarding whatever was really under way.

    `verify` is absent by default — exactly today's behaviour, and every existing call in this
    codebase's tests stays that way: an echoed visual context is trusted with no questions asked.
    The caller who actually has a holder to check against (Task 3) binds it into a closure over
    that holder and passes it here; `_visual_contexts` is where each context's seal is actually
    checked. `verify_offers` is the same for a search's offers, checked in `_offers`.
    """
    conversation_id = _identified(payload)
    if payload is None:
        return Conversation(conversation_id=conversation_id)
    activity_offers = _offers(payload, conversation_id=conversation_id, verify_offers=verify_offers)
    if payload.workflow is None:
        last_plan = query_to_domain(payload.last_plan) if payload.last_plan else None
        last_ratio = _ratio_plan(payload)
        pending_ratio = _ratio_draft(payload)
        last_comparison = _comparison_plan(payload)
        result_reference = _reference_state(
            payload, last_plan, last_ratio, pending_ratio, last_comparison
        )
        return Conversation(
            conversation_id=conversation_id,
            version=payload.version,
            last_plan=last_plan,
            prior_plan=(
                query_to_domain(payload.prior_plan) if payload and payload.prior_plan else None
            ),
            retry_plan=(
                query_to_domain(payload.retry_plan) if payload and payload.retry_plan else None
            ),
            # The branch this field exists for. A question asked with nothing half-built behind it
            # comes back exactly here — no workflow — and dropping the slot on the way in is what
            # let the same question be asked forever.
            last_asked=payload.last_asked,
            answered_the_balance=payload.answered_the_balance,
            last_ratio=last_ratio,
            pending_ratio=pending_ratio,
            last_comparison=last_comparison,
            result_reference=result_reference,
            account_aliases=_account_aliases(payload),
            counterparty_aliases=_counterparty_aliases(payload),
            recent_payment_rail=payload.recent_payment_rail,
            turns=_turns(payload),
            visual_contexts=_visual_contexts(
                payload, conversation_id=conversation_id, verify=verify
            ),
            activity_offers=activity_offers,
            account_selection=_account_selection(payload) if payload else None,
        )

    last_plan = query_to_domain(payload.last_plan) if payload.last_plan else None
    prior_plan = query_to_domain(payload.prior_plan) if payload.prior_plan else None
    retry_plan = query_to_domain(payload.retry_plan) if payload.retry_plan else None
    active = _active(payload)
    last_ratio = _ratio_plan(payload)
    pending_ratio = _ratio_draft(payload)
    last_comparison = _comparison_plan(payload)
    result_reference = _reference_state(
        payload, last_plan, last_ratio, pending_ratio, last_comparison
    )
    return Conversation(
        conversation_id=conversation_id,
        version=payload.version,
        active=active,
        last_plan=last_plan,
        prior_plan=prior_plan,
        retry_plan=retry_plan,
        last_asked=payload.last_asked,
        answered_the_balance=payload.answered_the_balance,
        last_ratio=last_ratio,
        pending_ratio=pending_ratio,
        last_comparison=last_comparison,
        result_reference=result_reference,
        account_aliases=_account_aliases(payload),
        counterparty_aliases=_counterparty_aliases(payload),
        recent_payment_rail=payload.recent_payment_rail,
        turns=_turns(payload),
        visual_contexts=_visual_contexts(payload, conversation_id=conversation_id, verify=verify),
        activity_offers=activity_offers,
        account_selection=_account_selection(payload),
    )


def _reference_state(
    payload: ConversationPayload,
    last_plan: FinancialPlan | TopMovements | None,
    last_ratio: RatioPlan | None,
    pending_ratio: RatioDraft | None,
    last_comparison: ComparisonPlan | None,
) -> ResultReference | None:
    if (
        last_ratio is not None
        or pending_ratio is not None
        or last_comparison is not None
        or payload.answered_the_balance
    ):
        return None
    category = payload.referenced_category
    group_by = payload.referenced_group_by
    group_key = payload.referenced_group_key
    ids = tuple(payload.referenced_transaction_ids)
    if category is None and group_by is None and group_key is None and not ids:
        return None
    if last_plan is None:
        raise InvalidValueError("an explicit result reference needs an answered query")
    return ResultReference(last_plan, category, group_by, group_key, ids)


def _turns(payload: ConversationPayload) -> tuple[Turn, ...]:
    """The conversation's own record of itself, rebuilt through the domain like everything else.

    `Turn` truncates an over-long sentence rather than refusing it, and the payload bounds the same
    length — so a caller sending more gets a 422 at the edge and a caller sending exactly the
    ceiling round-trips unchanged.
    """
    return tuple(
        Turn(sentence=row.sentence, outcome=row.outcome, plan=row.plan, question=row.question)
        for row in payload.turns
    )


def _account_aliases(payload: ConversationPayload) -> tuple[AccountAlias, ...]:
    return tuple(
        AccountAlias(row.alias, row.account_id, row.display_name) for row in payload.account_aliases
    )


def _account_selection(payload: ConversationPayload) -> AccountSelection | None:
    if payload.account_selection_mode is None:
        return None
    return AccountSelection(
        mode=payload.account_selection_mode,
        account_ids=tuple(payload.account_selection_ids),
    )


def _counterparty_aliases(payload: ConversationPayload) -> tuple[CounterpartyAlias, ...]:
    return tuple(
        CounterpartyAlias(row.alias, row.counterparty_name, row.counterparty_type)
        for row in payload.counterparty_aliases
    )


def _active(payload: ConversationPayload) -> ActiveWorkflow | None:
    """The state the payload describes.

    Nothing is recovered here any more. The payload's own validator has already refused a workflow
    whose fields do not match it, so a PAYMENT_CONFIRMATION reaching this function has an intent and
    a PAYMENT_IN_PROGRESS has a payment. That is why the asserts below are asserts and not branches:
    they are the invariant restated, not a case a caller can produce.
    """
    match payload.workflow:
        case Workflow.FINANCIAL_CLARIFICATION:
            assert payload.draft is not None  # the payload's validator requires it
            return FinancialClarification(
                draft=draft_to_domain(payload.draft),
                awaiting=payload.awaiting or Slot.WINDOW,
            )
        case Workflow.ACCOUNT_SCOPE:
            assert payload.pending_plan is not None
            return AccountScopeClarification(
                pending=query_to_domain(payload.pending_plan),
                candidates=tuple(
                    AccountCandidate(row.account_id, row.display_name, row.kind)
                    for row in payload.account_candidates
                ),
                requested_count=payload.pending_requested_count,
                explicit_visualization=payload.pending_explicit_visualization,
            )
        case Workflow.UNSUPPORTED_CAPABILITY:
            assert payload.capability  # the payload's validator requires it
            return UnsupportedCapability(capability=payload.capability)
        case Workflow.PAYMENT_IN_PROGRESS:
            assert payload.payment is not None  # the payload's validator requires it
            return PaymentInProgress(
                draft=_payment_to_domain(payload.payment),
                awaiting=payload.awaiting or Slot.AMOUNT,
                candidates=tuple(
                    Contact(row.contact_id, row.display_name, row.phone)
                    for row in payload.candidates
                ),
                suspended=payload.suspended,
            )
        case Workflow.TRANSFER_IN_PROGRESS:
            return TransferInProgress(
                to_account_id=payload.transfer_to,
                amount_minor=payload.transfer_amount_minor,
                awaiting=payload.awaiting or Slot.AMOUNT,
            )
        case Workflow.PAYMENT_CONFIRMATION:
            assert payload.intent_id is not None  # the payload's validator requires all three
            assert payload.recipient is not None
            assert payload.amount_minor is not None
            return PaymentConfirmation(
                intent_id=payload.intent_id,
                recipient=payload.recipient,
                amount_minor=payload.amount_minor,
                # The domain's default is EUR, and taking it here is how a preview prepared in
                # dollars came back from a round trip denominated in euros.
                currency=payload.currency or "EUR",
                concept=payload.concept,
                rail=payload.rail or Rail.BIZUM,
                recipient_iban=payload.recipient_iban,
            )
        case Workflow.BOOKING_CONFIRMATION:
            assert payload.booking_id is not None  # the payload's validator requires all three
            assert payload.booking_slot is not None
            assert payload.booking_agent_name is not None
            return BookingConfirmation(
                booking_id=payload.booking_id,
                slot=_slot(payload.booking_slot),
                agent_name=payload.booking_agent_name,
                context_id=payload.booking_context_id,
            )
        case _:
            return None


def _slot(payload: ActivitySlotPayload) -> ActivitySlot:
    """One slot off the wire, through the domain type that refuses what a verifier never passed."""
    return ActivitySlot(
        payload.agent_id,
        payload.activity,
        payload.start,
        payload.end,
        payload.free_places,
        payload.slot_ref,
    )


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


def _payment_to_domain(payload: PaymentDraftPayload) -> PaymentDraft:
    return PaymentDraft(
        recipient_name=payload.recipient_name,
        recipient_contact_id=payload.recipient_contact_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        concept=payload.concept,
        rail=payload.rail,
    )


def to_payload(conversation: Conversation) -> ConversationPayload:
    """What the client stores and sends back.

    Everything the next turn needs is here, and nothing else: a client that reads this to decide
    what to keep is the design this replaced.

    Built in one call rather than a base plus `model_copy` patches, because the payload validates
    itself now: a half-filled PAYMENT_CONFIRMATION is refused, and constructing one on the way to
    filling it would make this function raise on its own correct output.
    """
    referenced_category, referenced_group_by, referenced_group_key, referenced_transaction_ids = (
        _legacy_projection_of(conversation.result_reference)
    )
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
            "last_asked": conversation.last_asked,
            "answered_the_balance": conversation.answered_the_balance,
            "referenced_category": referenced_category,
            "referenced_group_by": referenced_group_by,
            "referenced_group_key": referenced_group_key,
            "referenced_transaction_ids": referenced_transaction_ids,
            "account_aliases": [
                {
                    "alias": alias.alias,
                    "account_id": alias.account_id,
                    "display_name": alias.display_name,
                }
                for alias in conversation.account_aliases
            ],
            "account_selection_mode": (
                conversation.account_selection.mode if conversation.account_selection else None
            ),
            "account_selection_ids": (
                list(conversation.account_selection.account_ids)
                if conversation.account_selection
                else []
            ),
            "counterparty_aliases": [
                {
                    "alias": alias.alias,
                    "counterparty_name": alias.counterparty_name,
                    "counterparty_type": alias.counterparty_type,
                }
                for alias in conversation.counterparty_aliases
            ],
            "recent_payment_rail": conversation.recent_payment_rail,
            "turns": [
                {
                    "sentence": turn.sentence,
                    "outcome": turn.outcome,
                    "plan": turn.plan,
                    "question": turn.question,
                }
                for turn in conversation.turns
            ],
            "visual_contexts": [
                _context_payload(context) for context in conversation.visual_contexts
            ],
            "activity_offers": _offers_payload(conversation.activity_offers),
            "last_plan": plan_payload(conversation.last_plan) if conversation.last_plan else None,
            "prior_plan": (
                plan_payload(conversation.prior_plan) if conversation.prior_plan else None
            ),
            "retry_plan": (
                plan_payload(conversation.retry_plan) if conversation.retry_plan else None
            ),
            "last_ratio_kind": (conversation.last_ratio.kind if conversation.last_ratio else None),
            "last_ratio_part": (
                plan_payload(conversation.last_ratio.part) if conversation.last_ratio else None
            ),
            "last_ratio_whole": (
                plan_payload(conversation.last_ratio.whole) if conversation.last_ratio else None
            ),
            "pending_ratio_kind": (
                conversation.pending_ratio.kind if conversation.pending_ratio else None
            ),
            "pending_ratio_part": (
                plan_payload(conversation.pending_ratio.part)
                if conversation.pending_ratio
                else None
            ),
            "pending_ratio_whole": (
                plan_payload(conversation.pending_ratio.whole)
                if conversation.pending_ratio
                else None
            ),
            "last_comparison_earlier": (
                plan_payload(conversation.last_comparison.earlier)
                if conversation.last_comparison
                else None
            ),
            "last_comparison_latest": (
                plan_payload(conversation.last_comparison.latest)
                if conversation.last_comparison
                else None
            ),
            **_active_payload(conversation.active),
        }
    )


def _legacy_projection_of(
    reference: ResultReference | None,
) -> tuple[str | None, GroupBy | None, str | None, list[UUID]]:
    """Project only a canonical reference; stale/absent values become an empty legacy wire."""
    if reference is None:
        return None, None, None, []
    category = reference.category
    group_by = reference.group_by
    group_key = reference.group_key
    if category is not None and group_by is None:
        group_by, group_key = GroupBy.CATEGORY, category
    elif group_by is GroupBy.CATEGORY and category is None:
        category = group_key
    return category, group_by, group_key, list(reference.transaction_ids)


def _ratio_plan(payload: ConversationPayload) -> RatioPlan | None:
    if payload.last_ratio_kind is None:
        return None
    assert payload.last_ratio_part is not None and payload.last_ratio_whole is not None
    part = query_to_domain(payload.last_ratio_part)
    whole = query_to_domain(payload.last_ratio_whole)
    if not isinstance(part, FinancialPlan) or not isinstance(whole, FinancialPlan):
        raise ValueError("ratio operands must be aggregate plans")
    return RatioPlan(part=part, whole=whole, kind=payload.last_ratio_kind)


def _comparison_plan(payload: ConversationPayload) -> ComparisonPlan | None:
    if payload.last_comparison_earlier is None:
        return None
    assert payload.last_comparison_latest is not None
    earlier = query_to_domain(payload.last_comparison_earlier)
    latest = query_to_domain(payload.last_comparison_latest)
    if not isinstance(earlier, FinancialPlan) or not isinstance(latest, FinancialPlan):
        raise ValueError("comparison operands must be aggregate plans")
    return ComparisonPlan(earlier=earlier, latest=latest)


def _ratio_draft(payload: ConversationPayload) -> RatioDraft | None:
    if payload.pending_ratio_kind is None:
        return None
    assert payload.pending_ratio_part is not None and payload.pending_ratio_whole is not None
    return RatioDraft(
        part=draft_to_domain(payload.pending_ratio_part),
        whole=draft_to_domain(payload.pending_ratio_whole),
        kind=payload.pending_ratio_kind,
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
