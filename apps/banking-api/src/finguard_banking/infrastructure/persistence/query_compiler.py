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
from finguard_banking.domain.models.movement_list import MovementListPlan

VIEW = "assistant_read.transactions_v1"

# The ceiling from the domain is the default; a deployment may only narrow it.
DEFAULT_GROUP_LIMIT = MAX_GROUPS
UNKNOWN_COUNTERPARTY = "UNKNOWN_COUNTERPARTY"


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
        case GroupBy.COUNTERPARTY:
            return f"COALESCE({_counterparty_name_expression()}, '{UNKNOWN_COUNTERPARTY}')"
        case GroupBy.ACCOUNT:
            return "COALESCE(a.display_name, t.account_id::text)"


def _counterparty_name_expression() -> str:
    """The same transparent fallback used by the result mapper, expressed in SQL."""
    return "COALESCE(NULLIF(BTRIM(t.merchant_name), ''), NULLIF(BTRIM(t.description), ''))"


def _counterparty_type_expression() -> str:
    """Classify from the ledger-native transaction type, never from a person's name."""
    return """CASE t.transaction_type
        WHEN 'CARD_PAYMENT' THEN 'MERCHANT'
        WHEN 'BIZUM' THEN 'PERSON'
        WHEN 'SALARY' THEN 'EMPLOYER'
        WHEN 'DIRECT_DEBIT' THEN 'BILLER'
        WHEN 'TRANSFER' THEN 'ACCOUNT'
        WHEN 'INTERNAL_TRANSFER' THEN 'ACCOUNT'
        WHEN 'ACCOUNT_OPENING' THEN 'BANK'
        ELSE 'UNKNOWN'
    END"""


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

    if query.transaction_ids:
        selected_transactions = parameters.bind(list(query.transaction_ids))
        conditions.append(f"t.transaction_id = ANY({selected_transactions})")
    if query.transaction_types:
        transaction_types = parameters.bind([item.value for item in query.transaction_types])
        conditions.append(f"t.transaction_type = ANY({transaction_types})")

    if query.internal_only:
        conditions.append("t.is_internal")
    elif not query.include_internal:
        # Before the direction, because it is the wider cut and reads in the order somebody
        # would say it: money that left the household, then which way it went.
        conditions.append("NOT t.is_internal")

    if query.direction is not Direction.ALL:
        conditions.append(f"t.direction = {parameters.bind(query.direction.value)}")

    if query.categories:
        conditions.append(f"t.category = ANY({parameters.bind(query.category_values())})")

    if query.excluded_categories:
        excluded = parameters.bind(query.excluded_category_values())
        # NULL means uncategorised, not one of the excluded categories. SQL's `NOT IN`-style
        # semantics would otherwise turn it into UNKNOWN and silently discard the row.
        conditions.append(f"(t.category IS NULL OR NOT (t.category = ANY({excluded})))")

    if query.merchants:
        # Lowered on both sides rather than ILIKE: this is equality, not a pattern, and a
        # name the user typed must not be read as one — "100%" is a legitimate shop name and
        # a wildcard in LIKE. Case-insensitive because a filter that needs the ledger's exact
        # capitalisation answers "no movements" to a real question, which reads as "you spent
        # nothing there".
        names = parameters.bind([merchant.strip().lower() for merchant in query.merchants])
        conditions.append(f"lower(t.merchant_name) = ANY({names})")

    merchant_terms = getattr(query, "merchant_terms", ())
    if merchant_terms:
        # Each fragment is a bound value, never a LIKE pattern: '%' remains a literal percent and
        # cannot widen the search. Terms are OR alternatives, so apuesta/bet finds either family.
        contains = [
            "strpos(lower(COALESCE(t.merchant_name, '')), "
            f"{parameters.bind(term.strip().lower())}) > 0"
            for term in merchant_terms
        ]
        conditions.append(f"({' OR '.join(contains)})")

    if query.excluded_merchants:
        names = parameters.bind([merchant.strip().lower() for merchant in query.excluded_merchants])
        conditions.append(
            f"(t.merchant_name IS NULL OR NOT (lower(t.merchant_name) = ANY({names})))"
        )

    counterparties = getattr(query, "counterparties", ())
    excluded_counterparties = getattr(query, "excluded_counterparties", ())
    counterparty_types = getattr(query, "counterparty_types", ())
    if counterparties:
        names = parameters.bind([value.strip().lower() for value in counterparties])
        conditions.append(f"lower({_counterparty_name_expression()}) = ANY({names})")

    if excluded_counterparties:
        names = parameters.bind([value.strip().lower() for value in excluded_counterparties])
        expression = _counterparty_name_expression()
        conditions.append(f"({expression} IS NULL OR NOT (lower({expression}) = ANY({names})))")

    if counterparty_types:
        types = parameters.bind([item.value for item in counterparty_types])
        conditions.append(f"({_counterparty_type_expression()}) = ANY({types})")

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
    # A running total is incorrect when a deployment's presentation limit drops calendar buckets.
    # The period itself is bounded, so returning every bucket remains bounded by MAX_GROUPS.
    effective_limit = (
        MAX_GROUPS if query.metric is Metric.CUMULATIVE else min(group_limit, MAX_GROUPS)
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
        LIMIT {effective_limit}
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
            t.transaction_type,
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


def compile_movement_list(plan: MovementListPlan) -> CompiledQuery:
    """Compile a bounded newest-first ledger page with a stable keyset cursor."""
    parameters = _Parameters()
    conditions: list[str] = []
    if plan.date_from is not None and plan.date_to is not None:
        tz = parameters.bind(plan.reporting_timezone)
        start = parameters.bind(plan.date_from)
        end = parameters.bind(plan.date_to)
        conditions.extend(
            [
                f"t.occurred_at >= (({start}::date)::timestamp AT TIME ZONE {tz})",
                f"t.occurred_at < (({end}::date + 1)::timestamp AT TIME ZONE {tz})",
            ]
        )
    if plan.account_ids:
        conditions.append(f"t.account_id = ANY({parameters.bind(list(plan.account_ids))})")
    elif not plan.across_accounts:
        conditions.append(f"t.account_id = {parameters.bind(plan.account_id)}")
    if plan.direction is not Direction.ALL:
        conditions.append(f"t.direction = {parameters.bind(plan.direction.value)}")
    if plan.categories:
        conditions.append(
            f"t.category = ANY({parameters.bind([item.value for item in plan.categories])})"
        )
    if plan.merchant_terms:
        terms = [
            "strpos(lower(COALESCE(t.merchant_name, '')), "
            f"{parameters.bind(term.strip().lower())}) > 0"
            for term in plan.merchant_terms
        ]
        conditions.append(f"({' OR '.join(terms)})")
    if not plan.include_internal:
        conditions.append("NOT t.is_internal")
    if plan.cursor is not None:
        timestamp = parameters.bind(plan.cursor.occurred_at)
        transaction_id = parameters.bind(plan.cursor.transaction_id)
        conditions.append(
            f"(t.occurred_at < {timestamp} OR (t.occurred_at = {timestamp} "
            f"AND t.transaction_id > {transaction_id}))"
        )
    limit = parameters.bind(plan.page_size + 1)
    # Every movement of every account, transfers included, names no condition at all: the view is
    # what scopes it to the holder, and the clause must still be valid SQL.
    sql = f"""
        SELECT t.transaction_id, t.occurred_at, t.description, t.merchant_name,
               t.category, t.category_is_custom, t.category_source, t.is_internal,
               t.transaction_type, t.direction, t.amount_minor, b.currency
        FROM {VIEW} t
        JOIN assistant_read.account_balances_v1 b ON b.account_id = t.account_id
        WHERE {" AND ".join(conditions) or "TRUE"}
        ORDER BY t.occurred_at DESC, t.transaction_id ASC
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
            t.transaction_type,
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
