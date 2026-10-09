"""The engine's vertical slice, in process: interpret, ground, transition, execute, present, save.

The interpreter is scripted (it returns the operations a test gives it), and the bank is the
episodes stack's own fakes, so what is under test is everything between the two: which plans reach
the ledger, what the person reads, and which state is saved under which version.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from support.episodes import (
    ACCOUNT,
    HOLDINGS,
    PIGGY,
    TODAY,
    Accounts,
    Contacts,
    Directory,
    Ledger,
)

from finguard_orchestrator.application.engine import answer_turn
from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler, EngineAnswer
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Reset,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import (
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    Measure,
    Missing,
    QueryFocus,
    Scope,
)
from finguard_orchestrator.domain.engine.transitions import InvariantViolationError
from finguard_orchestrator.domain.enums import Direction, Metric, PlanningOutcome, Spoken
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore

JUNE = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
CATEGORIES = frozenset({"RESTAURANTS", "LEISURE", "GROCERIES", "TRANSPORT_FUEL", "SALARY"})
SOMEBODY_ELSE = UUID(int=200)
EVERY_ACCOUNT = "Consultado en todas tus cuentas."


def spending(**fields: Any) -> PlanFields:
    return PlanFields(direction=Direction.EXPENSE, **fields)


def asked(window: AbsoluteRange, *categories: str, **rest: Any) -> FinancialPlan:
    """The plan as the state remembers it: unscoped, the scope lives beside it."""
    return FinancialPlan(
        direction=Direction.EXPENSE, date_range=window, categories=categories, **rest
    )


def ran(window: AbsoluteRange, *categories: str, **rest: Any) -> FinancialPlan:
    """The plan as the ledger saw it: across every account unless the scope narrowed it."""
    return replace(asked(window, *categories), across_accounts=True, **rest)


class Scripted:
    """The interpreter, scripted: each call answers the next turn's operations, in order."""

    def __init__(self, *turns: tuple[Operation, ...]) -> None:
        self._turns = list(turns)
        self.calls = 0
        self.seen: list[tuple[str, DialogueState, dict[UUID, str]]] = []

    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        self.calls += 1
        self.seen.append((sentence, state, dict(account_names)))
        # A model call is a suspension: two turns in flight interleave here, as two requests do.
        await asyncio.sleep(0)
        return self._turns.pop(0)


class ByWindow(Ledger):
    """The episodes ledger, with each window's own total and count, and `whole` for a plan with
    no subject (the whole a share is a part of)."""

    def __init__(
        self,
        figures: Mapping[AbsoluteRange, tuple[int, int]],
        whole: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self._figures = figures
        self._whole = whole
        self.down = False

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **rest: object
    ) -> QueryAnswer:
        if self.down:
            raise BankingUnavailableError("timeout")
        answer = await super().run(plan, movements=movements, **rest)
        subject = plan.categories or plan.merchants or plan.counterparties
        if self._whole is not None and not subject:
            figure: tuple[int, int] | None = self._whole
        else:
            figure = self._figures.get(plan.date_range) if plan.date_range else None
        if figure is None:
            return answer
        total, count = figure
        return replace(
            answer, total_minor=total, movement_count=count, groups=(Group("total", total, count),)
        )


@dataclass
class Rig:
    handler: AnswerTurnHandler
    interpreter: Scripted
    ledger: ByWindow
    store: MemoryDialogueStore

    async def say(self, sentence: str, after: EngineAnswer | None = None) -> EngineAnswer:
        """One turn, echoing the conversation and version the previous answer left."""
        return await self.handler.answer(
            sentence,
            conversation_id=after.state.conversation_id if after else None,
            version=after.state.version if after else None,
            account_id=ACCOUNT,
            today=TODAY,
            reporting_timezone="Europe/Madrid",
        )


def a_rig(
    *turns: tuple[Operation, ...],
    figures: Mapping[AbsoluteRange, tuple[int, int]] | None = None,
    whole: tuple[int, int] | None = None,
) -> Rig:
    interpreter = Scripted(*turns)
    ledger = ByWindow(figures or {}, whole)
    store = MemoryDialogueStore()
    handler = AnswerTurnHandler(
        interpreter,
        store,
        ledger,
        Accounts(),
        Directory(),
        Contacts(),
        CATEGORIES,
        count_calls=lambda: interpreter.calls,
    )
    return Rig(handler, interpreter, ledger, store)


RESTAURANTS_IN_JULY = (Ask(spending(window=JULY, categories=("RESTAURANTS",))),)
JUNE_VS_JULY_IN_LEISURE = Compare(
    spending(categories=("LEISURE",)), PlanFields(window=JUNE), PlanFields(window=JULY)
)


# --- one query ---------------------------------------------------------------------------------


async def test_a_query_runs_and_becomes_the_focus() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY)

    turn = await rig.say("¿cuánto gasté en restaurantes en julio?")

    assert rig.ledger.plans == [ran(JULY, "RESTAURANTS")]
    assert turn.result is not None and turn.delegate_to is None
    assert turn.result.outcome == PlanningOutcome.PLANNED.value
    assert turn.result.text.startswith("465,56 € en restaurantes del 2026-07-01 al 2026-07-31")
    assert turn.result.text.endswith(EVERY_ACCOUNT)
    assert turn.result.total_minor == 46_556 and turn.result.movement_count == 3
    assert turn.result.plan_in_words == "del 2026-07-01 al 2026-07-31, gastos, restaurantes"
    assert turn.state.focus == QueryFocus(asked(JULY, "RESTAURANTS"))
    assert turn.state.version == 1
    assert turn.state.turns == (
        DialogueTurn("¿cuánto gasté en restaurantes en julio?", turn.result.plan_in_words),
    )
    assert await rig.store.load(turn.state.conversation_id, account_id=ACCOUNT) == turn.state
    assert turn.model_calls == 1
    # The sentence went to the interpreter, with the holder's accounts by name (ruling R15).
    sentence, _, names = rig.interpreter.seen[0]
    assert sentence == "¿cuánto gasté en restaurantes en julio?"
    assert names == {h.account_id: h.display_name for h in HOLDINGS}


async def test_a_follow_up_patch_keeps_the_window() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY, (Patch("focus", PlanFields(categories=("LEISURE",))),))

    first = await rig.say("¿cuánto en restaurantes en julio?")
    second = await rig.say("¿y en ocio?", first)

    assert rig.ledger.plans == [ran(JULY, "RESTAURANTS"), ran(JULY, "LEISURE")]
    assert second.state.focus == QueryFocus(asked(JULY, "LEISURE"))
    assert second.state.version == 2
    # The second turn was read against what the first one left on screen.
    assert rig.interpreter.seen[1][1] == first.state


async def test_a_scope_narrows_the_plan_and_is_said_by_name() -> None:
    rig = a_rig((Ask(spending(window=JULY, categories=("RESTAURANTS",), accounts=("hucha",))),))

    turn = await rig.say("¿cuánto en restaurantes en julio en la hucha?")

    assert rig.ledger.plans == [replace(asked(JULY, "RESTAURANTS"), only_account_ids=(PIGGY,))]
    assert turn.state.scope == Scope((PIGGY,))
    assert turn.result is not None and turn.result.text.endswith("Consultado en Hucha.")


async def test_a_top_n_lists_the_n_largest() -> None:
    rig = a_rig((Ask(spending(window=JULY, limit=3)),))

    turn = await rig.say("mis 3 mayores gastos de julio")

    ranking = TopMovements(direction=Direction.EXPENSE, date_range=JULY, across_accounts=True)
    assert rig.ledger.ran == [ranking.at_rank(1), ranking.at_rank(2), ranking.at_rank(3)]
    assert turn.result is not None
    assert turn.result.text.startswith("Tus 3 mayores gastos en julio de 2026 fueron:\n  1. Cepsa")
    assert turn.result.movement_count == 3 and len(turn.result.movements) == 3
    assert turn.result.plan_in_words == (
        "del 2026-07-01 al 2026-07-31, gastos, nº 1 por importe, top 3"
    )


# --- a pair, its percentage, one of its sides ---------------------------------------------------


async def test_comparison_then_percent_then_select_operand() -> None:
    rig = a_rig(
        (JUNE_VS_JULY_IN_LEISURE,),
        (ChangeMeasure(Measure.PERCENT_CHANGE),),
        (SelectOperand("earlier"),),
        figures={JUNE: (10_280, 4), JULY: (15_946, 6)},
    )
    june, july = ran(JUNE, "LEISURE"), ran(JULY, "LEISURE")

    compared = await rig.say("compara junio y julio en ocio")
    assert rig.ledger.plans == [june, july]
    assert compared.result is not None
    assert compared.result.text == (
        "En junio de 2026: 102,80 €; en julio de 2026: 159,46 €. "
        "El gasto en ocio fue 56,66 € mayor en julio de 2026.\n" + EVERY_ACCOUNT
    )
    assert compared.result.difference_minor == 5_666 and compared.result.total_minor is None
    assert [g.key for g in compared.result.groups] == ["en junio de 2026", "en julio de 2026"]
    pair = ComparisonPlan(asked(JUNE, "LEISURE"), asked(JULY, "LEISURE"))
    assert compared.state.focus == ComparisonFocus(pair, Measure.AMOUNT)

    percent = await rig.say("¿en porcentaje?", compared)
    assert rig.ledger.plans[2:] == [july, june]
    assert percent.result is not None
    assert percent.result.text == (
        "El gasto aumentó un 55,12 %: de 102,80 € en junio de 2026 a 159,46 € en julio de 2026.\n"
        + EVERY_ACCOUNT
    )
    assert percent.state.focus == ComparisonFocus(pair, Measure.PERCENT_CHANGE)

    earlier = await rig.say("¿y junio solo?", percent)
    assert rig.ledger.plans[4:] == [june]
    assert earlier.result is not None and earlier.result.text.startswith("102,80 € en ocio")
    assert earlier.state.focus == QueryFocus(asked(JUNE, "LEISURE"))
    assert earlier.state.version == 3


async def test_an_undefined_percentage_still_says_both_amounts() -> None:
    # Two operations in one turn: the pair, then its percentage. Nothing was spent in June, so
    # there is no percentage, and the answer still says both figures and why.
    rig = a_rig(
        (JUNE_VS_JULY_IN_LEISURE, ChangeMeasure(Measure.PERCENT_CHANGE)),
        figures={JUNE: (0, 0), JULY: (2_306, 2)},
    )

    turn = await rig.say("junio vs julio en ocio, en porcentaje")

    assert turn.result is not None
    assert turn.result.text == (
        "No hay variación porcentual: en junio de 2026 no gastaste nada en ocio; "
        "en julio de 2026, 23,06 €.\n" + EVERY_ACCOUNT
    )


@pytest.mark.parametrize(
    ("figures", "sides"),
    [
        (
            {JUNE: (0, 0), JULY: (4_000, 2)},
            "en junio de 2026 no hubo movimientos en ocio; en julio de 2026, 20,00 €",
        ),
        (
            {JUNE: (4_000, 2), JULY: (0, 0)},
            "en junio de 2026, 20,00 €; en julio de 2026 no hubo movimientos en ocio",
        ),
        (
            {JUNE: (0, 0), JULY: (0, 0)},
            "en junio de 2026 no hubo movimientos en ocio; "
            "en julio de 2026 no hubo movimientos en ocio",
        ),
    ],
    ids=["earlier-empty", "latest-empty", "both-empty"],
)
async def test_an_average_with_an_empty_side_has_no_percentage(
    figures: dict[AbsoluteRange, tuple[int, int]], sides: str
) -> None:
    # A side with no movements has no average: «disminuyó un 100 %… a sin movimientos» would be
    # a change from a figure to the absence of one.
    averages = Compare(
        spending(categories=("LEISURE",), metric=Metric.AVERAGE),
        PlanFields(window=JUNE),
        PlanFields(window=JULY),
    )
    rig = a_rig((averages, ChangeMeasure(Measure.PERCENT_CHANGE)), figures=figures)

    turn = await rig.say("la media de junio frente a julio en ocio, en porcentaje")

    assert turn.result is not None
    assert turn.result.text == f"No hay variación porcentual: {sides}.\n{EVERY_ACCOUNT}"


@pytest.mark.parametrize(
    ("asked", "said"),
    [
        (
            spending(window=JULY, people=("Marta Solis",)),
            "Tus gastos a Marta Solis Vega representan el 25,00 % de tus gastos totales "
            "del 2026-07-01 al 2026-07-31: 500,00 € de 2.000,00 €.",
        ),
        (
            PlanFields(direction=Direction.INCOME, window=JULY, categories=("SALARY",)),
            "Tus ingresos en nómina representan el 25,00 % de tus ingresos totales "
            "del 2026-07-01 al 2026-07-31: 500,00 € de 2.000,00 €.",
        ),
    ],
    ids=["a-person", "income"],
)
async def test_a_share_says_what_its_part_is(asked: PlanFields, said: str) -> None:
    # The writer's label knows categories and shops of spending: a person was dropped («Tus
    # gastos»), and a payslip was «el gasto en nómina».
    rig = a_rig(
        (Ask(asked), ChangeMeasure(Measure.SHARE)), figures={JULY: (50_000, 1)}, whole=(200_000, 9)
    )

    turn = await rig.say("¿qué parte fue eso en julio?")

    assert turn.result is not None
    assert turn.result.text == f"{said}\n{EVERY_ACCOUNT}"


# --- a question asked back ---------------------------------------------------------------------


async def test_a_pending_window_is_asked_naming_the_subject_and_completed_by_the_reply() -> None:
    rig = a_rig(
        (Ask(spending(categories=("RESTAURANTS",))),),
        (Patch("pending", PlanFields(window=JULY)),),
    )

    question = await rig.say("¿cuánto gasté en restaurantes?")
    assert question.result is not None
    assert question.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert question.result.text == "¿De qué periodo quieres ver tus gastos en restaurantes?"
    assert question.result.question == question.result.text
    assert rig.ledger.plans == []
    assert question.state.pending is not None
    assert question.state.pending.missing is Missing.WINDOW
    assert question.state.version == 1

    answered = await rig.say("en julio", question)
    assert rig.ledger.plans == [ran(JULY, "RESTAURANTS")]
    assert answered.state.pending is None
    assert answered.state.focus == QueryFocus(asked(JULY, "RESTAURANTS"))


async def test_a_window_not_begun_is_said_when_it_is_asked_for_again() -> None:
    october = AbsoluteRange(date(2026, 10, 1), date(2026, 10, 31))  # after TODAY, 5 August
    rig = a_rig((Ask(spending(window=october, categories=("RESTAURANTS",))),))

    turn = await rig.say("¿cuánto gastaré en restaurantes en octubre?")

    assert turn.result is not None
    assert turn.result.text == (
        "Ese periodo todavía no ha empezado. "
        "¿De qué periodo quieres ver tus gastos en restaurantes?"
    )


async def test_a_past_window_is_never_said_not_to_have_begun() -> None:
    # M-3: the open question is about a person, with July said. A period question over it must
    # not claim that July has not begun.
    rig = a_rig(
        (Ask(spending(window=JULY, people=("Pepe",))),),
        (Clarify(Missing.WINDOW),),
    )
    first = await rig.say("¿cuánto le pasé a Pepe en julio?")

    turn = await rig.say("¿y de cuándo?", first)

    assert turn.result is not None
    assert turn.result.text == "¿De qué periodo quieres ver lo que le mandaste a Pepe?"


async def test_an_unknown_person_is_asked_about_by_name() -> None:
    rig = a_rig((Ask(spending(window=JULY, people=("Pepe",))),))

    turn = await rig.say("¿cuánto le pasé a Pepe en julio?")

    assert turn.result is not None
    assert turn.result.text == "No encuentro a Pepe entre tus contactos."
    assert rig.ledger.plans == []


async def test_a_question_from_the_model_never_quotes_its_own_words() -> None:
    # Ruling R21: only names grounding found are quoted. A figure the model wrote into `about`
    # reaches neither the person nor the turn the next prompt quotes back.
    rig = a_rig((Clarify(Missing.SUBJECT, "gastaste 999,99 €"),))

    turn = await rig.say("¿y eso?")

    assert turn.result is not None
    assert (
        turn.result.text == "¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo."
    )
    assert "999,99" not in turn.state.turns[-1].answered


async def test_an_ambiguous_person_is_asked_about_by_the_candidates() -> None:
    # The episodes' contacts hold two Martas: «No encuentro a Marta» would be false (M-1).
    rig = a_rig((Ask(spending(window=JULY, people=("Marta",))),))

    turn = await rig.say("¿cuánto le pasé a Marta en julio?")

    assert turn.result is not None
    assert turn.result.text == "¿A cuál te refieres: Marta Solis Vega o Marta Ruiz Gomez?"
    assert turn.state.pending is not None and turn.state.pending.missing is Missing.PERSON
    assert rig.ledger.plans == []


# --- turns that ask the ledger nothing ----------------------------------------------------------


async def test_reset_clears_focus() -> None:
    rig = a_rig(
        (Ask(spending(window=JULY, categories=("RESTAURANTS",), accounts=("hucha",))),),
        (Reset(),),
    )

    first = await rig.say("¿cuánto en restaurantes en julio en la hucha?")
    reset = await rig.say("empecemos de nuevo", first)

    assert reset.state.focus is None and reset.state.pending is None
    assert reset.state.scope == Scope((PIGGY,))
    assert reset.state.version == 2
    assert reset.result is not None and reset.result.outcome == Spoken.GUIDANCE.value
    assert len(rig.ledger.plans) == 1


async def test_balance_does_not_touch_the_focus() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY, (Balance(),))

    first = await rig.say("¿cuánto en restaurantes en julio?")
    balance = await rig.say("¿y cuánto tengo?", first)

    assert balance.result is not None and balance.result.outcome == Spoken.BALANCE.value
    assert balance.result.text.startswith("Tienes 8.571,98 € entre tus cuentas")
    assert balance.result.total_minor == 857_198
    assert balance.state.focus == first.state.focus
    assert balance.state.version == 2
    assert len(rig.ledger.plans) == 1


async def test_an_ask_then_a_balance_answers_the_balance() -> None:
    # Ruling R9: several works in one turn, the last is answered; the ask before it is not run and
    # does not become what is on screen.
    rig = a_rig((*RESTAURANTS_IN_JULY, Balance(accounts=("hucha",))))

    turn = await rig.say("restaurantes en julio y el saldo de la hucha")

    assert turn.result is not None and turn.result.text == "Tienes 6.415,50 € en Hucha."
    assert rig.ledger.plans == []
    assert turn.state.focus is None


async def test_smalltalk_is_answered_from_a_template() -> None:
    rig = a_rig((Smalltalk("GREETING"),))

    turn = await rig.say("hola")

    assert turn.result is not None and turn.result.outcome == Spoken.COURTESY.value
    assert rig.ledger.plans == []
    assert turn.state.version == 1


async def test_delegate_returns_no_result_and_keeps_state() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY, (Delegate("PAYMENT"),))

    first = await rig.say("¿cuánto en restaurantes en julio?")
    delegated = await rig.say("hazle un bizum de 20 a Marta", first)

    assert delegated.result is None and delegated.delegate_to == "PAYMENT"
    assert delegated.state == first.state
    assert await rig.store.load(first.state.conversation_id, account_id=ACCOUNT) == first.state
    assert len(rig.ledger.plans) == 1


# --- versions and failures ----------------------------------------------------------------------


async def test_a_stale_version_is_refused_and_nothing_changes() -> None:
    # Review Focus 2: a double submit. The second copy carries the version the first one moved.
    rig = a_rig(RESTAURANTS_IN_JULY, (Patch("focus", PlanFields(categories=("LEISURE",))),))
    first = await rig.say("¿cuánto en restaurantes en julio?")

    with pytest.raises(StaleDialogueError) as refused:
        await rig.handler.answer(
            "¿cuánto en restaurantes en julio?",
            conversation_id=first.state.conversation_id,
            version=0,
            account_id=ACCOUNT,
            today=TODAY,
            reporting_timezone="Europe/Madrid",
        )

    assert refused.value.current == first.state
    assert await rig.store.load(first.state.conversation_id, account_id=ACCOUNT) == first.state
    assert rig.interpreter.calls == 1
    assert len(rig.ledger.plans) == 1


async def test_another_holders_conversation_looks_unknown() -> None:
    # Ruling R20: another account naming this conversation and its version gets a conversation of
    # its own, and nothing of the holder's: not in its prompt, and not in a refusal.
    rig = a_rig(
        (Ask(spending(window=JULY, categories=("RESTAURANTS",), accounts=("hucha",))),),
        (Smalltalk("GREETING"),),
    )
    theirs = await rig.say("mi secreto: restaurantes en julio en la hucha")

    async def somebody_else_says(version: int | None) -> EngineAnswer:
        return await rig.handler.answer(
            "hola",
            conversation_id=theirs.state.conversation_id,
            version=version,
            account_id=SOMEBODY_ELSE,
            today=TODAY,
            reporting_timezone="Europe/Madrid",
        )

    with pytest.raises(StaleDialogueError) as refused:
        await somebody_else_says(theirs.state.version)
    stranger = refused.value.current
    assert stranger.conversation_id != theirs.state.conversation_id
    assert (stranger.version, stranger.turns, stranger.scope) == (0, (), Scope())

    mine = await somebody_else_says(None)
    _, seen, _ = rig.interpreter.seen[-1]
    assert (seen.turns, seen.scope, seen.focus) == ((), Scope(), None)
    assert mine.state.conversation_id != theirs.state.conversation_id
    assert mine.state.version == 1 and len(mine.state.turns) == 1
    assert await rig.store.load(theirs.state.conversation_id, account_id=ACCOUNT) == theirs.state


async def test_two_concurrent_turns_on_one_version_one_wins() -> None:
    rig = a_rig(
        RESTAURANTS_IN_JULY,
        (Patch("focus", PlanFields(categories=("LEISURE",))),),
        (Patch("focus", PlanFields(categories=("GROCERIES",))),),
    )
    first = await rig.say("¿cuánto en restaurantes en julio?")

    outcomes = await asyncio.gather(
        rig.say("¿y en ocio?", first), rig.say("¿y en súper?", first), return_exceptions=True
    )

    refused = [o for o in outcomes if isinstance(o, StaleDialogueError)]
    won = [o for o in outcomes if isinstance(o, EngineAnswer)]
    assert len(refused) == 1 and len(won) == 1
    assert won[0].state.version == 2
    assert (
        await rig.store.load(first.state.conversation_id, account_id=ACCOUNT)
        == won[0].state
        == refused[0].current
    )


async def test_a_banking_failure_saves_nothing() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY, (Patch("focus", PlanFields(categories=("LEISURE",))),))
    first = await rig.say("¿cuánto en restaurantes en julio?")
    rig.ledger.down = True

    failed = await rig.say("¿y en ocio?", first)

    assert failed.result is not None and failed.result.outcome == "BANKING_UNAVAILABLE"
    assert failed.state == first.state
    assert await rig.store.load(first.state.conversation_id, account_id=ACCOUNT) == first.state


async def test_an_interpreter_that_cannot_answer_saves_nothing() -> None:
    rig = a_rig(RESTAURANTS_IN_JULY)
    first = await rig.say("¿cuánto en restaurantes en julio?")

    async def unreachable(*_: object, **__: object) -> tuple[Operation, ...]:
        raise InterpretationFailedError("the interpreter's model failed: TimeoutError")

    rig.interpreter.interpret = unreachable  # type: ignore[method-assign]
    failed = await rig.say("¿y en ocio?", first)

    assert failed.result is not None
    assert failed.result.outcome == PlanningOutcome.PROVIDER_ERROR.value
    assert failed.state == first.state
    assert await rig.store.load(first.state.conversation_id, account_id=ACCOUNT) == first.state


async def test_an_invariant_violation_is_logged_and_asked_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig = a_rig(RESTAURANTS_IN_JULY)

    def broken(*_: object, **__: object) -> object:
        raise InvariantViolationError("a patch moved categories without naming it")

    monkeypatch.setattr(answer_turn, "apply", broken)
    with caplog.at_level(logging.ERROR):
        turn = await rig.say("¿cuánto en restaurantes en julio?")

    assert turn.result is not None
    assert turn.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert rig.ledger.plans == []
    assert any("engine.invariant_violation" in record.getMessage() for record in caplog.records)
