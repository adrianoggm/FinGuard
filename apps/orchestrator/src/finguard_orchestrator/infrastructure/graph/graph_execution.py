"""Counted adapters for the banking reads owned by one graph invocation."""

from dataclasses import replace
from typing import Any
from uuid import UUID

from finguard_orchestrator.application.engine.budgets import (
    MAX_BANK_EXECUTIONS,
    ReadBudgetExhaustedError,
)
from finguard_orchestrator.application.engine.executor import Executed, QueryRun, execute
from finguard_orchestrator.domain.engine.transitions import Work
from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.query_answer import (
    Movement,
    MovementListAnswer,
    QueryAnswer,
)
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.movement_list_plan import MovementListPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.graph.graph_state import GraphContext, TurnGraphState


def record_bank_calls(state: TurnGraphState, ctx: GraphContext) -> int:
    """Persist this step's port calls and include failed calls already counted before execution."""
    calls, ctx.unsaved_calls = state.get("bank_calls", 0) + ctx.unsaved_calls, 0
    ctx.financial_reads = calls
    return calls


async def execute_work(work: Work, state: TurnGraphState, ctx: GraphContext) -> Executed:
    """Run a validated work item through adapters that count every banking attempt."""
    ctx.financial_reads = max(ctx.financial_reads, state.get("bank_calls", 0))
    return await execute(
        work,
        queries=_CountedQueries(ctx.ports.queries, ctx),
        summary=_CountedSummary(ctx.ports.summary, ctx),
        account_id=UUID(state["account_id"]),
        today=ctx.today,
        reporting_timezone=ctx.reporting_timezone,
    )


async def read_by_merchant(
    work: Work, side: FinancialPlan, state: TurnGraphState, ctx: GraphContext
) -> QueryAnswer:
    """Read one comparison operand grouped by merchant for the bounded investigation."""
    plan = replace(
        side,
        group_by=GroupBy.MERCHANT,
        metric=Metric.SUM,
        group_limit=None,
    )
    run = await execute_work(Work("QUERY", plan, work.account_ids), state, ctx)
    assert isinstance(run, QueryRun)
    return run.answer


class _CountedQueries:
    """The query port, counting each call before it is made so failures count too."""

    def __init__(self, inner: FinancialQueryPort, ctx: GraphContext) -> None:
        self._inner, self._ctx = inner, ctx

    async def run(self, plan: FinancialPlan, **kwargs: Any) -> QueryAnswer:
        self._count()
        return await self._inner.run(plan, **kwargs)

    async def ranked(self, ranking: TopMovements, **kwargs: Any) -> Movement | None:
        self._count()
        return await self._inner.ranked(ranking, **kwargs)

    async def list_movements(self, plan: MovementListPlan, **kwargs: Any) -> MovementListAnswer:
        self._count()
        return await self._inner.list_movements(plan, **kwargs)

    def _count(self) -> None:
        _count_read(self._ctx)


class _CountedSummary:
    def __init__(self, inner: AccountSummaryPort, ctx: GraphContext) -> None:
        self._inner, self._ctx = inner, ctx

    async def of(self, account_id: UUID, **kwargs: Any) -> AccountSummary:
        _count_read(self._ctx)
        return await self._inner.of(account_id, **kwargs)


def _count_read(ctx: GraphContext) -> None:
    """Enforce the whole-turn bank cap inside the adapter, including executor subqueries."""
    if ctx.financial_reads >= MAX_BANK_EXECUTIONS:
        raise ReadBudgetExhaustedError(f"{ctx.financial_reads} reads made")
    ctx.unsaved_calls += 1
    ctx.financial_reads += 1
