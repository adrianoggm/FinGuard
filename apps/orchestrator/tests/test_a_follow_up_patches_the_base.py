"""A follow-up is a patch on the question answered last.

«¿Cuánto le mandé a Marta este año?» → «¿y ella a mí?» is the same question with the money going
the other way: Marta, this year, income. On the episodes stack at ed9ba9ce the turn reached the
planner as a sentence of its own, because `continuation_patch.read` refused any continuation that
stated a direction, and a model handed «ella» has no Marta to put back: it answered this year's
spending, with nobody in it. The patch now turns the direction round and keeps everything else
(spec §3.4).

«Mejor por categoría» after a total is the same total broken down, and is answered without a model
the same way. A bare «por categoría» used to be narrowed to a shop called «por categoría».

Nothing else: a person who is not the base's, a question of its own, a question still open, a
comparison, and every turn the arbitration claimed first are read where they always were.

Driven over HTTP on the episodes stack, except where a test sends the handler an arbitration of its
own.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, ask, asking_for_the_window_stack

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import CounterpartyType, Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation, Slot
from finguard_orchestrator.domain.services.turn_arbiter import Arbitration, TurnType
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_rule_answer_sets_continuation() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "¿cuánto le mandé a Marta este año?", SETTLED)
    stack.ledger.plans.clear()
    ask(stack, "¿y ella a mí?", first["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
    assert plan.period.period is Period.THIS_YEAR


@pytest.mark.parametrize(
    "sentence",
    ["y lo que me mando ella?", "¿y lo que me ha pagado ella?", "y lo que recibí de ella"],
)
def test_received_from_the_same_person_flips_only_the_direction(sentence: str) -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto le mande a Marta este mes", SETTLED)
    stack.ledger.plans.clear()
    ask(stack, sentence, first["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
    assert plan.period.period is Period.THIS_MONTH


def test_a_flip_without_a_person_in_the_base_invents_none() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste en julio", SETTLED)
    ask(stack, "¿y lo que me mandó ella?", first["conversation"])
    assert stack.ledger.plans[-1].counterparties == ()


def test_mejor_por_categoria_groups_the_base() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste este mes", SETTLED)
    ask(stack, "mejor por categoria", first["conversation"])
    assert stack.ledger.plans[-1].group_by is GroupBy.CATEGORY
    assert stack.ledger.plans[-1].period.period is Period.THIS_MONTH


# --- the direction, turned round on the person the answer was about ------------------------------

MARTA_THIS_MONTH = "cuanto le mande a Marta este mes"


def _after(opening: str, sentence: str) -> tuple[episodes.Stack, dict[str, Any]]:
    """`sentence` answered after `opening`, with the ledger and the planner's log cleared between
    them so only the follow-up's own plans and model calls are left to read."""
    stack = episodes.a_stack()
    first = ask(stack, opening, SETTLED)
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, sentence, first["conversation"])


def test_the_turn_round_keeps_everything_but_the_direction_and_needs_no_model() -> None:
    """The person, the window, the kind of movement and of counterparty are the question asked; the
    turn changed which way the money went, and nothing else — so nothing reached the planner."""
    stack, answer = _after(MARTA_THIS_MONTH, "¿y ella a mí?")

    (plan,) = stack.ledger.plans
    assert plan.direction is Direction.INCOME
    assert plan.counterparties == ("Marta",)
    assert plan.counterparty_types == (CounterpartyType.PERSON,)
    assert plan.period is not None and plan.period.period is Period.THIS_MONTH
    assert stack.planner.asked == []
    assert answer["outcome"] == "PLANNED"


@pytest.mark.parametrize("sentence", ["¿y lo que me mandó Marta?", "¿y Marta a mí?"])
def test_the_persons_own_name_turns_it_round_too(sentence: str) -> None:
    """«Ella» or her name: the base's person either way. At ed9ba9ce both reached the planner and
    came back as this month's spending with nobody in it."""
    stack, _ = _after(MARTA_THIS_MONTH, sentence)

    (plan,) = stack.ledger.plans
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME


def test_turned_round_again_it_is_what_the_holder_sent() -> None:
    """The inverse reads too, and the base is the answer just given: after «¿y ella a mí?»,
    «¿y yo a ella?» is the money sent to Marta again."""
    stack = episodes.a_stack()
    first = ask(stack, "¿cuánto le mandé a Marta este año?", SETTLED)
    received = ask(stack, "¿y ella a mí?", first["conversation"])
    stack.ledger.plans.clear()

    ask(stack, "¿y yo a ella?", received["conversation"])

    (plan,) = stack.ledger.plans
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.EXPENSE
    assert plan.period is not None and plan.period.period is Period.THIS_YEAR


@pytest.mark.parametrize("sentence", ["¿y lo que me mandó Javi?", "¿y Javi a mí?"])
def test_a_different_person_is_not_a_turn_round_of_this_base(sentence: str) -> None:
    """Javi is never Marta. The shape says the money turned round; the base says whose money it
    is, and Javi's is not in it — so the turn is read as a question of its own, by the model."""
    stack, _ = _after(MARTA_THIS_MONTH, sentence)

    assert stack.planner.asked == [sentence]
    assert not any(
        plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
        for plan in stack.ledger.plans
    )


def test_a_turn_round_without_a_person_in_the_base_reaches_the_model() -> None:
    """The proof behind `test_a_flip_without_a_person_in_the_base_invents_none`, which held at
    ed9ba9ce already (the planner, asked, invented nobody): with no person in the base there is no
    «ella» to find, so no patch is read and the sentence goes where it went."""
    stack, _ = _after("cuanto gaste en julio", "¿y lo que me mandó ella?")

    assert stack.planner.asked == ["¿y lo que me mandó ella?"]
    assert all(plan.direction is not Direction.INCOME for plan in stack.ledger.plans)


def test_a_whole_new_question_about_someone_else_is_not_a_patch() -> None:
    """«¿Cuánto me mandó Javi este año?» names its own person and its own window: not a turn-round
    of the Marta answer. (Which reader then answers it is not this patch's: see the report.)"""
    stack, _ = _after(MARTA_THIS_MONTH, "¿cuánto me mandó Javi este año?")

    assert not any(
        plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
        for plan in stack.ledger.plans
    )


# --- a breakdown of the answer in view -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "grouping"),
    [
        ("mejor por categoría", GroupBy.CATEGORY),
        ("¿y por categoría?", GroupBy.CATEGORY),
        ("por categoría", GroupBy.CATEGORY),
        ("agrupado por categoría", GroupBy.CATEGORY),
        ("mejor agrúpalo por comercio", GroupBy.COUNTERPARTY),
    ],
)
def test_a_breakdown_said_as_the_whole_turn_regroups_the_answer(
    sentence: str, grouping: GroupBy
) -> None:
    """The window and the direction stay; only the breakdown changes, without a model. At ed9ba9ce
    «mejor por categoría», «¿y por categoría?» and «agrupado por categoría» reached the planner
    and «por categoría» was narrowed to a shop by that name."""
    stack, answer = _after("cuanto gaste este mes", sentence)

    (plan,) = stack.ledger.plans
    assert plan.group_by is grouping
    assert plan.direction is Direction.EXPENSE
    assert plan.period is not None and plan.period.period is Period.THIS_MONTH
    assert stack.planner.asked == []
    assert answer["outcome"] == "PLANNED"


def test_a_breakdown_by_shop_keeps_the_subject() -> None:
    stack, _ = _after("cuanto gaste en restaurantes este mes", "mejor por comercio")

    (plan,) = stack.ledger.plans
    assert plan.group_by is GroupBy.COUNTERPARTY and plan.categories == ("RESTAURANTS",)


def test_a_breakdown_by_category_of_one_category_is_refused_with_its_reason() -> None:
    """Restaurants by category is one row. The patch builds exactly what was asked, and the
    verifier's own rule (`BREAKDOWN_ANSWERED_AS_ONE_GROUP`) refuses it and says why, offering a
    breakdown that can divide it — not a total dressed as a breakdown, and no model guessing
    whether «por categoría» meant to widen the subject."""
    stack, answer = _after("cuanto gaste en restaurantes este mes", "mejor por categoría")

    assert answer["outcome"] == "INVALID_DOMAIN"
    assert "«por mes»" in answer["text"]
    assert stack.ledger.plans == [] and stack.planner.asked == []


def test_mejor_por_categoria_is_not_a_correction() -> None:
    """«Mejor» opens a correction, and this one corrects nothing: no «¿Qué debería cambiar…?»
    question, and the conversation does not forget the answer it regrouped."""
    stack, answer = _after("cuanto gaste este mes", "mejor por categoria")

    assert answer["outcome"] == "PLANNED"
    assert answer.get("question") is None
    assert answer["conversation"]["last_plan"] is not None


def test_the_breakdown_is_the_base_the_next_follow_up_continues() -> None:
    """The regrouped answer is committed like any other, so «¿y ella a mí?»-style patches and
    windows after it continue the breakdown, not the total before it."""
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste este mes", SETTLED)
    grouped = ask(stack, "mejor por categoria", first["conversation"])
    stack.ledger.plans.clear()

    ask(stack, "¿y en julio?", grouped["conversation"])

    (plan,) = stack.ledger.plans
    assert plan.group_by is GroupBy.CATEGORY
    assert plan.date_range is not None and plan.date_range.start.month == 7


def test_a_calendar_regroup_is_still_the_follow_up_readers() -> None:
    """«Mejor por meses» is a follow-up too, and answered where it was: the readers know how a
    calendar breakdown meets the measure (an average, a running total) and the patch does not."""
    stack, _ = _after("cuanto gaste este mes", "mejor por meses")

    (plan,) = stack.ledger.plans
    assert plan.group_by is GroupBy.MONTH
    assert stack.planner.asked == []


def test_a_breakdown_after_a_comparison_is_read_as_it_was_at_the_base() -> None:
    """Notes item 5: a breakdown of a comparison is not this task's. «¿Y por categoría?» after
    «junio vs julio en restaurantes» does what it did at ed9ba9ce — the planner reads it with no
    base, one figure, ungrouped by the stub — and is not a patch on either operand (R27)."""
    stack, _ = _after("junio vs julio en restaurantes", "¿y por categoría?")

    assert stack.planner.asked == ["¿y por categoría?"]
    (plan,) = stack.ledger.plans
    assert plan.group_by is GroupBy.NONE and plan.categories == ()


# --- what is not a follow-up ---------------------------------------------------------------------


def test_a_breakdown_while_the_period_question_is_open_is_not_a_patch_on_the_last_answer() -> None:
    """A question waiting for a reply is what the turn is about: «mejor por categoría» to «¿De
    qué periodo?» does not regroup the answer before it."""
    stack = asking_for_the_window_stack()
    first = ask(stack, "cuanto gaste este mes")
    asked = ask(stack, "¿cuánto gasté en restaurantes?", first["conversation"])
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    stack.ledger.plans.clear()

    ask(stack, "mejor por categoría", asked["conversation"])

    assert not any(plan.group_by is GroupBy.CATEGORY for plan in stack.ledger.plans)


def _answered_to_marta() -> Conversation:
    stack = episodes.a_stack()
    first = ask(stack, MARTA_THIS_MONTH, SETTLED)
    app.dependency_overrides.clear()
    return conversation_mapper.to_domain(ConversationPayload.model_validate(first["conversation"]))


@pytest.mark.parametrize(
    "arbitration",
    [
        Arbitration(TurnType.RESET),
        Arbitration(TurnType.CONFIRMATION),
        Arbitration(TurnType.NEW_OPERATION),
        Arbitration(TurnType.ANSWER_PENDING, frozenset({Slot.WINDOW})),
    ],
    ids=lambda arbitration: arbitration.type.value,
)
@pytest.mark.parametrize("sentence", ["¿y ella a mí?", "mejor por categoría"])
async def test_a_turn_the_arbitration_claimed_is_never_patched_as_a_follow_up(
    arbitration: Arbitration, sentence: str
) -> None:
    """The arbitration decides first (spec §3.1): a turn it gave to a reset, a confirmation, an
    order or the question left open is not turned round or regrouped, whatever its words."""
    conversation = _answered_to_marta()
    assert isinstance(conversation.last_plan, FinancialPlan)
    ledger = episodes.Ledger()
    handler = AskFinancialQuestionHandler(
        episodes.AnyPlan(),  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=episodes.Directory(),  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
    )

    await handler.handle(
        AskFinancialQuestionQuery(
            sentence,
            episodes.ACCOUNT,
            episodes.TODAY,
            conversation=conversation,
            arbitration=arbitration,
        )
    )

    assert not any(
        plan.direction is Direction.INCOME or plan.group_by is GroupBy.CATEGORY
        for plan in ledger.plans
    )


async def test_the_follow_up_says_it_continued_the_answer() -> None:
    """F-H3: an answer patched from the one in view says so, which is the lineage a chart drawn
    from it carries — as the readers that patch it already say."""
    handler = AskFinancialQuestionHandler(
        episodes.AnyPlan(),  # type: ignore[arg-type]
        episodes.Ledger(),  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=episodes.Directory(),  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
    )

    result = await handler.handle(
        AskFinancialQuestionQuery(
            "¿y ella a mí?",
            episodes.ACCOUNT,
            episodes.TODAY,
            conversation=_answered_to_marta(),
        )
    )

    assert result.plan_continues_conversation is True
