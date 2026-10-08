"""A comparison is continued in the looser shapes people used, and through its percentage.

Validation fix 3, G4. On 2026-10-08 each of these reached the model with a comparison in view, and
lost one month or both:

- «vale y lo mismo pero de restaurantes» (comparar-mayo-junio 2): restaurants this month;
- «restaurantes!» (comparar-mayo-junio 4), with that same comparison on screen: «¿De qué periodo?»;
- «y en gasolina q?» (comparar-mayo-junio 6): the trailing «q» made it a sentence the both-sides
  reading refused, and fuel ran for last month;
- «y eso en %?» (impro-comparar-meses 2): UNSUPPORTED, saying it can compute «la variación entre
  dos periodos» about the two figures it had just shown;
- «y de ocio?» after the percentage (impro-comparar-meses 4): the period asked, because a
  percentage answer was no comparison any more.

Task 6's both-sides reading now takes «lo mismo pero (de|en) X», a bare category said with «!»,
and a trailing «q»/«que»; «%», «el porcentaje» and «¿cuánto en %?» are the percentage of the pair
in view; and after a percentage the pair is still the base. The every-token check stays: a turn
with a window of its own, or a whole question, is not a patch.

Driven over HTTP on the episodes stack, with a planner that loses the comparison as the local model
did (`TheLocalModel`: no comparison is ever handed to it as a base, R27, and where the real model
read the turn it returns what it returned).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

MAY_AND_JUNE = "comparame el super de mayo con el de junio"
JUNE_OR_JULY = "en q gaste mas en restaurantes, junio o julio?"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class TheLocalModel(AsksForTheWindow):
    """What qwen2.5-coder:7b returned on 2026-10-08 where it read these turns (fix round 1): «vale y
    lo mismo pero de restaurantes» was restaurants this month (comparar-mayo-junio 2), and «y en
    gasolina q?» fuel last month (comparar-mayo-junio 6). Anything else is `AsksForTheWindow`'s,
    which asks the period of a category said alone, as it did for «restaurantes!» (4)."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        folded = spanish.folded(sentence)
        if "lo mismo pero" in folded or "gasolina q" in folded:
            self.asked.append(sentence)
            fuel = "gasolina" in folded
            return PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.LAST_MONTH if fuel else Period.THIS_MONTH),
                    categories=("TRANSPORT_FUEL",) if fuel else ("RESTAURANTS",),
                )
            )
        return await super().plan(sentence, context=context)


def _after(*turns: str) -> tuple[Any, dict[str, Any]]:
    stack = asking_for_the_window_stack(planner=TheLocalModel())
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _both_sides(stack: Any, months: tuple[int, int], category: str) -> None:
    """The pair ran again, each side in its own month of 2026, in `category`, with no model."""
    plans: list[FinancialPlan] = stack.ledger.plans
    assert sorted(
        (plan.date_range.start.year, plan.date_range.start.month)
        for plan in plans
        if plan.date_range is not None
    ) == [(2026, months[0]), (2026, months[1])], plans
    assert all(plan.categories == (category,) for plan in plans), plans
    assert stack.planner.asked == [], "the comparison never reached the model"


@pytest.mark.parametrize(
    ("sentence", "category"),
    [
        ("vale y lo mismo pero de restaurantes", "RESTAURANTS"),
        ("lo mismo pero en restaurantes", "RESTAURANTS"),
        ("y lo mismo pero de gasolina?", "TRANSPORT_FUEL"),
        ("restaurantes!", "RESTAURANTS"),
        ("¡gasolina!", "TRANSPORT_FUEL"),
        ("y en gasolina q?", "TRANSPORT_FUEL"),
        ("y en gasolina que?", "TRANSPORT_FUEL"),
        ("y de salud q", "HEALTH"),
    ],
)
def test_the_looser_shapes_patch_both_sides(sentence: str, category: str) -> None:
    """comparar-mayo-junio 2, 4 and 6: May and June, in the subject said."""
    stack, answer = _after(MAY_AND_JUNE, sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _both_sides(stack, (5, 6), category)


def test_the_same_subject_shouted_is_the_same_comparison() -> None:
    """comparar-mayo-junio 3 and 4: «restaurantes!» over May and June in restaurants is May and
    June in restaurants, never «¿De qué periodo?»."""
    stack, answer = _after(MAY_AND_JUNE, "lo mismo pero de restaurantes", "restaurantes!")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _both_sides(stack, (5, 6), "RESTAURANTS")


@pytest.mark.parametrize(
    "sentence",
    ["y eso en %?", "en %", "¿y el porcentaje?", "¿cuánto en %?", "y en porcentaje?"],
)
def test_the_percentage_of_the_pair_in_view(sentence: str) -> None:
    """impro-comparar-meses 2: the change between the two figures just shown, from June to July,
    never UNSUPPORTED."""
    stack, answer = _after(JUNE_OR_JULY, sentence)

    _a_change_from_june_to_july(answer)
    _both_sides(stack, (6, 7), "RESTAURANTS")


def _a_change_from_june_to_july(answer: dict[str, Any]) -> None:
    """A percentage change, said from the earlier month to the later one. The episodes ledger
    answers every window alike, so the change it says is «no cambió»."""
    assert answer["outcome"] == "PLANNED", answer["text"]
    assert answer["text"].startswith("El gasto "), answer["text"]
    assert "en junio de 2026 a " in answer["text"] and "en julio de 2026." in answer["text"]


def test_after_the_percentage_the_pair_is_still_the_base() -> None:
    """impro-comparar-meses 4: «y de ocio?» after «y eso en %?» is June and July in leisure — and,
    since fix round 1 (M-2), the percentage of them, as the answer it continues was: never a plain
    comparison in its place."""
    stack, answer = _after(JUNE_OR_JULY, "y eso en %?", "y de ocio?")

    _a_change_from_june_to_july(answer)
    _both_sides(stack, (6, 7), "LEISURE")


def test_the_percentage_asked_again_is_the_same_pair() -> None:
    """impro-comparar-meses 3: «en porcentaje» after a percentage answers that percentage again."""
    stack, answer = _after(JUNE_OR_JULY, "y eso en %?", "en porcentaje")

    _a_change_from_june_to_july(answer)
    _both_sides(stack, (6, 7), "RESTAURANTS")


# --- the every-token check stays ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "lo mismo pero de restaurantes en julio",
        "y de ocio en agosto q?",
        "¿cuánto gasté en restaurantes!",
        "restaurantes y gasolina!",
    ],
)
def test_a_window_of_its_own_or_a_question_of_its_own_is_no_patch(sentence: str) -> None:
    """A window said, a question asked or two subjects at once are not both sides of May and June
    in one subject."""
    stack, _ = _after(MAY_AND_JUNE, sentence)

    ran: list[FinancialPlan] = stack.ledger.plans
    may_and_june = sorted(
        plan.date_range.start.month
        for plan in ran
        if plan.date_range is not None and plan.date_range.start.year == 2026
    )
    assert not (len(ran) == 2 and may_and_june == [5, 6] and len({p.categories for p in ran}) == 1)


@pytest.mark.parametrize("asked", ["el porcentaje", "en porcentaje", "y eso en %?"])
def test_the_percentage_of_two_answers_runs_from_the_earlier_month(asked: str) -> None:
    """Fix round 1, M-3: June, then «¿y en mayo?», then the percentage — from May to June, the
    order the months happened in, never from the answer given first to the one given last."""
    stack, answer = _after("¿cuánto gasté en el super en junio?", "¿y en mayo?", asked)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert "en mayo de 2026 a " in answer["text"], answer["text"]
    assert answer["text"].rstrip().endswith("en junio de 2026."), answer["text"]
    _both_sides(stack, (5, 6), "GROCERIES")
