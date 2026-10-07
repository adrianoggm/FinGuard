"""A comparison is a base a follow-up can continue.

Measured with the offline battery: after «junio vs julio en restaurantes», «¿y en gasolina?» was
planned from scratch — one query, last month, fuel — because the comparison's base was dropped
before the planner (`planning_context` passes a continuation only for a single query in focus).
The follow-up now patches the comparison itself: the subject changes on both sides and each side
keeps its window (spec §3.3, §3.4).

The measure is patched on both sides the same way: «¿y cuántas veces?» is the count of June and of
July, not a count of last month.

Nothing else. A turn the arbitration claimed, a follow-up that names its own window, a question of
its own that happens to name a category, and a question left open over the comparison are each
read where they always were.

Driven over HTTP on the episodes stack.
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, ask, asking_for_the_window_stack

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import ComparisonFocus, focus_of
from finguard_orchestrator.domain.services.turn_arbiter import Arbitration, TurnType
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

COMPARISON = "junio vs julio en restaurantes"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_comparison_followup() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "junio vs julio en restaurantes", SETTLED)
    assert len(stack.ledger.ran) == 2
    stack.ledger.ran.clear()
    second = ask(stack, "¿y en gasolina?", first["conversation"])
    assert len(stack.ledger.ran) == 2
    windows = sorted((p.date_range.start.month if p.date_range else None) for p in stack.ledger.ran)
    assert windows == [6, 7]
    assert all(p.categories == ("TRANSPORT_FUEL",) for p in stack.ledger.ran)
    assert second["outcome"] == "PLANNED", second["text"]


@pytest.mark.parametrize(
    "sentence",
    [
        "cuanto gaste en julio",
        "¿cuánto tengo?",
        "junio vs julio en restaurantes",
        "cuanto le mande a Marta este mes",
        "¿en qué gasté más en julio?",
    ],
)
def test_every_answered_turn_leaves_a_base(sentence: str) -> None:
    stack = episodes.a_stack()
    answered = ask(stack, sentence, SETTLED)
    # The wire's own mapper, so the base is the one the next turn will actually read back.
    conversation = conversation_mapper.to_domain(
        ConversationPayload.model_validate(answered["conversation"])
    )
    assert focus_of(conversation) is not None


def _continued(ran: list[object]) -> list[FinancialPlan]:
    """The plans the ledger ran, when they are the June/July pair a both-sides patch runs."""
    plans = [plan for plan in ran if isinstance(plan, FinancialPlan)]
    months = sorted(plan.date_range.start.month for plan in plans if plan.date_range)
    return plans if len(plans) == 2 and months == [6, 7] else []


def test_the_patched_comparison_is_the_one_the_next_follow_up_continues() -> None:
    """The answer commits the pair it ran, so a second ellipsis patches fuel, not restaurants."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    second = ask(stack, "¿y en gasolina?", first["conversation"])
    stack.ledger.ran.clear()

    ask(stack, "¿y en el supermercado?", second["conversation"])

    continued = _continued(stack.ledger.ran)
    assert continued and all(plan.categories == ("GROCERIES",) for plan in continued)


@pytest.mark.parametrize(
    ("sentence", "field", "value"),
    [
        ("y sin restaurantes", "excluded_categories", ("RESTAURANTS",)),
        ("y sin Mercadona", "excluded_merchants", ("Mercadona",)),
    ],
)
def test_an_exclusion_after_a_comparison_leaves_it_out_of_both_sides(
    sentence: str, field: str, value: tuple[str, ...]
) -> None:
    stack = episodes.a_stack()
    first = ask(stack, "compara mis gastos de junio y julio", SETTLED)
    stack.ledger.ran.clear()

    ask(stack, sentence, first["conversation"])

    continued = _continued(stack.ledger.ran)
    # Casefolded: a shop is matched case-insensitively, and the reader keeps the sentence's
    # spelling or its own.
    expected = tuple(item.casefold() for item in value)
    assert continued and all(
        tuple(item.casefold() for item in getattr(plan, field)) == expected for plan in continued
    )


def test_the_kind_a_category_names_narrows_both_sides() -> None:
    """«El gimnasio» is a kind of subscription, and the plan carries it as a merchant term on every
    path (`explicit_facts.of_its_kind`) — this one too, on both sides."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, "¿y en el gimnasio?", first["conversation"])

    continued = _continued(stack.ledger.ran)
    assert continued and all(
        plan.categories == ("SUBSCRIPTIONS",) and plan.merchant_terms == ("gimnasio",)
        for plan in continued
    )


@pytest.mark.parametrize(
    ("sentence", "metric"),
    [("¿y cuántas veces?", Metric.COUNT), ("¿y la media?", Metric.AVERAGE)],
)
def test_a_measure_after_a_comparison_is_measured_on_both_sides(
    sentence: str, metric: Metric
) -> None:
    """R26 (I-1). «¿y cuántas veces?» counted every expense of last month, and «¿y la media?»
    answered June alone: a real figure for a question nobody asked, and half of one."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, sentence, first["conversation"])

    continued = _continued(stack.ledger.ran)
    assert continued and all(
        plan.categories == ("RESTAURANTS",) and plan.metric is metric for plan in continued
    )


def test_a_count_then_a_category_counts_the_new_category_on_both_sides() -> None:
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    counted = ask(stack, "¿y cuántas veces?", first["conversation"])
    stack.ledger.ran.clear()

    ask(stack, "¿y en gasolina?", counted["conversation"])

    continued = _continued(stack.ledger.ran)
    assert continued and all(
        plan.categories == ("TRANSPORT_FUEL",) and plan.metric is Metric.COUNT for plan in continued
    )


def test_a_measure_that_regroups_is_not_a_both_sides_patch() -> None:
    """«¿y la media por mes?» is a measure *and* a grouping. A regroup is not this patch's to make
    — `ComparisonPlan.between` would normalise it away — so the turn stays where it was."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, "¿y la media por mes?", first["conversation"])

    assert not _continued(stack.ledger.ran)
    assert [
        (plan.metric, plan.group_by) for plan in stack.ledger.ran if isinstance(plan, FinancialPlan)
    ] == [(Metric.PERIOD_AVERAGE, GroupBy.MONTH)]


@pytest.mark.parametrize(
    ("comparison", "sentence", "category", "direction"),
    [
        ("junio vs julio en nómina", "¿y en gasolina?", "TRANSPORT_FUEL", Direction.EXPENSE),
        (
            "compara mis ingresos de junio y julio",
            "¿y en restaurantes?",
            "RESTAURANTS",
            Direction.EXPENSE,
        ),
        (COMPARISON, "¿y en nómina?", "SALARY", Direction.INCOME),
        ("junio vs julio", "¿y en gasolina?", "TRANSPORT_FUEL", Direction.ALL),
    ],
)
def test_a_new_category_brings_its_own_direction(
    comparison: str, sentence: str, category: str, direction: Direction
) -> None:
    """R26 (I-2), ruling R24. «¿y en gasolina?» after a payroll comparison ran fuel as *income*: the
    category replaced the subject and the direction of the old one stayed. A replacing category
    brings its own; a comparison of every movement, which has none to replace, stays one."""
    stack = episodes.a_stack()
    first = ask(stack, comparison, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, sentence, first["conversation"])

    continued = _continued(stack.ledger.ran)
    assert continued and all(
        plan.categories == (category,) and plan.direction is direction for plan in continued
    )


def test_a_follow_up_that_names_a_window_moves_the_view_to_it() -> None:
    """«¿Y en agosto?» is not a both-sides patch: the follow-up reader answers August, in the
    comparison's subject, as it did before."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, "¿y en agosto?", first["conversation"])

    assert len(stack.ledger.ran) == 1
    (august,) = stack.ledger.ran
    assert isinstance(august, FinancialPlan) and august.date_range is not None
    assert august.date_range.start.month == 8 and august.categories == ("RESTAURANTS",)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿y en gasolina en agosto?",
        "¿cuánto gasté en gasolina en julio?",
        "¿cuánto gasté en gasolina?",
        "¿y en gasolina y restaurantes?",
    ],
)
def test_a_question_of_its_own_is_not_a_patch_on_the_comparison(sentence: str) -> None:
    """A window of its own, a question word, or two categories over two windows: each is a new
    question with one figure, not the pair moved to fuel."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    stack.ledger.ran.clear()

    ask(stack, sentence, first["conversation"])

    assert len(stack.ledger.ran) == 1
    assert not _continued(stack.ledger.ran)


@pytest.mark.parametrize(
    "arbitration",
    [
        Arbitration(TurnType.RESET),
        Arbitration(TurnType.CONFIRMATION),
        Arbitration(TurnType.NEW_OPERATION),
        Arbitration(TurnType.ANSWER_PENDING, frozenset({Slot.CATEGORY})),
    ],
    ids=lambda arbitration: arbitration.type.value,
)
async def test_a_turn_the_arbitration_claimed_is_not_a_patch(arbitration: Arbitration) -> None:
    """The arbitration decides first (spec §3.1): a turn it gave to a reset, a confirmation, an
    order or the question left open is never read as a patch on the comparison behind it."""
    stack = episodes.a_stack()
    first = ask(stack, COMPARISON, SETTLED)
    conversation = conversation_mapper.to_domain(
        ConversationPayload.model_validate(first["conversation"])
    )
    assert isinstance(focus_of(conversation), ComparisonFocus)
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
            "¿y en gasolina?",
            episodes.ACCOUNT,
            episodes.TODAY,
            conversation=conversation,
            arbitration=arbitration,
        )
    )

    assert not _continued(ledger.ran)


def test_a_question_left_open_over_the_comparison_is_what_the_turn_is_about() -> None:
    """«¿Cuánto gasté en ocio?» after the comparison asks «¿De qué periodo?», and the comparison
    stays in the conversation behind the question. «¿Y en gasolina?» then changes the subject of
    the question being asked — it does not run the comparison the holder walked away from."""
    stack = asking_for_the_window_stack()
    first = ask(stack, COMPARISON)
    asked = ask(stack, "¿cuánto gasté en ocio?", first["conversation"])
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    assert asked["conversation"]["last_comparison_earlier"] is not None
    stack.ledger.ran.clear()

    ask(stack, "¿y en gasolina?", asked["conversation"])

    assert not _continued(stack.ledger.ran)
