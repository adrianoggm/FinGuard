"""The two readers that split one window rather than comparing two.

«Desglósame el gasto por categoría» asks for a row per group inside one window. «¿Cuánto gasté en
julio?» names a natural calendar period and asks for the total over it. They are one module because
they are the same reading of a sentence with one thing different — whether a dimension was named —
and because both of them ask `_a_comparison_owns_it` first, for the same reason: a sentence that is
really a comparison must not be answered as a single window with the second operand dropped.

`_natural_period_aggregate_plan` is here rather than in `metrics` on that basis. It names no
calculation; what it reads is a *window*, and what it does with it is what the breakdown reader
does without a dimension.
"""

from __future__ import annotations

import re
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _direction_of,
    _starts_new_financial_scope,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.comparisons import _a_comparison_owns_it
from finguard_orchestrator.application.queries.readers.seam import _recogniser, _stated
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    TransactionType,
)
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

# The clitic that makes a breakdown a follow-up. "Desglosa por cuenta" states its own question;
# "desglósalo por cuenta" says *that one*, and what "that one" is filtered by belongs to it.
_REFERS_TO_THE_QUERY_IN_VIEW: Final = re.compile(
    r"\b(?:desglosa|separa|agrupa|divide|reparte)(?:me)?(?:lo|los|la|las)\b"
)


@_recogniser
def _natural_period_aggregate_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | None:
    """A simple aggregate whose new calendar window and direction are both explicit."""
    if _a_comparison_owns_it(query):
        return None
    window = window_words.natural_period_in(query.sentence, today=query.today)
    if window is None:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    named = category_words.precisely_named_in(query.sentence)
    direction = facts.direction
    if direction is None and re.search(
        r"\bgast|\bpagu|\bcompr|\bcargos?\b|\bme\s+cobr", spanish.folded(query.sentence)
    ):
        direction = Direction.EXPENSE
    if direction is None and named:
        direction = _direction_of(named)
    if direction is None or facts.metric in {
        Metric.AVERAGE,
        Metric.PERIOD_AVERAGE,
        Metric.CUMULATIVE,
        Metric.FREQUENCY,
        Metric.RECURRENCE,
    }:
        return None
    plan = FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named,
        group_by=facts.group_by or GroupBy.NONE,
        metric=facts.metric or Metric.SUM,
        transaction_types=(TransactionType.DIRECT_DEBIT,) if facts.asks_direct_debits else (),
    )
    return explicit_facts.enforce_exclusions(plan, facts)


@_recogniser
def _deterministic_breakdown_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Build the two common breakdowns whose meaning is fully stated by the sentence."""
    if _a_comparison_owns_it(query):
        return None

    # "Desglósalo por cuenta" is not fully stated by the sentence: the *lo* is the query in view,
    # and that query is filtered to the shop the previous answer singled out. This reader builds a
    # fresh breakdown and drops the shop, so it would answer about the whole category — a real
    # figure about a wider question than the one asked.
    #
    # The one tie of the four that runs the other way. Everywhere else the general follow-up stands
    # aside for a reader that knows more about the sentence; here it is the one that knows more,
    # because it keeps what the pronoun is pointing at.
    previous = query.conversation.last_plan
    if (
        _REFERS_TO_THE_QUERY_IN_VIEW.search(spanish.folded(query.sentence))
        and isinstance(previous, FinancialPlan)
        and (previous.counterparties or previous.merchants)
    ):
        return None

    facts = explicit_facts.read(query.sentence, today=query.today)
    named_categories = category_words.precisely_named_in(query.sentence)
    if not (
        facts.asks_top_category
        or facts.asks_account_breakdown
        or (facts.compared_categories and facts.metric is None)
    ):
        return None

    previous = (
        None
        if _starts_new_financial_scope(query.sentence, today=query.today)
        else query.conversation.last_plan
    )
    previous_financial = previous if isinstance(previous, FinancialPlan) else None
    window = facts.window or (
        previous.period or previous.date_range if previous is not None else None
    )
    direction = (
        Direction.EXPENSE
        if facts.asks_top_category or facts.compared_categories
        else (previous.direction if previous is not None else (facts.direction or Direction.ALL))
    )
    group_by = (
        GroupBy.CATEGORY
        if facts.asks_top_category or facts.compared_categories
        else GroupBy.ACCOUNT
    )
    categories = (
        facts.compared_categories
        if facts.compared_categories
        else (
            ()
            if facts.asks_top_category
            else (named_categories or (previous.categories if previous is not None else ()))
        )
    )
    merchants = (
        ()
        if facts.asks_top_category or named_categories
        else (previous.merchants if previous is not None else ())
    )
    merchant_terms = (
        ()
        if facts.asks_top_category or named_categories
        else (previous.merchant_terms if previous is not None else ())
    )
    excluded_categories = (
        tuple(
            category
            for category in previous.excluded_categories
            if category not in named_categories
        )
        if previous is not None
        else ()
    )
    excluded_merchants = (
        () if named_categories else (previous.excluded_merchants if previous is not None else ())
    )
    include_internal = previous_financial.include_internal if previous_financial else False
    internal_only = previous_financial.internal_only if previous_financial else False
    across_accounts = (
        True
        if facts.asks_account_breakdown
        else (previous_financial.across_accounts if previous_financial else False)
    )
    only_account_id = (
        None
        if facts.asks_account_breakdown
        else (previous_financial.only_account_id if previous_financial else None)
    )
    only_account_ids = (
        ()
        if facts.asks_account_breakdown
        else (previous_financial.only_account_ids if previous_financial else ())
    )
    transaction_ids = previous_financial.transaction_ids if previous_financial else ()
    transaction_types = previous_financial.transaction_types if previous_financial else ()
    if window is None:
        return explicit_facts.enforce_exclusions(
            FinancialPlanDraft(
                direction=direction,
                categories=categories,
                excluded_categories=excluded_categories,
                merchants=merchants,
                merchant_terms=merchant_terms,
                excluded_merchants=excluded_merchants,
                group_by=group_by,
                metric=Metric.SUM,
                include_internal=include_internal,
                internal_only=internal_only,
                across_accounts=across_accounts,
                only_account_id=only_account_id,
                only_account_ids=only_account_ids,
                transaction_ids=transaction_ids,
                transaction_types=transaction_types,
            ),
            facts,
        )
    return explicit_facts.enforce_exclusions(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=excluded_categories,
            merchants=merchants,
            merchant_terms=merchant_terms,
            excluded_merchants=excluded_merchants,
            group_by=group_by,
            metric=Metric.SUM,
            include_internal=include_internal,
            internal_only=internal_only,
            across_accounts=across_accounts,
            only_account_id=only_account_id,
            only_account_ids=only_account_ids,
            transaction_ids=transaction_ids,
            transaction_types=transaction_types,
        ),
        facts,
    )


def _asks_for_a_breakdown(query: AskFinancialQuestionQuery) -> bool:
    facts = _stated(query)
    return bool(
        facts.asks_top_category
        or facts.asks_account_breakdown
        or (facts.compared_categories and facts.metric is None)
    )


# The rows for the readers above, and they live here rather than in one table because a row is
# mostly a *precondition*, and a precondition has to read the vocabulary its reader reads. Kept
# somewhere else, the two drift and nothing says so: the reader learns a phrasing, its precondition
# does not, and the sentence is refused before the reader that would have claimed it is asked. That
# failure is silent by construction, which is why the row is here where the pattern it reads is.
#
# `readers/__init__` concatenates these into the one `Registry`, which validates the whole table at
# import and derives the order from it. `stood_at` is unchanged from the cascade this replaced —
# see the note there for why it is not renumbered.
ROWS: Final = (
    Reader(
        name="_natural_period_aggregate_plan",
        read=_natural_period_aggregate_plan,
        stood_at=13,
        when=Precondition(
            "the sentence names a natural calendar period",
            lambda query: (
                window_words.natural_period_in(query.sentence, today=query.today) is not None
            ),
        ),
    ),
    Reader(
        name="_deterministic_breakdown_plan",
        read=_deterministic_breakdown_plan,
        stood_at=14,
        when=Precondition(
            "the sentence asks for a breakdown by category or by account",
            _asks_for_a_breakdown,
        ),
        yields_to=("contextual_follow_up_plan",),
        because=(
            "«hazme el desglose por cuenta» and «sepáralos por cuenta» are both a breakdown "
            "request and an ellipsis, and the ellipsis wins: the follow-up keeps what the "
            "query in view is filtered by, and this reader builds a fresh plan that drops it. "
            "Its own `_REFERS_TO_THE_QUERY_IN_VIEW` guard already stands aside on the "
            "«desglósalo» form; these two are that argument with no clitic to see it by. "
            "Measured on two corpus turns."
        ),
    ),
)
