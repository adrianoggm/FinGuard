"""The investigation graph end to end, in process, with fakes for every model and for the bank.

The interpreter is scripted (it returns the operations a test gives it), the assessor is scripted
(it returns the steps a test gives it), and the ledger answers each window by merchant from a table.
What is under test is what the graph adds and what it must keep: the loop and its budgets, the
allow-list, the narrative's numbers, failure and resume, duplicate and concurrent requests, the
checkpoint's contents and its version, and that money never runs here.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from support.episodes import ACCOUNT, Accounts, Contacts, Directory

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler, EngineAnswer
from finguard_orchestrator.application.graph import graph as investigation_graph
from finguard_orchestrator.application.graph import graph_turn
from finguard_orchestrator.application.graph.graph import GraphPorts
from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Compare,
    Delegate,
    Operation,
    PlanFields,
)
from finguard_orchestrator.domain.engine.state import ComparisonFocus, DialogueState
from finguard_orchestrator.domain.engine.transitions import apply
from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.investigation_assessor import (
    AssessmentFailedError,
    Inspect,
    InvestigationSummary,
    Stop,
)
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
)
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore
from finguard_orchestrator.infrastructure.graph.memory_turn_reservations import (
    MemoryTurnReservations,
)

TODAY = date(2026, 8, 5)
JUNE = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
SENTENCE = "¿En qué comercios gasto más que antes y se debe a más compras o a importes mayores?"
BY_MERCHANT = (
    Compare(
        PlanFields(direction=Direction.EXPENSE, group_by=GroupBy.MERCHANT),
        PlanFields(window=JUNE),
        PlanFields(window=JULY),
    ),
)
GROCERIES_IN_JULY = (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),)
# June against July. Amazon rose by both, Cepsa by bigger amounts, Mercadona by more purchases.
FIGURES: dict[AbsoluteRange, tuple[Group, ...]] = {
    JUNE: (Group("Mercadona", 10_000, 4), Group("Amazon", 5_000, 1), Group("Cepsa", 6_000, 2)),
    JULY: (Group("Mercadona", 15_000, 6), Group("Amazon", 17_000, 2), Group("Cepsa", 9_000, 2)),
}
LARGEST = {"Amazon": 15_000, "Cepsa": 4_000}
GROUNDED_AMAZON = "En Amazon, la subida se debe a una compra: sin la de 150,00 € del 2026-07-12"


class Calls:
    """Model calls in this request, as `counting_provider` counts them."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> int:
        return self.count


class Interpreter:
    def __init__(self, calls: Calls, *turns: tuple[Operation, ...]) -> None:
        self._calls = calls
        self._turns = list(turns)
        self.failure: Exception | None = None
        self.sentences: list[str] = []

    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        self._calls.count += 1
        self.sentences.append(sentence)
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        await asyncio.sleep(0)
        return self._turns.pop(0)


class Assessor:
    def __init__(self, calls: Calls, *steps: Inspect | Stop | Exception) -> None:
        self._calls = calls
        self._steps = list(steps)
        self.seen: list[InvestigationSummary] = []

    async def assess(self, summary: InvestigationSummary) -> Inspect | Stop:
        self._calls.count += 1
        self.seen.append(summary)
        step = self._steps.pop(0) if self._steps else Stop()
        if isinstance(step, Exception):
            raise step
        return step


class MerchantLedger:
    """Each window by merchant from `FIGURES`; the largest purchase of a merchant from `LARGEST`.
    `failures` are raised, one per read, before the read is answered."""

    def __init__(self) -> None:
        self.ran: list[FinancialPlan | TopMovements] = []
        self.failures: list[Exception] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self._fail()
        self.ran.append(plan)
        assert plan.date_range is not None
        groups = FIGURES[plan.date_range] if plan.group_by is GroupBy.MERCHANT else ()
        total = sum(g.total_minor for g in FIGURES[plan.date_range])
        return QueryAnswer("EUR", total, 9, groups=groups)

    async def ranked(self, ranking: TopMovements, **_: object) -> Movement | None:
        self._fail()
        self.ran.append(ranking)
        (merchant,) = ranking.merchants
        if merchant not in LARGEST:
            return None
        moment = datetime(2026, 7, 12, 18, 30, tzinfo=UTC)
        return Movement(moment, f"Compra privada en {merchant}", LARGEST[merchant], "EXPENSE")

    def _fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)


class World:
    def __init__(
        self, *turns: tuple[Operation, ...], steps: tuple[Inspect | Stop | Exception, ...] = ()
    ) -> None:
        self.calls = Calls()
        self.interpreter = Interpreter(self.calls, *turns)
        self.assessor = Assessor(self.calls, *steps)
        self.ledger = MerchantLedger()
        self.store = MemoryDialogueStore()
        self.saver = InMemorySaver()
        self.reservations = MemoryTurnReservations()
        self.handler = self.build()

    def build(self) -> GraphTurnHandler:
        ports = GraphPorts(
            self.interpreter,
            self.assessor,
            self.store,
            self.ledger,
            Accounts(),
            Directory(),
            Contacts(),
            frozenset({"GROCERIES", "RESTAURANTS"}),
            count_calls=self.calls,
        )
        return GraphTurnHandler(ports, self.reservations, self.saver)

    async def ask(
        self,
        *,
        conversation_id: UUID | None = None,
        version: int | None = None,
        request_id: str | None = "req-1",
        sentence: str = SENTENCE,
    ) -> EngineAnswer:
        return await self.handler.answer(
            sentence,
            conversation_id=conversation_id,
            version=version,
            account_id=ACCOUNT,
            today=TODAY,
            reporting_timezone="Europe/Madrid",
            request_id=request_id,
        )


def text_of(answer: EngineAnswer) -> str:
    assert answer.result is not None
    return answer.result.text


# --- the capability -------------------------------------------------------------------------------


async def test_the_current_engine_refuses_a_comparison_by_merchant() -> None:
    """The control (Step 2): the canonical transition asks back rather than drop the breakdown."""
    grounds = Grounds(frozenset(), (), ())

    moved = apply(DialogueState(UUID(int=1)), BY_MERCHANT, today=TODAY, grounds=grounds)

    assert moved.work is None and moved.reply is not None


async def test_it_names_the_risen_merchants_with_their_cause_and_inspects_one() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))

    answer = await world.ask()

    text = text_of(answer)
    assert "  · Amazon: 50,00 € (1 compra) → 170,00 € (2 compras); por más compras y por " in text
    assert "  · Mercadona: 100,00 € (4 compras) → 150,00 € (6 compras); por más compras." in text
    assert "  · Cepsa: 60,00 € (2 compras) → 90,00 € (2 compras); por importes mayores." in text
    assert GROUNDED_AMAZON in text
    assert text.endswith("Consultado en todas tus cuentas.")
    # Two windows by merchant, then the one inspection: three banking executions.
    assert [type(p).__name__ for p in world.ledger.ran] == [
        "FinancialPlan",
        "FinancialPlan",
        "TopMovements",
    ]
    assert answer.model_calls == 3
    # The canonical state moved once, to the pair of totals the transition validated.
    assert answer.state.version == 1
    assert isinstance(answer.state.focus, ComparisonFocus)
    assert answer.state.turns[-1].said == SENTENCE


async def test_reads_carry_the_validated_window_scope_and_filters() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))

    await world.ask()

    earlier, latest, ranking = world.ledger.ran
    assert isinstance(earlier, FinancialPlan) and isinstance(latest, FinancialPlan)
    assert (earlier.date_range, latest.date_range) == (JUNE, JULY)
    assert earlier.group_by is latest.group_by is GroupBy.MERCHANT
    assert earlier.across_accounts and latest.across_accounts
    assert isinstance(ranking, TopMovements)
    assert (ranking.merchants, ranking.date_range, ranking.rank) == (("Amazon",), JULY, 1)
    assert ranking.across_accounts


async def test_the_assessor_sees_a_value_minimised_summary_and_never_the_sentence() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))

    await world.ask()

    first = world.assessor.seen[0]
    assert first.may_inspect == ("Amazon", "Cepsa")
    assert [(m.merchant, m.cause) for m in first.risen] == [
        ("Amazon", "BOTH"),
        ("Mercadona", "MORE_PURCHASES"),
        ("Cepsa", "BIGGER_AMOUNTS"),
    ]
    said = json.dumps([repr(summary) for summary in world.assessor.seen])
    assert SENTENCE not in said and "Compra privada" not in said
    assert world.assessor.seen[1].may_inspect == ("Cepsa",)


async def test_with_no_cause_to_investigate_no_assessment_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(BY_MERCHANT)
    monkeypatch.setitem(FIGURES, JULY, (Group("Mercadona", 15_000, 6),))

    answer = await world.ask()

    assert world.assessor.seen == []
    assert answer.model_calls == 1
    assert "por más compras." in text_of(answer)


# --- the loop, its budgets and the allow-list ----------------------------------------------------


async def test_the_loop_stops_at_the_bank_budget_with_a_grounded_partial_answer() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Inspect("Cepsa"), Stop("")))

    answer = await world.ask()

    assert len(world.ledger.ran) == investigation_graph.MAX_BANK_EXECUTIONS
    assert text_of(answer).count("· ") == 3
    assert "No he investigado más" in text_of(answer)
    assert "Cepsa, la" not in text_of(answer)  # never inspected


async def test_the_loop_stops_at_the_model_budget() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"),))
    world.calls.count = 0

    class Repairing(Interpreter):
        async def interpret(self, *args: Any, **kwargs: Any) -> tuple[Operation, ...]:
            self._calls.count += 1  # a repair call: the interpreter used two
            return await super().interpret(*args, **kwargs)

    world.interpreter = Repairing(world.calls, BY_MERCHANT)
    world.handler = world.build()

    answer = await world.ask()

    # Two interpreter calls and one assessment: the second assessment is never asked.
    assert answer.model_calls == investigation_graph.MAX_MODEL_CALLS
    assert len(world.assessor.seen) == 1
    assert "No he investigado más" in text_of(answer)


@pytest.mark.parametrize("asked", ["Mercadona", "Spotify", "Marta"])
async def test_an_inspection_outside_the_allow_list_is_refused_without_a_bank_read(
    asked: str,
) -> None:
    """Mercadona rose by more purchases (one purchase cannot explain it); Spotify is not in the
    result; Marta is not a merchant at all. None of them reaches the bank."""
    world = World(BY_MERCHANT, steps=(Inspect(asked),))

    answer = await world.ask()

    assert len(world.ledger.ran) == 2
    assert "No he investigado más" in text_of(answer)
    assert answer.state.version == 1


# --- no unsupported numeric claims ----------------------------------------------------------------


async def test_a_narrative_quoting_grounded_figures_is_kept() -> None:
    why = "En Amazon pasaste de 1 compra a 2, y la de 150,00 € del 2026-07-12 explica la subida."
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop(why)))

    answer = await world.ask()

    assert why in text_of(answer)


@pytest.mark.parametrize(
    "why",
    [
        "Amazon ha subido un 240 % por la subida de precios.",
        "Gastas 120,00 € más en Amazon.",
        "En Amazon pagaste 150 € de una vez.",
    ],
)
async def test_a_narrative_with_a_number_no_bank_result_holds_is_refused(why: str) -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop(why)))

    answer = await world.ask()

    text = text_of(answer)
    assert why not in text
    # The grounded answer still leaves, whole.
    assert GROUNDED_AMAZON in text
    assert answer.state.version == 1


# --- failure and recovery -------------------------------------------------------------------------


async def test_an_assessor_outage_answers_with_what_the_bank_already_said() -> None:
    world = World(BY_MERCHANT, steps=(AssessmentFailedError("down"),))

    answer = await world.ask()

    assert "· Amazon" in text_of(answer)
    assert "No he investigado más" in text_of(answer)
    assert answer.state.version == 1


async def test_an_interpreter_outage_saves_nothing() -> None:
    world = World(BY_MERCHANT)
    world.interpreter.failure = InterpretationFailedError("down")

    answer = await world.ask()

    assert answer.result is not None and answer.result.outcome == "PROVIDER_ERROR"
    assert answer.state.version == 0
    assert world.ledger.ran == []


async def test_a_bank_outage_mid_loop_resumes_without_asking_any_model_again() -> None:
    """The same request after an outage resumes at the read that failed: the interpreter and the
    first assessment are not asked again, and the conversation moves exactly once."""
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))
    # The two windows read, then the inspection fails.
    real_ranked = world.ledger.ranked
    outage = [BankingUnavailableError("down")]

    async def ranked_once_down(ranking: TopMovements, **rest: object) -> Movement | None:
        if outage:
            raise outage.pop()
        return await real_ranked(ranking, **rest)

    world.ledger.ranked = ranked_once_down  # type: ignore[method-assign]

    failed = await world.ask(request_id="req-1")
    assert failed.result is not None and failed.result.outcome == "BANKING_UNAVAILABLE"
    assert failed.state.version == 0
    calls_after_failure = world.calls.count

    resumed = await world.ask(request_id="req-1")

    assert GROUNDED_AMAZON in text_of(resumed)
    assert resumed.state.version == 1
    # Resumed at the inspection: no interpreter call, only the final assessment.
    assert len(world.interpreter.sentences) == 1
    assert world.calls.count == calls_after_failure + 1
    assert [type(p).__name__ for p in world.ledger.ran] == [
        "FinancialPlan",
        "FinancialPlan",
        "TopMovements",
    ]


async def test_a_failed_turn_can_be_taken_over_by_a_new_request() -> None:
    world = World(BY_MERCHANT, BY_MERCHANT, steps=(Stop(""), Stop("")))
    world.ledger.failures = [BankingUnavailableError("down")]

    await world.ask(request_id="req-1")
    answer = await world.ask(request_id="req-2")

    assert answer.state.version == 1


# --- duplicate and concurrent requests ------------------------------------------------------------


async def test_a_duplicate_request_replays_its_answer_without_model_or_bank_work() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))
    first = await world.ask(request_id="req-1")
    calls, reads = world.calls.count, len(world.ledger.ran)

    again = await world.ask(request_id="req-1")

    assert again == first
    assert (world.calls.count, len(world.ledger.ran)) == (calls, reads)
    assert (await world.store.load(first.state.conversation_id, account_id=ACCOUNT)) == first.state


async def test_a_duplicate_of_a_follow_up_turn_replays_too() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, steps=(Stop(""),))
    first = await world.ask(request_id="req-1")
    conversation = first.state.conversation_id

    second = await world.ask(conversation_id=conversation, version=1, request_id="req-2")
    again = await world.ask(conversation_id=conversation, version=1, request_id="req-2")

    assert again == second and second.state.version == 2


async def test_another_request_against_a_version_a_turn_is_in_flight_on_is_stale() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT, steps=(Stop(""), Stop("")))
    first = await world.ask(request_id="req-1")
    conversation = first.state.conversation_id

    async def turn(request_id: str) -> EngineAnswer | Exception:
        try:
            return await world.ask(conversation_id=conversation, version=1, request_id=request_id)
        except StaleDialogueError as stale:
            return stale

    outcomes = await asyncio.gather(turn("req-2"), turn("req-3"))

    answered = [o for o in outcomes if isinstance(o, EngineAnswer)]
    stale = [o for o in outcomes if isinstance(o, StaleDialogueError)]
    assert len(answered) == 1 and len(stale) == 1
    assert stale[0].current.version == 1  # the state the stale request was made against
    held = await world.store.load(conversation, account_id=ACCOUNT)
    assert held is not None and held.version == 2


async def test_a_version_moved_by_another_engine_meanwhile_loses_the_compare_and_set() -> None:
    """The final compare-and-set still decides: a save made elsewhere between the reservation and
    the commit makes this turn stale, and it saves nothing."""
    world = World(BY_MERCHANT, steps=(Stop(""),))
    store = world.store

    real_save = store.save

    async def raced_save(state: DialogueState, **kwargs: Any) -> DialogueState:
        await real_save(replace(state, turns=()), **kwargs)  # somebody else's turn lands first
        return await real_save(state, **kwargs)

    store.save = raced_save  # type: ignore[method-assign]

    with pytest.raises(StaleDialogueError):
        await world.ask()


async def test_a_stale_version_is_refused_before_any_work() -> None:
    world = World(GROCERIES_IN_JULY)
    first = await world.ask()
    calls = world.calls.count

    with pytest.raises(StaleDialogueError):
        await world.ask(conversation_id=first.state.conversation_id, version=0, request_id="r2")

    assert world.calls.count == calls


# --- the checkpoint -------------------------------------------------------------------------------


async def test_the_checkpoint_holds_no_sentence_no_prompt_and_no_movement_text() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop("Nada más que añadir.")))

    await world.ask()

    written = [found.checkpoint["channel_values"] for found in world.saver.list(None)]
    pending = [write for found in world.saver.list(None) for write in found.pending_writes or ()]
    dumped = repr(written) + repr(pending)
    assert written
    assert SENTENCE not in dumped
    assert "Compra privada" not in dumped
    assert "Summary:" not in dumped  # the assessor's prompt
    final = written[0]
    assert final["graph_version"] == investigation_graph.GRAPH_VERSION
    assert final["schema_version"] == investigation_graph.STATE_SCHEMA_VERSION
    assert final["inspected"] == [["Amazon", 15_000, "2026-07-12T18:30:00+00:00"]]


async def test_a_checkpoint_from_another_graph_version_is_not_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(BY_MERCHANT, BY_MERCHANT, steps=(Inspect("Amazon"), Stop("")))
    world.ledger.failures = [BankingUnavailableError("down")]
    await world.ask(request_id="req-1")  # fails at the first read, checkpointed before it

    # The deployed graph is now a later version than the one that wrote the checkpoint.
    monkeypatch.setattr(graph_turn, "GRAPH_VERSION", "investigation/2")
    answer = await world.ask(request_id="req-1")

    # Started again: the interpreter was asked a second time, and the turn still saved once.
    assert len(world.interpreter.sentences) == 2
    assert answer.state.version == 1
    assert GROUNDED_AMAZON in text_of(answer)


# --- what never runs here -------------------------------------------------------------------------


@pytest.mark.parametrize("to", ["PAYMENT", "TRANSFER", "BOOKING"])
async def test_a_money_flow_leaves_the_graph_unsaved_and_unread(to: Any) -> None:
    world = World((Delegate(to),))

    answer = await world.ask(sentence="Hazme una transferencia de 50 euros a Marta")

    assert answer.result is None and answer.delegate_to == to
    assert answer.state.version == 0
    assert world.ledger.ran == []
    assert world.assessor.seen == []


async def test_an_ordinary_question_is_answered_as_the_engine_answers_it() -> None:
    world = World(GROCERIES_IN_JULY)
    canonical = AnswerTurnHandler(
        Interpreter(Calls(), GROCERIES_IN_JULY),
        MemoryDialogueStore(),
        MerchantLedger(),
        Accounts(),
        Directory(),
        Contacts(),
        frozenset({"GROCERIES", "RESTAURANTS"}),
    )

    answer = await world.ask()
    control = await canonical.answer(
        SENTENCE,
        conversation_id=None,
        version=None,
        account_id=ACCOUNT,
        today=TODAY,
        reporting_timezone="Europe/Madrid",
    )

    assert answer.result == control.result
    assert replace(answer.state, conversation_id=control.state.conversation_id) == control.state
    assert world.assessor.seen == []
