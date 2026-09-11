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
    ConversationPayload,
    PaymentDraftPayload,
    PlanPayload,
    SpokenContactResponse,
    VisualContextPayload,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    draft_to_domain,
    plan_to_domain,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.observability import name_the_conversation

# D-3b-4 (fix wave): named once, used by both `_visual_contexts` and `to_domain` below -- the two
# signatures have to move together by definition (one is the closure the other threads through to
# it), and a hand-written copy at each site is exactly the kind of pair this module's other
# comments already warn drifts apart.
type ContextVerifier = Callable[[str | None, dict[str, object]], bool]

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
    verify: ContextVerifier | None = None,
) -> tuple[VisualContext, ...]:
    if payload is None:
        return ()
    result: list[VisualContext] = []
    for item in payload.visual_contexts:
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
        # What still narrows, and why it is safe to: `cardinality` is the one dataset-level number
        # the wire carries (the donut rule's), copied onto every field below rather than kept per
        # field, so whichever field `role("dimension")` lands on -- the true dimension, or an old
        # payload's series misread as one -- re-emits that same count. `row_count` and
        # `plan_metric` are not on the wire at all: neither `valid_for`, `merge_encoding_patch` nor
        # `verify` reads either, and a count of rows is not something a browser should be able to
        # assert. The floor never runs on a round-tripped profile regardless -- it runs on one
        # built fresh from the dataset in the same turn.
        profile = VisualProfile(
            fields=tuple(
                VisualField(
                    field.name,
                    field.type,
                    field.role
                    if field.role is not None
                    else ("measure" if field.type == "quantitative" else "dimension"),
                    item.profile.cardinality,
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
            row_count=0,
            plan_metric=None,
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
        trusted = verify(item.token, context_semantics(item)) if verify is not None else True
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
                labels=tuple(item.labels),
                series_labels=tuple(item.series_labels),
                ranked=item.ranked,
                window=window,
                plan_digest=item.plan_digest,
                source=item.source,
                fallback_reason=item.fallback_reason,
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
                }
                for field in context.profile.fields
            ],
            # The wire payload keeps one dataset-level count, same as before the profile carried
            # per-field cardinality. It is the nominal dimension's -- what `_limit` and the donut
            # rule both key off of (`_limit` reads its `order`, the donut rule its `cardinality`),
            # never "the field that lands in `color`": in a comparison that field is the series,
            # not the dimension. It is 0 whenever the chart's dimension is temporal rather than
            # nominal -- read that as "not a donut's business", never as "no rows": the rows are
            # still all there.
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
    context: VisualContext, holder_reference: str, codec: MetricTokenCodec
) -> VisualContext:
    """`context`, sealed for `holder_reference`: same wire shape `to_payload` would emit, signed.

    Serialised through `_context_payload` -- the same dict `to_payload` builds -- and validated
    through `VisualContextPayload.model_validate` before it is sealed, so the digest covers the
    pydantic-normalised shape a caller's echoed payload will also be read through at verification,
    never the domain object's own, unvalidated, representation of itself.
    """
    payload = VisualContextPayload.model_validate(_context_payload(context))
    return replace(context, token=codec.seal_context(holder_reference, context_semantics(payload)))


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
    checked.
    """
    conversation_id = _identified(payload)
    if payload is None:
        return Conversation(conversation_id=conversation_id)
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
            visual_contexts=_visual_contexts(payload, verify=verify),
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
        visual_contexts=_visual_contexts(payload, verify=verify),
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
        case _:
            return None


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
    return {}
