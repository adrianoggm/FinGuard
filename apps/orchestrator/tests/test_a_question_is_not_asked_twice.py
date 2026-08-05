"""The same question, a second time, is a dead end rather than a clarification.

The loop detector already existed and read `was.awaiting`, which is the slot of the *active
workflow*. That works when the model stopped to ask having read something — the clarification
carries a draft, the draft carries the slot. When it read nothing there is no draft, `_next_state`
returns None, and the turn leaves no state at all:

    "¿cuánto?"          → "¿de qué periodo?"      nothing stored
    "pues eso"          → "¿de qué periodo?"      nothing stored, and nothing noticed
    "en el que sea"     → "¿de qué periodo?"      …

Three identical questions and no way out, because the only thing the user can do is rephrase and
rephrasing is exactly what failed. `last_asked` is the memory that closes it: the slot travels
beside `last_plan` rather than inside a workflow, so a question with nothing behind it still
remembers what it asked.

It has to last exactly one turn. A slot that outlived its question would make an unrelated
clarification three turns later look like a repeat, and answer a real question with a list of
suggestions.
"""

import uuid
from datetime import UTC, date, datetime

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import Conversation, Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import query_recovery, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)

# A sentence with nothing in it this service reads by itself. That matters: a category or a window
# would be rescued deterministically and never reach the model's question at all.
VAGUE = "¿qué tal va la cosa?"


class AsksForever:
    """A planner that always stops to ask the same thing, with nothing half-built behind it."""

    def __init__(self, draft: FinancialPlanDraft | None = None) -> None:
        self._draft = draft
        self.asked = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        return PlanningResult.needs_clarification(
            "¿De qué periodo?", draft=self._draft, awaiting=Slot.WINDOW
        )


class AnswersOnce:
    """Asks the first time and answers after that, so the memory can be watched being cleared."""

    def __init__(self) -> None:
        self.asked = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        if self.asked == 1:
            return PlanningResult.needs_clarification("¿De qué periodo?", awaiting=Slot.WINDOW)
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(period=Period.THIS_MONTH),
            )
        )


class Ledger:
    async def run(self, plan: object, **_: object) -> QueryAnswer:
        return QueryAnswer(
            currency="EUR",
            total_minor=12_000,
            movement_count=3,
            groups=(Group(key="total", total_minor=12_000, movement_count=3),),
            movements=(),
        )


class Bank:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=(Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),),
        )


async def asked(planner: object, was: Conversation, sentence: str = VAGUE):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        Ledger(),  # type: ignore[arg-type]
        accounts=Bank(),  # type: ignore[arg-type]
        contacts=None,  # type: ignore[arg-type]
    )
    return await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=was
        )
    )


# --- the hole: a question with nothing behind it -----------------------------------------


async def test_a_draft_less_question_still_remembers_what_it_asked() -> None:
    """The field the fix is. Before it, this turn left an entirely empty conversation."""
    answer = await asked(AsksForever(), Conversation())

    assert answer.conversation.active is None, "there is no draft, so there is no workflow"
    assert answer.conversation.last_asked is Slot.WINDOW


async def test_asking_it_a_second_time_offers_choices_instead() -> None:
    """The reported conversation. Two identical questions is the point at which asking again stops
    being a clarification — the user's only move is to rephrase, and rephrasing already failed."""
    first = await asked(AsksForever(), Conversation())

    second = await asked(AsksForever(), first.conversation)

    assert second.suggestions == query_recovery.WINDOW_CHOICES
    assert second.text != "¿De qué periodo?"


async def test_the_choices_offered_are_ones_this_service_reads_itself() -> None:
    """The contract of the whole recovery: a suggestion that falls through to a model is a button
    whose behaviour depends on the configured provider."""
    first = await asked(AsksForever(), Conversation())

    second = await asked(AsksForever(), first.conversation)

    assert second.suggestions
    for choice in second.suggestions:
        assert window_words.window_in(choice, today=TODAY) is not None, choice


# --- what must not change ----------------------------------------------------------------


async def test_a_question_with_a_draft_behind_it_still_works_the_same_way() -> None:
    """`awaiting` covered this case before and still does. The new field is a second place to look,
    not a replacement — a clarification carrying a draft records its slot on the workflow."""
    half_read = FinancialPlanDraft(direction=Direction.EXPENSE, categories=("GROCERIES",))
    first = await asked(AsksForever(half_read), Conversation())

    second = await asked(AsksForever(half_read), first.conversation)

    assert first.conversation.awaiting is Slot.WINDOW
    assert second.suggestions == query_recovery.WINDOW_CHOICES


async def test_the_memory_does_not_outlive_the_question_it_belongs_to() -> None:
    """The failure the other direction. A slot kept after the question was answered would make the
    next unrelated clarification look like a repeat, and answer it with a list of periods instead
    of asking — a real question, dismissed as a loop.
    """
    planner = AnswersOnce()
    first = await asked(planner, Conversation())
    assert first.conversation.last_asked is Slot.WINDOW

    answered = await asked(planner, first.conversation, "¿cuánto gasté este mes?")

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert answered.conversation.last_asked is None


async def test_a_different_question_the_second_time_is_asked_rather_than_dismissed() -> None:
    """Two clarifications in a row about different fields is an ordinary conversation, not a loop.
    The slot is what tells them apart, which is why this reads the slot and not the wording."""

    class AsksSomethingElse:
        async def plan(self, *_: object, **__: object) -> PlanningResult:
            return PlanningResult.needs_clarification("¿De qué categoría?", awaiting=Slot.CATEGORY)

    first = await asked(AsksForever(), Conversation())

    second = await asked(AsksSomethingElse(), first.conversation)

    assert second.text == "¿De qué categoría?"
    assert second.suggestions == []
