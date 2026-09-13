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


# --- D-H-9 (fix wave, IMPORTANT): the widened claim, pinned at its edges --------------------
#
# Item 5 (T8b) moved `_SPLIT_BY_NAMES` ahead of the five-word ellipsis gate below it in
# `read_contextual_follow_up` so "sepáralo en transporte público y gasolina" (six words) would
# still be claimed -- but moving it ahead of that gate means NO length gate applies to this
# branch any more, at any length, which is a wider claim than the one word count used to bound.
# These three probes are what the branch's own precondition (`len(named) >= 2 and
# _SPLIT_BY_NAMES.search(folded)`) says it should and should not do, pinned so a future change to
# either the regex or the named-category count cannot silently widen or narrow it again.


def test_a_long_split_sentence_is_still_claimed() -> None:
    """Longer than any ellipsis gate would ever admit, but still nothing but the split verb and
    two names precisely known -- claimed, same as the five-word original."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))
    long_sentence = (
        "Sepáralo por favor en transporte público de todo tipo y también en gasolina, gracias"
    )

    split = read_contextual_follow_up(
        AskFinancialQuestionQuery(
            long_sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=plan)
        )
    )

    assert split is not None
    assert split.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert split.group_by is GroupBy.CATEGORY


def test_a_long_sentence_naming_only_one_category_is_not_claimed_as_a_split() -> None:
    """The same length, the same opening verb, but only one category precisely named -- the
    branch's own `len(named) >= 2` guard is what keeps this from claiming a "split" of one
    thing, at any length."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))
    long_sentence = "Sepáralo por favor y muéstrame solo lo que gasté en gasolina este año, gracias"

    split = read_contextual_follow_up(
        AskFinancialQuestionQuery(
            long_sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=plan)
        )
    )

    assert split is None


def test_a_split_verb_beside_an_explicit_total_request_is_claimed_as_the_split_it_names() -> None:
    """ "Separa X e Y y dime cuánto gasté en total" -- a split verb naming two precise categories,
    beside an explicit request for the combined total. DECIDED (D-H-9): claimed as the split, not
    refused as a data-asking sentence under F-H1's rule -- unlike the VISUAL restyle path F-H1
    guards (where a data-asking word beside a style word means the *data* question would otherwise
    be silently discarded in favour of a chart nobody asked to change), this branch's own result
    is itself the data: a `GroupBy.CATEGORY` plan over exactly the two named categories, whose
    answer states each category's amount and the total behind them together. "Dime cuánto gasté
    en total" asks for the number the split's own answer already gives; there is no second,
    un-plannable question here for a refusal to protect."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))
    sentence = "Separa transporte público y gasolina y dime cuánto gasté en total"

    split = read_contextual_follow_up(
        AskFinancialQuestionQuery(
            sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=plan)
        )
    )

    assert split is not None
    assert split.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert split.group_by is GroupBy.CATEGORY
    assert split.metric.value == "SUM"
