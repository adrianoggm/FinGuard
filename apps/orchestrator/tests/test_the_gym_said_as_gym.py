"""«Gym» is the gym, «solo el gym» narrows to it, and a corrected subject keeps its window.

Validation fix 3, G3. On 2026-10-08:

- «q me cuesta el gym al mes?» (gym-marta 1) and «cuanto pago del gym?» (impro-gimnasio-marta 1)
  were every subscription, 54,88 € in three: the model read SUBSCRIPTIONS, as it does for
  «gimnasio», but «gym» was no word of the kind B1 narrows to (`category_words._KINDS`), so
  nothing put the gym's term on the plan;
- «el mes pasado, solo el gym» (gym-marta 2) kept every subscription, for the same reason;
- «no, solo el gimnasio» over every subscription this month (impro-gimnasio-marta 2) reached the
  model, which asked the period: the correction narrowed the subject and dropped the window in
  view. «No, solo lo del gimnasio» had kept it (gym-marta 3) — the model's mood, not a rule.

«Gym» is now a spelling of the kind «gimnasio»: the plan carries «gimnasio», the word the shop's
name carries. And «solo X» led by «no,», or a subject corrected with «no, digo X», is the follow-up
reader's narrowing of the answer in view, with its window.

Driven over HTTP on the episodes stack, with a planner scripted as the local model behaved
(`ReadsTheGymAsSubscriptions`), so only the deterministic reading can pass.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation, Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import category_words, explicit_facts, spanish
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.planning.llm_query_planner import WINDOW_QUESTION

THE_GYM = ("gimnasio",)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ReadsTheGymAsSubscriptions(AsksForTheWindow):
    """What the local model returned on 2026-10-08.

    A sentence about the gym or about subscriptions: SUBSCRIPTIONS and no shop, in the window the
    sentence said or this month, by month when it said «al mes». A correction narrowing to the gym
    over an answer (a forced follow-up): the window asked for again, with the gym's term on the
    draft — «¿De qué periodo quieres ver tus gastos en comercios con «gimnasio» en el nombre?».
    """

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        folded = spanish.folded(sentence)
        continuation = context.continuation
        if continuation is not None and continuation.force_follow_up and "gimnasio" in folded:
            self.asked.append(sentence)
            return PlanningResult.needs_clarification(
                WINDOW_QUESTION,
                FinancialPlanDraft(
                    direction=Direction.EXPENSE,
                    categories=("SUBSCRIPTIONS",),
                    merchant_terms=THE_GYM,
                ),
                Slot.WINDOW,
            )
        if not any(word in folded for word in ("gym", "gimnasio", "suscripciones")):
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        window = explicit_facts.read(sentence, today=episodes.TODAY).window or RelativePeriod(
            period=Period.THIS_MONTH
        )
        relative = isinstance(window, RelativePeriod)
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=window if relative else None,
                date_range=None if relative else window,
                categories=("SUBSCRIPTIONS",),
                group_by=GroupBy.MONTH if "al mes" in folded else GroupBy.NONE,
            )
        )


class ReadsACorrection:
    """An interpreter that reads a turn opening with «no» as a correction, as a model may."""

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        if spanish.folded(sentence).startswith(("no ", "no,")):
            return Intent.CORRECTION
        return await Deterministic().interpret(sentence, conversation=conversation)


def _after(*turns: str, interpreter: Any = None) -> tuple[episodes.Stack, dict[str, Any]]:
    """The last of `turns` answered after the others, with only its own reads left to read."""
    stack = asking_for_the_window_stack(planner=ReadsTheGymAsSubscriptions())
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _the_gym(stack: episodes.Stack, period: Period) -> FinancialPlan:
    """The plan the answer ran: the gym, in `period`. The last one, because a sentence naming the
    gym by its article may first list the holder's shops of the kind (`_the_shop_it_names`)."""
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("SUBSCRIPTIONS",), plan
    assert plan.merchant_terms == THE_GYM, "the gym, not every subscription"
    assert plan.period is not None and plan.period.period is period, plan
    return plan


@pytest.mark.parametrize("sentence", ["q me cuesta el gym al mes?", "cuanto pago del gym?"])
def test_the_gym_said_as_gym_is_the_gym(sentence: str) -> None:
    """gym-marta 1 and impro-gimnasio-marta 1: Altafit, never 54,88 € of every subscription."""
    stack, answer = _after(sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _the_gym(stack, Period.THIS_MONTH)
    assert "gimnasio" in answer["plan"]


def test_last_month_only_the_gym_is_the_gym_last_month() -> None:
    """gym-marta 2: «solo el gym» leaves the other subscriptions out, in the window it says."""
    stack, answer = _after("q me cuesta el gym al mes?", "el mes pasado, solo el gym")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _the_gym(stack, Period.LAST_MONTH)


@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
@pytest.mark.parametrize(
    "narrowing",
    [
        "no, solo el gimnasio",
        "no, solo el gym",
        "solo el gimnasio",
        "solo el gym",
        "no, digo el gimnasio",
    ],
)
def test_only_the_gym_over_every_subscription_keeps_the_window(
    narrowing: str, interpreter: Any
) -> None:
    """impro-gimnasio-marta 2: the subject narrowed to the gym, «en lo que va de mes» kept — no
    window question, and no model asked to re-plan what the answer in view already says."""
    stack, answer = _after(
        "¿cuánto pago de suscripciones este mes?", narrowing, interpreter=interpreter
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    _the_gym(stack, Period.THIS_MONTH)
    assert stack.planner.asked == []


@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_a_corrected_subject_keeps_the_window_in_view(interpreter: Any) -> None:
    """The same rule for any subject: «no, digo gasolina» over restaurants last month is fuel last
    month, not a question about the period."""
    stack, answer = _after(
        "¿cuánto gasté en restaurantes el mes pasado?", "no, digo gasolina", interpreter=interpreter
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    (plan,) = stack.ledger.plans
    assert plan.categories == ("TRANSPORT_FUEL",), plan
    assert plan.period is not None and plan.period.period is Period.LAST_MONTH, plan


# --- the vocabulary -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence", ["q me cuesta el gym al mes?", "cuanto pago del gym?", "solo el gym", "el GYM"]
)
def test_gym_names_the_gym_inside_subscriptions(sentence: str) -> None:
    assert category_words.precisely_named_in(sentence) == ("SUBSCRIPTIONS",)
    assert category_words.kinds_named_in(sentence, "SUBSCRIPTIONS") == THE_GYM


@pytest.mark.parametrize(
    "sentence",
    [
        "suscripciones, gym incluido",
        "sin el gym",
        "aparte del gym",
        "¿cuánto gasté en la gymkhana?",
        "no solo el gym, también spotify",
    ],
)
def test_gym_beside_its_category_or_set_apart_is_not_the_gym_asked_about(sentence: str) -> None:
    """The kinds rule's own refusals hold for the new spelling: another word of the category, a
    kind left out or set apart — and a longer word that merely starts the same."""
    assert category_words.kinds_named_in(sentence, "SUBSCRIPTIONS") == ()


@pytest.mark.parametrize(
    "sentence",
    ["no solo el gym, también spotify", "no solo el gimnasio sino también netflix"],
)
def test_not_only_the_gym_is_not_only_the_gym(sentence: str) -> None:
    """Fix round 1, M-1: «no solo X, también Y» widens, so it is never the narrowing «no, solo X»
    — that needs the comma, and nothing after it that adds a second subject."""
    stack, _ = _after("¿cuánto pago de suscripciones este mes?", sentence)

    assert not any(plan.merchant_terms == THE_GYM for plan in stack.ledger.plans)
