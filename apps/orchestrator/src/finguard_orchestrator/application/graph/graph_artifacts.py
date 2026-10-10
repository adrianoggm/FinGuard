"""Pure conversions between executed bank results and canonical graph artifacts."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from finguard_orchestrator.application.engine.executor import (
    Executed,
    MovementListRun,
    PairRun,
    QueryRun,
    RankingRun,
)
from finguard_orchestrator.application.graph.graph_state import TurnGraphState
from finguard_orchestrator.application.queries.financial_turn import SpokenPlan
from finguard_orchestrator.domain.engine.state import MovementListArtifact
from finguard_orchestrator.domain.enums import GroupBy, ResultKind
from finguard_orchestrator.domain.models.chart import VisualDataset, VisualRow
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer, category_words
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


def pair_run_from_state(state: TurnGraphState, pair: ComparisonPlan) -> PairRun:
    """Rebuild a comparison from checkpointed figures after a graph-node resume."""
    groups = state["comparison_groups"]
    counts = state["comparison_counts"]
    earlier = QueryAnswer(
        state["currency"],
        state["totals"][0],
        counts[0],
        groups=tuple(Group(*group) for group in groups[0]),
    )
    latest = QueryAnswer(
        state["currency"],
        state["totals"][1],
        counts[1],
        groups=tuple(Group(*group) for group in groups[1]),
    )
    latest_value = answer_writer.comparison_value(latest, pair.latest)
    earlier_value = answer_writer.comparison_value(earlier, pair.earlier)
    difference = latest_value - earlier_value
    return PairRun(pair, earlier, latest, difference)


def context_plan(run: Executed | None) -> FinancialPlan | TopMovements | None:
    """The executed query reduced to the safe fields sent with its answer."""
    if isinstance(run, QueryRun):
        return run.plan
    if isinstance(run, RankingRun):
        return run.ranking
    if isinstance(run, PairRun):
        return run.pair.latest
    return None


def artifact_ids(state: TurnGraphState) -> tuple[str, str]:
    """Stable across retries and unique per committed canonical query version."""
    query_id = uuid5(
        NAMESPACE_URL,
        f"finguard-query:{state['conversation_id']}:{state['expected_version'] + 1}",
    ).hex
    return query_id, uuid5(NAMESPACE_URL, f"finguard-artifact:{query_id}").hex


def artifact_result_kind(plan: FinancialPlan) -> ResultKind:
    if plan.result_kind is not None:
        return plan.result_kind
    if plan.group_by is GroupBy.MONTH:
        return ResultKind.TIME_SERIES
    if plan.group_by is not GroupBy.NONE:
        return ResultKind.BREAKDOWN
    return ResultKind.SCALAR


def movement_list_artifact(
    run: MovementListRun,
    *,
    result_version: int,
    query_id: str,
    artifact_id: str,
) -> MovementListArtifact | None:
    """Retain only a bounded chart summary of the exact movement page returned by banking."""
    movements = run.answer.movements
    if not movements:
        return None
    currencies = {item.currency for item in movements}
    if len(currencies) != 1:
        return None
    currency = next(iter(currencies))
    if currency is None:
        return None
    if all(item.category for item in movements):
        group_by = GroupBy.CATEGORY

        def label_of(item: Any) -> str:
            return category_words.name_of(str(item.category))

    elif all(item.merchant_name or item.counterparty_name for item in movements):
        group_by = GroupBy.MERCHANT

        def label_of(item: Any) -> str:
            return str(item.merchant_name or item.counterparty_name)

    else:
        return None

    directions = {item.direction for item in movements}
    grouped: dict[tuple[str, str | None], int] = {}
    for item in movements:
        label = label_of(item)
        if not label or len(label) > 256:
            return None
        series = (
            {"EXPENSE": "Gastos", "INCOME": "Ingresos"}.get(item.direction, item.direction)
            if len(directions) > 1
            else None
        )
        key = (label, series)
        grouped[key] = grouped.get(key, 0) + item.amount_minor
    if len(grouped) > 100:
        return None
    dimension = "categoría" if group_by is GroupBy.CATEGORY else "comercio"
    dataset = VisualDataset(
        rows=tuple(
            VisualRow(dimension=label, value_minor=value, series=series)
            for (label, series), value in grouped.items()
        ),
        metric="SUM",
        title=f"Importe por {dimension} en esta página ({len(movements)} movimientos)",
    )
    return MovementListArtifact(
        dataset=dataset,
        currency=currency,
        movement_count=len(movements),
        group_by=group_by,
        result_version=result_version,
        query_id=query_id,
        artifact_id=artifact_id,
    )


def spoken_plan(plan: FinancialPlan | TopMovements) -> SpokenPlan:
    """Create the safe public plan trace from a bank-executed plan."""
    period = plan.period
    date_range = plan.date_range
    common: dict[str, Any] = {
        "direction": plan.direction.value,
        "period": period.period.value if period is not None else None,
        "n": period.n if period is not None else None,
        "start": date_range.start.isoformat() if date_range is not None else None,
        "end": date_range.end.isoformat() if date_range is not None else None,
        "categories": list(plan.categories),
        "merchants": list(plan.merchants),
        "merchant_terms": list(plan.merchant_terms),
        "excluded_merchants": list(plan.excluded_merchants),
        "across_accounts": plan.across_accounts,
        "only_account_id": str(plan.only_account_id) if plan.only_account_id is not None else None,
        "only_account_ids": [str(item) for item in plan.only_account_ids],
    }
    if isinstance(plan, TopMovements):
        return SpokenPlan(**common, rank=plan.rank, result_kind="RANKING")
    return SpokenPlan(
        **common,
        excluded_categories=list(plan.excluded_categories),
        counterparties=list(plan.counterparties),
        excluded_counterparties=list(plan.excluded_counterparties),
        counterparty_types=[item.value for item in plan.counterparty_types],
        group_by=plan.group_by.value,
        metric=plan.metric.value,
        result_kind=plan.result_kind.value if plan.result_kind is not None else None,
        group_limit=plan.group_limit,
        include_internal=plan.include_internal,
        internal_only=plan.internal_only,
        transaction_ids=[str(item) for item in plan.transaction_ids],
        transaction_types=[item.value for item in plan.transaction_types],
    )
