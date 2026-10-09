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

A conversation is loaded and saved under the caller's account (ruling R20). An id the store does not
hold under it, whether never saved, forgotten by a restart or another holder's, starts a new
conversation under a new id, whatever version the client names with it (ruling R27): there is no
held state to be stale against, and refusing would refuse the same client on every turn after a
restart. The three look the same from outside, which is what keeps someone else's from being found
by trying ids.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from uuid import UUID, uuid4

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.executor import MixedCurrenciesError, execute
from finguard_orchestrator.application.engine.transition_guard import apply_guarded
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import Delegate
from finguard_orchestrator.domain.engine.state import DialogueState, DialogueTurn
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
        request_id: str | None = None,
    ) -> EngineAnswer:
        # Only the graph stores durable turn idempotency; this adapter accepts the shared
        # invocation contract while retaining its existing in-memory behavior.
        del request_id
        calls_before = self._count_calls()
        stages = _Stages()
        held = await self._held(conversation_id, account_id)
        if held is not None and version is not None and version != held.version:
            raise StaleDialogueError(held)
        state = held or DialogueState(uuid4())

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
            moved = apply_guarded(state, ops, today, grounds)
        if isinstance(moved.reply, Delegate):
            return unsaved(None, moved.reply.to)

        if moved.work is None:
            result = presenter.replied(moved.reply, moved.state, names, today=today)
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
                account_id=account_id,
                expected_version=state.version,
            )
        calls = self._count_calls() - calls_before
        return EngineAnswer(result, saved, None, calls, stages.ms)

    async def _held(self, conversation_id: UUID | None, account_id: UUID) -> DialogueState | None:
        """The state this account saved under `conversation_id`, if it saved one."""
        if conversation_id is None:
            return None
        return await self._store.load(conversation_id, account_id=account_id)

    async def _grounds(self, account_id: UUID) -> Grounds:
        """The holder's catalogues, read once per turn: names are grounded against these only."""
        holdings, contacts = await asyncio.gather(
            self._accounts.all_of(account_id), self._contacts.all_of(account_id)
        )
        return Grounds(self._categories, holdings, contacts)


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
