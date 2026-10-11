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
from support.episodes import ACCOUNT, INTENT, PIGGY, Accounts, Bank, Contacts, Directory
from support.memory_dialogue_store import MemoryDialogueStore
from support.memory_turn_reservations import MemoryTurnReservations

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
)
from finguard_orchestrator.application.engine.executor import QueryRun
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.domain.constants import MAX_SUPPORTING_MOVEMENTS
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    AskCorrection,
    Balance,
    Clarify,
    Compare,
    Delegate,
    InvestigateComparison,
    ListCategories,
    ListContacts,
    ListMovements,
    Operation,
    Patch,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
    RestorePrevious,
    Smalltalk,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import (
    ArtifactStatus,
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    Missing,
    MovementListFocus,
    PaymentConfirmationPending,
    PaymentDraftPending,
    Pending,
    QueryFocus,
)
from finguard_orchestrator.domain.engine.transitions import apply
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    PaymentOutcome,
    Period,
    PlanningOutcome,
    Rail,
    ResultKind,
    Spoken,
)
from finguard_orchestrator.domain.errors import BankingUnavailableError, NotAuthorisedError
from finguard_orchestrator.domain.models.conversation import Workflow
from finguard_orchestrator.domain.models.query_answer import (
    Group,
    Movement,
    MovementListAnswer,
    QueryAnswer,
)
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
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.movement_list_plan import (
    MovementCursor,
    MovementListPlan,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import ChartVisualizationResponse
from finguard_orchestrator.infrastructure.graph import codec, graph_turn, investigation
from finguard_orchestrator.infrastructure.graph import graph as investigation_graph
from finguard_orchestrator.infrastructure.graph.graph_state import GraphPorts
from finguard_orchestrator.infrastructure.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.infrastructure.graph.llm_assessor import LlmInvestigationAssessor
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto

TODAY = date(2026, 8, 5)
MAY = AbsoluteRange(date(2026, 5, 1), date(2026, 5, 31))
JUNE = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
AUGUST = AbsoluteRange(date(2026, 8, 1), date(2026, 8, 31))
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
    AUGUST: (Group("Mercadona", 11_000, 4), Group("Amazon", 4_000, 1), Group("Cepsa", 5_000, 2)),
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
        self.requested_movements: list[int | None] = []
        self.failures: list[Exception] = []
        self.fail_on: dict[int, Exception] = {}
        self.attempts = 0
        self.listed: list[MovementListPlan] = []

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self._fail()
        self.ran.append(plan)
        self.requested_movements.append(movements)
        assert plan.date_range is not None
        groups = FIGURES[plan.date_range] if plan.group_by is GroupBy.MERCHANT else ()
        total = sum(g.total_minor for g in FIGURES[plan.date_range])
        rows = (
            (Movement(datetime(2026, 7, 5, tzinfo=UTC), "Compra Mercadona", -1_250, "EXPENSE"),)
            if plan.result_kind is ResultKind.RECORDS
            else ()
        )
        return QueryAnswer("EUR", total, 9, groups=groups, movements=rows)

    async def ranked(self, ranking: TopMovements, **_: object) -> Movement | None:
        self._fail()
        self.ran.append(ranking)
        (merchant,) = ranking.merchants
        if merchant not in LARGEST:
            return None
        moment = datetime(2026, 7, 12, 18, 30, tzinfo=UTC)
        return Movement(moment, f"Compra privada en {merchant}", LARGEST[merchant], "EXPENSE")

    async def list_movements(self, plan: MovementListPlan, **_: object) -> MovementListAnswer:
        self._fail()
        self.listed.append(plan)
        first = plan.cursor is None
        row_id = UUID(int=91 if first else 92)
        moment = datetime(2026, 7, 5 if first else 4, 10, tzinfo=UTC)
        row = Movement(
            moment,
            f"Movimiento {row_id.int}",
            1200,
            "EXPENSE",
            transaction_id=str(row_id),
            currency="EUR",
        )
        cursor = MovementCursor(moment, row_id) if first else None
        return MovementListAnswer(ACCOUNT, (row,), has_more=first, next_cursor=cursor)

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


async def test_a_period_clarification_resumes_the_typed_movement_list() -> None:
    world = World(
        (
            Clarify(
                Missing.WINDOW,
                draft=PlanFields(direction=Direction.ALL, metric=Metric.COUNT),
            ),
        ),
        (Patch("pending", PlanFields(window=JULY)),),
    )

    first = await world.ask(
        request_id="list-1",
        sentence="Perdona, ¿qué movimientos he tenido?",
    )

    assert first.result is not None
    assert first.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION
    assert first.state.pending is not None
    assert first.state.pending.draft.metric is Metric.COUNT

    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        request_id="list-2",
        sentence="julio",
    )

    assert second.result is not None
    assert second.state.pending is None
    assert world.ledger.ran[-1].direction is Direction.ALL
    assert world.ledger.ran[-1].metric is Metric.COUNT
    assert "movimientos" in second.result.text


@pytest.mark.asyncio
async def test_movement_list_pages_continue_from_the_persisted_focus_cursor() -> None:
    world = World(
        (ListMovements(page_size=1),),
        (ListMovements(page_size=1, continue_current=True),),
        (ListMovements(page_size=1, continue_current=True),),
    )

    first = await world.ask(request_id="page-1", sentence="Enséñame los últimos gastos")

    assert first.result is not None and len(first.result.movements) == 1
    rendered_first = question_to_dto(first.result)
    assert rendered_first.movements[0].transaction_id == str(UUID(int=91))
    assert rendered_first.movements[0].currency == "EUR"
    assert isinstance(first.state.focus, MovementListFocus)
    first_cursor = first.state.focus.plan.cursor
    assert first_cursor == MovementCursor(datetime(2026, 7, 5, 10, tzinfo=UTC), UUID(int=91))

    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        request_id="page-2",
        sentence="Dame otros gastos",
    )

    assert second.result is not None and len(second.result.movements) == 1
    assert second.result.movements[0].transaction_id == str(UUID(int=92))
    assert world.ledger.listed[1].cursor == first_cursor
    assert isinstance(second.state.focus, MovementListFocus)
    assert second.state.focus.plan.cursor is None

    third = await world.ask(
        conversation_id=second.state.conversation_id,
        version=second.state.version,
        request_id="page-3",
        sentence="Dame otros gastos",
    )
    assert third.result is not None
    assert "No hay movimientos más antiguos" in third.result.text
    assert len(world.ledger.listed) == 2


@pytest.mark.asyncio
async def test_new_list_task_switches_account_and_next_financial_task_drops_that_filter() -> None:
    world = World(
        (ListMovements(page_size=1),),
        (ListMovements(accounts=("Hucha",), page_size=1),),
        (ListMovements(page_size=1, continue_current=True),),
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=AUGUST,
                    categories=("RESTAURANTS",),
                )
            ),
        ),
    )
    first = await world.ask(request_id="session-list-1", sentence="Últimos gastos")
    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        request_id="session-list-2",
        sentence="¿Y los de Hucha?",
    )
    third = await world.ask(
        conversation_id=second.state.conversation_id,
        version=second.state.version,
        request_id="session-list-3",
        sentence="Dame otros 10",
    )
    fourth = await world.ask(
        conversation_id=third.state.conversation_id,
        version=third.state.version,
        request_id="session-list-4",
        sentence="¿Cuánto gasté en restaurantes este mes?",
    )

    assert world.ledger.listed[0].across_accounts is True
    assert world.ledger.listed[1].account_ids == (PIGGY,)
    assert world.ledger.listed[2].account_ids == (PIGGY,)
    assert world.ledger.listed[2].cursor == MovementCursor(
        datetime(2026, 7, 5, 10, tzinfo=UTC), UUID(int=91)
    )
    assert world.ledger.ran[-1].across_accounts is True
    assert fourth.state.scope.account_ids == ()


@pytest.mark.asyncio
async def test_ten_turn_banking_session_keeps_context_and_never_confirms_a_cancelled_bizum() -> (
    None
):
    """A scripted session acceptance: the graph carries state across distinct user goals.

    The interpreter is scripted on purpose. This exercises graph/state/bank/payment contracts;
    provider interpretation and human UX need separate evaluations with real conversation data.
    """

    class CategoryLedger(MerchantLedger):
        async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
            answer = await super().run(plan, **kwargs)
            return replace(
                answer,
                groups=(
                    Group("HOUSING_RENT", 4_000, 1),
                    Group("GROCERIES", 2_000, 2),
                    Group("RESTAURANTS", 1_500, 1),
                ),
            )

    world = World(
        (Balance(),),
        (ListMovements(accounts=("Cuenta corriente",), page_size=2),),
        (ListMovements(accounts=("Hucha",), page_size=2),),
        (ListMovements(page_size=2, continue_current=True),),
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=JULY,
                    categories=("RESTAURANTS",),
                    group_by=GroupBy.CATEGORY,
                )
            ),
        ),
        (
            Compare(
                PlanFields(
                    direction=Direction.EXPENSE,
                    categories=("RESTAURANTS",),
                    group_by=GroupBy.CATEGORY,
                ),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
        (Visualize("ACTIVE_RESULT", "BAR"),),
        (Delegate("UNSUPPORTED"),),
        (PrepareBizum("Aitor Beltran Diaz", 1_500),),
    )
    world.ledger = CategoryLedger()
    world.handler = world.build()
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    session: EngineAnswer | None = None
    seen: list[EngineAnswer] = []

    async def turn(
        sentence: str,
        request_id: str,
        *,
        caller_scopes: frozenset[str] = frozenset(),
    ) -> EngineAnswer:
        nonlocal session
        answer = await world.ask(
            conversation_id=session.state.conversation_id if session else None,
            version=session.state.version if session else None,
            request_id=request_id,
            sentence=sentence,
            caller_scopes=caller_scopes,
        )
        session = answer
        seen.append(answer)
        return answer

    balances = await turn("Â¿CÃ³mo estÃ¡n mis cuentas?", "session-balance")
    corriente = await turn(
        "EnsÃ©Ã±ame mis Ãºltimos 20 gastos de Cuenta corriente", "session-current"
    )
    hucha = await turn("Â¿Y los de la Hucha?", "session-hucha")
    more = await turn("Dame otros 10", "session-more")
    restaurants = await turn(
        "Â¿CuÃ¡nto gastÃ© en restaurantes el mes pasado?", "session-restaurants"
    )
    compared = await turn("CompÃ¡ralo con junio", "session-compare")
    chart = await turn("Ponme una grÃ¡fica", "session-chart")
    unsupported = await turn("Â¿QuÃ© porcentaje de mi sueldo se va en ocio?", "session-unsupported")
    preview = await turn("EnvÃ­a 15 euros a Aitor", "session-bizum", caller_scopes=scopes)
    cancelled = await turn("No, mejor d\u00e9jalo", "session-cancel", caller_scopes=scopes)

    assert balances.result is not None and "cuentas" in balances.result.text.lower()
    assert corriente.result is not None and corriente.result.movements
    assert hucha.result is not None and hucha.result.movements
    assert more.result is not None and more.result.movements
    assert world.ledger.listed[1].account_ids == (PIGGY,)
    assert world.ledger.listed[2].account_ids == (PIGGY,)
    assert world.ledger.listed[2].cursor == MovementCursor(
        datetime(2026, 7, 5, 10, tzinfo=UTC), UUID(int=91)
    )
    assert restaurants.result is not None and "410,00" in restaurants.result.text
    assert compared.result is not None
    assert chart.result is not None and chart.result.visual_decision is not None
    assert unsupported.result is not None
    assert unsupported.result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert cancelled.result is not None
    assert cancelled.result.outcome == PaymentOutcome.CANCELLED.value
    assert bank.confirmed == []
    assert bank.cancelled == [INTENT]
    assert [answer.state.version for answer in seen] == list(range(1, 11))


@pytest.mark.asyncio
async def test_an_empty_model_ask_keeps_an_unanswered_account_clarification() -> None:
    draft = PlanFields(
        direction=Direction.EXPENSE,
        window=JULY,
        accounts=("cuenta principal",),
    )
    world = World(
        (Clarify(Missing.ACCOUNT, about="cuenta principal", draft=draft),),
        (Patch("focus", PlanFields(window=JUNE)),),
        (Patch("focus", PlanFields(accounts=("Cuenta corriente",))),),
    )
    first = await world.ask(
        sentence="Cuánto gasté en la cuenta principal este mes", request_id="account-1"
    )
    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        sentence="¿Y el mes pasado?",
        request_id="account-2",
    )

    assert second.result is not None
    assert second.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert second.state.pending == Pending(Missing.ACCOUNT, draft.merged(PlanFields(window=JUNE)))
    assert "cuenta" in second.result.text

    third = await world.ask(
        conversation_id=second.state.conversation_id,
        version=second.state.version,
        sentence="Cuenta corriente",
        request_id="account-3",
    )

    assert third.result is not None and third.result.outcome == PlanningOutcome.PLANNED.value
    assert third.state.pending is None
    assert world.ledger.ran[-1].date_range == JUNE


async def test_scalar_comparison_checkpoints_both_reads_without_merchant_relabeling() -> None:
    world = World(
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        )
    )

    answer = await world.ask()

    assert [plan.group_by for plan in world.ledger.ran] == [GroupBy.NONE, GroupBy.NONE]
    assert answer.result is not None
    assert "210,00" in answer.result.text and "410,00" in answer.result.text
    assert answer.state.active_artifact is not None
    assert answer.state.active_artifact.comparison is not None
    assert answer.state.active_artifact.result_kind is ResultKind.COMPARISON
    assert answer.state.active_artifact.status is ArtifactStatus.VERIFIED
    assert answer.state.active_artifact.comparison_groups == ((), ())


async def test_investigate_comparison_keeps_its_periods_and_reads_merchants() -> None:
    pair = ComparisonPlan.between(
        FinancialPlan(Direction.EXPENSE, date_range=JUNE),
        FinancialPlan(Direction.EXPENSE, date_range=JULY),
    )
    assert pair is not None
    conversation_id = UUID(int=2)
    world = World((InvestigateComparison(),))
    stored = await world.store.save(
        DialogueState(conversation_id, focus=ComparisonFocus(pair)),
        account_id=ACCOUNT,
        expected_version=0,
    )

    answer = await world.ask(
        conversation_id=conversation_id,
        version=stored.version,
        sentence="A que se debe la diferencia?",
    )

    assert [plan.date_range for plan in world.ledger.ran] == [JUNE, JULY]
    assert all(plan.group_by is GroupBy.MERCHANT for plan in world.ledger.ran)
    assert answer.result is not None and "Amazon" in answer.result.text


async def test_investigation_does_not_replace_the_query_before_a_comparison() -> None:
    conversation_id = UUID(int=2)
    world = World(
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        ),
        (InvestigateComparison(),),
        (RestorePrevious(),),
    )
    original = FinancialPlan(Direction.EXPENSE, date_range=JUNE)
    saved = await world.store.save(
        DialogueState(conversation_id, focus=QueryFocus(original)),
        account_id=ACCOUNT,
        expected_version=0,
    )

    compared = await world.ask(conversation_id=conversation_id, version=saved.version)
    investigated = await world.ask(
        conversation_id=conversation_id,
        version=compared.state.version,
        sentence="A que se debe la diferencia?",
    )
    restored = await world.ask(
        conversation_id=conversation_id,
        version=investigated.state.version,
        sentence="Vuelve a la consulta anterior",
    )

    assert isinstance(restored.state.focus, QueryFocus)
    assert restored.state.focus.plan == original
    assert restored.result is not None and "2026-06-01" in restored.result.text


async def test_scalar_comparison_resumes_at_the_failed_later_window() -> None:
    world = World(
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
        )
    )
    world.ledger.fail_on = {2: BankingUnavailableError("down")}

    failed = await world.ask(request_id="comparison-retry")
    assert failed.result is not None and failed.result.outcome == "BANKING_UNAVAILABLE"
    assert world.ledger.attempts == 2

    resumed = await world.ask(request_id="comparison-retry")

    assert resumed.result is not None and resumed.result.outcome == PlanningOutcome.PLANNED.value
    assert world.ledger.attempts == 3
    assert [plan.date_range for plan in world.ledger.ran] == [JUNE, JULY]
    assert len(world.interpreter.sentences) == 1


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


async def test_restore_from_an_open_comparison_question_repeats_the_last_answered_query() -> None:
    world = World(
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),),
        (
            Compare(
                PlanFields(direction=Direction.EXPENSE),
                PlanFields(),
                PlanFields(),
            ),
        ),
        (RestorePrevious(),),
        (Patch("focus", PlanFields(window=JUNE)),),
    )

    first = await world.ask()
    clarification = await world.ask(
        conversation_id=first.state.conversation_id, version=1, request_id="open-question"
    )
    restored = await world.ask(
        conversation_id=first.state.conversation_id, version=2, request_id="dismiss-question"
    )
    followed_up = await world.ask(
        conversation_id=first.state.conversation_id, version=3, request_id="month-follow-up"
    )

    assert clarification.state.pending is not None
    assert clarification.result is not None
    assert clarification.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert restored.state.pending is None
    assert isinstance(restored.state.focus, QueryFocus)
    assert restored.state.focus.plan.date_range == JULY
    assert clarification.state.turns[-1] not in restored.state.turns
    assert isinstance(followed_up.state.focus, QueryFocus)
    assert followed_up.state.focus.plan.date_range == JUNE
    assert [plan.date_range for plan in world.ledger.ran] == [JULY, JULY, JUNE]


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


@pytest.mark.asyncio
async def test_explicit_payment_confirmation_survives_discarding_an_old_graph_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    preview = await world.ask(
        sentence="Hazle un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="old-checkpoint-payment-prepare",
        caller_scopes=scopes,
    )
    calls = world.calls.count

    monkeypatch.setattr(graph_turn, "GRAPH_VERSION", f"{investigation_graph.GRAPH_VERSION}+next")
    paid = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="sí",
        request_id="old-checkpoint-payment-confirm",
        caller_scopes=scopes,
    )

    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [INTENT]
    assert world.calls.count == calls


# --- what never runs here -------------------------------------------------------------------------


@pytest.mark.parametrize("to", ["PAYMENT", "TRANSFER", "BOOKING"])
async def test_a_money_flow_is_refused_without_bank_reads_or_execution(to: Any) -> None:
    world = World((Delegate(to),))

    answer = await world.ask(sentence="Hazme una transferencia de 50 euros a Marta")

    assert answer.result is not None and answer.delegate_to is None
    assert answer.state.version == 1
    if to == "TRANSFER":
        assert "contactos bancarios guardados" in answer.result.text
    else:
        assert "disponibles" in answer.result.text
    assert world.ledger.ran == []
    assert world.assessor.seen == []


@pytest.mark.asyncio
async def test_payment_capability_question_explains_preview_and_never_prepares_a_payment() -> None:
    world = World((Delegate("PAYMENT"),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="¿Puedes hacer Bizums?",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == Spoken.GUIDANCE.value
    assert "contactos guardados" in answer.result.text
    assert "No se envía hasta que confirmes" in answer.result.text
    assert bank.prepared == []
    assert bank.confirmed == []
    assert world.ledger.ran == [] and world.ledger.listed == []


@pytest.mark.asyncio
async def test_help_explains_available_bizums_without_preparing_one() -> None:
    world = World((Smalltalk("HELP"),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="¿Puedes hacer Bizums?",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == Spoken.IDENTITY.value
    assert "preparar un Bizum" in answer.result.text
    assert "No se envía hasta que confirmes" in answer.result.text
    assert bank.prepared == [] and bank.confirmed == []


async def test_an_unsupported_operation_keeps_its_distinct_outcome_and_context() -> None:
    world = World(GROCERIES_IN_JULY, (Delegate("UNSUPPORTED"),))
    initial = await world.ask(request_id="query", sentence="gastos de supermercado en julio")

    answer = await world.ask(
        conversation_id=initial.state.conversation_id,
        version=initial.state.version,
        request_id="unsupported",
        sentence="¿Qué porcentaje de mi sueldo se va en ocio?",
    )

    assert answer.result is not None
    assert answer.result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert answer.state.version == 2
    assert answer.state.focus == initial.state.focus
    assert len(world.ledger.ran) == 1
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
    assert chart.result.text == "Aqu\u00ed tienes el gr\u00e1fico de los resultados de la consulta."
    assert restyled.result.text == "He actualizado el gr\u00e1fico con la misma consulta."
    assert reads_after_chart == reads_after_query
    assert chart.state.active_artifact == query.state.active_artifact
    assert chart.state.active_visual is not None
    assert chart.state.active_visual.version == 1
    assert chart.state.active_visual.result_version == query.state.active_artifact.result_version
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


async def test_analytical_chart_regroup_reruns_banking_and_replaces_the_chart() -> None:
    class ReshapeLedger(MerchantLedger):
        async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
            self._fail()
            self.ran.append(plan)
            self.requested_movements.append(kwargs.get("movements"))  # type: ignore[arg-type]
            groups = (
                (Group("2026-06", 8_000, 3), Group("2026-07", 12_000, 4))
                if plan.group_by is GroupBy.MONTH
                else (
                    Group("HOUSING_RENT", 5_000, 1),
                    Group("GROCERIES", 4_000, 2),
                    Group("RESTAURANTS", 3_000, 2),
                    Group("TRANSPORT_FUEL", 2_000, 1),
                    Group("TRANSFER_BIZUM", 1_500, 1),
                    Group("SUBSCRIPTIONS", 1_200, 1),
                    Group("HEALTH", 1_000, 1),
                    Group("TRANSPORT_PUBLIC", 800, 1),
                    Group("LEISURE", 1_500, 1),
                )
            )
            return QueryAnswer("EUR", 20_000, 7, groups=groups)

    window = RelativePeriod(Period.LAST_N_MONTHS, 3)
    world = World(
        (Ask(PlanFields(direction=Direction.EXPENSE, window=window, group_by=GroupBy.CATEGORY)),),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
        (
            Patch(
                "focus",
                PlanFields(group_by=GroupBy.MONTH, result_kind=ResultKind.TIME_SERIES),
            ),
            Visualize("ACTIVE_CHART", "AUTO"),
        ),
    )
    world.ledger = ReshapeLedger()
    world.handler = world.build()

    query = await world.ask(request_id="query", sentence="gastos por categorÃ­a en tres meses")
    chart = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        request_id="chart",
        sentence="ensÃ©Ã±amelo en un grÃ¡fico",
    )
    reads_before_reshape = len(world.ledger.ran)
    reshaped = await world.ask(
        conversation_id=chart.state.conversation_id,
        version=chart.state.version,
        request_id="reshape",
        sentence="agrÃºpalo por mes",
    )

    assert len(world.ledger.ran) == reads_before_reshape + 1
    assert world.ledger.ran[-1].group_by is GroupBy.MONTH
    assert world.ledger.ran[-1].result_kind is ResultKind.TIME_SERIES
    assert reshaped.result is not None and reshaped.result.visual_decision is not None
    assert reshaped.result.visual_dataset is not None
    assert [row.dimension for row in reshaped.result.visual_dataset.rows] == [
        "2026-06",
        "2026-07",
    ]
    assert reshaped.state.active_artifact is not None
    assert reshaped.state.active_artifact.plan.group_by is GroupBy.MONTH
    assert reshaped.state.active_visual is not None
    assert (
        reshaped.state.active_visual.result_version == reshaped.state.active_artifact.result_version
    )
    assert reshaped.state.version == chart.state.version + 1


async def test_regroup_and_update_chart_is_refused_when_no_chart_is_active() -> None:
    regroup = (
        Patch(
            "focus",
            PlanFields(group_by=GroupBy.MONTH, result_kind=ResultKind.TIME_SERIES),
        ),
        Visualize("ACTIVE_CHART", "AUTO"),
    )
    world = World(
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),),
        regroup,
    )
    first = await world.ask(request_id="query", sentence="gastos en tres meses")
    reads_before = len(world.ledger.ran)

    refused = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        request_id="no-chart",
        sentence="agrÃºpalo por mes",
    )

    assert len(world.ledger.ran) == reads_before
    assert refused.state.focus == first.state.focus
    assert refused.state.active_visual is None
    assert refused.state.version == first.state.version + 1


async def test_category_chart_uses_human_readable_labels() -> None:
    class CategoryLedger(MerchantLedger):
        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self._fail()
            self.ran.append(plan)
            return QueryAnswer(
                "EUR",
                12_000,
                3,
                groups=(
                    Group("HOUSING_RENT", 4_000, 1),
                    Group("GROCERIES", 2_000, 2),
                    Group("RESTAURANTS", 1_500, 1),
                    Group("TRANSPORT_FUEL", 1_200, 1),
                    Group("TRANSFER_BIZUM", 1_000, 1),
                    Group("SUBSCRIPTIONS", 800, 1),
                    Group("HEALTH", 600, 1),
                    Group("TRANSPORT_PUBLIC", 500, 1),
                    Group("LEISURE", 400, 1),
                ),
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
        "restaurantes",
        "gasolina",
        "bizum",
        "suscripciones",
        "salud",
        "transporte p\u00fablico",
        "ocio",
    ]
    assert len(chart.result.visual_dataset.rows) == 9
    assert chart.result.visual_decision is not None
    assert chart.result.visual_decision.spec.limit is None


async def test_query_and_visualization_compose_in_one_turn_without_hiding_the_breakdown() -> None:
    class CategoryLedger(MerchantLedger):
        async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
            answer = await super().run(plan, **kwargs)
            return replace(
                answer,
                groups=(
                    Group("HOUSING_RENT", 4_000, 1),
                    Group("GROCERIES", 2_000, 2),
                    Group("RESTAURANTS", 1_500, 1),
                ),
            )

    world = World(
        (
            Ask(
                PlanFields(
                    direction=Direction.EXPENSE,
                    window=JULY,
                    result_kind=ResultKind.BREAKDOWN,
                    group_by=GroupBy.CATEGORY,
                )
            ),
            Visualize("ACTIVE_RESULT", "BAR"),
        )
    )
    world.ledger = CategoryLedger()
    world.handler = world.build()

    answer = await world.ask(
        sentence="Desglósame los gastos por categoría de julio y enséñamelo en un gráfico"
    )

    assert answer.result is not None
    assert answer.result.outcome == PlanningOutcome.PLANNED.value
    assert answer.result.visual_dataset is not None
    assert answer.result.visual_decision is not None
    rendered = question_to_dto(answer.result)
    replayed_render = question_to_dto(answer.result)
    assert rendered.plan_context is not None
    assert rendered.plan_context.query_id
    assert rendered.plan_context.artifact_id
    assert rendered.plan_context.view_id
    assert isinstance(rendered.visualization, ChartVisualizationResponse)
    assert rendered.visualization.chart_id == rendered.plan_context.view_id
    assert replayed_render.visualization == rendered.visualization
    assert len(answer.result.groups) == 3
    assert [group.key for group in answer.result.groups] == [
        "HOUSING_RENT",
        "GROCERIES",
        "RESTAURANTS",
    ]
    assert answer.state.version == 1
    assert answer.state.active_artifact is not None
    assert answer.state.active_artifact.result_kind is ResultKind.BREAKDOWN
    assert answer.state.active_artifact.status is ArtifactStatus.VERIFIED
    assert answer.state.active_artifact.source.value == "BANKING_API"
    assert answer.state.active_visual is not None
    assert rendered.plan_context.query_id == answer.state.active_artifact.query_id
    assert rendered.plan_context.artifact_id == answer.state.active_artifact.artifact_id
    assert answer.state.active_visual.view_id == rendered.plan_context.view_id
    assert len(world.ledger.ran) == 1


async def test_ordinary_bank_execution_is_checkpointed_before_presentation() -> None:
    world = World(GROCERIES_IN_JULY)

    answer = await world.ask(request_id="ordinary-checkpoint")

    assert answer.result is not None
    saved_graph = (await world.handler._graph.aget_state(world.last_config())).values  # noqa: SLF001
    assert saved_graph["status"] == "committed"
    assert isinstance(codec.loads_typed(saved_graph["execution_result"]), QueryRun)
    assert saved_graph["bank_calls"] == world.ledger.attempts == 1

    # Replaying the same request returns its committed answer and never repeats the banking read.
    replay = await world.ask(request_id="ordinary-checkpoint")
    assert replay.result == answer.result
    assert world.ledger.attempts == 1


async def test_records_result_kind_requests_and_returns_bank_movements() -> None:
    world = World(
        (
            Ask(
                PlanFields(
                    direction=Direction.ALL,
                    window=JULY,
                    metric=Metric.COUNT,
                    result_kind=ResultKind.RECORDS,
                )
            ),
        )
    )

    answer = await world.ask(sentence="Enséñame los movimientos de julio")

    assert answer.result is not None
    assert world.ledger.requested_movements == [MAX_SUPPORTING_MOVEMENTS]
    assert len(answer.result.movements) == 1


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

    async def stuck(config: Any, context: Any) -> None:
        persisting.set()
        await never.wait()

    world.handler._persist_progress = stuck  # type: ignore[method-assign]  # noqa: SLF001
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

    async def broken(config: Any, context: Any) -> None:
        raise RuntimeError("the checkpointer is down")

    world.handler._persist_progress = broken  # type: ignore[method-assign]  # noqa: SLF001
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
async def test_listing_contacts_keeps_a_pending_bizum_draft_in_graph_state() -> None:
    world = World(
        (PrepareBizum("alguien", None),),
        (ListContacts(),),
        (PrepareBizum("Marta", None),),
        (PrepareBizum("Marta Solis Vega", 1_500),),
        (PrepareBizum("Marta Solis Vega", None),),
    )
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    draft = await world.ask(
        sentence="Quiero hacer un bizum a alguien",
        request_id="draft-before-contacts",
        caller_scopes=scopes,
    )
    assert isinstance(draft.state.payment, PaymentDraftPending)
    assert draft.state.payment.draft.recipient_contact_id is None

    listed = await world.ask(
        conversation_id=draft.state.conversation_id,
        version=draft.state.version,
        sentence="Que contactos tengo?",
        request_id="list-contacts-during-payment",
    )

    assert listed.result is not None and listed.result.outcome == "CONTACTS"
    assert listed.result.contacts
    assert isinstance(listed.state.payment, PaymentDraftPending)

    selected = await world.ask(
        conversation_id=listed.state.conversation_id,
        version=listed.state.version,
        sentence="Vale que te parece si le hacemos un bizum a Marta con concepto tapas",
        request_id="select-contact-for-payment",
        caller_scopes=scopes,
    )

    assert isinstance(selected.state.payment, PaymentDraftPending)
    assert selected.state.payment.candidate_contact_ids == (
        UUID("55555555-5555-5555-5555-555555555555"),
        UUID("66666666-6666-6666-6666-666666666666"),
    )
    assert selected.state.payment.draft.concept == "tapas"
    assert selected.result is not None
    assert selected.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    amount = await world.ask(
        conversation_id=selected.state.conversation_id,
        version=selected.state.version,
        sentence="15 euros",
        request_id="amount-with-ambiguous-contact",
        caller_scopes=scopes,
    )

    assert isinstance(amount.state.payment, PaymentDraftPending)
    assert (
        amount.state.payment.candidate_contact_ids == selected.state.payment.candidate_contact_ids
    )
    assert amount.state.payment.draft.amount_minor == 1_500
    assert amount.result is not None
    assert amount.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    resolved = await world.ask(
        conversation_id=amount.state.conversation_id,
        version=amount.state.version,
        sentence="Marta Solis Vega",
        request_id="select-marta-and-preview",
        caller_scopes=scopes,
    )

    assert resolved.result is not None
    assert resolved.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert isinstance(resolved.state.payment, PaymentConfirmationPending)
    assert 'con el concepto "tapas"' in resolved.result.text
    assert bank.prepared[0].amount_minor == 1_500
    assert bank.prepared[0].recipient_contact_id == UUID("55555555-5555-5555-5555-555555555555")
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_no_cancels_an_ambiguous_bizum_draft_without_another_model_or_bank_call() -> None:
    world = World((PrepareBizum("Marta", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    draft = await world.ask(
        sentence="Haz un bizum a Marta de 20 euros",
        request_id="ambiguous-marta-draft",
        caller_scopes=scopes,
    )
    assert isinstance(draft.state.payment, PaymentDraftPending)
    assert draft.state.payment.candidate_contact_ids
    calls_before_cancel = world.calls.count

    cancelled = await world.ask(
        conversation_id=draft.state.conversation_id,
        version=draft.state.version,
        sentence="no",
        request_id="cancel-ambiguous-marta-draft",
        caller_scopes=scopes,
    )

    assert cancelled.result is not None
    assert cancelled.result.outcome == PaymentOutcome.CANCELLED.value
    assert "No se ha enviado nada" in cancelled.result.text
    assert cancelled.state.payment is None
    assert cancelled.state.version == draft.state.version + 1
    assert world.calls.count == calls_before_cancel
    assert bank.prepared == []
    assert bank.confirmed == []


async def test_payment_with_redundant_clarify_uses_the_safe_payment_question() -> None:
    world = World((PrepareBizum("alguien", None), Clarify(Missing.SUBJECT)))
    with_payment_commands(world)

    answer = await world.ask(
        sentence="Quiero hacer un bizum a alguien",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "quieres enviar el dinero" in answer.result.text
    assert answer.result.conversation is not None


@pytest.mark.asyncio
async def test_payment_draft_stays_paused_after_subject_change_until_explicit_resume() -> None:
    world = World(
        (PrepareBizum("Marta Solis Vega", None),),
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY)),),
        (PrepareBizum("Marta Solis Vega", 1_500),),
    )
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    draft = await world.ask(
        sentence="Hazle un bizum a Marta Solis Vega",
        caller_scopes=scopes,
    )
    assert isinstance(draft.state.payment, PaymentDraftPending)
    assert not draft.state.payment.suspended

    query = await world.ask(
        conversation_id=draft.state.conversation_id,
        version=draft.state.version,
        sentence="Cuánto gasté en gasolina en julio?",
    )
    assert query.result is not None
    assert isinstance(query.state.payment, PaymentDraftPending)
    assert query.state.payment.suspended

    paused = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        sentence="15 euros",
        caller_scopes=scopes,
    )
    assert paused.result is not None
    assert paused.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "está pausado" in paused.result.text
    assert isinstance(paused.state.payment, PaymentDraftPending)
    assert paused.state.payment.suspended
    assert bank.prepared == []

    resumed = await world.ask(
        conversation_id=paused.state.conversation_id,
        version=paused.state.version,
        sentence="Continuar con el bizum, 15 euros",
        caller_scopes=scopes,
    )
    assert resumed.result is not None
    assert resumed.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert isinstance(resumed.state.payment, PaymentConfirmationPending)
    assert len(bank.prepared) == 1
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_graph_prepares_a_grounded_contact_transfer_and_requires_explicit_refusal() -> None:
    world = World((PrepareTransfer("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})

    preview = await world.ask(
        sentence="Haz una transferencia de 20 euros a Aitor Beltran Diaz",
        request_id="prepare-transfer",
        caller_scopes=scopes,
    )

    assert preview.result is not None
    assert preview.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert preview.state.version == 1
    assert isinstance(preview.state.payment, PaymentConfirmationPending)
    assert bank.prepared[0].rail is Rail.TRANSFER
    assert bank.prepared[0].recipient_contact_id == UUID(int=8)
    assert preview.state.payment.preview.recipient_iban == "ES9121000418450200051332"
    assert bank.confirmed == []

    refused = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="no",
        request_id="cancel-transfer",
        caller_scopes=scopes,
    )

    assert refused.result is not None
    assert refused.result.outcome == PaymentOutcome.CANCELLED.value
    assert refused.state.version == 2
    assert bank.cancelled == [INTENT]
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_payment_amount_correction_cancels_old_preview_and_requires_new_confirmation() -> (
    None
):
    class IdentifiedBank(Bank):
        async def prepare(self, request: Any, *, account_id: UUID, key: str) -> Any:
            preview = await super().prepare(request, account_id=account_id, key=key)
            return replace(preview, intent_id=UUID(int=600 + len(self.prepared)))

    world = World((PrepareBizum("Marta Solis Vega", 1_500),))
    bank = with_payment_commands(world, IdentifiedBank())
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    preview = await world.ask(
        sentence="Hazle un bizum de 15 euros a Marta Solis Vega con concepto tapas",
        request_id="correct-bizum-prepare",
        caller_scopes=scopes,
    )
    assert isinstance(preview.state.payment, PaymentConfirmationPending)
    old_intent = preview.state.payment.preview.intent_id
    assert preview.state.payment.draft is not None
    assert preview.state.payment.draft.concept == "tapas"

    world.interpreter._turns.append((PrepareBizum(None, 2_000),))  # noqa: SLF001
    corrected = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="mejor 20 euros",
        request_id="correct-bizum-amount",
        caller_scopes=scopes,
    )

    assert corrected.result is not None
    assert corrected.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert "20,00" in corrected.result.text
    assert isinstance(corrected.state.payment, PaymentConfirmationPending)
    new_intent = corrected.state.payment.preview.intent_id
    assert new_intent != old_intent
    assert corrected.state.payment.preview.amount_minor == 2_000
    assert corrected.state.payment.draft is not None
    assert corrected.state.payment.draft.recipient_name == "Marta Solis Vega"
    assert corrected.state.payment.draft.concept == "tapas"
    assert bank.cancelled == [old_intent]
    assert bank.confirmed == []
    assert [request.amount_minor for request in bank.prepared] == [1_500, 2_000]

    paid = await world.ask(
        conversation_id=corrected.state.conversation_id,
        version=corrected.state.version,
        sentence="sí",
        request_id="confirm-corrected-bizum",
        caller_scopes=scopes,
    )
    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [new_intent]


@pytest.mark.asyncio
async def test_question_during_preview_preserves_and_confirms_payment() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),), GROCERIES_IN_JULY)
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    preview = await world.ask(
        sentence="Hazle un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="question-during-payment-prepare",
        caller_scopes=scopes,
    )

    world.ledger.groups_for_next_turn = (Group("Mercadona", 41_000, 7),)
    answered = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="¿Cuánto gasté en julio?",
        request_id="question-during-payment-preview",
        caller_scopes=scopes,
    )

    assert answered.result is not None and "410,00" in answered.result.text
    assert isinstance(answered.state.payment, PaymentConfirmationPending)
    assert answered.state.payment.preview.amount_minor == 2_000
    assert bank.cancelled == [] and bank.confirmed == []

    paid = await world.ask(
        conversation_id=answered.state.conversation_id,
        version=answered.state.version,
        sentence="sí",
        request_id="confirm-after-interruption",
        caller_scopes=scopes,
    )
    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [INTENT]


@pytest.mark.asyncio
async def test_category_catalogue_is_answered_during_payment_and_preserves_the_preview() -> None:
    world = World((PrepareBizum("Aitor Beltran Diaz", 2_000),), (ListCategories(),))
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    preview = await world.ask(
        sentence="Hazle un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="category-during-payment-prepare",
        caller_scopes=scopes,
    )

    catalogue = await world.ask(
        conversation_id=preview.state.conversation_id,
        version=preview.state.version,
        sentence="¿Qué categorías puedo consultar?",
        request_id="category-during-payment-preview",
        caller_scopes=scopes,
    )

    assert catalogue.result is not None
    assert catalogue.result.outcome == "CATEGORIES"
    assert "Puedo consultar estas categorías" in catalogue.result.text
    assert isinstance(catalogue.state.payment, PaymentConfirmationPending)
    assert catalogue.state.payment.preview.amount_minor == 2_000
    assert bank.cancelled == [] and bank.confirmed == []

    paid = await world.ask(
        conversation_id=catalogue.state.conversation_id,
        version=catalogue.state.version,
        sentence="sí",
        request_id="confirm-after-category-catalogue",
        caller_scopes=scopes,
    )
    assert paid.result is not None and paid.result.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [INTENT]


@pytest.mark.asyncio
async def test_bare_correction_keeps_focus_and_applies_the_next_named_change() -> None:
    world = World(
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, categories=("GROCERIES",))),),
        (AskCorrection(),),
        (Patch("focus", PlanFields(categories=("RESTAURANTS",))),),
    )
    first = await world.ask(
        sentence="Cuánto gasté en supermercado en julio", request_id="correction-1"
    )
    asked = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        sentence="Perdona, no me has entendido",
        request_id="correction-2",
    )

    assert asked.result is not None
    assert asked.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "corregir" in asked.result.text
    assert asked.state.pending is not None
    assert asked.state.pending.missing is Missing.CORRECTION
    assert asked.state.focus == first.state.focus

    corrected = await world.ask(
        conversation_id=asked.state.conversation_id,
        version=asked.state.version,
        sentence="Me refería a restaurantes",
        request_id="correction-3",
    )

    assert corrected.state.pending is None
    assert world.ledger.ran[-1].date_range == JULY
    assert world.ledger.ran[-1].categories == ("RESTAURANTS",)


@pytest.mark.asyncio
async def test_transfer_operation_cannot_change_a_bizum_sentence_into_a_transfer() -> None:
    world = World((PrepareTransfer("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="Haz un bizum de 20 euros a Aitor Beltran Diaz",
        request_id="mismatched-transfer-rail",
        caller_scopes=frozenset({"payments:prepare"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert bank.prepared == [] and bank.confirmed == []


@pytest.mark.asyncio
async def test_transfer_to_contact_without_saved_account_refuses_without_claiming_iban_input() -> (
    None
):
    world = World((PrepareTransfer("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    bank.no_transfer_contacts.add(UUID(int=8))

    answer = await world.ask(
        sentence="Haz una transferencia de 20 euros a Aitor Beltran Diaz",
        request_id="transfer-without-saved-account",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert "no tiene una cuenta bancaria guardada" in answer.result.text
    assert "No he preparado ningún pago." in answer.result.text
    assert answer.state.version == 1
    assert answer.state.payment is None
    assert bank.prepared[0].rail is Rail.TRANSFER
    assert bank.confirmed == [] and bank.cancelled == []


@pytest.mark.asyncio
async def test_graph_uses_user_typed_iban_for_a_transfer_to_a_known_contact() -> None:
    world = World((PrepareTransfer("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)
    bank.no_transfer_contacts.add(UUID(int=8))

    answer = await world.ask(
        sentence=(
            "Haz una transferencia de 20 euros a Aitor Beltran Diaz, "
            "IBAN ES91 2100 0418 4502 0005 1332"
        ),
        request_id="typed-iban-transfer",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.prepared[0].rail is Rail.TRANSFER
    assert bank.prepared[0].recipient_iban == "ES9121000418450200051332"
    assert bank.prepared[0].recipient_contact_id is None
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_graph_keeps_typed_iban_when_the_amount_is_provided_on_a_later_turn() -> None:
    world = World(
        (PrepareTransfer("Aitor Beltran Diaz", None),),
        (PrepareTransfer(None, 2_000),),
    )
    bank = with_payment_commands(world)
    scopes = frozenset({"payments:prepare", "payments:confirm"})
    first = await world.ask(
        sentence="Haz una transferencia a Aitor Beltran Diaz con IBAN ES9121000418450200051332",
        request_id="typed-iban-needs-amount",
        caller_scopes=scopes,
    )
    assert first.result is not None
    assert first.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value, first.result.text
    assert isinstance(first.state.payment, PaymentDraftPending)
    assert first.state.payment.draft.recipient_iban == "ES9121000418450200051332"

    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        sentence="20 euros",
        request_id="typed-iban-amount-answer",
        caller_scopes=scopes,
    )

    assert second.result is not None
    assert second.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.prepared[0].recipient_iban == "ES9121000418450200051332"
    assert bank.prepared[0].amount_minor == 2_000
    assert bank.prepared[0].recipient_contact_id is None


@pytest.mark.asyncio
async def test_graph_prepares_transfer_to_new_recipient_from_user_typed_name_and_iban() -> None:
    world = World((PrepareTransfer("Juan Perez", 2_000),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence="Haz una transferencia de 20 euros a Juan Perez con IBAN ES9121000418450200051332",
        request_id="typed-new-recipient",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert isinstance(answer.state.payment, PaymentConfirmationPending)
    assert answer.state.payment.preview.recipient == "Juan Perez"
    assert answer.state.payment.preview.recipient_iban == "ES9121000418450200051332"
    assert bank.prepared[0].recipient_name == "Juan Perez"
    assert bank.prepared[0].recipient_contact_id is None
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_graph_retains_new_recipient_and_iban_while_asking_for_amount() -> None:
    world = World(
        (PrepareTransfer("Juan Perez", None),),
        # Model misclassification cannot change the persisted transfer when this reply omits a rail.
        (PrepareBizum("Juan Perez", 2_000),),
    )
    bank = with_payment_commands(world)
    first = await world.ask(
        sentence="Haz una transferencia a Juan Perez con IBAN ES9121000418450200051332",
        request_id="new-recipient-needs-amount",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert first.result is not None
    assert first.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert isinstance(first.state.payment, PaymentDraftPending)
    assert first.state.payment.draft.recipient_name == "Juan Perez"
    assert first.state.payment.draft.recipient_iban == "ES9121000418450200051332"

    second = await world.ask(
        conversation_id=first.state.conversation_id,
        version=first.state.version,
        sentence="20 euros",
        request_id="new-recipient-amount-answer",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert second.result is not None
    assert second.result.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.prepared[0].recipient_name == "Juan Perez"
    assert bank.prepared[0].recipient_iban == "ES9121000418450200051332"
    assert bank.prepared[0].recipient_contact_id is None
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_typed_iban_payee_cannot_be_redirected_to_a_different_saved_contact() -> None:
    world = World((PrepareTransfer("Aitor Beltran Diaz", 2_000),))
    bank = with_payment_commands(world)

    answer = await world.ask(
        sentence=(
            "Haz una transferencia de 20 euros a Aitor Beltran Diaz pero a Juan Perez "
            "con IBAN ES9121000418450200051332"
        ),
        request_id="typed-iban-conflicting-payees",
        caller_scopes=frozenset({"payments:prepare", "payments:confirm"}),
    )

    assert answer.result is not None
    assert answer.result.outcome == PlanningOutcome.INVALID_DOMAIN.value
    assert bank.prepared == []
    assert bank.confirmed == []


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
    assert query.state.payment.suspended
    assert bank.prepared == []

    paused = await world.ask(
        conversation_id=query.state.conversation_id,
        version=query.state.version,
        sentence="20 euros",
        request_id="bizum-interrupt-paused-amount",
        caller_scopes=scopes,
    )
    assert paused.result is not None
    assert paused.result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "está pausado" in paused.result.text
    assert isinstance(paused.state.payment, PaymentDraftPending)
    assert bank.prepared == []

    world.interpreter._turns.append((PrepareBizum(None, 2_000),))  # noqa: SLF001
    preview = await world.ask(
        conversation_id=paused.state.conversation_id,
        version=paused.state.version,
        sentence="Continuar con el bizum, 20 euros",
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
    assert answer.state.active_artifact is not None
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
    world.interpreter._turns.append((PrepareBizum(None, None),))  # noqa: SLF001

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
    assert answer.state.active_artifact is not None
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
    assert chart.state.active_artifact is not None
    assert chart.state.active_artifact.comparison is not None
    assert chart.state.active_artifact.comparison_groups is not None
