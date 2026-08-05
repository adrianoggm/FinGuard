"""A rule certain enough to refuse on is certain enough to correct.

From the reported conversation, and still visible in the browser after the accent fix:

    "De esos gastos cuantos fueron en el supermercado ?"
    → "He entendido la pregunta, pero la consulta que he construido no se corresponde con ella."

That is an improvement on the "449,62 €" it used to answer with, and it is still a dead end. The
plan was right about everything except the metric — `en lo que va de mes, expense, groceries` with
SUM — and `COUNT_ANSWERED_AS_AMOUNT` fired because the sentence says "cuantos".

The rule already knows the answer. It is allowed to refuse a well-formed plan precisely because
Spanish agreement makes it certain — "cuántos" asks for a tally, and there is one field that says
so. Refusing while holding the correction is the assistant reporting on itself instead of helping.

Only where the correction is determinate. A misread category has no repair: the sentence names one
thing, the plan another, and picking either would be guessing which of the two the user meant.
"""

from dataclasses import replace

import pytest

from finguard_orchestrator.domain.enums import Direction, Metric, Period
from finguard_orchestrator.domain.services import plan_verifier
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

THIS_MONTH = RelativePeriod(period=Period.THIS_MONTH)
GROCERIES_TOTAL = FinancialPlan(
    direction=Direction.EXPENSE,
    period=THIS_MONTH,
    categories=("GROCERIES",),
    metric=Metric.SUM,
)

HOW_MANY = "De esos gastos cuantos fueron en el supermercado ?"


def test_a_count_question_answered_with_a_total_is_repaired_not_refused() -> None:
    """The reported turn. One field is wrong, the rule knows which, and the corrected plan answers
    the question the user asked rather than asking them to rephrase it."""
    broken = plan_verifier.disagreement(HOW_MANY, GROCERIES_TOTAL)

    mended = plan_verifier.repaired(broken or "", GROCERIES_TOTAL)

    assert mended == replace(GROCERIES_TOTAL, metric=Metric.COUNT)


def test_the_repair_leaves_everything_else_where_it_was() -> None:
    """A repair that widened the window or dropped the category would answer a third question,
    which is worse than refusing: it would look like an answer."""
    mended = plan_verifier.repaired("COUNT_ANSWERED_AS_AMOUNT", GROCERIES_TOTAL)

    assert mended is not None
    assert mended.categories == ("GROCERIES",)
    assert mended.period == THIS_MONTH
    assert mended.direction is Direction.EXPENSE


def test_the_repaired_plan_no_longer_disagrees() -> None:
    """What makes the repair safe to take: it resolves the disagreement instead of moving it."""
    mended = plan_verifier.repaired("COUNT_ANSWERED_AS_AMOUNT", GROCERIES_TOTAL)

    assert mended is not None
    assert plan_verifier.disagreement(HOW_MANY, mended) is None


@pytest.mark.parametrize("rule", ["CATEGORY_MISREAD", "SPENDING_ANSWERED_AS_EVERYTHING"])
def test_a_disagreement_with_no_determinate_repair_still_refuses(rule: str) -> None:
    """The important half. "¿Cuántas veces fui al cine?" planned as RESTAURANTS could be repaired to
    ENTERTAINMENT, and choosing that is the same guess the model already got wrong — the difference
    is that the guess would arrive wearing a correction's confidence."""
    assert plan_verifier.repaired(rule, GROCERIES_TOTAL) is None


def test_a_ranking_has_nothing_to_repair() -> None:
    """It has no metric, so the rule that has a repair cannot fire on it. Total on its own type
    rather than raising, for the same reason the rules themselves are."""
    ranking = TopMovements(direction=Direction.EXPENSE, period=THIS_MONTH, rank=1)

    assert plan_verifier.repaired("COUNT_ANSWERED_AS_AMOUNT", ranking) is None
