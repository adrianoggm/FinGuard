"""A question left open, and what the next turn does to it — the stacks those tests share.

The conversation-state slice is one property seen from several sides: a pending question is one
possibility for the next turn, never a cage. Each side has its own test file, and every one of them
needs the same two situations — a spending question the planner answered with «¿De qué periodo?»,
and a complete question `_scoped` answered with «¿En qué cuenta…?». Built here once, so no test file
re-invents them and two files cannot disagree about what "the window question" is.

Everything below the planner is `episodes.a_stack()`'s: the real routes, the real handler, the real
payment path with the two Martas. Only the planner is scripted, because the window question is a
decision a *model* makes — `AnyPlan` answers every sentence with a plan and never asks, so on the
corpus stack no window question exists to interrupt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    ListContactsQuery,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import get_ask_handler, get_query_bus
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.models.conversation import AccountScopeDefault, Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import category_words, explicit_facts, spanish
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlanDraft
from finguard_orchestrator.infrastructure.planning.llm_query_planner import WINDOW_QUESTION
from support import episodes

# The conversation a settled thread is in: every account, chosen. `episodes.opening` opens a
# `scope: settled` episode with exactly this, for the reason its docstring gives.
SETTLED: Final = {"account_selection_mode": "ALL"}


@dataclass
class PendingStack(episodes.Stack):
    """`episodes.Stack`, plus the conversation a turn sent with none starts from.

    `opening` is how a stack is "settled" without a preamble turn: `episodes.opening` does the same
    for a `scope: settled` episode, and for the same reason — the account question is production
    behaviour, but a test about the *window* question that had to answer it first would be a test
    about the account question.
    """

    opening: dict[str, Any] | None = None


def ask(
    stack: episodes.Stack, question: str, conversation: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One turn over `/v1/questions`, the route the corpus drives.

    With no conversation, a `PendingStack` sends its `opening` (`SETTLED` when it was built
    settled); every other stack sends none, exactly as a first turn does.
    """
    if conversation is None and isinstance(stack, PendingStack):
        conversation = stack.opening
    response = stack.client.post(
        "/v1/questions", json={"question": question, "conversation": conversation}
    )
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


# An elliptical continuation: «¿y en gasolina?», «y el mes pasado». The real planner merges these
# into the previous plan (`isFollowUp`) and inherits its window, so asking for one would be a
# behaviour no deployment has.
_CONTINUES: Final = re.compile(r"^[\s¿?¡!]*y\b")


class AsksForTheWindow(episodes.AnyPlan):
    """`AnyPlan`, except that a spending question naming no window gets the window question back.

    The real planner's behaviour, scripted: when everything read except the window it returns
    `WINDOW_QUESTION` with the draft it had already built, awaiting `Slot.WINDOW`
    (`llm_query_planner.py`, the `if window is None:` branch after the follow-up merge). The draft
    keeps the subject — direction and the categories the sentence names — so a reply that fills
    the window completes this question instead of starting another.

    A continuation the planner would merge is not asked about: a correction (`force_follow_up`) or
    an ellipsis («¿y en gasolina?») inherits the window it continues, as `AnyPlan` already does.
    """

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        facts = explicit_facts.read(sentence, today=episodes.TODAY)
        continuation = context.continuation
        continues = continuation is not None and (
            continuation.force_follow_up or _CONTINUES.match(spanish.folded(sentence)) is not None
        )
        if facts.window is not None or continues:
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            WINDOW_QUESTION,
            FinancialPlanDraft(
                direction=facts.direction or Direction.EXPENSE,
                categories=tuple(category_words.precisely_named_in(sentence)),
                group_by=facts.group_by or GroupBy.NONE,
            ),
            Slot.WINDOW,
        )


class KeepsTheBase(AsksForTheWindow):
    """`AsksForTheWindow`, except that a forced base comes back exactly as it was handed over.

    The least helpful model a reply to the open question can meet, and a real one: told to patch
    the draft it is given, it keeps the draft's subject and changes nothing the reply said. The
    stub's own reading of every sentence would hide that, because it rebuilds the plan from the
    reply's words — so a test that a reply is absorbed *without* the model runs on this one, where
    the model could not have done it.
    """

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        continuation = context.continuation
        if continuation is not None and continuation.force_follow_up:
            self.asked.append(sentence)
            return PlanningResult.planned(continuation.plan)
        return await super().plan(sentence, context=context)


def _with_planner(stack: episodes.Stack, planner: episodes.AnyPlan) -> None:
    """Rewire `stack`'s routes to a handler built on `planner`, keeping its ledger and directory.

    Built the way `tests/test_a_reply_to_the_account_question.py` builds a handler by hand, and
    registered the way `episodes.a_stack` registers its own: the stack's ledger stays the one the
    tests read, and the contact directory stays on the bus because a payment continued from the
    chat asks it who exists.
    """
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        stack.ledger,  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=episodes.Directory(),  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
        account_scope_default=stack.account_scope_default,
    )
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)
    queries.register(ListContactsQuery, episodes.ContactDirectory())
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler


def asking_for_the_window_stack(
    *,
    settled: bool = True,
    planner: AsksForTheWindow | None = None,
    account_scope_default: AccountScopeDefault | None = None,
) -> PendingStack:
    """The episodes stack, with a planner that answers a window-less spending question with the
    window clarification and a draft that keeps its subject (`AsksForTheWindow`).

    `settled` opens every conversation a test starts without one at `SETTLED`, so the question in
    play is the window and nothing else. `settled=False` opens with nothing chosen: the directory
    holds three accounts, and a plan completed with no account named reads all three — or asks
    «¿En qué cuenta…?», on a stack built to ask.

    `planner` is for a test about what reaches the model, which needs a model that behaves a
    particular way there — `KeepsTheBase`, typically. It must still ask for the window the same way.

    `account_scope_default` is `episodes.a_stack`'s: `ask` for a test about the account question,
    which a stack with nothing settled asks only on a deployment configured to.
    """
    stack = episodes.a_stack(account_scope_default=account_scope_default)
    planner = planner or AsksForTheWindow()
    _with_planner(stack, planner)
    return PendingStack(
        stack.client,
        planner,
        stack.ledger,
        stack.bank,
        stack.gyms,
        stack.account_scope_default,
        opening=dict(SETTLED) if settled else None,
    )


def account_question_stack() -> PendingStack:
    """The episodes stack with a directory and nothing settled, whose planner answers «¿Cuánto llevo
    gastado en Spotify?» with a complete plan, so `_scoped` asks the account — the live path of the
    2026-10-07 failure, on a deployment that still asks it
    (`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask`).

    `AnyPlan` is that planner already: it answers every sentence with a complete plan, last month
    when the sentence names no window. «Llevo gastado» names one since spec §3.5 — the year so far
    — so a test about the window a reply brings asks «¿Cuánto gasté en Spotify?», whose plan waits
    over last month. With three accounts and none chosen, `_scoped` raises
    `AccountScopeRequiredError` and the turn is «¿En qué cuenta quieres consultarlo?» with the plan
    pending behind it.
    """
    stack = episodes.a_stack(account_scope_default="ask")
    return PendingStack(
        stack.client,
        stack.planner,
        stack.ledger,
        stack.bank,
        stack.gyms,
        stack.account_scope_default,
    )
