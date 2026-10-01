"""A calendar regroup that also names a category is handed to the planner, not answered here.

I-1 (Wave 4.2b fix wave): the presentation-dimension gate widened to admit day/week/month
regroups, and the lexicon's plural extended admission to "por meses" -- together they let a
sentence like "Dame el total por meses de gasolina" through this reader, which has no way to
honour the category it names (`explicit_facts.enforce` never applies `facts.categories`). The
category was silently dropped and the answer became every category's monthly total, one banking
call and no model. At BASE the same sentence declined -- the ellipsis guard or `if named: return
None` ceded the turn to the planner, which reads the category and the grouping both. This pins
that route back.

A plain calendar regroup naming no category must keep claiming: that is the whole point of the
branch (Task 2, Wave 4.2b), and the fix above must not take it away.
"""

from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.readers.follow_up import read_contextual_follow_up
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = UUID(int=1)
TODAY = date(2026, 8, 26)

PLAIN_PLAN = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))
AVERAGE_PLAN = FinancialPlan(
    direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR), metric=Metric.AVERAGE
)


def query(sentence: str, plan: FinancialPlan = PLAIN_PLAN) -> AskFinancialQuestionQuery:
    return AskFinancialQuestionQuery(
        sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=plan)
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Dame el total por meses de gasolina",
        "Dame el total por mes de gasolina",
        "Agrúpalo por meses de gasolina",
    ],
)
def test_a_regroup_naming_a_category_declines_and_reaches_the_planner(sentence: str) -> None:
    assert read_contextual_follow_up(query(sentence)) is None


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Ahora compáralo por meses.", GroupBy.MONTH),
        ("¿Y por semana?", GroupBy.WEEK),
    ],
)
def test_a_plain_calendar_regroup_still_claims(sentence: str, expected: GroupBy) -> None:
    plan = read_contextual_follow_up(query(sentence))
    assert plan is not None
    assert plan.group_by is expected
    assert plan.categories == ()


def test_the_average_deference_is_untouched_by_the_named_category_guard() -> None:
    """ "¿Y por semana?" over an AVERAGE plan is `_average_plan`'s own turn (M-3).

    Was covered only indirectly, by corpus episodes 14 and 24, and by the comment beside the
    gate. Named here as its own test so a future edit to either guard cannot silently make this
    reader contest `_average_plan` again (`test_which_recogniser_claims_a_turn.py` is the episode
    that found that regression the first time).
    """
    assert read_contextual_follow_up(query("¿Y por semana?", AVERAGE_PLAN)) is None
