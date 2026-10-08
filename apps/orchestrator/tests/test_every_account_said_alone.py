"""«Todas mis cuentas» said on its own, with nothing open, is about the answer just given.

Validation fix 1, F1. In the real conversations of 2026-10-08 (`evaluation/real/
2026-10-08-6de5d7e9.json`) the default was already every account, so the person saying «Todas mis
cuentas» or «en todas mis cuentas» after an answer was restating or widening its scope. No reader
took the turn — the scope follow-up wanted «ahora» in front, the follow-up patch saw no
continuation marker, and the account follow-up reads kinds of account, not «todas» — so it reached
the model as a question of its own, and the model read it as «everything, by account»: a one-group
breakdown of all spending, sometimes negative, with the gym or Marta dropped (gimnasio-ultimamente
2, gimnasio-marta 2, mandado-recibido 2, suscripciones-olvidalo 2). After a comparison it asked the
period of «tus movimientos» (spotify-llevo 3, transfer-then-compare 3, comparar-meses 2). With
nothing answered yet the interpreter called it a balance (gimnasio-meses 2). Every later turn then
inherited the wrong plan.

Now the turn re-runs the base across every account, single or comparison, keeping subject, window,
direction and grouping; with no base it is short guidance; with a question open it is still that
question's reply (Task 5). Driven over HTTP on the episodes stack, with a planner that answers the
turn the way the real model did, so only the deterministic path can make these pass.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation, Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import account_words
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic, Hybrid
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.infrastructure.planning.llm_query_planner import WINDOW_QUESTION

ALREADY = "Ya estaba consultado en todas tus cuentas."


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ReadsEveryAccountItsOwnWay(AsksForTheWindow):
    """What the real model made of «Todas mis cuentas» after an answer, on 2026-10-08.

    Handed a single answer to continue, it built «every movement, by account» over that answer's
    window (gimnasio-marta 2: direction ALL, group_by ACCOUNT, across every account). Handed none —
    a comparison is never a planner's base — it asked the period of «tus movimientos» (spotify-llevo
    3, comparar-meses 2). Everything else is `AsksForTheWindow`'s.
    """

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        if not account_words.asks_for_all_accounts(sentence):
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        if context.continuation is None:
            return PlanningResult.needs_clarification(
                WINDOW_QUESTION, FinancialPlanDraft(direction=Direction.ALL), Slot.WINDOW
            )
        base = context.continuation.plan
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.ALL,
                period=base.period,
                date_range=base.date_range,
                group_by=GroupBy.ACCOUNT,
                across_accounts=True,
            )
        )


class CallsItABalance:
    """The model behind the interpreter, reading «Todas mis cuentas» as money held — as it did for
    gimnasio-meses 2, where nothing had been answered yet."""

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        return Intent.ACCOUNT_BALANCE


def _stack() -> episodes.Stack:
    """Nothing chosen, on the shipped default (every account), with the real model's reading."""
    return asking_for_the_window_stack(settled=False, planner=ReadsEveryAccountItsOwnWay())


def _after(stack: episodes.Stack, opening: str, sentence: str) -> tuple[Any, dict[str, Any]]:
    """`sentence` after `opening`: the plan the opening ran, and the answer to `sentence`, with the
    ledger and the planner's log cleared between them."""
    first = ask(stack, opening)
    assert first["outcome"] == "PLANNED", first["text"]
    ran = stack.ledger.ran[-1]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()
    return ran, ask(stack, sentence, first["conversation"])


@pytest.mark.parametrize(
    "sentence",
    [
        "Todas mis cuentas",
        "en todas mis cuentas",
        "¿y en todas mis cuentas?",
        "pues en todas mis cuentas",
    ],
)
def test_every_account_after_an_answer_runs_that_answer_again(sentence: str) -> None:
    """gimnasio-ultimamente 2, gimnasio-marta 2: the gym, its window and its grouping, again."""
    stack = _stack()
    ran, answer = _after(stack, "¿cuánto gasté en el gimnasio este mes?", sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == [], "no model reads the turn"
    assert stack.ledger.ran == [ran], "the same question, across every account"
    (again,) = stack.ledger.ran
    assert isinstance(again, FinancialPlan)
    assert again.across_accounts and again.group_by is not GroupBy.ACCOUNT
    assert again.merchant_terms == ("gimnasio",)
    assert answer["conversation"]["account_selection_mode"] == "ALL", "stored as chosen"
    assert answer["text"].endswith(ALREADY), answer["text"]


def test_every_account_keeps_the_person_and_what_follows_inherits_it() -> None:
    """mandado-recibido 2 and 3: Marta Ruiz stays, and «Ampliar a tres meses» after it is Marta
    over three months, not all spending by account."""
    stack = _stack()
    ran, answer = _after(stack, "cuanto le e mandado a marta ruiz este mes?", "Todas mis cuentas")

    assert stack.ledger.ran == [ran]
    assert stack.planner.asked == []
    wider = ask(stack, "Ampliar a tres meses", answer["conversation"])

    assert wider["outcome"] == "PLANNED", wider["text"]
    plan = stack.ledger.ran[-1]
    assert isinstance(plan, FinancialPlan)
    assert plan.counterparties == ("Marta Ruiz",) and plan.direction is Direction.EXPENSE
    assert plan.period is not None and plan.period.period is Period.LAST_N_MONTHS
    assert plan.group_by is GroupBy.NONE


def test_every_account_after_a_comparison_runs_the_comparison_again() -> None:
    """transfer-then-compare 3, comparar-meses 2: both months, the fuel, every account — never the
    period of «tus movimientos»."""
    stack = _stack()
    ran = ask(stack, "¿Gasté más en gasolina en junio o en julio?")
    assert ran["outcome"] == "PLANNED", ran["text"]
    before = list(stack.ledger.ran)
    stack.ledger.ran.clear()

    answer = ask(stack, "Todas mis cuentas", ran["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert stack.ledger.ran == before
    assert all(
        isinstance(plan, FinancialPlan)
        and plan.categories == ("TRANSPORT_FUEL",)
        and plan.across_accounts
        for plan in stack.ledger.ran
    )
    assert answer["conversation"]["last_comparison_earlier"] is not None
    assert answer["conversation"]["account_selection_mode"] == "ALL"
    assert ALREADY in answer["text"]


def test_every_account_over_a_narrower_answer_widens_it_and_says_nothing_back() -> None:
    """Over the hucha alone the turn widens the question; the person said «todas» themselves, so
    nothing is said back (ruling R44), and not that it already was."""
    stack = _stack()
    first = ask(stack, "¿cuánto gasté en la hucha este mes?")
    narrowed = stack.ledger.ran[-1]
    assert narrowed.only_account_id == episodes.PIGGY, first["text"]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()

    answer = ask(stack, "Todas mis cuentas", first["conversation"])

    (wider,) = stack.ledger.ran
    assert wider.across_accounts and wider.only_account_id is None
    assert wider.period == narrowed.period
    assert ALREADY not in answer["text"] and "Consultado en" not in answer["text"]
    assert stack.planner.asked == []


def test_every_account_with_nothing_answered_is_guidance_never_the_balance() -> None:
    """gimnasio-meses 2: the first question failed, and «Todas mis cuentas» was the balance."""
    stack = _stack()
    app.dependency_overrides[turn_interpreter] = lambda: Hybrid(CallsItABalance(), Deterministic())

    answer = ask(stack, "Todas mis cuentas")

    assert answer["outcome"] == "GUIDANCE", answer["text"]
    assert "Tienes" not in answer["text"] and "entre tus cuentas" not in answer["text"]
    assert stack.ledger.ran == [] and stack.planner.asked == []
    assert answer["conversation"]["account_selection_mode"] == "ALL"


def test_every_account_after_an_answer_is_not_the_balance_either() -> None:
    """The same interpreter after an answer: the answer again, not money held."""
    stack = _stack()
    app.dependency_overrides[turn_interpreter] = lambda: Hybrid(CallsItABalance(), Deterministic())
    ran, answer = _after(stack, "¿cuánto gasté en el gimnasio este mes?", "Todas mis cuentas")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.ledger.ran == [ran]


def test_with_a_question_open_every_account_is_still_its_reply() -> None:
    """Task 5 unchanged: to «¿De qué periodo?» the account is absorbed and the period asked again,
    naming the subject."""
    stack = _stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["conversation"]["awaiting"] == "WINDOW"

    answer = ask(stack, "en todas mis cuentas", asked["conversation"])

    assert answer["outcome"] == "NEEDS_CLARIFICATION", answer["text"]
    assert "restaurantes" in answer["text"]
    assert answer["conversation"]["awaiting"] == "WINDOW"
    assert stack.ledger.ran == []


def test_by_account_is_a_breakdown_only_when_the_turn_says_so() -> None:
    """«por cuenta» is not the bare every-account turn, and is not re-run as one."""
    stack = _stack()
    _, answer = _after(
        stack, "¿cuánto gasté en el gimnasio este mes?", "en todas mis cuentas por cuenta"
    )

    assert ALREADY not in answer["text"]


@pytest.mark.parametrize(
    "sentence",
    [
        "en todas mis cuentas gracias",
        "Todas mis cuentas, gracias",
        "en todas mis cuentas por favor",
    ],
)
def test_courtesy_after_every_account_is_still_that_turn(sentence: str) -> None:
    """Round 2, M-1: «gracias» after it made the turn a question of its own again."""
    stack = _stack()
    ran, answer = _after(stack, "¿cuánto gasté en el gimnasio este mes?", sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert stack.ledger.ran == [ran]
