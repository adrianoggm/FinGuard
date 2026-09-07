"""Result ↔ DTO. The only place that knows both shapes.

Queries and commands answer with Results, never with domain objects, so what arrives
here is already flat. This adds the wire format and nothing else.
"""

import functools
from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from typing import Literal, cast
from uuid import UUID, uuid4

from finguard_orchestrator.application import (
    AccountSummaryResult,
    AnswerMessageResult,
    AskFinancialQuestionResult,
    BreakdownResult,
    CatalogueResult,
    ChargesResult,
    ListContactsResult,
    ModelRow,
    MovementsResult,
    PolicyRow,
    SpokenPlan,
    WalletResult,
)
from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    VisualContext,
    VisualDataset,
    Visualization,
    VisualRow,
)
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.models.visual_plan import canonical, draft_of
from finguard_orchestrator.domain.services.visual_interpreter import (
    apply_visual_intent,
    interpret_visual,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    AccountSummaryResponse,
    BreakdownResponse,
    CardPayload,
    CatalogueResponse,
    ChargePayload,
    ChargesResponse,
    ChartVisualizationResponse,
    ContactPayload,
    ContactsResponse,
    GroupPayload,
    HoldingPayload,
    KpiVisualizationResponse,
    MessageResponse,
    ModelResponse,
    MovementsResponse,
    PlanPayload,
    PolicyResponse,
    QuestionResponse,
    SaveCandidate,
    SpokenContactResponse,
    SpokenGroupResponse,
    SpokenMovementResponse,
    SummaryMovementResponse,
    VisualizationResponse,
    WalletResponse,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.metrics.codec import CodecError
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def _model_to_dto(row: ModelRow) -> ModelResponse:
    return ModelResponse(alias=row.alias, provider=row.provider, model=row.model)


def _policy_to_dto(row: PolicyRow) -> PolicyResponse:
    return PolicyResponse(
        model=_model_to_dto(row.model),
        temperature=row.temperature,
        max_output_tokens=row.max_output_tokens,
    )


def catalogue_to_dto(result: CatalogueResult) -> CatalogueResponse:
    return CatalogueResponse(
        active=_policy_to_dto(result.active),
        fallback=result.fallback,
        available_models=[_model_to_dto(row) for row in result.available_models],
        available_providers=result.available_providers,
    )


def answer_to_dto(result: AnswerMessageResult) -> MessageResponse:
    return MessageResponse(
        text=result.text,
        policy=PolicyResponse(
            model=ModelResponse(
                alias=result.model_alias, provider=result.provider, model=result.model
            ),
            temperature=result.temperature,
            max_output_tokens=result.max_output_tokens,
        ),
        attempted=list(result.attempted),
    )


def plan_to_domain(payload: PlanPayload) -> FinancialPlan:
    """The echoed plan, rebuilt as a plan.

    Pydantic has already refused anything that is not one of the vocabulary's values;
    the domain now refuses the combinations that are individually valid and jointly
    wrong — both ends of a range, neither window, `n` on a period that has no count.
    An InvalidValueError from here is a 422, which is the honest answer: what came back
    is not something this service ever handed out.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlan(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        excluded_categories=tuple(payload.excluded_categories),
        merchants=tuple(payload.merchants),
        merchant_terms=tuple(payload.merchant_terms),
        excluded_merchants=tuple(payload.excluded_merchants),
        counterparties=tuple(payload.counterparties),
        excluded_counterparties=tuple(payload.excluded_counterparties),
        counterparty_types=tuple(payload.counterparty_types),
        group_by=payload.group_by,
        metric=payload.metric,
        group_limit=payload.group_limit,
        include_internal=payload.include_internal,
        internal_only=payload.internal_only,
        across_accounts=payload.across_accounts,
        only_account_id=payload.only_account_id,
        only_account_ids=tuple(payload.only_account_ids),
        transaction_ids=tuple(payload.transaction_ids),
        transaction_types=tuple(payload.transaction_types),
    )


def draft_to_domain(payload: PlanPayload) -> FinancialPlanDraft:
    """The echoed draft, rebuilt as a draft.

    The same payload as a plan and a different type on the way out: a draft is allowed to
    carry no window, which is the one invariant a plan will not relax. Everything else is
    checked identically — Pydantic has already refused values outside the vocabulary, and
    two windows at once is as wrong here as it is there.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlanDraft(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        excluded_categories=tuple(payload.excluded_categories),
        merchants=tuple(payload.merchants),
        merchant_terms=tuple(payload.merchant_terms),
        excluded_merchants=tuple(payload.excluded_merchants),
        counterparties=tuple(payload.counterparties),
        excluded_counterparties=tuple(payload.excluded_counterparties),
        counterparty_types=tuple(payload.counterparty_types),
        group_by=payload.group_by,
        metric=payload.metric,
        group_limit=payload.group_limit,
        include_internal=payload.include_internal,
        internal_only=payload.internal_only,
        across_accounts=payload.across_accounts,
        only_account_id=payload.only_account_id,
        only_account_ids=tuple(payload.only_account_ids),
        transaction_ids=tuple(payload.transaction_ids),
        transaction_types=tuple(payload.transaction_types),
    )


def _plan_to_dto(plan: SpokenPlan) -> PlanPayload:
    return PlanPayload.model_validate(
        {
            "direction": plan.direction,
            "period": plan.period,
            "n": plan.n,
            "start": plan.start,
            "end": plan.end,
            "categories": plan.categories,
            "excluded_categories": plan.excluded_categories,
            "merchants": plan.merchants,
            "merchant_terms": plan.merchant_terms,
            "excluded_merchants": plan.excluded_merchants,
            "counterparties": plan.counterparties,
            "excluded_counterparties": plan.excluded_counterparties,
            "counterparty_types": plan.counterparty_types,
            "group_by": plan.group_by,
            "metric": plan.metric,
            "group_limit": plan.group_limit,
            "include_internal": plan.include_internal,
            "internal_only": plan.internal_only,
            "across_accounts": plan.across_accounts,
            "only_account_id": plan.only_account_id,
            "only_account_ids": plan.only_account_ids,
            "transaction_ids": plan.transaction_ids,
            "transaction_types": plan.transaction_types,
        }
    )


# A row's attributes in wire order. Read from the dataclass, never hand-copied: these are the
# names `VisualProfile.fields` can declare, so the two cannot drift.
_ROW_FIELDS: tuple[str, ...] = tuple(item.name for item in dataclass_fields(VisualRow))


def rows_on_the_wire(dataset: VisualDataset, declared: frozenset[str]) -> list[dict[str, object]]:
    """Every row carries every field in `declared`, `None` where the row has no value."""
    return [
        {name: getattr(row, name) for name in _ROW_FIELDS if name in declared}
        for row in dataset.rows
    ]


def _visualization_to_dto(
    result: AskFinancialQuestionResult,
) -> VisualizationResponse | None:
    dataset = result.visual_dataset
    if dataset is None or not dataset.rows:
        return None
    if len(dataset.rows) == 1:
        if not result.explicit_visualization:
            return None
        # A KPI has no decision and no profile: it carries the fields its one row has.
        only = dataset.rows[0]
        present_fields = frozenset(name for name in _ROW_FIELDS if getattr(only, name) is not None)
        return KpiVisualizationResponse(
            unit="count" if dataset.metric == "COUNT" else "money",
            data=rows_on_the_wire(dataset, present_fields),
        )

    decision = result.visual_decision
    if decision is None:
        return None
    # Rendered, not decided. The chart was chosen by the application, once, on a profile built
    # from this same dataset; the rows carry exactly the fields that profile declared, so
    # `valid_for` checks the spec against the set the model and the verifier reasoned over --
    # the intersection this replaced dropped charts the verifier had accepted.
    declared = decision.profile.field_names()
    if not decision.spec.valid_for(declared):
        return None
    rows = rows_on_the_wire(dataset, declared)
    spec = replace(decision.spec, title=_visual_title(result, dataset))
    presented = present(Visualization(spec=spec, data=tuple(rows)))
    chart_id = _chart_id(result, spec)
    # Unit is a domain decision made while building the profile. Re-inferring it here from the
    # overloaded dataset marker loses COUNT comparisons (`metric="COMPARISON"`).
    unit = cast(Literal["money", "count", "ratio"], decision.profile.unit)
    return ChartVisualizationResponse(
        chart_id=chart_id,
        unit=unit,
        spec=presented["spec"],
        data=presented["data"],
        limit=spec.limit,
    )


def _chart_id(result: AskFinancialQuestionResult, spec: ChartSpec) -> str:
    return "chart-" + uuid4().hex[:16]


def _visual_title(result: AskFinancialQuestionResult, dataset: VisualDataset) -> str:
    """Return a short, stable Spanish title; never leak planner prose into Vega."""
    context = result.plan_context
    direction = {
        "EXPENSE": "Gastos",
        "INCOME": "Ingresos",
        "ALL": "Movimientos",
    }.get(context.direction if context is not None else "", "Importes")
    grouping = context.group_by if context is not None else ""
    dimension = {
        "MONTH": "mes",
        "WEEK": "semana",
        "DAY": "día",
        "CATEGORY": "categoría",
        "MERCHANT": "comercio",
        "COUNTERPARTY": "persona",
        "ACCOUNT": "cuenta",
    }.get(grouping, "periodo")
    period = ""
    if context is not None:
        if context.period == "LAST_N_MONTHS" and context.n:
            period = f"Últimos {context.n} meses"
        elif context.period == "LAST_N_DAYS" and context.n:
            period = f"Últimos {context.n} días"
        elif context.period == "THIS_MONTH":
            period = "Este mes"
        elif context.period == "LAST_MONTH":
            period = "Mes pasado"
        elif context.period == "THIS_YEAR":
            period = "Este año"
    if dataset.metric == "FREQUENCY":
        subject = "Frecuencia de movimientos"
    elif dataset.metric == "COUNT":
        subject = "Movimientos"
    elif dataset.metric == "AVERAGE":
        subject = {
            "Gastos": "Gasto medio",
            "Ingresos": "Ingreso medio",
        }.get(direction, "Importe medio")
    elif dataset.metric == "CUMULATIVE":
        subject = {
            "Gastos": "Gasto acumulado",
            "Ingresos": "Ingreso acumulado",
        }.get(direction, "Importe acumulado")
    elif dataset.metric == "RECURRENCE":
        subject = "Gastos recurrentes"
    elif dataset.metric == "RATIO_SHARE":
        subject = "Distribución"
    else:
        subject = direction
    grouped = "" if dataset.metric == "RECURRENCE" else f" por {dimension}"
    return f"{subject}{grouped}" + (f" · {period}" if period else "")


def _evidence_of(plan: FinancialPlan | ComparisonPlan | RatioPlan | None) -> tuple[UUID, ...]:
    """The movements behind the figures, when the plan names them. Only FinancialPlan does."""
    return plan.transaction_ids if isinstance(plan, FinancialPlan) else ()


def _remember_visual_context(
    result: AskFinancialQuestionResult,
    visualization: VisualizationResponse | None,
    *,
    seal: Callable[[VisualContext], VisualContext] | None = None,
) -> AskFinancialQuestionResult:
    if not isinstance(visualization, ChartVisualizationResponse) or result.visual_dataset is None:
        return result
    decision = result.visual_decision
    if decision is None:
        # Unreachable, and a narrowing rather than a fallback: the only `ChartVisualizationResponse`
        # this module builds is the one above, and it is built only when a decision exists. If this
        # ever returns early with a chart in hand, the bug is upstream -- do not decide here.
        return result
    # The decision's own profile, not a second one built from the same dataset. That second build
    # is what this function used to do, and it omitted `plan_limited` and `plan_metric`, so the
    # profile stored on the conversation disagreed with the one the chart was chosen from.
    spec = replace(decision.spec, title=_visual_title(result, result.visual_dataset))
    # The newest chart BEFORE this turn's is appended -- read now, because `with_visual_context`
    # below appends the new one and would otherwise name itself.
    previous = (
        result.conversation.visual_contexts[-1] if result.conversation.visual_contexts else None
    )
    derived_from = (
        previous.chart_id
        if (
            result.visual_follow_up is not None
            and result.visual_follow_up.kind == "ANALYTICAL_RESHAPE"
            and previous is not None
        )
        else None
    )
    context = VisualContext(
        visualization.chart_id,
        spec,
        decision.profile,
        decision=decision,
        source_plan=result.metric_definition,
        evidence_ids=_evidence_of(result.metric_definition),
        turn_id=uuid4(),
        derived_from=derived_from,
    )
    # Task 3: sealed here, where the context is born -- before it ever reaches `to_payload` --
    # rather than by a second pass over the wire once the response is built. `seal` is `None` for
    # every caller with no holder to seal for (every existing call in this codebase's tests), and
    # the context stays exactly as it always has: unsealed, `trusted=True` in-process.
    if seal is not None:
        context = seal(context)
    return replace(
        result,
        conversation=result.conversation.with_visual_context(context),
    )


def _chart_suggestions(context: VisualContext) -> list[str]:
    """Visual follow-ups this chart will accept, written the way the interpreter reads them.

    Deterministic ones only. `interpret_visual` is a regex over an allow-list, but a sentence
    that parses is not a chip that survives: every candidate below is filtered through the same
    gate `_visual_only_response` (`llm_controller.py`) runs on a holder's own sentence --
    `interpret_visual(chip, (context,))`, merged with `apply_visual_intent`, then judged by
    `verify(draft_of(merged.spec), context.profile)`. A chip that gets refused is worse than no
    chip: it teaches somebody that the chips lie, and then the ones that work go untried too. One
    definition of "will this be accepted" -- imported from where the controller imports it, never
    copied -- is what keeps that true after the gate changes and this generator does not.

    Filters are the obvious omission and they are omitted on measurement, not on suspicion.
    `excluded_categories` and `excluded_merchants` are on the plan and banking honours them, and the
    planner reads them correctly when the exclusion arrives in the same sentence as the question.
    A chip is the other shape: the sentence is nothing but the filter, with the chart's plan behind
    it. `evaluation/planning/metamorphic.yaml` now asks exactly that, and over three runs with no
    spread "Sin el alquiler" after a breakdown of last month by category drops both the window and
    the grouping and plans for today -- a figure, not a clarification. A chip for it is a button
    that shows a wrong number, so there is no chip until the planner keeps the window.

    Task 3: a chip whose target is not trusted offers nothing, full stop -- every candidate below
    would still be filtered through `_chip_verifies`, which calls `_visual_only_response`'s own
    gate on a context nobody has vouched for. That gate now refuses one outright (`VISUAL_UNTRUSTED`
    before any merge), so every chip would silently vanish anyway; this says so up front instead of
    relying on that refusal to happen to produce the same empty list.
    """
    if not context.trusted:
        return []
    spec = context.spec
    candidates = []
    if spec.limit is not None:
        candidates.append("Ver todos")
    candidates.append("Ponlo en línea" if spec.mark == "bar" else "Ponlo en barras")
    temporal = any(item.type == "temporal" for item in spec.encodings if item.channel in {"x", "y"})
    # Sorting a series by its measure shuffles time, which is not a reordering of anything. Both
    # directions are offered here (F2, fix wave 2): which one is a no-op depends on the axis's own
    # sort, not on `spec.sort` alone (F1's own shape leaves that `None` even when an axis is
    # already ranked), and `_chip_verifies`'s canonical-equality check below is what actually
    # drops whichever direction changes nothing -- this generator no longer has to guess it.
    if not temporal:
        candidates.append("Ordena de menor a mayor")
        candidates.append("Ordena de mayor a menor")
    return [chip for chip in candidates if _chip_verifies(chip, context)]


def _chip_verifies(chip: str, context: VisualContext) -> bool:
    """Whether offering `chip` over `context` is a change the controller would actually accept.

    A chip whose intent does not parse (`interpret_visual` returns `None`, or a `VisualIntent`
    with no patch -- an explanation only), whose merge `apply_visual_intent` refuses outright, or
    whose merged spec draws identically to what is already on screen (F2: the same
    canonical-equality the controller's own `VISUAL_UNCHANGED` answer uses) is dropped the same
    as one the verifier refuses: none of the four is a chip worth offering.
    """
    intent = interpret_visual(chip, (context,))
    if intent is None or intent.patch is None:
        return False
    try:
        merged = apply_visual_intent(intent, context)
    except ValueError:
        return False
    if canonical(merged.spec) == canonical(context.spec):
        return False
    return isinstance(verify(draft_of(merged.spec), context.profile), ChartSpec)


def question_to_dto(
    result: AskFinancialQuestionResult,
    *,
    holder_reference: str | None = None,
    metric_tokens: MetricTokenCodec | None = None,
    offer_saving: bool = False,
) -> QuestionResponse:
    """Fix round 1 (Task 3): `holder_reference`/`metric_tokens` and `offer_saving` answer two
    different questions, and conflating them is what sent the corpus red. The first pair is "can
    this chart be sealed for its holder" -- unconditional in both routes, because a context drawn
    with metrics off (a voice turn, a deployment with `ORCHESTRATOR_METRICS_ENABLED=false`, this
    codebase's own conversation-corpus harness) still needs a seal or every later restyle of it is
    refused as untrusted, and `app.state.metric_tokens` always exists regardless of the flag. The
    second, `offer_saving`, is "may this turn also offer to keep the answer as a saved metric" --
    still exactly the routes' own pre-Task-3 gate (`metrics_on and request.channel == "TEXT"`), now
    named and passed explicitly instead of silently riding along on the first two.
    """
    visualization = _visualization_to_dto(result)
    # The seal is built whenever both are given, with no dependency on `offer_saving`: a fresh
    # chart is signed for its holder the same turn it is drawn, whether or not this turn may also
    # offer to save it.
    seal = (
        functools.partial(
            conversation_mapper.seal_context, holder_reference=holder_reference, codec=metric_tokens
        )
        if holder_reference is not None and metric_tokens is not None
        else None
    )
    result = _remember_visual_context(result, visualization, seal=seal)
    chart_id = (
        visualization.chart_id if isinstance(visualization, ChartVisualizationResponse) else None
    )
    context = next(
        (
            item
            for item in reversed(result.conversation.visual_contexts)
            if item.chart_id == chart_id
        ),
        None,
    )
    candidate = None
    # What makes an answer worth keeping is that it came from a plan and produced a figure. It used
    # to be that it came with a chart, which meant "cuanto he gastado este mes" -- one number, and
    # the most obvious metric anybody would save -- was the one answer with no way to save it. The
    # figure is also what keeps a payment preview out: those carry the last plan too.
    answered_a_figure = result.total_minor is not None or result.movement_count is not None
    if (
        isinstance(result.metric_definition, FinancialPlan)
        and answered_a_figure
        and holder_reference is not None
        and metric_tokens is not None
        and offer_saving
    ):
        # No chart is not the same as a chart the conversation has forgotten: the first saves a
        # figure, the second would save a spec that is no longer the one on screen.
        if context is not None or chart_id is None:
            try:
                token = metric_tokens.issue(
                    holder_reference,
                    SavedMetricDefinition(
                        result.metric_definition, context.spec if context else None
                    ),
                )
            except CodecError:
                pass
            else:
                candidate = SaveCandidate(chart_id=chart_id, definition_token=token)
    return QuestionResponse(
        outcome=result.outcome,
        text=result.text,
        question=result.question,
        plan=result.plan,
        conversation=conversation_mapper.to_payload(result.conversation),
        contacts=[
            SpokenContactResponse(
                contact_id=UUID(contact.contact_id),
                display_name=contact.display_name,
                phone=contact.phone,
            )
            for contact in result.contacts
        ],
        currency=result.currency,
        total_minor=result.total_minor,
        difference_minor=result.difference_minor,
        movement_count=result.movement_count,
        groups=[
            SpokenGroupResponse(
                key=group.key,
                total_minor=group.total_minor,
                movement_count=group.movement_count,
                value_minor=group.value_minor,
            )
            for group in result.groups
        ],
        movements=[
            SpokenMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                direction=movement.direction,
                merchant_name=movement.merchant_name,
                category=movement.category,
                transaction_type=movement.transaction_type,
                counterparty_name=movement.counterparty_name,
                counterparty_type=movement.counterparty_type,
                receipt_category=movement.receipt_category,
            )
            for movement in result.movements
        ],
        visualization=visualization,
        # Never over the planner's own: those recover a question that did not land, and a chip about
        # bar charts does not belong next to "¿de qué periodo?".
        suggestions=result.suggestions or (_chart_suggestions(context) if context else []),
        save_candidate=candidate,
    )


def summary_to_dto(result: AccountSummaryResult) -> AccountSummaryResponse:
    return AccountSummaryResponse(
        currency=result.currency,
        booked_minor=result.booked_minor,
        available_minor=result.available_minor,
        as_of=result.as_of,
        movements=[
            SummaryMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                merchant_name=movement.merchant_name,
                category=movement.category,
            )
            for movement in result.movements
        ],
        holdings=[
            HoldingPayload(
                account_id=holding.account_id,
                kind=holding.kind,
                display_name=holding.display_name,
                currency=holding.currency,
                booked_minor=holding.booked_minor,
            )
            for holding in result.holdings
        ],
    )


def contacts_to_dto(result: ListContactsResult) -> ContactsResponse:
    return ContactsResponse(
        contacts=[
            ContactPayload(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                phone=contact.phone,
            )
            for contact in result.contacts
        ]
    )


def movements_to_dto(result: MovementsResult) -> MovementsResponse:
    return MovementsResponse(
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        movements=[
            SummaryMovementResponse(
                occurred_at=movement.occurred_at,
                description=movement.description,
                amount_minor=movement.amount_minor,
                merchant_name=movement.merchant_name,
                category=movement.category,
                transaction_id=movement.transaction_id,
                category_is_custom=movement.category_is_custom,
                category_source=movement.category_source,
                is_internal=movement.is_internal,
            )
            for movement in result.movements
        ],
    )


def breakdown_to_dto(result: BreakdownResult) -> BreakdownResponse:
    return BreakdownResponse(
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        groups=[
            GroupPayload(
                key=group.key,
                total_minor=group.total_minor,
                movement_count=group.movement_count,
            )
            for group in result.groups
        ],
    )


def wallet_to_dto(result: WalletResult) -> WalletResponse:
    return WalletResponse(
        since=result.since,
        cards=[
            CardPayload(
                card_id=card.card_id,
                display_name=card.display_name,
                brand=card.brand,
                kind=card.kind,
                last_four=card.last_four,
                expires=card.expires,
                status=card.status,
                spending_limit_minor=card.spending_limit_minor,
                spent_minor=card.spent_minor,
                charge_count=card.charge_count,
            )
            for card in result.cards
        ],
    )


def charges_to_dto(result: ChargesResult) -> ChargesResponse:
    return ChargesResponse(
        card_id=result.card_id,
        since=result.since,
        charges=[
            ChargePayload(
                transaction_id=charge.transaction_id,
                occurred_at=charge.occurred_at,
                description=charge.description,
                merchant_name=charge.merchant_name,
                category=charge.category,
                amount_minor=charge.amount_minor,
            )
            for charge in result.charges
        ],
    )
