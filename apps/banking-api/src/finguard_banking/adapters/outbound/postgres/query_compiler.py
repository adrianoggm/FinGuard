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

from finguard_banking.domain.financial_query import Direction, FinancialQuery, GroupBy

VIEW = "assistant_read.transactions_v1"

# Cap on rows returned per query (vision.md §8.6).
MAX_GROUPS = 500


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
            # Contract values, so English: the assistant is the one that phrases
            # them for a Spanish-speaking user.
            return "COALESCE(t.category, 'UNCATEGORISED')"
        case GroupBy.MERCHANT:
            return "COALESCE(t.merchant_name, 'UNKNOWN_MERCHANT')"


def _amount_expression(direction: Direction) -> str:
    if direction is Direction.ALL:
        # With both directions mixed, only a signed total makes sense.
        return "t.signed_amount_minor"
    # In a single direction the amount is positive: "I spent 34.20", not "-34.20".
    return "t.amount_minor"


def _conditions(query: FinancialQuery, parameters: _Parameters, tz: str) -> list[str]:
    account = parameters.bind(query.account_id)
    start = parameters.bind(query.date_range.start)
    end = parameters.bind(query.date_range.end)

    conditions = [
        f"t.account_id = {account}",
        # The range includes the whole final day, in the reporting time zone.
        f"t.occurred_at >= (({start}::date)::timestamp AT TIME ZONE {tz})",
        f"t.occurred_at < (({end}::date + 1)::timestamp AT TIME ZONE {tz})",
    ]

    if query.direction is not Direction.ALL:
        conditions.append(f"t.direction = {parameters.bind(query.direction.value)}")

    if query.categories:
        conditions.append(f"t.category = ANY({parameters.bind(list(query.categories))})")

    return conditions


def compile_aggregate(query: FinancialQuery, reporting_timezone: str) -> CompiledQuery:
    parameters = _Parameters()
    tz = parameters.bind(reporting_timezone)
    conditions = _conditions(query, parameters, tz)

    sql = f"""
        SELECT
            {_group_expression(query.group_by, tz)} AS key,
            COALESCE(SUM({_amount_expression(query.direction)}), 0) AS total_minor,
            COUNT(*) AS movement_count
        FROM {VIEW} t
        WHERE {" AND ".join(conditions)}
        GROUP BY 1
        ORDER BY 1
        LIMIT {MAX_GROUPS}
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
            {_amount_expression(query.direction)} AS amount_minor,
            t.direction
        FROM {VIEW} t
        WHERE {" AND ".join(conditions)}
        ORDER BY t.occurred_at DESC, t.transaction_id
        LIMIT {limit}
    """
    return CompiledQuery(sql=sql, parameters=parameters.values)
