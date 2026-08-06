"""Browsing an account: the movement list, and the breakdown a finance screen draws.

Both build their plan **here, in code**. A screen asking "last month by category" has already said
everything the plan needs — window, grouping and filter are all in the request — so putting a model
between the tap and the query would add a way to be wrong and nothing else. A model earns its place
when the input is a sentence, and these are not sentences.

What that buys, concretely: the numbers on the accounts screen and the numbers in the conversation
come from the same compiler over the same ledger, and only one of the two paths can misread a
question.

The bounds are here rather than in the route because they are the domain's: a window longer than a
year and a list longer than a page are not things a screen asks for, and a route that accepted them
would be handing banking a plan it will refuse.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

# What a screen may ask for. Both are ceilings, not defaults: the caller says what it wants and this
# refuses to pass on something banking would reject anyway.
MAX_DAYS = 366
MAX_MOVEMENTS = 100


@dataclass(frozen=True)
class BrowsedMovement:
    occurred_at: str
    description: str
    amount_minor: int
    merchant_name: str | None = None
    category: str | None = None
    # The ledger's identifier, and whose category is being shown. The movement list is the one
    # screen that can refile a movement, so it is the one that needs both.
    transaction_id: str | None = None
    category_is_custom: bool = False
    category_source: str = "NONE"
    is_internal: bool = False


@dataclass(frozen=True)
class BrowsedGroup:
    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class MovementsResult:
    currency: str
    total_minor: int
    movement_count: int
    movements: list[BrowsedMovement] = field(default_factory=list)


@dataclass(frozen=True)
class BreakdownResult:
    currency: str
    total_minor: int
    movement_count: int
    groups: list[BrowsedGroup] = field(default_factory=list)


@dataclass(frozen=True)
class GetMovementsQuery(Query[MovementsResult]):
    """The account's movements, optionally narrowed.

    `category` and `merchant` are what the search field sends. A merchant is free text because the
    ledger's shop names are data rather than vocabulary; a category is an identifier and banking
    refuses one it does not have, which is the drift detector doing its job.
    """

    account_id: UUID
    today: date
    days: int
    limit: int
    reporting_timezone: str = "Europe/Madrid"
    category: str | None = None
    merchant: str | None = None
    # Money the holder moved between their own accounts. Off by default, because the tally beside
    # the list is a net and a transfer nets to nothing across the household. On, the list shows them
    # tagged — hiding a 200 € transfer means the movements do not reconcile with the balance, and
    # somebody looking for where it went cannot find it. ADR-009 §2 left this open for a screen.
    include_internal: bool = False
    # Which account the list is about. `None` is the credential's, which is what every caller got
    # before there were several; `across` is all of them. Whether a named one is the holder's is
    # not decided here — banking answers from the view that listed them, so an account that is not
    # theirs returns nothing rather than being refused.
    only_account_id: UUID | None = None
    across_accounts: bool = False


@dataclass(frozen=True)
class GetBreakdownQuery(Query[BreakdownResult]):
    """Where the money went, grouped. `EXPENSE` by default because that is the question a finance
    screen is asked; income grouped by category is one row called SALARY."""

    account_id: UUID
    today: date
    days: int
    group_by: GroupBy
    reporting_timezone: str = "Europe/Madrid"
    direction: Direction = Direction.EXPENSE


def _window(days: int) -> RelativePeriod:
    return RelativePeriod(period=Period.LAST_N_DAYS, n=min(max(days, 1), MAX_DAYS))


class GetMovementsHandler:
    def __init__(self, queries: FinancialQueryPort) -> None:
        self._queries = queries

    async def handle(self, query: GetMovementsQuery) -> MovementsResult:
        plan = FinancialPlan(
            # ALL, because a movement list shows what happened. A list filtered to expenses would
            # quietly hide the salary that explains the balance.
            direction=Direction.ALL,
            period=_window(query.days),
            categories=(query.category,) if query.category else (),
            merchants=(query.merchant,) if query.merchant else (),
            group_by=GroupBy.NONE,
            metric=Metric.SUM,
            include_internal=query.include_internal,
            across_accounts=query.across_accounts,
            only_account_id=query.only_account_id,
        )
        answer = await self._queries.run(
            plan,
            # The credential's account, always. Which one the figures are *about* travels on the
            # plan instead — sending it here would have banking declare it, and declaring somebody
            # else's account opens their scope rather than filtering within yours.
            account_id=query.account_id,
            today=query.today,
            reporting_timezone=query.reporting_timezone,
            # The screen's count, not the chat's. Before this the limit was accepted and dropped,
            # so a list asking for fifty got whatever the conversation was configured with.
            movements=query.limit,
        )
        return MovementsResult(
            currency=answer.currency,
            total_minor=answer.total_minor,
            movement_count=answer.movement_count,
            movements=[
                BrowsedMovement(
                    occurred_at=movement.occurred_at.isoformat(),
                    description=movement.description,
                    amount_minor=movement.amount_minor,
                    merchant_name=movement.merchant_name,
                    category=movement.category,
                    transaction_id=movement.transaction_id,
                    category_is_custom=movement.category_is_custom,
                    category_source=movement.category_source,
                    is_internal=movement.is_internal,
                )
                for movement in answer.movements
            ],
        )


class GetBreakdownHandler:
    def __init__(self, queries: FinancialQueryPort) -> None:
        self._queries = queries

    async def handle(self, query: GetBreakdownQuery) -> BreakdownResult:
        plan = FinancialPlan(
            direction=query.direction,
            period=_window(query.days),
            group_by=query.group_by,
            metric=Metric.SUM,
        )
        answer = await self._queries.run(
            plan,
            account_id=query.account_id,
            today=query.today,
            reporting_timezone=query.reporting_timezone,
        )
        return BreakdownResult(
            currency=answer.currency,
            total_minor=answer.total_minor,
            movement_count=answer.movement_count,
            groups=[
                BrowsedGroup(
                    key=group.key,
                    total_minor=group.total_minor,
                    movement_count=group.movement_count,
                )
                for group in answer.groups
            ],
        )
