"""The same sentence from the other side: the verifier refused the plan that got it right.

Found on 2026-09-14 while writing the dataset case for the reader fix in
`test_a_shop_named_beside_its_category_is_still_the_shop.py`, and it is the mirror image of it.
«¿Cuánto he gastado en suscripciones de Netflix este mes?» names a category *and* a shop inside
it, and the two readers disagree about what a correct plan looks like:

  · plan the CATEGORY and the answer is five times too wide -- that is the reader fix;
  · plan the SHOP, which is right, and `CATEGORY_MISREAD` refused it: a category the sentence
    names was missing from the plan. `repaired` has no correction for that rule, so the turn was
    lost outright.

One sentence, broken in both directions, and the second half had never been noticed because no
case in `evaluation/planning/*.yaml` named a category and a shop together.

The rule keeps its point. Every failure it was built from is a plan answering something *wider*
than the question -- cine answered as restaurants, a total answered as everything. A merchant
filter cannot widen anything, so a plan that sheds a named category to keep a shop the same
sentence named is the narrower true reading, not a misread. A shop the sentence never named
excuses nothing, and a *wrong* category is still refused.
"""

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.services import plan_verifier
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


def a_plan(**fields: object) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        **fields,  # type: ignore[arg-type]
    )


def test_keeping_the_shop_and_dropping_its_category_is_not_a_misread() -> None:
    sentence = "¿Cuánto he gastado en suscripciones de Netflix este mes?"
    assert plan_verifier.disagreement(sentence, a_plan(merchants=("Netflix",))) is None


def test_keeping_both_is_not_a_misread_either() -> None:
    sentence = "¿Cuánto he gastado en suscripciones de Netflix este mes?"
    plan = a_plan(categories=("SUBSCRIPTIONS",), merchants=("Netflix",))
    assert plan_verifier.disagreement(sentence, plan) is None


def test_dropping_the_category_for_a_shop_nobody_named_is_still_a_misread() -> None:
    """The exemption is about the sentence, not about merchants in general: a plan may not shed a
    named category by inventing a shop."""
    sentence = "¿Cuánto he gastado en suscripciones este mes?"
    assert plan_verifier.disagreement(sentence, a_plan(merchants=("Netflix",))) == (
        "CATEGORY_MISREAD"
    )


def test_the_wrong_category_beside_a_named_shop_is_still_a_misread() -> None:
    """And it excuses only the missing category, never a wrong one."""
    sentence = "¿Cuánto he gastado en suscripciones de Netflix este mes?"
    plan = a_plan(categories=("RESTAURANTS",))
    assert plan_verifier.disagreement(sentence, plan) == "CATEGORY_MISREAD"
