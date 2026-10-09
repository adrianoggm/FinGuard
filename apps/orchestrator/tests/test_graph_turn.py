"""The investigation graph end to end, in process, with fakes for every model and for the bank.

The interpreter is scripted (it returns the operations a test gives it), the assessor is scripted
(it returns the steps a test gives it), and the ledger answers each window by merchant from a table.
What is under test is what the graph adds and what it must keep: the loop and its budgets, the
allow-list, that no model text reaches the reply, failure and resume, duplicate and concurrent
requests, the checkpoint's contents and its version, and that money never runs here.
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
from support.episodes import ACCOUNT, INTENT, Accounts, Bank, Contacts, Directory

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
)
from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler, EngineAnswer
from finguard_orchestrator.application.graph import graph as investigation_graph
from finguard_orchestrator.application.graph import graph_turn, investigation
from finguard_orchestrator.application.graph.graph import GraphPorts
from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Compare,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    RestorePrevious,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import (
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    PaymentConfirmationPending,
    PaymentDraftPending,
    QueryFocus,
)
from finguard_orchestrator.domain.engine.transitions import apply
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    PaymentOutcome,
    Period,
    PlanningOutcome,
)
from finguard_orchestrator.domain.errors import BankingUnavailableError, NotAuthorisedError
from finguard_orchestrator.domain.models.conversation import Workflow
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
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import ChartVisualizationResponse
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore
from finguard_orchestrator.infrastructure.graph.llm_assessor import LlmInvestigationAssessor
from finguard_orchestrator.infrastructure.graph.memory_turn_reservations import (
    MemoryTurnReservations,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto

TODAY = date(2026, 8, 5)
MAY = AbsoluteRange(date(2026, 5, 1), date(2026, 5, 31))
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
MERCHANT_BREAKDOWN_IN_JULY = (
    Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, group_by=GroupBy.MERCHANT)),
)
# June against July. Amazon rose by both, Cepsa by bigger amounts, Mercadona by more purchases.
FIGURES: dict[AbsoluteRange, tuple[Group, ...]] = {
    MAY: (Group("Mercadona", 8_000, 3), Group("Amazon", 3_000, 1), Group("Cepsa", 4_000, 2)),
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
    `failures` are raised, one per read, before the read is answered; `fail_on` raises at the
    n-th port call (1-based). `attempts` counts every port call, failed ones included."""

    def __init__(self) -> None:
        self.ran: list[FinancialPlan | TopMovements] = []
        self.failures: list[Exception] = []
        self.fail_on: dict[int, Exception] = {}
        self.attempts = 0

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
        self.attempts += 1
        if self.attempts in self.fail_on:
            raise self.fail_on.pop(self.attempts)
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
            visual_policy=VisualPlanningPolicy(
                model=None,
                fallback=DeterministicVisualPlanner(),
                verifier=verify,
                mode="deterministic",
                budget_seconds=1,
            ),
            count_calls=self.calls,
        )
        return GraphTurnHandler(ports, self.reservations, self.saver)

    def last_config(self) -> Any:
        """The thread of the last turn, for reading back its checkpoint."""
        (thread,) = {found.config["configurable"]["thread_id"] for found in self.saver.list(None)}
        return {"configurable": {"thread_id": thread}}

    async def ask(
        self,
        *,
        conversation_id: UUID | None = None,
        version: int | None = None,
        request_id: str | None = "req-1",
        sentence: str = SENTENCE,
        caller_scopes: frozenset[str] = frozenset(),
    ) -> EngineAnswer:
        return await self.handler.answer(
            sentence,
            conversation_id=conversation_id,
            version=version,
            account_id=ACCOUNT,
            today=TODAY,
            reporting_timezone="Europe/Madrid",
            request_id=request_id,
            caller_scopes=caller_scopes,
        )


def text_of(answer: EngineAnswer) -> str:
    assert answer.result is not None
    return answer.result.text


def with_payment_commands(world: World, bank: Bank | None = None) -> Bank:
    bank = bank or Bank()
    commands = CommandBus()
    commands.register(PrepareChosenPaymentCommand, PrepareChosenPaymentHandler(bank))
    commands.register(ConfirmPaymentCommand, ConfirmPaymentHandler(bank))
    commands.register(PrepareOwnTransferCommand, PrepareOwnTransferHandler(bank))
    ports = replace(world.handler._ports, commands=commands)  # noqa: SLF001
    world.handler = GraphTurnHandler(ports, world.reservations, world.saver)
    return bank


# --- the capability -------------------------------------------------------------------------------


async def test_the_current_engine_refuses_a_comparison_by_merchant() -> None:
    """The control (Step 2): the canonical transition asks back rather than drop the breakdown."""
    grounds = Grounds(frozenset(), (), ())

    moved = apply(DialogueState(UUID(int=1)), BY_MERCHANT, today=TODAY, grounds=grounds)

    assert moved.work is None and moved.reply is not None


def test_typed_period_patch_keeps_the_active_merchant_and_category_filters() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=1),
        categories=("SUBSCRIPTIONS",),
        merchant_terms=("gimnasio",),
    )
    current = DialogueState(
        UUID(int=1),
        focus=QueryFocus(plan),
        turns=(DialogueTurn("gastos en gimnasio", "59,80 euros"),),
    )

    operations = (
        Patch(
            "focus",
            PlanFields(window=AbsoluteRange(date(2026, 10, 1), date(2026, 10, 31))),
        ),
    )
    moved = apply(
        current,
        operations,
        today=date(2026, 10, 9),
        grounds=Grounds(frozenset({"SUBSCRIPTIONS"}), (), ()),
    )

    assert isinstance(moved.state.focus, QueryFocus)
    updated = moved.state.focus.plan
    assert isinstance(updated, FinancialPlan)
    assert updated.date_range == AbsoluteRange(date(2026, 10, 1), date(2026, 10, 31))
    assert updated.categories == ("SUBSCRIPTIONS",)
    assert updated.merchant_terms == ("gimnasio",)


async def test_it_names_the_risen_merchants_with_their_cause_and_inspects_one() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))

    answer = await world.ask()

    text = text_of(answer)
    assert "  · Amazon: 50,00 € → 170,00 €; por más compras (de 1 compra a 2 compras) y" in text
    assert "  · Mercadona: 100,00 € → 150,00 €; por más compras (de 4 compras a 6" in text
    assert "  · Cepsa: 60,00 € → 90,00 €; por importes mayores (media de 30,00 € a 45,00 €" in text
    assert GROUNDED_AMAZON in text
    assert text.endswith("Consultado en todas tus cuentas.")
    # Two windows by merchant, then the one inspection: three banking executions.
    assert [type(p).__name__ for p in world.ledger.ran] == [
        "FinancialPlan",
        "FinancialPlan",
        "TopMovements",
    ]
    # The interpreter and one assessment: after the inspection no read is left in the budget, so
    # the assessment is not asked again and the answer says Cepsa was not looked into.
    assert answer.model_calls == 2
    assert "No he investigado más" in text
    # The canonical state moved once, to the pair of totals the transition validated.
    assert answer.state.version == 1
    assert isinstance(answer.state.focus, ComparisonFocus)
    assert answer.state.turns[-1].said == SENTENCE


async def test_reads_carry_the_validated_window_scope_and_filters() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))

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
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))

    await world.ask()

    first = world.assessor.seen[0]
    assert first.may_inspect == ("Amazon", "Cepsa")
    assert [(m.merchant, m.explanation) for m in first.risen] == [
        ("Amazon", "BOTH"),
        ("Mercadona", "MORE_PURCHASES"),
        ("Cepsa", "HIGHER_AMOUNTS"),
    ]
    said = json.dumps([repr(summary) for summary in world.assessor.seen])
    assert SENTENCE not in said and "Compra privada" not in said
    assert len(world.assessor.seen) == 1


async def test_with_no_cause_to_investigate_no_assessment_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(BY_MERCHANT)
    monkeypatch.setitem(FIGURES, JULY, (Group("Mercadona", 15_000, 6),))

    answer = await world.ask()

    assert world.assessor.seen == []
    assert answer.model_calls == 1
    assert "por más compras (de 4 compras a 6 compras" in text_of(answer)


# --- the loop, its budgets and the allow-list ----------------------------------------------------


async def test_the_loop_stops_at_the_bank_budget_with_a_grounded_partial_answer() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Inspect("Cepsa"), Stop()))

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


# --- no model-written text reaches the person (R42) -----------------------------------------------

MARKER = "MARKER-7f3"


class Provider:
    """A model behind the real assessor adapter: each call answers the next canned text."""

    name = "canned"

    def __init__(self, calls: Calls, *answers: str) -> None:
        self._calls = calls
        self._answers = list(answers)

    async def complete(self, prompt: str, **_: Any) -> str:
        self._calls.count += 1
        return self._answers.pop(0)


def with_real_assessor(world: World, *answers: str) -> None:
    world.assessor = LlmInvestigationAssessor(  # type: ignore[assignment]
        Provider(world.calls, *answers), model="m", temperature=0.0, max_output_tokens=50
    )
    world.handler = world.build()


@pytest.mark.parametrize(
    "answers",
    [
        # Text inside a stop: a figure, a percentage, a reason the bank cannot know.
        ('{"next": {"stop": {"why": "MARKER-7f3: Gastas cien euros más, un 31 % más."}}}',),
        # A merchant the allow-list does not hold, then text after an inspection.
        ('{"next": {"inspect": {"merchant": "MARKER-7f3"}}}',),
        (
            '{"next": {"inspect": {"merchant": "Amazon"}}}',
            '{"next": {"stop": {"why": "MARKER-7f3 el triple"}, "note": "MARKER-7f3"}}',
        ),
        # Not the schema at all.
        ("MARKER-7f3 Subió 100 euros.",),
    ],
)
async def test_no_string_the_model_writes_reaches_the_reply(answers: tuple[str, ...]) -> None:
    world = World(BY_MERCHANT)
    with_real_assessor(world, *answers)

    answer = await world.ask()

    assert MARKER not in text_of(answer)
    assert "cien" not in text_of(answer) and "%" not in text_of(answer)
    assert "  · Amazon: 50,00 € → 170,00 €;" in text_of(answer)
    assert answer.state.version == 1
    # Nor does it reach the checkpoint (R45): the model's choice is kept as a position in the
    # allow-list, never as the string it wrote.
    stored = [found.checkpoint["channel_values"] for found in world.saver.list(None)]
    pending = [w for found in world.saver.list(None) for w in found.pending_writes or ()]
    assert MARKER not in repr(stored) + repr(pending)


async def test_the_whole_reply_is_the_template_over_the_bank_figures() -> None:
    """Rebuilt from the bank's figures alone, the reply is equal to it: there is nothing else."""
    world = World(BY_MERCHANT)
    with_real_assessor(
        world, '{"next": {"inspect": {"merchant": "Amazon"}}}', '{"next": {"stop": {}}}'
    )

    answer = await world.ask()

    june = QueryAnswer("EUR", 0, 0, groups=FIGURES[JUNE])
    july = QueryAnswer("EUR", 0, 0, groups=FIGURES[JULY])
    changes = investigation.risen(june, july)
    moment = datetime(2026, 7, 12, 18, 30, tzinfo=UTC)
    amazon = investigation.inspected(changes[0], Movement(moment, "", LARGEST["Amazon"], "EXPENSE"))
    expected = investigation.answer_text(
        changes,
        (amazon,),
        earlier=FinancialPlan(direction=Direction.EXPENSE, date_range=JUNE),
        latest=FinancialPlan(direction=Direction.EXPENSE, date_range=JULY),
        earlier_total=21_000,
        latest_total=41_000,
        currency="EUR",
        partial=True,  # Cepsa left uninspected: the budget is spent
    )
    assert text_of(answer).splitlines() == [
        *expected.splitlines(),
        "Consultado en todas tus cuentas.",
    ]


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


async def test_a_failure_on_the_second_read_resumes_within_three_bank_calls() -> None:
    """R44. The first window is read once and kept; the failed second read counts; the resume
    reads only the second window, asks no interpreter again, and the inspection the assessment then
    asks for would be a fourth call, so it is refused and the answer says it stopped."""
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))
    world.ledger.fail_on = {2: BankingUnavailableError("down")}

    failed = await world.ask(request_id="req-1")
    assert failed.result is not None and failed.result.outcome == "BANKING_UNAVAILABLE"
    assert failed.state.version == 0
    assert world.ledger.attempts == 2

    resumed = await world.ask(request_id="req-1")

    assert world.ledger.attempts == investigation_graph.MAX_BANK_EXECUTIONS == 3
    assert [p.date_range for p in world.ledger.ran] == [JUNE, JULY]  # June never read again
    assert len(world.interpreter.sentences) == 1
    assert "  · Amazon: 50,00 € → 170,00 €;" in text_of(resumed)
    assert "No he investigado más" in text_of(resumed)
    assert resumed.state.version == 1
    stored = (await world.handler._graph.aget_state(world.last_config())).values  # noqa: SLF001
    assert stored["bank_calls"] == 3


async def test_a_failed_inspection_is_not_retried_past_the_budget() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))
    world.ledger.fail_on = {3: BankingUnavailableError("down")}

    await world.ask(request_id="req-1")
    resumed = await world.ask(request_id="req-1")

    assert world.ledger.attempts == 3
    assert GROUNDED_AMAZON not in text_of(resumed)
    assert "No he investigado más" in text_of(resumed)
    assert resumed.state.version == 1


async def test_the_budget_holds_across_resumes() -> None:
    """Two failed reads of the first window and one good one leave no call for the second: the
    turn says so rather than read a fourth time, and saves nothing."""
    world = World(BY_MERCHANT)
    world.ledger.failures = [BankingUnavailableError("down"), BankingUnavailableError("down")]

    for _ in range(2):
        await world.ask(request_id="req-1")
    last = await world.ask(request_id="req-1")

    assert world.ledger.attempts == 3
    assert last.result is not None and last.result.outcome == "BANKING_UNAVAILABLE"
    assert "límite" in last.result.text
    assert last.state.version == 0


async def test_a_failed_turn_can_be_taken_over_by_a_new_request() -> None:
    world = World(BY_MERCHANT, BY_MERCHANT, steps=(Stop(), Stop()))
    world.ledger.failures = [BankingUnavailableError("down")]

    await world.ask(request_id="req-1")
    answer = await world.ask(request_id="req-2")

    assert answer.state.version == 1


# --- duplicate and concurrent requests ------------------------------------------------------------


async def test_a_duplicate_request_replays_its_answer_without_model_or_bank_work() -> None:
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))
    first = await world.ask(request_id="req-1")
    calls, reads = world.calls.count, len(world.ledger.ran)

    again = await world.ask(request_id="req-1")

    assert again == first
    assert (world.calls.count, len(world.ledger.ran)) == (calls, reads)
    assert (await world.store.load(first.state.conversation_id, account_id=ACCOUNT)) == first.state


async def test_a_duplicate_of_a_follow_up_turn_replays_too() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, steps=(Stop(),))
    first = await world.ask(request_id="req-1")
    conversation = first.state.conversation_id

    second = await world.ask(conversation_id=conversation, version=1, request_id="req-2")
    again = await world.ask(conversation_id=conversation, version=1, request_id="req-2")

    assert again == second and second.state.version == 2


async def test_restore_previous_requeries_bank_with_the_original_plan() -> None:
    may_query = (Ask(PlanFields(direction=Direction.EXPENSE, window=MAY)),)
    june_query = (Ask(PlanFields(direction=Direction.EXPENSE, window=JUNE)),)
    july_query = (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),)
    world = World(
        may_query,
        june_query,
        july_query,
        (RestorePrevious(2),),
        (RestorePrevious(2),),
    )

    first = await world.ask()
    second = await world.ask(
        conversation_id=first.state.conversation_id, version=1, request_id="july"
    )
    third = await world.ask(
        conversation_id=first.state.conversation_id, version=2, request_id="back"
    )
    restored = await world.ask(
        conversation_id=first.state.conversation_id, version=3, request_id="back-two"
    )
    restored_again = await world.ask(
        conversation_id=first.state.conversation_id, version=4, request_id="back-three"
    )

    assert isinstance(second.state.focus, QueryFocus)
    assert second.state.focus.plan.date_range == JUNE
    assert isinstance(third.state.focus, QueryFocus)
    assert third.state.focus.plan.date_range == JULY
    assert isinstance(restored.state.focus, QueryFocus)
    assert restored.state.focus.plan.date_range == MAY
    assert isinstance(restored_again.state.focus, QueryFocus)
    assert restored_again.state.focus.plan.date_range == JUNE
    assert [plan.date_range for plan in world.ledger.ran] == [MAY, JUNE, JULY, MAY, JUNE]
    assert restored.state.version == 4
    assert restored_again.state.version == 5


async def test_history_keeps_only_the_final_focus_of_a_composed_turn() -> None:
    may_query = (Ask(PlanFields(direction=Direction.EXPENSE, window=MAY)),)
    june_then_july = (
        Ask(PlanFields(direction=Direction.EXPENSE, window=JUNE)),
        Patch("focus", PlanFields(window=JULY)),
    )
    world = World(may_query, june_then_july, (RestorePrevious(),))

    first = await world.ask()
    second = await world.ask(
        conversation_id=first.state.conversation_id, version=1, request_id="composed"
    )
    restored = await world.ask(
        conversation_id=first.state.conversation_id, version=2, request_id="back"
    )

    assert isinstance(second.state.focus, QueryFocus)
    assert second.state.focus.plan.date_range == JULY
    assert isinstance(restored.state.focus, QueryFocus)
    assert restored.state.focus.plan.date_range == MAY
    assert [plan.date_range for plan in world.ledger.ran] == [MAY, JULY, MAY]


async def test_another_request_against_a_version_a_turn_is_in_flight_on_is_stale() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT, steps=(Stop(), Stop()))
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
    world = World(BY_MERCHANT, steps=(Stop(),))
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
    world = World(BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))

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
    world = World(BY_MERCHANT, BY_MERCHANT, steps=(Inspect("Amazon"), Stop()))
    world.ledger.failures = [BankingUnavailableError("down")]
    await world.ask(request_id="req-1")  # fails at the first read, checkpointed before it

    # The deployed graph is now a later version than the one that wrote the checkpoint.
    monkeypatch.setattr(graph_turn, "GRAPH_VERSION", f"{investigation_graph.GRAPH_VERSION}+next")
    answer = await world.ask(request_id="req-1")

    # Started again: the interpreter was asked a second time, and the turn still saved once.
    assert len(world.interpreter.sentences) == 2
    assert answer.state.version == 1
    assert GROUNDED_AMAZON in text_of(answer)


# --- what never runs here -------------------------------------------------------------------------


@pytest.mark.parametrize("to", ["PAYMENT", "TRANSFER", "BOOKING"])
async def test_a_money_flow_is_refused_without_bank_reads_or_execution(to: Any) -> None:
    world = World((Delegate(to),))

    answer = await world.ask(sentence="Hazme una transferencia de 50 euros a Marta")

    assert answer.result is not None and answer.delegate_to is None
    assert answer.state.version == 1
    assert "aún no están disponibles" in answer.result.text
    assert world.ledger.ran == []
    assert world.assessor.seen == []


async def test_chart_operation_reuses_the_prior_banking_result_and_keeps_continuity() -> None:
    world = World(
        MERCHANT_BREAKDOWN_IN_JULY,
        (Visualize("ACTIVE_RESULT", "AUTO"),),
        (Visualize("ACTIVE_CHART", "LINE"),),
        GROCERIES_IN_JULY,
    )

    query = await world.ask(request_id="query-1", sentence="gastos por comercio en julio")
    reads_after_query = len(world.ledger.ran)
    chart = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        request_id="visual-1",
        sentence="muéstramelo en un gráfico",
    )
    replay = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        request_id="visual-1",
        sentence="muéstramelo en un gráfico",
    )
    reads_after_chart = len(world.ledger.ran)
    restyled = await world.ask(
        conversation_id=chart.state.conversation_id,
        version=chart.state.version,
        request_id="visual-2",
        sentence="cámbialo a líneas",
    )
    reads_after_restyle = len(world.ledger.ran)
    next_turn = await world.ask(
        conversation_id=restyled.state.conversation_id,
        version=restyled.state.version,
        request_id="query-2",
    )

    assert chart == replay
    assert chart.result is not None and chart.result.visual_decision is not None
    assert chart.result.visual_dataset is not None
    rendered = question_to_dto(chart.result)
    assert isinstance(rendered.visualization, ChartVisualizationResponse)
    assert restyled.result is not None and restyled.result.visual_decision is not None
    assert restyled.result.visual_decision.spec.mark == "line"
    assert reads_after_chart == reads_after_query
    assert chart.state.active_result == query.state.active_result
    assert chart.state.active_visual is not None
    assert chart.state.active_visual.version == 1
    assert chart.state.active_visual.result_version == query.state.active_result.result_version
    assert restyled.state.active_visual is not None
    assert restyled.state.active_visual.version == 2
    assert restyled.state.active_visual.result_version == chart.state.active_visual.result_version
    restored = graph_turn.codec.loads_state(graph_turn.codec.dumps_state(restyled.state))
    assert restored == restyled.state
    assert reads_after_restyle == reads_after_query
    assert chart.state.version == 2
    assert restyled.state.version == 3
    assert next_turn.state.version == 4
    assert next_turn.result is not None
    assert len(world.interpreter.sentences) == 4


async def test_category_chart_uses_human_readable_labels() -> None:
    class CategoryLedger(MerchantLedger):
        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self._fail()
            self.ran.append(plan)
            return QueryAnswer(
                "EUR",
                12_000,
                3,
                groups=(Group("HOUSING_RENT", 10_000, 1), Group("GROCERIES", 2_000, 2)),
            )

    world = World(
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=JULY,
                    group_by=GroupBy.CATEGORY,
                )
            ),
        ),
        (Visualize("ACTIVE_RESULT", "BAR"),),
    )
    world.ledger = CategoryLedger()
    world.handler = world.build()
    query = await world.ask(sentence="gastos por categoría en julio")
    chart = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        request_id="category-chart",
        sentence="muéstramelo en un gráfico de barras",
    )

    assert chart.result is not None and chart.result.visual_dataset is not None
    assert [row.dimension for row in chart.result.visual_dataset.rows] == [
        "alquiler",
        "supermercado",
    ]
    assert [row.value_minor for row in chart.result.visual_dataset.rows] == [10_000, 2_000]


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
    # The graph keeps a bounded bank aggregate in its canonical state for native chart operations;
    # the legacy adapter remains frozen and has no such reusable artifact yet.
    assert (
        replace(answer.state, conversation_id=control.state.conversation_id, active_result=None)
        == control.state
    )
    assert answer.state.active_result is not None
    assert world.assessor.seen == []


# --- the reservation is released on every exit (R43) ----------------------------------------------


class Blocking(Interpreter):
    """Holds the turn inside the model call until the test lets it go or cancels it."""

    def __init__(self, calls: Calls, *turns: tuple[Operation, ...]) -> None:
        super().__init__(calls, *turns)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def interpret(self, *args: Any, **kwargs: Any) -> tuple[Operation, ...]:
        if self.block:
            self.block = False
            self.entered.set()
            await self.release.wait()
        return await super().interpret(*args, **kwargs)


async def started(world: World) -> UUID:
    first = await world.ask(request_id="req-0", sentence="primera")
    return first.state.conversation_id


async def test_a_cancelled_turn_leaves_the_conversation_usable_at_the_same_version() -> None:
    world = World()
    world.interpreter = Blocking(world.calls, GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT)
    world.handler = world.build()
    conversation = await started(world)
    world.interpreter.block = True

    turn = asyncio.create_task(world.ask(conversation_id=conversation, version=1, request_id="r1"))
    await world.interpreter.entered.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    answer = await world.ask(conversation_id=conversation, version=1, request_id="r2")
    assert answer.state.version == 2


@pytest.mark.parametrize("failure", [RuntimeError("a port nobody expected"), AssertionError()])
async def test_an_unexpected_failure_releases_the_turn(failure: BaseException) -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT)
    conversation = await started(world)
    world.ledger.failures = [failure]  # type: ignore[list-item]

    with pytest.raises(type(failure)):
        await world.ask(conversation_id=conversation, version=1, request_id="r1")

    answer = await world.ask(conversation_id=conversation, version=1, request_id="r2")
    assert answer.state.version == 2


# --- a request id is bound to its sentence (R45) --------------------------------------------------


async def test_a_request_id_reused_with_another_sentence_is_refused() -> None:
    world = World(GROCERIES_IN_JULY, GROCERIES_IN_JULY)
    first = await world.ask(request_id="req-1", sentence="¿cuánto gasté en julio?")

    with pytest.raises(graph_turn.RequestReusedError):
        await world.ask(
            conversation_id=first.state.conversation_id,
            version=0,
            request_id="req-1",
            sentence="¿y en agosto?",
        )
    with pytest.raises(graph_turn.RequestReusedError):
        await world.ask(request_id="req-1", sentence="¿y en agosto?")

    again = await world.ask(request_id="req-1", sentence="¿cuánto gasté en julio?")
    assert again == first


async def test_a_failed_request_cannot_be_resumed_with_another_sentence() -> None:
    world = World(BY_MERCHANT, BY_MERCHANT, steps=(Stop(), Stop()))
    world.ledger.failures = [BankingUnavailableError("down")]
    await world.ask(request_id="req-1")

    with pytest.raises(graph_turn.RequestReusedError):
        await world.ask(request_id="req-1", sentence="otra pregunta")

    resumed = await world.ask(request_id="req-1")
    assert resumed.state.version == 1


# --- the release survives its own failures, and a saved turn is never resumable (fix round 2) ----


async def failed_on_the_second_read(world: World) -> UUID:
    """A conversation at version 1, whose next turn's second read fails, so its exit has calls
    to persist before the release."""
    conversation = await started(world)
    world.ledger.fail_on = {world.ledger.attempts + 2: BankingUnavailableError("down")}
    return conversation


async def test_a_cancellation_while_persisting_the_calls_still_releases_the_turn() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT, steps=(Stop(), Stop()))
    conversation = await failed_on_the_second_read(world)
    persisting, never = asyncio.Event(), asyncio.Event()

    async def stuck(config: Any, calls: int) -> None:
        persisting.set()
        await never.wait()

    world.handler._persist_calls = stuck  # type: ignore[method-assign]  # noqa: SLF001
    turn = asyncio.create_task(world.ask(conversation_id=conversation, version=1, request_id="r1"))
    await persisting.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    answer = await world.ask(conversation_id=conversation, version=1, request_id="r2")
    assert answer.state.version == 2


async def test_a_persist_that_raises_neither_holds_the_turn_nor_masks_the_answer() -> None:
    world = World(GROCERIES_IN_JULY, BY_MERCHANT, BY_MERCHANT, steps=(Stop(), Stop()))
    conversation = await failed_on_the_second_read(world)

    async def broken(config: Any, calls: int) -> None:
        raise RuntimeError("the checkpointer is down")

    world.handler._persist_calls = broken  # type: ignore[method-assign]  # noqa: SLF001
    failed = await world.ask(conversation_id=conversation, version=1, request_id="r1")

    assert failed.result is not None and failed.result.outcome == "BANKING_UNAVAILABLE"
    answer = await world.ask(conversation_id=conversation, version=1, request_id="r2")
    assert answer.state.version == 2


@pytest.mark.parametrize("interruption", ["raises", "cancelled"])
async def test_a_turn_saved_before_its_reservation_closed_is_replayed_not_resumed(
    interruption: str,
) -> None:
    """The save succeeded and `done()` did not return: the turn is saved, so it must never be
    marked resumable. Its duplicate replays it instead of finding a stale version."""
    world = World(BY_MERCHANT, steps=(Stop(),))
    real_done = world.reservations.done
    closing, never = asyncio.Event(), asyncio.Event()
    interrupted = [True]

    async def done_once_interrupted(*args: Any) -> None:
        if interrupted.pop() if interrupted else False:
            if interruption == "raises":
                raise RuntimeError("the reservation store hiccuped")
            closing.set()
            await never.wait()
        await real_done(*args)

    world.reservations.done = done_once_interrupted  # type: ignore[method-assign,assignment]
    turn = asyncio.create_task(world.ask(request_id="r1"))
    if interruption == "cancelled":
        await closing.wait()
        turn.cancel()
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await turn

    again = await world.ask(request_id="r1")

    assert again.state.version == 1
    assert len(world.interpreter.sentences) == 1  # replayed: no second turn ran


@pytest.mark.asyncio
async def test_conversation_graph_prepares_and_resumes_bizum_on_one_checkpoint() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    preview = await world.ask(
        sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="prepare-bizum",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert preview.state.version == 1
    assert isinstance(preview.state.payment, PaymentConfirmationPending)
    assert preview.state.payment.workflow_id == world.last_config()["configurable"]["thread_id"]
    assert bank.prepared[0].amount_minor == 2_000
    assert preview.result.conversation.active is not None
    assert await world.handler.owns_workflow(
        preview.state.conversation_id,
        account_id=ACCOUNT,
        workflow=Workflow.PAYMENT_CONFIRMATION,
    )

    paid = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="sí",
        request_id="confirm-bizum",
        caller_scopes=scopes,
    )

    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert paid.state.version == 2
    assert paid.state.payment is None
    assert bank.confirmed == [INTENT]
    assert not await world.handler.owns_workflow(
        paid.state.conversation_id,
        account_id=ACCOUNT,
        workflow=Workflow.PAYMENT_CONFIRMATION,
    )


@pytest.mark.asyncio
async def test_bizum_without_prepare_scope_never_calls_banking() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)

    with pytest.raises(NotAuthorisedError):
        await world.ask(
            sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
            request_id="missing-prepare-scope",
        )

    assert bank.prepared == [] and bank.confirmed == []


@pytest.mark.asyncio
async def test_bizum_draft_resumes_when_the_user_supplies_the_missing_amount() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", None),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    asked = await world.ask(
        sentence="Haz un bizum a Aitor Beltran Diaz",
        request_id="bizum-draft",
        caller_scopes=scopes,
    )

    assert asked.result is not None and "bizum a Aitor" in asked.result.text
    assert isinstance(asked.state.payment, PaymentDraftPending)
    assert asked.state.payment.draft.recipient_contact_id is not None
    assert asked.state.version == 1
    assert bank.prepared == []

    world.interpreter._turns.append((PrepareBizum(None, 2_000),))  # noqa: SLF001
    preview = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="20 euros",
        request_id="bizum-draft-amount",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert preview.state.version == 2
    assert isinstance(preview.state.payment, PaymentConfirmationPending)
    assert bank.prepared[0].amount_minor == 2_000
    assert bank.prepared[0].recipient_contact_id == UUID(int=8)


@pytest.mark.asyncio
async def test_bizum_ambiguous_contact_stays_restricted_to_named_candidates() -> None:
    world = World((PrepareBizum("Marta", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare"})

    asked = await world.ask(
        sentence="Haz un bizum de 20 euros a Marta",
        request_id="bizum-ambiguous",
        caller_scopes=scopes,
    )

    assert asked.result is not None and "Marta Solis Vega" in asked.result.text
    assert isinstance(asked.state.payment, PaymentDraftPending)
    candidates = asked.state.payment.candidate_contact_ids
    assert len(candidates) == 2 and bank.prepared == []

    world.interpreter._turns.append((PrepareBizum("Aitor Beltran Diaz", None),))  # noqa: SLF001
    rejected = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="Aitor Beltran Diaz",
        request_id="bizum-outside-candidates",
        caller_scopes=scopes,
    )

    assert rejected.result is not None
    assert rejected.result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert isinstance(rejected.state.payment, PaymentDraftPending)
    assert rejected.state.payment.candidate_contact_ids == candidates
    assert bank.prepared == []

    world.interpreter._turns.append((PrepareBizum("Marta Solis Vega", None),))  # noqa: SLF001
    preview = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=rejected.state.version,
        sentence="Marta Solis Vega",
        request_id="bizum-choose-marta",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.prepared[0].recipient_contact_id == UUID("55555555-5555-5555-5555-555555555555")


@pytest.mark.asyncio
async def test_bizum_draft_survives_an_unrelated_query_and_then_resumes() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", None),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare"})
    asked = await world.ask(
        sentence="Haz un bizum a Aitor Beltran Diaz",
        request_id="bizum-interrupt-start",
        caller_scopes=scopes,
    )

    world.interpreter._turns.append(GROCERIES_IN_JULY)  # noqa: SLF001
    query = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="Cuanto gaste en julio",
        request_id="bizum-interrupt-query",
        caller_scopes=scopes,
    )

    assert query.result is not None and "410,00" in query.result.text
    assert isinstance(query.state.payment, PaymentDraftPending)
    assert query.state.payment.draft.recipient_name == "Aitor Beltran Diaz"
    assert bank.prepared == []

    world.interpreter._turns.append((PrepareBizum(None, 2_000),))  # noqa: SLF001
    preview = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        sentence="20 euros",
        request_id="bizum-interrupt-resume",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.prepared[0].amount_minor == 2_000


@pytest.mark.asyncio
async def test_new_transfer_cannot_overwrite_an_unfinished_bizum_draft() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", None),))
    bank = with_payment_commands(world)
    pending = await world.ask(
        sentence="Haz un bizum a Aitor Beltran Diaz",
        request_id="bizum-before-transfer",
        caller_scopes=frozenset({"payments:prepare"}),
    )
    assert isinstance(pending.state.payment, PaymentDraftPending)

    world.interpreter._turns.append((PrepareOwnTransfer("Fondo indexado"),))  # noqa: SLF001
    rejected = await world.ask(
        conversation_id=pending.state.conversation_id,
        version=pending.state.version,
        sentence="Traspasa 20 euros al fondo indexado",
        request_id="transfer-while-bizum-pending",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert rejected.result is not None
    assert rejected.result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert isinstance(rejected.state.payment, PaymentDraftPending)
    assert rejected.state.own_transfer is None
    assert bank.prepared == []


@pytest.mark.asyncio
async def test_bizum_out_of_range_amount_does_not_reuse_a_saved_amount() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", None),))
    bank = with_payment_commands(world)
    asked = await world.ask(
        sentence="Haz un bizum a Aitor Beltran Diaz",
        request_id="bizum-prepare-for-invalid-amount",
        caller_scopes=frozenset({"payments:prepare"}),
    )
    assert asked.result is not None
    assert isinstance(asked.state.payment, PaymentDraftPending)

    world.interpreter._turns.append((PrepareBizum(None, None),))  # noqa: SLF001
    invalid = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="20000000 euros",
        request_id="bizum-invalid-amount",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert invalid.result is not None
    assert invalid.result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert isinstance(invalid.state.payment, PaymentDraftPending)
    assert bank.prepared == []


@pytest.mark.asyncio
async def test_mixed_query_and_bizum_commit_once_and_keep_query_result() -> None:
    world = World(GROCERIES_IN_JULY + (PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="¿Cuánto gasté en julio? Además, haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="query-and-bizum",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert answer.result is not None
    assert "20,00" in answer.result.text
    assert "410,00" in answer.result.text
    assert answer.state.version == 1
    assert answer.state.active_result is not None
    assert isinstance(answer.state.payment, PaymentConfirmationPending)
    assert len(world.ledger.ran) == 1
    assert len(bank.prepared) == 1


@pytest.mark.asyncio
async def test_bizum_amount_must_match_user_sentence_before_bank_prepare() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_500),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="mismatched-amount",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert answer.result is not None and answer.state.version == 1
    assert bank.prepared == []
    assert answer.state.payment is None


@pytest.mark.asyncio
async def test_unclear_bizum_reply_reinterrupts_then_explicit_yes_confirms_once() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    prepared = await world.ask(
        sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="prepare-unclear",
        caller_scopes=scopes,
    )

    unclear = await world.ask(
        conversation_id=prepared.state.conversation_id,
        version=prepared.state.version,
        sentence="sí, pero cambia el importe",
        request_id="unclear-reply",
        caller_scopes=scopes,
    )

    assert unclear.result is not None
    assert unclear.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert isinstance(unclear.state.payment, PaymentConfirmationPending)
    assert unclear.state.version == 2
    assert bank.confirmed == []

    paid = await world.ask(
        conversation_id=unclear.state.conversation_id,
        version=unclear.state.version,
        sentence="sí",
        request_id="explicit-yes",
        caller_scopes=scopes,
    )

    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert paid.state.payment is None and paid.state.version == 3
    assert bank.confirmed == [INTENT]


@pytest.mark.asyncio
async def test_confirmation_without_confirm_scope_does_not_resume_payment() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    prepared = await world.ask(
        sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="prepare-no-confirm-scope",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    with pytest.raises(NotAuthorisedError):
        await world.ask(
            conversation_id=prepared.state.conversation_id,
            version=prepared.state.version,
            sentence="sí",
            request_id="yes-no-confirm-scope",
            caller_scopes=frozenset({"payments:prepare"}),
        )

    assert bank.confirmed == []
    assert isinstance(prepared.state.payment, PaymentConfirmationPending)


@pytest.mark.asyncio
async def test_own_transfer_asks_for_amount_then_previews_and_confirms_once() -> None:
    world = World((PrepareOwnTransfer("Fondo indexado"),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    asked = await world.ask(
        sentence="Traspasa dinero al fondo indexado",
        request_id="own-transfer-amount",
        caller_scopes=scopes,
    )

    assert asked.result is not None and "Cu\u00e1nto quieres mover" in asked.result.text
    assert asked.state.version == 1
    assert asked.state.own_transfer is not None
    assert asked.state.own_transfer.to_account_id is not None
    assert bank.prepared == []

    world.interpreter._turns.append((PrepareOwnTransfer("Fondo indexado"),))  # noqa: SLF001
    preview = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="75 euros",
        request_id="own-transfer-preview",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert preview.state.version == 2
    assert preview.state.own_transfer is None
    assert isinstance(preview.state.payment, PaymentConfirmationPending)
    assert bank.prepared[0].rail.value == "INTERNAL"
    assert bank.prepared[0].to_account_id == UUID("33333333-3333-3333-3333-333333333333")
    assert bank.prepared[0].amount_minor == 7_500

    paid = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="si",
        request_id="own-transfer-confirm",
        caller_scopes=scopes,
    )

    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [INTENT]


@pytest.mark.asyncio
async def test_own_transfer_without_prepare_scope_never_calls_banking() -> None:
    world = World((PrepareOwnTransfer("Fondo indexado"),))
    bank = with_payment_commands(world)

    with pytest.raises(NotAuthorisedError):
        await world.ask(
            sentence="Traspasa 20 euros al fondo indexado",
            request_id="own-transfer-no-scope",
        )

    assert bank.prepared == [] and bank.confirmed == []


@pytest.mark.asyncio
async def test_mixed_query_and_own_transfer_share_one_canonical_commit() -> None:
    world = World(GROCERIES_IN_JULY + (PrepareOwnTransfer("Fondo indexado"),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence=("Cuanto gaste en julio y traspasa 20 euros al fondo indexado"),
        request_id="query-and-own-transfer",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert answer.result is not None
    assert "410,00" in answer.result.text
    assert "20,00" in answer.result.text
    assert answer.state.version == 1
    assert answer.state.active_result is not None
    assert isinstance(answer.state.payment, PaymentConfirmationPending)
    assert len(world.ledger.ran) == 1
    assert len(bank.prepared) == 1


@pytest.mark.asyncio
async def test_declining_own_transfer_cancels_preview_without_confirming() -> None:
    world = World((PrepareOwnTransfer("Fondo indexado"),))
    bank = with_payment_commands(world)
    prepared = await world.ask(
        sentence="Traspasa 20 euros al fondo indexado",
        request_id="own-transfer-cancel-preview",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    cancelled = await world.ask(
        conversation_id=prepared.state.conversation_id,
        version=prepared.state.version,
        sentence="no",
        request_id="own-transfer-cancel",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert cancelled.result is not None
    assert cancelled.result.outcome == PaymentOutcome.CANCELLED.value
    assert bank.cancelled == [INTENT]
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_own_transfer_bank_outage_keeps_draft_and_reuses_idempotency_key() -> None:
    class FlakyBank(Bank):
        def __init__(self) -> None:
            super().__init__()
            self.keys: list[str] = []

        async def prepare(self, request: Any, *, account_id: UUID, key: str) -> Any:
            self.keys.append(key)
            if len(self.keys) == 1:
                raise BankingUnavailableError("temporarily unavailable")
            return await super().prepare(request, account_id=account_id, key=key)

    world = World((PrepareOwnTransfer("Fondo indexado"),))
    bank = with_payment_commands(world, FlakyBank())
    scopes = frozenset({"payments:prepare"})

    unavailable = await world.ask(
        sentence="Traspasa 75 euros al fondo indexado",
        request_id="own-transfer-outage",
        caller_scopes=scopes,
    )

    assert unavailable.result is not None
    assert unavailable.result.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
    assert unavailable.state.own_transfer is not None
    assert unavailable.state.own_transfer.amount_minor == 7_500

    world.interpreter._turns.append((PrepareOwnTransfer("Fondo indexado"),))  # noqa: SLF001
    recovered = await world.ask(
        conversation_id=unavailable.state.conversation_id,
        version=unavailable.state.version,
        sentence="Reintenta",
        request_id="own-transfer-retry",
        caller_scopes=scopes,
    )

    assert recovered.result is not None
    assert recovered.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert len(bank.keys) == 2 and bank.keys[0] == bank.keys[1]
    assert len(bank.prepared) == 1 and bank.prepared[0].amount_minor == 7_500


async def test_a_comparison_explains_decreases_without_calling_them_increases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(FIGURES, JUNE, (Group("Mercadona", 20_000, 4),))
    monkeypatch.setitem(FIGURES, JULY, (Group("Mercadona", 10_000, 2),))
    world = World(BY_MERCHANT)

    answer = await world.ask(sentence="comparison explanation")

    text = text_of(answer)
    assert "Comercios en los que gastas menos" in text
    assert "Mercadona" in text and "200,00" in text and "100,00" in text
    assert "por menos compras" in text
    assert "Comercios en los que gastas m\u00e1s" not in text
    assert world.assessor.seen == []


async def test_explained_comparison_can_be_charted_and_restyled_without_another_bank_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(FIGURES, JUNE, (Group("Mercadona", 20_000, 4), Group("Dia", 8_000, 3)))
    monkeypatch.setitem(FIGURES, JULY, (Group("Mercadona", 10_000, 2),))
    world = World(
        BY_MERCHANT, (Visualize("ACTIVE_RESULT", "BAR"),), (Visualize("ACTIVE_CHART", "LINE"),)
    )

    answer = await world.ask(sentence="comparison explanation")
    read_count = len(world.ledger.ran)
    chart = await world.ask(
        conversation_id=answer.state.conversation_id,
        version=answer.state.version,
        request_id="comparison-chart",
        sentence="muestramelo en un grafico",
    )
    restyled = await world.ask(
        conversation_id=chart.state.conversation_id,
        version=chart.state.version,
        request_id="comparison-lines",
        sentence="cambialo a lineas",
    )

    assert chart.result is not None and chart.result.visual_dataset is not None
    rows = chart.result.visual_dataset.rows
    assert {(row.dimension, row.series, row.value_minor) for row in rows} == {
        ("Mercadona", "del 2026-06-01 al 2026-06-30, gastos", 20_000),
        ("Dia", "del 2026-06-01 al 2026-06-30, gastos", 8_000),
        ("Mercadona", "del 2026-07-01 al 2026-07-31, gastos", 10_000),
    }
    assert restyled.result is not None and restyled.result.visual_decision is not None
    assert restyled.result.visual_decision.spec.mark == "line"
    assert len(world.ledger.ran) == read_count
    assert chart.state.active_result is not None
    assert chart.state.active_result.comparison is not None
    assert chart.state.active_result.comparison_groups is not None
