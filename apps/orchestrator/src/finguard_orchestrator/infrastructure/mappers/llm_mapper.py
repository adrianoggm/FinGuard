"""Result ↔ DTO. The only place that knows both shapes.

Queries and commands answer with Results, never with domain objects, so what arrives
here is already flat. This adds the wire format and nothing else.
"""

from dataclasses import replace
from typing import Literal
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
from finguard_orchestrator.application.queries.ask_financial_question import VisualDataset
from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    VisualContext,
    Visualization,
    VisualProfile,
    derive_chart,
)
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
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


def _visualization_to_dto(
    result: AskFinancialQuestionResult,
) -> VisualizationResponse | None:
    dataset = result.visual_dataset
    if dataset is None or not dataset.rows:
        return None

    rows: list[dict[str, object]] = []
    for row in dataset.rows:
        serialized: dict[str, object] = {
            "dimension": row.dimension,
            "value_minor": row.value_minor,
        }
        if row.series is not None:
            serialized["series"] = row.series
        if row.occurred_at is not None:
            serialized["occurred_at"] = row.occurred_at
        rows.append(serialized)

    fields = frozenset.intersection(*(frozenset(row) for row in rows))
    if len(rows) == 1:
        if not result.explicit_visualization:
            return None
        return KpiVisualizationResponse(
            unit="count" if dataset.metric == "COUNT" else "money",
            data=rows,
        )

    spec = _chart_for_dataset(result, fields, len(rows))
    if spec is None or not spec.valid_for(fields):
        return None
    spec = replace(spec, title=_visual_title(result, dataset))
    presented = present(Visualization(spec=spec, data=tuple(rows)))
    chart_id = _chart_id(result, spec)
    unit: Literal["money", "count", "ratio"] = (
        "count"
        if dataset.metric in {"COUNT", "FREQUENCY"}
        else "ratio"
        if dataset.metric == "RATIO_SHARE"
        else "money"
    )
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


def _remember_visual_context(
    result: AskFinancialQuestionResult, visualization: VisualizationResponse | None
) -> AskFinancialQuestionResult:
    if not isinstance(visualization, ChartVisualizationResponse) or result.visual_dataset is None:
        return result
    row_fields = {"dimension", "value_minor"}
    if any(row.series is not None for row in result.visual_dataset.rows):
        row_fields.add("series")
    if any(row.occurred_at is not None for row in result.visual_dataset.rows):
        row_fields.add("occurred_at")
    spec = _chart_for_dataset(result, frozenset(row_fields), len(result.visual_dataset.rows))
    if spec is None:
        return result
    spec = replace(spec, title=_visual_title(result, result.visual_dataset))
    profile = VisualProfile.from_spec(
        spec,
        cardinality=len({row.dimension for row in result.visual_dataset.rows}),
        metric=result.visual_dataset.metric,
        unit=(
            "count"
            if result.visual_dataset.metric in {"COUNT", "FREQUENCY"}
            else "ratio"
            if result.visual_dataset.metric == "RATIO_SHARE"
            else "money"
        ),
        positive=all(row.value_minor > 0 for row in result.visual_dataset.rows),
        mutually_exclusive=all(row.series is None for row in result.visual_dataset.rows),
        verified_total=(
            result.visual_dataset.metric == "RATIO_SHARE"
            or (
                result.total_minor is not None
                and sum(row.value_minor for row in result.visual_dataset.rows) == result.total_minor
            )
        ),
    )
    return replace(
        result,
        conversation=result.conversation.with_visual_context(
            VisualContext(visualization.chart_id, spec, profile)
        ),
    )


def _chart_for_dataset(
    result: AskFinancialQuestionResult,
    fields: frozenset[str],
    row_count: int,
) -> ChartSpec | None:
    dataset = result.visual_dataset
    if dataset is None:
        return None

    if dataset.metric == "RECURRENCE":
        if not {"occurred_at", "dimension", "value_minor"} <= fields:
            return None
        encodings: tuple[ChartEncoding, ...] = (
            ChartEncoding("x", "occurred_at", "temporal", "Fecha"),
            ChartEncoding("y", "dimension", "nominal", "Recurrente"),
            ChartEncoding("tooltip", "occurred_at", "temporal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        )
        if "series" in fields:
            encodings += (ChartEncoding("color", "series", "nominal", "Cadencia"),)
        return ChartSpec(mark="point", encodings=encodings)

    if dataset.metric == "FREQUENCY":
        return ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("x", "dimension", "nominal", "Día"),
                ChartEncoding("y", "value_minor", "quantitative", "Movimientos"),
                ChartEncoding("tooltip", "dimension", "nominal"),
                ChartEncoding("tooltip", "value_minor", "quantitative"),
            ),
            sort="input",
        )

    if dataset.metric == "RATIO_SHARE":
        return ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("x", "value_minor", "quantitative", "Porcentaje"),
                ChartEncoding("color", "dimension", "nominal", "Parte"),
                ChartEncoding("tooltip", "dimension", "nominal"),
                ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
            ),
            stack="normalize",
        )

    group_by = result.plan_context.group_by if result.plan_context is not None else "NONE"
    metric = result.plan_context.metric if result.plan_context is not None else dataset.metric
    # A plan that already cut its rows has answered the question of how many to show.
    ranked = result.plan_context is not None and result.plan_context.group_limit is not None
    spec = derive_chart(
        group_by=group_by,
        metric=metric,
        rows=row_count,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
        plan_limited=ranked,
    )
    if spec is None or "series" not in fields:
        return spec
    return ChartSpec(
        mark=spec.mark,
        encodings=spec.encodings + (ChartEncoding("color", "series", "nominal", "Serie"),),
        stack=spec.stack,
        sort=spec.sort,
        responsive=spec.responsive,
    )


def question_to_dto(
    result: AskFinancialQuestionResult,
    *,
    holder_reference: str | None = None,
    metric_tokens: MetricTokenCodec | None = None,
) -> QuestionResponse:
    visualization = _visualization_to_dto(result)
    result = _remember_visual_context(result, visualization)
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
    ):
        chart_id = (
            visualization.chart_id
            if isinstance(visualization, ChartVisualizationResponse)
            else None
        )
        context = next(
            (
                item
                for item in reversed(result.conversation.visual_contexts)
                if item.chart_id == chart_id
            ),
            None,
        )
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
        suggestions=result.suggestions,
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
