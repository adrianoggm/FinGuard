""" "Separa X e Y" patches only the categories and the grouping that presents them -- everything
else the plan in view carries that this patch does not itself state must be cleared, not
inherited.

I-2: a merchant filter or a merchant term left in view survives the split as written today, and
the next question becomes an intersection nobody asked for -- "gasto en transporte público en
Mercadona" -- which returns approximately nothing and reads to the user as "no gastaste nada en
transporte público". The three sibling branches in this same function
(`_CATEGORY_REFERENCE`, `_CATEGORY_FILTER_FOLLOW_UP`, `_COUNT_FOLLOW_UP`) all clear
`merchants`/`merchant_terms` for exactly this reason; the split cleared only
`excluded_categories`.

M-1: `previous.group_limit` survives it too. Harmless at three (both named categories fit inside
a top-3 breakdown), but a `group_limit=1` plan would draw one of the two categories the split
just asked for by name.
"""

from datetime import date
from uuid import UUID

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.readers.follow_up import read_contextual_follow_up
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = UUID(int=1)
TODAY = date(2026, 8, 26)

SENTENCE = "Separa transporte público y gasolina."


def query(plan: FinancialPlan) -> AskFinancialQuestionQuery:
    return AskFinancialQuestionQuery(
        SENTENCE, ACCOUNT, TODAY, conversation=Conversation(last_plan=plan)
    )


def test_a_split_clears_a_merchant_filter() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        merchants=("Mercadona",),
    )

    split = read_contextual_follow_up(query(plan))

    assert split is not None
    assert split.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert split.merchants == ()


def test_a_split_clears_a_merchant_term() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        merchant_terms=("merca",),
    )

    split = read_contextual_follow_up(query(plan))

    assert split is not None
    assert split.merchant_terms == ()


def test_a_split_clears_the_row_cut() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.CATEGORY,
        group_limit=3,
    )

    split = read_contextual_follow_up(query(plan))

    assert split is not None
    assert split.group_limit is None
