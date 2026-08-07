"""Compiles a validated query plan into parameterised SQL.

Rule from vision.md §9.3: SQL produced by the LLM is never executed. Here the shape
of the SQL comes exclusively from enums — closed sets defined in code — and every
value coming from outside travels as a parameter. Not one piece of user data is
concatenated into the string.

It reads the `assistant_read.transactions_v1` view, never the tables. What counts as
booked and which sign each entry carries are decisions of the view, not of this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finguard_banking.domain.constants import MAX_GROUPS, UNCATEGORISED, UNKNOWN_MERCHANT
from finguard_banking.domain.enums import Direction, GroupBy, Metric
from finguard_banking.domain.models.financial_query import FinancialQuery

VIEW = "assistant_read.transactions_v1"

# The ceiling from the domain is the default; a deployment may only narrow it.
DEFAULT_GROUP_LIMIT = MAX_GROUPS


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: list[Any]


class _Parameters:
    """Numbers the placeholders and keeps their values in the same order."""

    def __init__(self) -> None:
        self._values: list[Any] = []

    def bind(self, value: Any) -> str:
        self._values.append(value)
        return f"${len(self._values)}"

    @property
    def values(self) -> list[Any]:
        return list(self._values)


def _group_expression(group_by: GroupBy, tz: str) -> str:
    match group_by:
        case GroupBy.NONE:
            return "'total'"
        case GroupBy.DAY:
            return f"to_char(t.occurred_at AT TIME ZONE {tz}, 'YYYY-MM-DD')"
        case GroupBy.WEEK:
            return f"to_char(date_trunc('week', t.occurred_at AT TIME ZONE {tz}), 'YYYY-MM-DD')"
        case GroupBy.MONTH:
            return f"to_char(t.occurred_at AT TIME ZONE {tz}, 'YYYY-MM')"
        case GroupBy.CATEGORY:
            return f"COALESCE(t.category, '{UNCATEGORISED}')"
        case GroupBy.MERCHANT:
            return f"COALESCE(t.merchant_name, '{UNKNOWN_MERCHANT}')"
        case GroupBy.ACCOUNT:
            return "COALESCE(a.display_name, t.account_id::text)"


def _group_ordering(group_by: GroupBy, metric: Metric, direction: Direction) -> str:
    """Which groups survive the limit.

    This was `ORDER BY 1`, which with a LIMIT keeps the groups whose *key* sorts first.
    For a category or a merchant that is alphabetical, so "¿en qué me he gastado más?"
    returned the first five names and presented them as the largest. A wrong answer with
    no error anywhere, and the demo's opening question.

    Derived from the plan rather than carried in the contract, deliberately. The correct
    ordering is a function of what was asked in every case the dataset has, so deriving it
    is always right, while three new contract fields would be three more things for a model
    to choose wrongly. `limit` is the separable part — "los tres comercios donde más gasté"
    needs it — and it can arrive when top-N is actually wanted.
    """
    if group_by in (GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH):
        # Time is the one grouping whose key carries the meaning. A month is not more
        # interesting for being expensive, and a series out of order is not a series.
        return "key"
    if metric is Metric.COUNT:
        return "movement_count DESC"
    if metric is Metric.AVERAGE:
        # Use the aggregate directly. PostgreSQL accepts an output alias as a bare
        # ORDER BY item, but not embedded inside another expression.
        return f"AVG({_amount_expression(direction)}) DESC"
    return "total_minor DESC"


def _amount_expression(direction: Direction) -> str:
    if direction is Direction.ALL:
        # With both directions mixed, only a signed total makes sense.
        return "t.signed_amount_minor"
    # In a single direction the amount is positive: "I spent 34.20", not "-34.20".
    return "t.amount_minor"


def _conditions(query: FinancialQuery, parameters: _Parameters, tz: str) -> list[str]:
    start = parameters.bind(query.date_range.start)
    end = parameters.bind(query.date_range.end)

    conditions = [
        # The range includes the whole final day, in the reporting time zone.
        f"t.occurred_at >= (({start}::date)::timestamp AT TIME ZONE {tz})",
        f"t.occurred_at < (({end}::date + 1)::timestamp AT TIME ZONE {tz})",
    ]

    if query.only_account_ids:
        selected = parameters.bind(list(query.only_account_ids))
        conditions.append(f"t.account_id = ANY({selected})")
    elif not query.across_accounts:
        # The same fact enforced twice from two independent places: the session declared this
        # account and the compiler filters on it, so a compiler that dropped its WHERE would still
        # see only the holder's rows.
        #
        # Asking across accounts drops *this* filter and nothing else. The view's own scope is what
        # remains, which is the rule written once — there is no list of accounts to pass and so no
        # way for this to reach past the holder. `across_accounts` cannot widen; it can only stop
        # narrowing.
        conditions.append(f"t.account_id = {parameters.bind(query.scoped_to)}")

    if not query.include_internal:
        # Before the direction, because it is the wider cut and reads in the order somebody
        # would say it: money that left the household, then which way it went.
        conditions.append("NOT t.is_internal")

    if query.direction is not Direction.ALL:
        conditions.append(f"t.direction = {parameters.bind(query.direction.value)}")

    if query.categories:
        conditions.append(f"t.category = ANY({parameters.bind(query.category_values())})")

    if query.merchants:
        # Lowered on both sides rather than ILIKE: this is equality, not a pattern, and a
        # name the user typed must not be read as one — "100%" is a legitimate shop name and
        # a wildcard in LIKE. Case-insensitive because a filter that needs the ledger's exact
        # capitalisation answers "no movements" to a real question, which reads as "you spent
        # nothing there".
        names = parameters.bind([merchant.strip().lower() for merchant in query.merchants])
        conditions.append(f"lower(t.merchant_name) = ANY({names})")

    return conditions


def compile_aggregate(
    query: FinancialQuery, reporting_timezone: str, group_limit: int = DEFAULT_GROUP_LIMIT
) -> CompiledQuery:
    parameters = _Parameters()
    tz = parameters.bind(reporting_timezone)
    conditions = _conditions(query, parameters, tz)
    # The transaction view remains the authority for which movements are visible. This
    # holder-scoped join adds only the account's display name for an account breakdown.
    account_join = (
        "LEFT JOIN assistant_read.my_accounts_v1 a ON a.account_id = t.account_id"
        if query.group_by is GroupBy.ACCOUNT
        else ""
    )

    sql = f"""
        SELECT
            {_group_expression(query.group_by, tz)} AS key,
            COALESCE(SUM({_amount_expression(query.direction)}), 0) AS total_minor,
            COUNT(*) AS movement_count
        FROM {VIEW} t
        {account_join}
        WHERE {" AND ".join(conditions)}
        GROUP BY 1
        ORDER BY {_group_ordering(query.group_by, query.metric, query.direction)}
        LIMIT {min(group_limit, MAX_GROUPS)}
    """
    return CompiledQuery(sql=sql, parameters=parameters.values)


def compile_movements(query: FinancialQuery, reporting_timezone: str) -> CompiledQuery:
    parameters = _Parameters()
    tz = parameters.bind(reporting_timezone)
    conditions = _conditions(query, parameters, tz)
    limit = parameters.bind(query.supporting_limit)

    sql = f"""
        SELECT
            t.transaction_id,
            t.occurred_at,
            COALESCE(t.description, '') AS description,
            t.merchant_name,
            t.category,
            t.category_is_custom,
            t.category_source,
            t.is_internal,
            {_amount_expression(query.direction)} AS amount_minor,
            t.direction
        FROM {VIEW} t
        WHERE {" AND ".join(conditions)}
        ORDER BY t.occurred_at DESC, t.transaction_id
        LIMIT {limit}
    """
    return CompiledQuery(sql=sql, parameters=parameters.values)


def compile_ranked(query: FinancialQuery, reporting_timezone: str, *, rank: int) -> CompiledQuery:
    """The nth largest movement of the period, or nothing when the period has fewer than n.

    A different statement from `compile_movements` in two places: the ordering, and the offset. It
    shares `_conditions` with everything else here, which is the point — the filters of "the largest
    expense in fuel this year" are exactly the filters of "how much did I spend on fuel this year",
    and two copies of them would be two places for the account to be forgotten.

    **The ordering is total.** `amount_minor DESC` alone leaves ties to the plan, so "el segundo"
    would be either of two equal charges and could differ between two runs of the same question. The
    date breaks the tie and the identifier breaks the rest.

    `rank` becomes an OFFSET and travels as a parameter, like every other value here. It is a number
    a model chose, and §9.3 does not care how harmless a number looks.
    """
    parameters = _Parameters()
    tz = parameters.bind(reporting_timezone)
    conditions = _conditions(query, parameters, tz)
    offset = parameters.bind(max(0, rank - 1))

    sql = f"""
        SELECT
            t.transaction_id,
            t.occurred_at,
            COALESCE(t.description, '') AS description,
            t.merchant_name,
            t.category,
            t.category_is_custom,
            t.category_source,
            t.is_internal,
            {_amount_expression(query.direction)} AS amount_minor,
            t.direction
        FROM {VIEW} t
        WHERE {" AND ".join(conditions)}
        ORDER BY {_ranking_ordering(query.direction)}, t.occurred_at DESC, t.transaction_id
        LIMIT 1 OFFSET {offset}
    """
    return CompiledQuery(sql=sql, parameters=parameters.values)


def _ranking_ordering(direction: Direction) -> str:
    """Which movement is "the largest" when both directions are in scope.

    In one direction the amount is already positive, so `DESC` is the size order. With `ALL` the
    amount is signed, and `DESC` then puts the biggest *income* first — so "¿cuál fue mi movimiento
    más grande?" answered with a salary while a larger charge sat below zero. Magnitude is what
    "más grande" means about a movement whose direction was never narrowed.
    """
    if direction is Direction.ALL:
        return "ABS(amount_minor) DESC"
    return "amount_minor DESC"
