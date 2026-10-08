"""The follow-up reader: a category brings its own direction, and a question of its own is not a
window patch on the old person.

Validation fix 3, G6 (P-5 and P-7, parked in R51 while `readers/follow_up.py` was blocked; R60
unblocked it):

- P-5. «¿cuánto cobré de nómina en julio?» → «¿y en gasolina?» ran fuel as INCOME. A category that
  replaces the subject brings its own direction (R24), unless the base is every movement (ALL) —
  as Task 6 and fix round 1 already did on a comparison, and the single-plan reader did not.
- P-7. After an answer about Marta, «¿cuánto me mandó Javi este año?» ran Marta's sending this
  year: the reader's window branch took a whole question naming another person and its own
  direction as «este año» said of the answer in view.

Pinned beside them: «¿y en julio?» after Marta keeps Marta, and «¿y en gasolina?» after a spending
question stays spending.

Driven over HTTP on the episodes stack, with a planner scripted as a model reads these sentences.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import CounterpartyType, Direction, Period
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import explicit_facts, spanish
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)

MARTA_THIS_MONTH = "cuanto le mande a marta ruiz este mes?"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ReadsAsAModelDoes(AsksForTheWindow):
    """How a model plans the sentences these tests open with or hand over.

    The payroll as INCOME; every movement as ALL; and «¿cuánto me mandó Javi este año?» as what
    Javi sent, this year. Anything else is `AsksForTheWindow`'s.
    """

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        folded = spanish.folded(sentence)
        window = explicit_facts.read(sentence, today=episodes.TODAY).window
        if window is None or not any(
            word in folded for word in ("nomina", "movimientos", "me mando javi")
        ):
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        relative = isinstance(window, RelativePeriod)
        fields: dict[str, Any] = {"direction": Direction.ALL}
        if "nomina" in folded:
            fields = {"direction": Direction.INCOME, "categories": ("SALARY",)}
        elif "javi" in folded:
            fields = {
                "direction": Direction.INCOME,
                "counterparties": ("Javi",),
                "counterparty_types": (CounterpartyType.PERSON,),
            }
        return PlanningResult.planned(
            FinancialPlan(
                period=window if relative else None,
                date_range=None if relative else window,
                **fields,
            )
        )


def _after(*turns: str) -> tuple[Any, dict[str, Any]]:
    stack = asking_for_the_window_stack(planner=ReadsAsAModelDoes())
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _the_plan(stack: Any) -> FinancialPlan:
    (plan,) = stack.ledger.plans
    assert isinstance(plan, FinancialPlan)
    return plan


# --- P-5: the category's own direction ------------------------------------------------------------


@pytest.mark.parametrize("sentence", ["¿y en gasolina?", "solo en gasolina", "¿y de restaurantes?"])
def test_a_spending_category_after_the_payroll_is_spending(sentence: str) -> None:
    """P-5: fuel spent in July, never fuel received."""
    stack, answer = _after("¿cuánto cobré de nómina en julio?", sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.direction is Direction.EXPENSE, plan
    assert plan.categories and "SALARY" not in plan.categories, plan
    assert plan.date_range is not None and plan.date_range.start.month == 7, "July is kept"


def test_a_spending_category_after_spending_stays_spending() -> None:
    stack, answer = _after("¿cuánto gasté en restaurantes en julio?", "¿y en gasolina?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert (plan.direction, plan.categories) == (Direction.EXPENSE, ("TRANSPORT_FUEL",))


def test_the_payroll_after_spending_is_income() -> None:
    """The same rule the other way: «¿y la nómina?» over restaurants is the payroll received."""
    stack, answer = _after("¿cuánto gasté en restaurantes en julio?", "¿y en nómina?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert (plan.direction, plan.categories) == (Direction.INCOME, ("SALARY",))


def test_every_movement_stays_every_movement() -> None:
    """R24: a base over every movement (ALL) has no direction to replace."""
    stack, answer = _after("¿qué movimientos tuve en julio?", "¿y en gasolina?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert (plan.direction, plan.categories) == (Direction.ALL, ("TRANSPORT_FUEL",))


# --- P-7: a question of its own is not the old person's -------------------------------------------


def test_another_person_asked_whole_is_not_the_answers_person() -> None:
    """P-7: what Javi sent this year, as the model reads it — never Marta's sending."""
    stack, answer = _after(MARTA_THIS_MONTH, "¿cuánto me mandó Javi este año?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.counterparties == ("Javi",), plan
    assert plan.direction is Direction.INCOME, plan
    assert stack.planner.asked == ["¿cuánto me mandó Javi este año?"]


def test_a_window_alone_after_a_person_keeps_the_person() -> None:
    """«¿Y en julio?» after Marta is Marta in July."""
    stack, answer = _after(MARTA_THIS_MONTH, "¿y en julio?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.counterparties == ("Marta Ruiz",), plan
    assert plan.direction is Direction.EXPENSE, plan
    assert plan.date_range is not None and plan.date_range.start.month == 7


def test_a_window_said_as_a_new_question_of_the_same_person_is_hers() -> None:
    """«¿Cuánto le mandé a Marta Ruiz este año?» names her again: hers, this year."""
    stack, answer = _after(MARTA_THIS_MONTH, "¿cuánto le mandé a Marta Ruiz este año?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.counterparties == ("Marta Ruiz",), plan
    assert plan.period is not None and plan.period.period is Period.THIS_YEAR


# --- fix round 1, I-4: only another person, or the money turned round, is a question of its own --


def test_the_same_person_asked_again_with_a_window_keeps_the_patch() -> None:
    """«¿Cuánto le mandé este año?» after Marta is Marta this year, read as it was at f73697b9:
    deterministically, with no model."""
    stack, answer = _after(MARTA_THIS_MONTH, "¿cuánto le mandé este año?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.counterparties == ("Marta Ruiz",), plan
    assert plan.direction is Direction.EXPENSE, plan
    assert plan.period is not None and plan.period.period is Period.THIS_YEAR
    assert stack.planner.asked == []


def test_money_received_asked_again_with_a_window_keeps_the_patch() -> None:
    """«¿Cuánto me mandó este año?» after what Javi sent this month is Javi, this year."""
    stack, answer = _after("¿cuánto me mandó Javi este mes?", "¿cuánto me mandó este año?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _the_plan(stack)
    assert plan.counterparties == ("Javi",), plan
    assert plan.direction is Direction.INCOME, plan
    assert plan.period is not None and plan.period.period is Period.THIS_YEAR
    assert stack.planner.asked == []


@pytest.mark.parametrize(
    "base", ["¿cuánto gasté en restaurantes en julio?", "¿cuánto cobré de nómina en julio?"]
)
def test_money_sent_to_somebody_after_no_person_is_a_question_of_its_own(base: str) -> None:
    """After restaurants or the payroll, «¿cuánto le mandé este año?» is about a person the answer
    does not have: the model reads it, never the restaurants or the payroll this year."""
    stack, _ = _after(base, "¿cuánto le mandé este año?")

    assert stack.planner.asked == ["¿cuánto le mandé este año?"]
    assert not any(
        plan.categories in {("RESTAURANTS",), ("SALARY",)} for plan in stack.ledger.plans
    )
