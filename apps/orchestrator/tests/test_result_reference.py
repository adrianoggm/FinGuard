from datetime import date
from typing import cast
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    _contextual_follow_up_plan,
)
from finguard_orchestrator.domain.constants import MAX_SELECTED_TRANSACTIONS
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import (
    BalanceFocus,
    ComparisonFocus,
    PendingRatioFocus,
    RatioFocus,
    focus_of,
)
from finguard_orchestrator.domain.models.result_reference import (
    ResultReference,
    result_reference_of,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan

ACCOUNT = UUID(int=1)
TODAY = date(2026, 8, 26)
SOURCE = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.THIS_YEAR),
    categories=("GROCERIES",),
    group_by=GroupBy.CATEGORY,
)


def query(sentence: str, conversation: Conversation) -> AskFinancialQuestionQuery:
    return AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=conversation)


def handler_without_dependencies() -> AskFinancialQuestionHandler:
    return cast(AskFinancialQuestionHandler, object.__new__(AskFinancialQuestionHandler))


def referenced(
    *,
    group_by: GroupBy = GroupBy.CATEGORY,
    group_key: str = "GROCERIES",
    transaction_ids: tuple[UUID, ...] = (UUID(int=11), UUID(int=12)),
) -> Conversation:
    return Conversation().answered(
        SOURCE,
        referenced_group_by=group_by,
        referenced_group_key=group_key,
        referenced_transaction_ids=transaction_ids,
    )


def test_result_reference_is_an_immutable_source_bound_value() -> None:
    result = ResultReference(
        source_query=SOURCE,
        category="GROCERIES",
        group_by=GroupBy.CATEGORY,
        group_key="GROCERIES",
        transaction_ids=(UUID(int=11), UUID(int=12)),
    )

    assert result.source_query is SOURCE
    assert result.category == "GROCERIES"
    with pytest.raises(AttributeError):
        result.group_key = "LEISURE"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"group_by": GroupBy.MERCHANT},
        {"group_key": "Mercadona"},
        {"group_by": GroupBy.MONTH, "group_key": "2026-08"},
        {"group_by": GroupBy.MERCHANT, "group_key": " "},
        {
            "category": "LEISURE",
            "group_by": GroupBy.CATEGORY,
            "group_key": "GROCERIES",
        },
        {"transaction_ids": (UUID(int=1), UUID(int=1))},
        {
            "transaction_ids": tuple(
                UUID(int=index + 1) for index in range(MAX_SELECTED_TRANSACTIONS + 1)
            )
        },
    ],
)
def test_result_reference_rejects_partial_unbounded_or_duplicate_memory(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(InvalidValueError):
        ResultReference(SOURCE, **kwargs)  # type: ignore[arg-type]


def test_factory_projects_one_coherent_reference_from_query_focus() -> None:
    conversation = referenced()

    result = result_reference_of(conversation, focus_of(conversation))

    assert result == ResultReference(
        source_query=SOURCE,
        category="GROCERIES",
        group_by=GroupBy.CATEGORY,
        group_key="GROCERIES",
        transaction_ids=(UUID(int=11), UUID(int=12)),
    )


def test_factory_rejects_a_category_that_disagrees_with_the_selected_group() -> None:
    conversation = Conversation(
        last_plan=SOURCE,
        referenced_category="LEISURE",
        referenced_group_by=GroupBy.CATEGORY,
        referenced_group_key="GROCERIES",
    )

    assert result_reference_of(conversation, focus_of(conversation)) is None


def test_factory_derives_a_legacy_singleton_category_from_the_authorised_source() -> None:
    conversation = Conversation(
        last_plan=SOURCE,
        referenced_group_by=GroupBy.MERCHANT,
        referenced_group_key="Mercadona",
    )

    result = result_reference_of(conversation, focus_of(conversation))

    assert result is not None
    assert result.category == "GROCERIES"
    assert result.group_key == "Mercadona"


def test_factory_rejects_a_non_category_group_with_an_ungrounded_legacy_category() -> None:
    conversation = Conversation(
        last_plan=SOURCE,
        referenced_category="LEISURE",
        referenced_group_by=GroupBy.MERCHANT,
        referenced_group_key="Mercadona",
    )

    assert result_reference_of(conversation, focus_of(conversation)) is None


def test_answered_and_with_references_keep_the_legacy_projection_coherent() -> None:
    category = Conversation().answered(SOURCE, referenced_category="GROCERIES")
    merchant = category.with_references(
        group_by=GroupBy.MERCHANT,
        group_key="Mercadona",
    )

    assert result_reference_of(category, focus_of(category)) is not None
    assert merchant.referenced_category == "GROCERIES"
    assert result_reference_of(merchant, focus_of(merchant)) == ResultReference(
        source_query=SOURCE,
        category="GROCERIES",
        group_by=GroupBy.MERCHANT,
        group_key="Mercadona",
    )


def test_with_references_clears_a_category_not_grounded_by_the_source_query() -> None:
    unfiltered = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.CATEGORY,
    )
    category = Conversation().answered(unfiltered, referenced_category="GROCERIES")

    merchant = category.with_references(
        group_by=GroupBy.MERCHANT,
        group_key="Mercadona",
    )

    assert merchant.referenced_category is None


def test_a_new_answer_clears_the_previous_result_reference() -> None:
    previous = referenced()
    new_source = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    current = previous.answered(new_source)

    assert result_reference_of(current, focus_of(current)) is None


@pytest.mark.parametrize("special", ["ratio", "pending_ratio", "comparison"])
def test_operand_based_focuses_cannot_lend_a_legacy_result_reference(special: str) -> None:
    conversation = referenced()
    window = RelativePeriod(Period.THIS_YEAR)
    if special == "ratio":
        focus = RatioFocus(
            RatioPlan(
                FinancialPlan(Direction.EXPENSE, period=window),
                FinancialPlan(Direction.INCOME, period=window),
                RatioKind.SHARE,
            )
        )
    elif special == "pending_ratio":
        focus = PendingRatioFocus(
            RatioDraft(
                FinancialPlanDraft(direction=Direction.EXPENSE),
                FinancialPlanDraft(direction=Direction.INCOME),
                RatioKind.SHARE,
            )
        )
    else:
        focus = ComparisonFocus(
            ComparisonPlan(
                FinancialPlan(
                    Direction.EXPENSE,
                    period=RelativePeriod(Period.LAST_MONTH),
                    categories=("GROCERIES",),
                    group_by=GroupBy.CATEGORY,
                ),
                SOURCE,
            )
        )

    assert result_reference_of(conversation, focus) is None


def test_balance_requires_an_explicitly_authorised_return() -> None:
    conversation = referenced().answering_the_balance()
    focus = focus_of(conversation)

    assert isinstance(focus, BalanceFocus)
    assert result_reference_of(conversation, focus) is None
    assert result_reference_of(conversation, focus, returned_from_balance=True) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sentence", "group_by", "group_key", "field", "expected"),
    [
        (
            "Cuánto gasté en esa categoría?",
            GroupBy.CATEGORY,
            "GROCERIES",
            "categories",
            ("GROCERIES",),
        ),
        (
            "Cuánto gasté en ese comercio?",
            GroupBy.MERCHANT,
            "Mercadona",
            "merchants",
            ("Mercadona",),
        ),
    ],
)
async def test_result_pronouns_read_the_row_bound_to_the_current_query(
    sentence: str,
    group_by: GroupBy,
    group_key: str,
    field: str,
    expected: tuple[str, ...],
) -> None:
    plan = await handler_without_dependencies()._referenced_result_plan(
        query(sentence, referenced(group_by=group_by, group_key=group_key))
    )

    assert plan is not None
    assert getattr(plan, field) == expected
    assert plan.group_by is GroupBy.NONE


@pytest.mark.asyncio
async def test_those_movements_read_only_the_evidence_bound_to_the_source_query() -> None:
    selected = (UUID(int=11), UUID(int=12))

    plan = await handler_without_dependencies()._referenced_result_plan(
        query("Cuánto suman esos movimientos?", referenced(transaction_ids=selected))
    )

    assert plan is not None
    assert plan.transaction_ids == selected
    assert plan.metric is Metric.SUM


@pytest.mark.asyncio
async def test_legacy_plan_selection_is_safe_evidence_for_those_movements() -> None:
    selected = (UUID(int=31), UUID(int=32))
    source = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        transaction_ids=selected,
    )
    conversation = Conversation(last_plan=source)

    plan = await handler_without_dependencies()._referenced_result_plan(
        query("Cuánto suman esos movimientos?", conversation)
    )

    assert plan is not None
    assert plan.transaction_ids == selected


@pytest.mark.asyncio
async def test_explicit_return_from_balance_uses_the_suspended_reference_and_wording() -> None:
    conversation = referenced(group_by=GroupBy.MERCHANT, group_key="Mercadona")
    conversation = conversation.answering_the_balance()

    plan = await handler_without_dependencies()._referenced_result_plan(
        query(
            "Volviendo a lo de antes, cuánto gasté en ese comercio?",
            conversation,
        )
    )

    assert plan is not None
    assert plan.merchants == ("Mercadona",)


def test_contextual_category_branch_uses_only_a_category_tied_to_query_focus() -> None:
    coherent = referenced(transaction_ids=())
    dual = Conversation(
        last_plan=SOURCE,
        referenced_category="GROCERIES",
        referenced_group_by=GroupBy.MERCHANT,
        referenced_group_key="Mercadona",
    )

    planned = _contextual_follow_up_plan(query("Y esa categoría el mes pasado", coherent))
    planned_from_dual = _contextual_follow_up_plan(query("Y esa categoría el mes pasado", dual))

    assert planned is not None
    assert planned.categories == ("GROCERIES",)
    assert planned_from_dual is not None
    assert planned_from_dual.categories == ("GROCERIES",)
