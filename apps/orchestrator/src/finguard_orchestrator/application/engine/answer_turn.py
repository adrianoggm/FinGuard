"""One turn of the model-led engine: interpret, ground, transition, execute, present, save.

The person's sentence goes to the interpreter and to nothing else that reads it; it is kept, as
said, only in the turn the state remembers for the interpreter's next prompt. Every later step works
on the operations the interpreter returned.

What is saved, and when:
- an answer, a question back, a courtesy or a reset: the transition's state, with this turn
  remembered, under the next version;
- a turn another flow owns (`Delegate`): nothing, so the conversation is exactly as it was when that
  flow hands it back;
- a turn the bank or the model could not answer: nothing. Work the bank never answered must not
  become what is on screen, and a version moved by a failure would turn the client's retry into a
  stale one.

A version that is not the stored one is refused before anything runs (`StaleDialogueError`, raised
to the caller), and the save itself is a compare-and-set, so of two turns made against one version
exactly one is kept.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.executor import MixedCurrenciesError, execute
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import Clarify, Delegate, Operation
from finguard_orchestrator.domain.engine.state import DialogueState, DialogueTurn, Missing
from finguard_orchestrator.domain.engine.transitions import (
    InvariantViolationError,
    Transition,
    apply,
)
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.dialogue_store import DialogueStorePort, StaleDialogueError
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.turn_operations import (
    InterpretationFailedError,
    TurnOperationsPort,
)

logger = logging.getLogger(__name__)

# What the bank can answer instead of figures. Each is answered honestly and saves nothing.
_UNANSWERED = (BankingUnavailableError, QueryRejectedError, MixedCurrenciesError, InvalidValueError)


@dataclass(frozen=True)
class EngineAnswer:
    """`result` is None exactly when `delegate_to` names the flow that owns this turn. `state` is
    the state as saved, or as loaded when nothing was saved."""

    result: AskFinancialQuestionResult | None
    state: DialogueState
    delegate_to: str | None
    model_calls: int
    stages_ms: dict[str, int]


class AnswerTurnHandler:
    def __init__(
        self,
        interpreter: TurnOperationsPort,
        store: DialogueStorePort,
        queries: FinancialQueryPort,
        summary: AccountSummaryPort,
        accounts: AccountDirectoryPort,
        contacts: ContactDirectoryPort,
        categories: frozenset[str],
        *,
        count_calls: Callable[[], int] = lambda: 0,
    ) -> None:
        """`count_calls` reads the model calls made so far in this request (ruling R1); the turn
        reports how many it added."""
        self._interpreter = interpreter
        self._store = store
        self._queries = queries
        self._summary = summary
        self._accounts = accounts
        self._contacts = contacts
        self._categories = categories
        self._count_calls = count_calls

    async def answer(
        self,
        sentence: str,
        *,
        conversation_id: UUID | None,
        version: int | None,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
    ) -> EngineAnswer:
        calls_before = self._count_calls()
        stages = _Stages()
        state = await self._loaded(conversation_id)
        if version is not None and version != state.version:
            raise StaleDialogueError(state)

        def unsaved(
            result: AskFinancialQuestionResult | None, delegate_to: str | None = None
        ) -> EngineAnswer:
            calls = self._count_calls() - calls_before
            return EngineAnswer(result, state, delegate_to, calls, stages.ms)

        try:
            with stages("ground"):
                grounds = await self._grounds(account_id)
            names = {holding.account_id: holding.display_name for holding in grounds.accounts}
            with stages("interpret"):
                ops = await self._interpreter.interpret(
                    sentence, state, today=today, account_names=names
                )
        except (BankingUnavailableError, InterpretationFailedError) as unread:
            logger.warning("engine.turn_unread error=%s", type(unread).__name__)
            return unsaved(presenter.failed(unread))

        with stages("transition"):
            moved = _applied(state, ops, today, grounds)
        if isinstance(moved.reply, Delegate):
            return unsaved(None, moved.reply.to)

        if moved.work is None:
            result = presenter.replied(moved.reply, moved.state, names)
            # A template: it carries no figure, so it can be remembered as it was said.
            in_words = result.text
        else:
            try:
                with stages("execute"):
                    run = await execute(
                        moved.work,
                        queries=self._queries,
                        summary=self._summary,
                        account_id=account_id,
                        today=today,
                        reporting_timezone=reporting_timezone,
                    )
            except _UNANSWERED as unanswered:
                logger.warning("engine.work_unanswered error=%s", type(unanswered).__name__)
                return unsaved(presenter.failed(unanswered))
            result = presenter.answered(
                run,
                scope=moved.work.account_ids,
                account_names=names,
                reporting_timezone=reporting_timezone,
            )
            # The plan in words and never the text: a figure in the prompt is one a model repeats.
            in_words = result.plan_in_words or ""

        with stages("save"):
            saved = await self._store.save(
                moved.state.remembered(DialogueTurn(said=sentence, answered=in_words)),
                expected_version=state.version,
            )
        calls = self._count_calls() - calls_before
        return EngineAnswer(result, saved, None, calls, stages.ms)

    async def _loaded(self, conversation_id: UUID | None) -> DialogueState:
        """The stored state, or a new one: under a new id, or under the id the client named when
        nothing is stored for it (a restart of this single-instance store)."""
        if conversation_id is None:
            return DialogueState(uuid4())
        return await self._store.load(conversation_id) or DialogueState(conversation_id)

    async def _grounds(self, account_id: UUID) -> Grounds:
        """The holder's catalogues, read once per turn: names are grounded against these only."""
        holdings, contacts = await asyncio.gather(
            self._accounts.all_of(account_id), self._contacts.all_of(account_id)
        )
        return Grounds(self._categories, holdings, contacts)


def _applied(
    state: DialogueState, ops: Sequence[Operation], today: date, grounds: Grounds
) -> Transition:
    """`apply`, with its one escaping error turned into a question back (ruling R3). That error
    means a transition broke a rule it exists to keep: a bug here, not the model's, so it is logged
    as one and the person is asked rather than answered from a plan nobody checked."""
    try:
        return apply(state, ops, today=today, grounds=grounds)
    except InvariantViolationError as broken:
        logger.error("engine.invariant_violation detail=%s", broken)
        return Transition(state, None, Clarify(Missing.SUBJECT))


class _Stages:
    """Milliseconds per stage of one turn, for the latency the evaluation reports by stage."""

    def __init__(self) -> None:
        self.ms: dict[str, int] = {}

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = round((time.perf_counter() - started) * 1000)
