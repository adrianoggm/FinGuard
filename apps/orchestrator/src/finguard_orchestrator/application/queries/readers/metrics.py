"""The four readers that name the calculation: a running total, a recurrence, a rate, a mean.

The grouping is not a judgement call here — it is `Metric`. These four are one-to-one with the
enum's members, and three of their preconditions are literally `_stated(query).metric is
Metric.X`. A fifth metric would arrive as a fifth reader in this module and nowhere else.

They also share the shape of their hardest case, which is the follow-up: «¿y por semana?» over a
running total and «¿y en gasolina?» over an average are the same question asked of two different
calculations, and both of them are contested with `follow_up`'s reader — `_cumulative_plan` and
`_average_plan` are two of the three readers that yield to it. Keeping them together keeps that
argument in one place; the rows say so, and `_average_follow_up_plan` is the code that does it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _clean_turn,
    _direction_of,
    _starts_new_financial_scope,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.comparisons import _a_comparison_owns_it
from finguard_orchestrator.application.queries.readers.dimensions import (
    _named_group_dimension,
)
from finguard_orchestrator.application.queries.readers.seam import _recogniser, _stated
from finguard_orchestrator.domain.constants import (
    RECURRING_EXPENSE_CATEGORIES,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.models.conversation import (
    PlannedQuery,
)
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    spanish,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

_AVERAGE_PERIOD_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:el\s+|la\s+)?(?:"
    r"(?:por|al|cada)\s+dia|(?:por|a\s+la|cada)\s+semana|(?:por|al|cada)\s+mes"
    r"|diari[oa]|semanal|semanl|semnal|mensual(?:mente)?|mensula|mesnual"
    r")$"
)


_NATURAL_PERIOD_AVERAGE_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:eso\s+)?cuanto\s+(?:sale|seria|es)\s+"
    r"(?:por|al|a\s+la|cada)\s+(?P<unit>dia|semana|mes)$"
)


_AVERAGE_MOVEMENT_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:por\s+(?:movimiento|operacion|compra|pago|cargo|ingreso)s?"
    r"|(?:el\s+)?ticket\s+medio)$"
)


@_recogniser
def _frequency_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """A complete cadence question over a grounded category, with a useful default horizon."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.metric is not Metric.FREQUENCY:
        return None
    categories = category_words.precisely_named_in(query.sentence)
    if not categories and re.search(
        r"\b(?:cuando|que\s+dia|cada\s+cuanto)\b.{0,45}"
        r"\b(?:me\s+pagan|pagarme|cobro|cobrar)\b",
        spanish.folded(query.sentence),
    ):
        categories = ("SALARY",)
    if not categories:
        # Free merchant names remain on the grounded planner path. Treating every unknown noun as
        # a shop here would turn "cuándo gasto" into one invented merchant filter.
        return None
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=facts.direction or _direction_of(categories),
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=categories,
        metric=Metric.FREQUENCY,
    )


@_recogniser
def _cumulative_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Build a running calendar total without asking the model to reinterpret arithmetic."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    previous = query.conversation.last_plan
    previous_financial = previous if isinstance(previous, FinancialPlan) else None
    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    unit_follow_up = (
        previous_financial is not None
        and previous_financial.metric is Metric.CUMULATIVE
        and facts.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
        and folded.startswith(("y ", "ahora ", "por "))
    )
    if facts.metric is not Metric.CUMULATIVE and not unit_follow_up:
        return None
    # A running total accumulates over calendar buckets, so the grouping *is* the bucket and the
    # domain refuses a non-temporal one beside CUMULATIVE. "Gasto acumulado por comercio" states
    # both, and this built the forbidden plan: the constructor below raised straight out of the
    # handler, and an ordinary Spanish sentence came back 422 invalid_input. Unlike the average
    # family, the capability guard does not refuse "acumulado", and it runs after this recogniser
    # anyway — so declining here is what keeps the sentence answerable at all.
    #
    # Declining rather than dropping the grouping, which is the same rule `_average_plan` already
    # follows below: this path exists to answer what it is sure of, and a request for a running
    # total broken down by shop needs two grouping axes where a plan has one.
    if facts.group_by is not None and facts.group_by not in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        return None
    contextual = not _starts_new_financial_scope(query.sentence, today=query.today)
    if not contextual and not unit_follow_up:
        previous_financial = None
    named = category_words.precisely_named_in(query.sentence)
    if re.search(r"\bingres|\bcobr|\bnomina", folded):
        direction = Direction.INCOME
    elif re.search(r"\bgast|\bpag|\bcompr|\bcargo", folded):
        direction = Direction.EXPENSE
    elif named:
        direction = _direction_of(named)
    elif previous_financial is not None:
        direction = previous_financial.direction
    else:
        direction = Direction.ALL
    window = facts.window or (
        previous_financial.period or previous_financial.date_range
        if previous_financial is not None
        else RelativePeriod(Period.THIS_YEAR)
    )
    return FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (previous_financial.categories if previous_financial else ()),
        excluded_categories=(previous_financial.excluded_categories if previous_financial else ()),
        merchants=previous_financial.merchants if previous_financial else (),
        merchant_terms=previous_financial.merchant_terms if previous_financial else (),
        excluded_merchants=previous_financial.excluded_merchants if previous_financial else (),
        group_by=facts.group_by or GroupBy.MONTH,
        metric=Metric.CUMULATIVE,
        include_internal=previous_financial.include_internal if previous_financial else False,
        internal_only=previous_financial.internal_only if previous_financial else False,
        across_accounts=previous_financial.across_accounts if previous_financial else False,
        only_account_id=previous_financial.only_account_id if previous_financial else None,
        only_account_ids=previous_financial.only_account_ids if previous_financial else (),
        transaction_types=previous_financial.transaction_types if previous_financial else (),
    )


@_recogniser
def _recurrence_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Discover periodic expenses from a conservative, complete category scope."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.metric is not Metric.RECURRENCE:
        return None
    named = category_words.precisely_named_in(query.sentence)
    direct_debits_only = explicit_facts.asks_direct_debits(query.sentence)
    # A bank receipt is already a conservative structured scope. Applying the historical
    # subscription/rent catalogue as well would silently omit utilities, insurance, tax or loan
    # receipts. A category explicitly named by the user still narrows the direct debits.
    categories = named or (() if direct_debits_only else RECURRING_EXPENSE_CATEGORIES)
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=categories,
        metric=Metric.RECURRENCE,
        transaction_types=(TransactionType.DIRECT_DEBIT,) if direct_debits_only else (),
    )


@_recogniser
def _average_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Build either kind of average from explicit words, preserving safe conversation context.

    The distinction is arithmetic, not semantic guesswork: AVERAGE divides by matching movements;
    PERIOD_AVERAGE divides by the DAY/WEEK/MONTH buckets named in the sentence. A missing window is
    kept as a draft so the next turn can answer it instead of being told the capability is absent.
    """
    if _a_comparison_owns_it(query):
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    previous = query.conversation.last_plan
    natural_period = _NATURAL_PERIOD_AVERAGE_FOLLOW_UP.fullmatch(_clean_turn(query.sentence))
    if natural_period is not None and isinstance(previous, FinancialPlan):
        groups = {"dia": GroupBy.DAY, "semana": GroupBy.WEEK, "mes": GroupBy.MONTH}
        return replace(
            previous,
            group_by=groups[natural_period.group("unit")],
            metric=Metric.PERIOD_AVERAGE,
        )
    follow_up = _average_follow_up_plan(query, facts, previous)
    if follow_up is not None:
        return follow_up
    if facts.metric not in {Metric.AVERAGE, Metric.PERIOD_AVERAGE}:
        return None

    named = category_words.precisely_named_in(query.sentence)
    previous_categories = previous.categories if previous is not None else ()
    previous_excluded_categories = previous.excluded_categories if previous is not None else ()
    previous_merchants = previous.merchants if previous is not None else ()
    previous_merchant_terms = previous.merchant_terms if previous is not None else ()
    previous_excluded_merchants = previous.excluded_merchants if previous is not None else ()
    asks_to_spend = re.search(
        r"\bgast|\bpagu|\bcompr|\bcargo|\bme\s+cobr", spanish.folded(query.sentence)
    )
    explicit_direction = facts.direction or (Direction.EXPENSE if asks_to_spend else None)
    changes_direction = (
        explicit_direction is not None
        and previous is not None
        and explicit_direction is not previous.direction
    )
    categories = named or (() if changes_direction else previous_categories)
    merchants = () if named or changes_direction else previous_merchants
    merchant_terms = () if named or changes_direction else previous_merchant_terms
    if named:
        previous_excluded_categories = tuple(
            category for category in previous_excluded_categories if category not in named
        )
        previous_excluded_merchants = ()
    direction = explicit_direction
    direction = direction or (previous.direction if previous is not None else None)
    if direction is None:
        direction = _direction_of(categories) if categories else Direction.ALL

    if facts.group_by is not None:
        group_by = facts.group_by
    elif (named_dimension := _named_group_dimension(spanish.folded(query.sentence))) is not None:
        # A dimension the sentence states outright beats anything inherited. "Top 2 categorías por
        # ticket medio" came in after two turns about comercios and was read as a merchant average,
        # because `explicit_facts` does not treat that phrasing as a breakdown request and the
        # grouping was therefore inherited. Only the order of the cascade kept it off the wire.
        group_by = named_dimension
    elif (
        facts.metric is Metric.AVERAGE
        and isinstance(previous, FinancialPlan)
        and previous.metric is Metric.PERIOD_AVERAGE
        and previous.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
    ):
        # "Ticket medio" changes the divisor back to movements. A temporal grouping inherited
        # from the previous period-average would otherwise survive and answer a different question.
        group_by = GroupBy.NONE
    elif not named and isinstance(previous, FinancialPlan):
        group_by = previous.group_by
    else:
        group_by = GroupBy.NONE

    # Not defensive, and not about future drift — this fires today. `explicit_facts` reads
    # "gasto medio mensual por comercio" as PERIOD_AVERAGE grouped by COUNTERPARTY, because the
    # sentence states both and the grouping cascade puts the shop ahead of the month. The comment
    # here used to say the pair could not be produced; it can, and this check is the only reason
    # this recogniser does not build a plan the domain refuses.
    #
    # Declining sends it on to the capability guard, whose AVERAGE family owns the refusal: a
    # per-period average broken down by shop needs two grouping axes and a plan has one.
    if facts.metric is Metric.PERIOD_AVERAGE and group_by not in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        return None

    window = facts.window
    if window is None and previous is not None:
        window = previous.period or previous.date_range

    if window is None:
        return explicit_facts.enforce(
            FinancialPlanDraft(
                direction=direction,
                categories=categories,
                excluded_categories=previous_excluded_categories,
                merchants=merchants,
                merchant_terms=merchant_terms,
                excluded_merchants=previous_excluded_merchants,
                group_by=group_by,
                metric=facts.metric,
                include_internal=(
                    previous.include_internal if isinstance(previous, FinancialPlan) else False
                ),
                internal_only=(
                    previous.internal_only if isinstance(previous, FinancialPlan) else False
                ),
                across_accounts=(
                    previous.across_accounts if isinstance(previous, FinancialPlan) else False
                ),
                only_account_id=(
                    previous.only_account_id if isinstance(previous, FinancialPlan) else None
                ),
                only_account_ids=(
                    previous.only_account_ids if isinstance(previous, FinancialPlan) else ()
                ),
            ),
            facts,
        )
    return explicit_facts.enforce(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=previous_excluded_categories,
            merchants=merchants,
            merchant_terms=merchant_terms,
            excluded_merchants=previous_excluded_merchants,
            group_by=group_by,
            metric=facts.metric,
            include_internal=(
                previous.include_internal if isinstance(previous, FinancialPlan) else False
            ),
            internal_only=(
                previous.internal_only if isinstance(previous, FinancialPlan) else False
            ),
            across_accounts=(
                previous.across_accounts if isinstance(previous, FinancialPlan) else False
            ),
            only_account_id=(
                previous.only_account_id if isinstance(previous, FinancialPlan) else None
            ),
            only_account_ids=(
                previous.only_account_ids if isinstance(previous, FinancialPlan) else ()
            ),
        ),
        facts,
    )


def _average_follow_up_plan(
    query: AskFinancialQuestionQuery,
    facts: explicit_facts.ExplicitFacts,
    previous: PlannedQuery | None,
) -> FinancialPlan | None:
    """Apply short, explicit changes to an average without reconstructing it with a model."""
    if not isinstance(previous, FinancialPlan) or previous.metric not in {
        Metric.AVERAGE,
        Metric.PERIOD_AVERAGE,
    }:
        return None
    # A complete average in the new sentence outranks context. This helper only fills the meaning
    # of elliptical turns such as "¿y al mes?"; applying it to "coste semanal medio" would retain
    # the old monthly divisor merely because the sentence also changed category.
    if facts.metric is not None:
        return None

    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    named = category_words.precisely_named_in(query.sentence)
    metric = previous.metric
    group_by = previous.group_by

    if _AVERAGE_PERIOD_FOLLOW_UP.fullmatch(folded) and facts.group_by in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        metric = Metric.PERIOD_AVERAGE
        group_by = facts.group_by
    elif _AVERAGE_MOVEMENT_FOLLOW_UP.fullmatch(folded):
        metric = Metric.AVERAGE
        if group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
            group_by = GroupBy.NONE
    elif not (folded.startswith("y ") and (facts.window is not None or named)):
        return None

    window = facts.window or previous.period or previous.date_range
    categories = named or previous.categories
    direction = facts.direction
    if direction is None:
        direction = _direction_of(named) if named else previous.direction
    return explicit_facts.enforce(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=tuple(
                category for category in previous.excluded_categories if category not in named
            ),
            merchants=() if named else previous.merchants,
            merchant_terms=() if named else previous.merchant_terms,
            excluded_merchants=() if named else previous.excluded_merchants,
            group_by=group_by,
            metric=metric,
            include_internal=previous.include_internal,
            internal_only=previous.internal_only,
            across_accounts=previous.across_accounts,
            only_account_id=previous.only_account_id,
            only_account_ids=previous.only_account_ids,
        ),
        facts,
    )


def _asks_a_running_total(query: AskFinancialQuestionQuery) -> bool:
    previous = query.conversation.last_plan
    return _stated(query).metric is Metric.CUMULATIVE or (
        isinstance(previous, FinancialPlan) and previous.metric is Metric.CUMULATIVE
    )


def _asks_an_average(query: AskFinancialQuestionQuery) -> bool:
    averages = {Metric.AVERAGE, Metric.PERIOD_AVERAGE}
    if _stated(query).metric in averages:
        return True
    previous = query.conversation.last_plan
    if not isinstance(previous, FinancialPlan):
        return False
    return previous.metric in averages or bool(
        _NATURAL_PERIOD_AVERAGE_FOLLOW_UP.fullmatch(_clean_turn(query.sentence))
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
        name="_cumulative_plan",
        read=_cumulative_plan,
        stood_at=16,
        when=Precondition(
            "the sentence asks for a running total, or one is in view",
            _asks_a_running_total,
        ),
        yields_to=("contextual_follow_up_plan",),
        because=(
            "«¿y por semana?» over a running total is read by both, and the two have the same "
            "branch written twice — `unit_follow_up` here, and the CUMULATIVE clause in the "
            "follow-up reader. The follow-up wins because it patches the plan in view instead "
            "of rebuilding one. Measured on one corpus turn; the duplication is Section B's."
        ),
    ),
    Reader(
        name="_recurrence_plan",
        read=_recurrence_plan,
        stood_at=17,
        when=Precondition(
            "the sentence asks which expenses repeat",
            lambda query: _stated(query).metric is Metric.RECURRENCE,
        ),
    ),
    Reader(
        name="_frequency_plan",
        read=_frequency_plan,
        stood_at=18,
        when=Precondition(
            "the sentence asks how often something happens",
            lambda query: _stated(query).metric is Metric.FREQUENCY,
        ),
    ),
    Reader(
        name="_average_plan",
        read=_average_plan,
        stood_at=19,
        when=Precondition(
            "the sentence asks for an average, or an average is in view",
            _asks_an_average,
        ),
        yields_to=("contextual_follow_up_plan", "_group_ranking_plan"),
        because=(
            "Two different arguments, and both were already written down in prose. Against "
            "the follow-up: «¿y en gasolina?» after an average is an ellipsis, and the "
            "follow-up keeps the average's own divisor and filters — nine corpus turns. "
            "Against the group ranking: «ahora top 2 categorías por ticket medio» is a "
            "ranking that happens to name a metric, and this reader's own comment says «only "
            "the order of the cascade kept it off the wire». It is this row that keeps it off "
            "the wire now. One corpus turn."
        ),
    ),
)
