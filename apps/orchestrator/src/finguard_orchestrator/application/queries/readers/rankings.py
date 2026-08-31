"""The three readers that ask for an order rather than a total.

«¿Cuál fue mi mayor gasto?» wants one movement. «Dame los 5 movimientos más grandes» wants several.
«Top 3 categorías» wants a row per group. Three readers, one question — *which came first* — and
one vocabulary of numbers and superlatives that all three read.

Grouped because the vocabulary overlaps and not because the dispatch does: these three go three
different ways once they claim (`_ranked`, `_ranked_many`, and the ordinary `_answer` with a group
limit on the plan). Grouping by where a claim is dispatched would have split them, which is why
that is not the axis this package uses — a reader's Spanish is what the next defect will be about,
not its return type.
"""

from __future__ import annotations

import re
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _query_for_reader,
    _starts_new_financial_scope,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.dimensions import (
    _GROUP_DIMENSION_NOUNS,
    _GROUP_RANKING_SIGNAL,
    _group_dimension_in,
    _named_group_dimension,
)
from finguard_orchestrator.application.queries.readers.seam import _recogniser
from finguard_orchestrator.domain.constants import (
    MAX_GROUP_RANK,
    MAX_GROUP_ROWS,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    spanish,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

_RANKING_REQUEST: Final = re.compile(
    r"\b(?:cual|que)\b.{0,30}\b"
    r"(?:gasto|ingreso|movimiento|compra|pago|cargo|cobro|nomina|sueldo|salario)\b.{0,30}"
    r"\b(?:mas\s+(?:alto|alta|caro|cara|grande)|mayor)\b"
)


_GROUP_RANKING_FOLLOW_UP_PREFIX: Final = re.compile(
    r"^(?:(?:vale|bueno|y|ahora|mejor)(?:\s+|\s*[,;:]\s*))*"
    r"(?P<request>por\s+.+)$"
)


_GROUP_RANKING_FOLLOW_UP_REQUEST: Final = re.compile(
    r"^por\s+(?:(?:numero|cantidad)\s+de\s+)?"
    r"(?:ticket\s+medio|importe\s+medio|media|promedio|operaciones|movimientos|compras|pagos|"
    r"importe|gasto|total)$"
)


_GROUP_RANKING_COUNT: Final = re.compile(
    r"\b(?:top\s*)?(?P<count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    rf"(?:{_GROUP_DIMENSION_NOUNS})\b"
)


# The same count when the sentence does not glue it to the dimension: "top 25 gastos por categoria"
# names the number and the dimension in different halves. Read only after the pattern above misses,
# and only from inside `_group_ranking_plan`, which has already established that a dimension was
# named -- so a bare number cannot be taken for one here. Without it the sentence fell through to
# the default of five, which answers a question about twenty-five with five rows and says nothing.
_GROUP_RANKING_TOP_COUNT: Final = re.compile(
    r"\btop\s*(?P<count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b"
)


_GROUP_RANKING_NUMBERS: Final = {
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}


_MULTIPLE_MOVEMENT_RANKING: Final = re.compile(
    r"\b(?P<count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:(?:mayores?|principales?)\s+)?"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b.{0,25}"
    r"\b(?:mas\s+(?:altos?|grandes?|caros?)|mayores?)\b"
    r"|\b(?P<leading_count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:mayores?|principales?)\s+"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b"
    r"|\btop\s*(?P<top_count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b"
)


_ORDERED_MOVEMENT_LIST: Final = re.compile(
    r"\b(?:gastos|ingresos|movimientos|compras|pagos|cargos)\b.{0,90}"
    r"\bde\s+(?:mayor(?:es)?\s+a\s+menor(?:es)?"
    r"|mas\s+altos?\s+a\s+mas\s+bajos?"
    r"|mas\s+grandes?\s+a\s+mas\s+pequenos?"
    r"|mas\s+caros?\s+a\s+mas\s+baratos?)\b"
)


@_recogniser
def _contextual_ranking_plan(query: AskFinancialQuestionQuery) -> TopMovements | None:
    """Turn a superlative into a ranking while retaining the prior window and filters."""
    selected = _query_for_reader(query)
    facts = explicit_facts.read(selected.wording, today=query.today)
    if facts.asks_top_category or not _RANKING_REQUEST.search(spanish.folded(selected.wording)):
        return None
    previous = (
        None
        if not selected.returned_from_balance
        and _starts_new_financial_scope(selected.wording, today=query.today)
        else selected.plan
    )
    window = facts.window or (
        previous.period or previous.date_range if previous is not None else None
    )
    if window is None:
        return None
    folded = spanish.folded(selected.wording)
    if re.search(r"\bingres|\bcobr|\bnomina|\bsueldo|\bsalario", folded):
        direction = Direction.INCOME
    elif re.search(r"\bgast|\bpagu|\bcompr|\bcargo", folded):
        direction = Direction.EXPENSE
    elif previous is not None:
        direction = previous.direction
    else:
        direction = Direction.ALL
    named = category_words.precisely_named_in(selected.wording)
    return TopMovements(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (previous.categories if previous is not None else ()),
        excluded_categories=(
            tuple(category for category in previous.excluded_categories if category not in named)
            if previous is not None
            else ()
        ),
        merchants=() if named else (previous.merchants if previous is not None else ()),
        merchant_terms=(() if named else (previous.merchant_terms if previous is not None else ())),
        excluded_merchants=(previous.excluded_merchants if previous is not None else ()),
        rank=1,
    )


@_recogniser
def _group_ranking_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Rank aggregate groups by the exact metric named in the sentence."""
    selected = _query_for_reader(query)
    folded = spanish.folded(selected.wording)
    dimension_match = _group_dimension_in(folded)
    previous = selected.plan
    previous_ranking = (
        previous
        if isinstance(previous, FinancialPlan) and previous.group_limit is not None
        else None
    )
    follow_up_match = _GROUP_RANKING_FOLLOW_UP_PREFIX.fullmatch(folded.strip(" ¿?¡!.,;:"))
    follow_up_request = follow_up_match.group("request") if follow_up_match is not None else None
    ranking_follow_up = (
        previous_ranking is not None
        and follow_up_request is not None
        and _GROUP_RANKING_FOLLOW_UP_REQUEST.fullmatch(follow_up_request) is not None
    )
    if dimension_match is None and not ranking_follow_up:
        return None
    if not ranking_follow_up and _GROUP_RANKING_SIGNAL.search(folded) is None:
        return None

    named_dimension = _named_group_dimension(folded) if dimension_match else None
    if named_dimension is not None:
        group_by = named_dimension
    else:
        assert previous_ranking is not None
        group_by = previous_ranking.group_by

    if re.search(r"\b(?:ticket\s+medio|importe\s+medio|media|promedio)\b", folded):
        metric = Metric.AVERAGE
    elif re.search(r"\b(?:operaciones|movimientos|compras|pagos)\b", folded):
        metric = Metric.COUNT
    elif ranking_follow_up and not re.search(r"\b(?:importe|gasto|total)\b", folded):
        assert previous_ranking is not None
        metric = previous_ranking.metric
    else:
        metric = Metric.SUM

    count_match = _GROUP_RANKING_COUNT.search(folded) or _GROUP_RANKING_TOP_COUNT.search(folded)
    if count_match is None:
        if ranking_follow_up:
            assert previous_ranking is not None
            group_limit = previous_ranking.group_limit
        else:
            group_limit = 5
    else:
        raw_count = count_match.group("count")
        asked = int(raw_count) if raw_count.isdigit() else _GROUP_RANKING_NUMBERS[raw_count]
        # Clamped, not refused. Asking for more rows than exist is not a mistake -- "top 25
        # comercios" on an account with twelve of them is twelve -- and the plan used to raise
        # InvalidValueError above the ceiling, which turned an answerable question into a failure
        # somewhere the sentence could no longer explain itself.
        group_limit = max(1, min(asked, MAX_GROUP_ROWS))

    facts = explicit_facts.read(selected.wording, today=query.today)
    contextual = (
        ranking_follow_up
        or selected.returned_from_balance
        or not _starts_new_financial_scope(selected.wording, today=query.today)
    )
    base = previous_ranking if contextual else None
    window = facts.window or (
        base.period or base.date_range if base is not None else RelativePeriod(Period.THIS_YEAR)
    )
    named = category_words.precisely_named_in(selected.wording)
    if re.search(r"\bingres|\bcobr|\bnomina", folded):
        direction = Direction.INCOME
    elif base is not None and ranking_follow_up:
        direction = base.direction
    else:
        direction = Direction.EXPENSE
    return FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (base.categories if base is not None else ()),
        excluded_categories=base.excluded_categories if base is not None else (),
        merchants=base.merchants if base is not None and group_by is not GroupBy.MERCHANT else (),
        merchant_terms=(
            base.merchant_terms if base is not None and group_by is not GroupBy.MERCHANT else ()
        ),
        excluded_merchants=base.excluded_merchants if base is not None else (),
        group_by=group_by,
        metric=metric,
        group_limit=group_limit,
        include_internal=base.include_internal if base is not None else False,
        internal_only=base.internal_only if base is not None else False,
        across_accounts=(group_by is GroupBy.ACCOUNT) or (base.across_accounts if base else False),
        only_account_id=None
        if group_by is GroupBy.ACCOUNT
        else (base.only_account_id if base else None),
        only_account_ids=()
        if group_by is GroupBy.ACCOUNT
        else (base.only_account_ids if base else ()),
        transaction_types=base.transaction_types if base is not None else (),
    )


@_recogniser
def _multiple_movement_ranking(
    query: AskFinancialQuestionQuery,
) -> tuple[TopMovements, int] | None:
    """Several individual movements, distinct from a ranking of aggregate groups."""
    folded = spanish.folded(query.sentence)
    found = _MULTIPLE_MOVEMENT_RANKING.search(folded)
    ordered_list = _ORDERED_MOVEMENT_LIST.search(folded)
    if found is None and ordered_list is None:
        return None
    if found is None:
        count = MAX_GROUP_RANK
    else:
        raw_count = found.group("count") or found.group("leading_count") or found.group("top_count")
        count = int(raw_count) if raw_count.isdigit() else _GROUP_RANKING_NUMBERS[raw_count]
    if not 2 <= count <= MAX_GROUP_RANK:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    window = facts.window or RelativePeriod(Period.THIS_YEAR)
    named = category_words.precisely_named_in(query.sentence)
    direction = (
        Direction.INCOME
        if re.search(r"\bingres|\bcobr|\bnomina|\bsalario|\bsueldo", folded)
        else Direction.EXPENSE
    )
    return (
        TopMovements(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=named,
            rank=1,
        ),
        count,
    )


def _asks_to_rank_groups(query: AskFinancialQuestionQuery) -> bool:
    selected = _query_for_reader(query)
    if _group_dimension_in(spanish.folded(selected.wording)) is not None:
        return True
    return isinstance(selected.plan, FinancialPlan) and selected.plan.group_limit is not None


def _asks_for_the_biggest_movement(query: AskFinancialQuestionQuery) -> bool:
    selected = _query_for_reader(query)
    facts = explicit_facts.read(selected.wording, today=query.today)
    return not facts.asks_top_category and bool(
        _RANKING_REQUEST.search(spanish.folded(selected.wording))
    )


def _asks_for_several_movements(query: AskFinancialQuestionQuery) -> bool:
    folded = spanish.folded(query.sentence)
    return bool(_MULTIPLE_MOVEMENT_RANKING.search(folded) or _ORDERED_MOVEMENT_LIST.search(folded))


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
        name="_multiple_movement_ranking",
        read=_multiple_movement_ranking,
        stood_at=11,
        when=Precondition(
            "the sentence asks for several individual movements",
            _asks_for_several_movements,
        ),
    ),
    Reader(
        name="_group_ranking_plan",
        read=_group_ranking_plan,
        stood_at=12,
        when=Precondition(
            "the sentence names a dimension to rank, or a ranking is in view",
            _asks_to_rank_groups,
        ),
    ),
    Reader(
        name="_contextual_ranking_plan",
        read=_contextual_ranking_plan,
        stood_at=15,
        when=Precondition(
            "the sentence asks which single movement was the biggest",
            _asks_for_the_biggest_movement,
        ),
    ),
)
