"""What a turn asks the bank, run through the banking ports and nothing else.

Every figure comes from `FinancialQueryPort` or `AccountSummaryPort`. The only arithmetic here is
over figures banking returned: the change between the two sides of a pair, in the unit the pair
measures (`answer_writer.comparison_value`, the same function the rules route uses). Turning any
of it into words is the presenter's.

The scope is applied here, once, to every plan a work carries: an empty selection reads every
account, a selection narrows to exactly those. The ids come from grounding against the holder's
own account directory, never from the model.
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Final, assert_never
from uuid import UUID

from finguard_orchestrator.domain.constants import MAX_SUPPORTING_MOVEMENTS
from finguard_orchestrator.domain.engine.transitions import Work
from finguard_orchestrator.domain.enums import Metric, ResultKind
from finguard_orchestrator.domain.errors import DomainError
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.query_answer import (
    Movement,
    MovementListAnswer,
    QueryAnswer,
)
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.services import answer_writer, cumulative_calculator
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.movement_list_plan import MovementListPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# Movements beside each side of a pair or a ratio: a handful as evidence, as on the rules route.
_EVIDENCE: Final = 5
# Answers that are read off the movements themselves (when something happens, what recurs) need
# all of them, not a sample: a pattern drawn from ten of sixty charges is a guess.
_READ_FROM_MOVEMENTS: Final = frozenset({Metric.FREQUENCY, Metric.RECURRENCE})


class MixedCurrenciesError(DomainError):
    """Two figures to relate came back in different currencies, so there is no change or share
    between them to state."""

    code = "mixed_currencies"


@dataclass(frozen=True)
class QueryRun:
    plan: FinancialPlan
    answer: QueryAnswer


@dataclass(frozen=True)
class RankingRun:
    """The `ranking.rank` largest movements, largest first; fewer when the window holds fewer."""

    ranking: TopMovements
    movements: tuple[Movement, ...]


@dataclass(frozen=True)
class MovementListRun:
    plan: MovementListPlan
    answer: MovementListAnswer


@dataclass(frozen=True)
class NoMoreMovementsRun:
    """The active ledger list has no cursor; answer without repeating a bank read."""


@dataclass(frozen=True)
class PairRun:
    pair: ComparisonPlan
    earlier: QueryAnswer
    latest: QueryAnswer
    # latest minus earlier, in the unit the pair measures (movements for a count).
    difference: int


@dataclass(frozen=True)
class RatioRun:
    ratio: RatioPlan
    part: QueryAnswer
    whole: QueryAnswer


@dataclass(frozen=True)
class BalanceRun:
    summary: AccountSummary
    # None is every account; otherwise the holdings named, as the directory holds them.
    selected: tuple[Holding, ...] | None


Executed = (
    QueryRun | RankingRun | MovementListRun | NoMoreMovementsRun | PairRun | RatioRun | BalanceRun
)


async def execute(
    work: Work,
    *,
    queries: FinancialQueryPort,
    summary: AccountSummaryPort,
    account_id: UUID,
    today: date,
    reporting_timezone: str,
) -> Executed:
    """Run `work` and return what banking answered, with every plan as it was run.

    Raises what the ports raise (`BankingUnavailableError`, `QueryRejectedError`) and
    `MixedCurrenciesError`; nothing is retried here.
    """

    async def run(plan: FinancialPlan, movements: int | None = None) -> QueryAnswer:
        return await queries.run(
            plan,
            account_id=account_id,
            today=today,
            reporting_timezone=reporting_timezone,
            movements=movements,
        )

    ids = work.account_ids
    if work.kind == "NO_MORE_MOVEMENTS":
        return NoMoreMovementsRun()
    match work.plan:
        case None:
            found = await summary.of(account_id, today=today, reporting_timezone=reporting_timezone)
            selected = tuple(h for h in found.holdings if h.account_id in ids) if ids else None
            return BalanceRun(found, selected)
        case FinancialPlan() as asked:
            plan = _scoped(asked, ids)
            answer = await run(
                plan,
                MAX_SUPPORTING_MOVEMENTS
                if plan.metric in _READ_FROM_MOVEMENTS or plan.result_kind is ResultKind.RECORDS
                else None,
            )
            if plan.metric is Metric.CUMULATIVE:
                answer = cumulative_calculator.accumulate(answer, plan.group_by)
            return QueryRun(plan, answer)
        case MovementListPlan() as plan:
            page = await queries.list_movements(
                plan,
                account_id=account_id,
                today=today,
                reporting_timezone=reporting_timezone,
            )
            return MovementListRun(plan, page)
        case TopMovements() as top:
            # Phase 1 reads a rank as "the N largest" (ruling R5), one position at a time as the
            # rules route does, and stops at the first position the window does not hold.
            ranking = _scoped(top, ids)
            movements: list[Movement] = []
            for rank in range(1, ranking.rank + 1):
                movement = await queries.ranked(
                    ranking.at_rank(rank),
                    account_id=account_id,
                    today=today,
                    reporting_timezone=reporting_timezone,
                )
                if movement is None:
                    break
                movements.append(movement)
            return RankingRun(ranking, tuple(movements))
        case ComparisonPlan() as compared:
            pair = ComparisonPlan(_scoped(compared.earlier, ids), _scoped(compared.latest, ids))
            earlier = await run(pair.earlier, _EVIDENCE)
            latest = await run(pair.latest, _EVIDENCE)
            _one_currency(earlier, latest, pair.metric)
            value = answer_writer.comparison_value
            difference = value(latest, pair.latest) - value(earlier, pair.earlier)
            return PairRun(pair, earlier, latest, difference)
        case RatioPlan() as related:
            ratio = RatioPlan(_scoped(related.part, ids), _scoped(related.whole, ids), related.kind)
            part = await run(ratio.part, _EVIDENCE)
            whole = await run(ratio.whole, _EVIDENCE)
            _one_currency(part, whole, ratio.part.metric)
            return RatioRun(ratio, part, whole)
        case _:
            assert_never(work.plan)


def _scoped[P: (FinancialPlan, TopMovements)](plan: P, account_ids: tuple[UUID, ...]) -> P:
    """`plan` read from the accounts selected, or from every account when none is."""
    return replace(
        plan,
        across_accounts=not account_ids,
        only_account_id=None,
        only_account_ids=account_ids,
    )


def _one_currency(first: QueryAnswer, second: QueryAnswer, metric: Metric) -> None:
    """A count is movements in any currency; an amount is not comparable across two."""
    if metric is not Metric.COUNT and first.currency != second.currency:
        raise MixedCurrenciesError(f"{first.currency} and {second.currency}")
