from dataclasses import replace
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
    answered_with_result,
    focused_on_result,
    result_reference_of,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

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
    return answered_with_result(
        Conversation(),
        ResultReference(
            SOURCE,
            category="GROCERIES",
            group_by=group_by,
            group_key=group_key,
            transaction_ids=transaction_ids,
        ),
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


def test_answered_with_result_uses_canonical_wire_projection() -> None:
    evidence = (UUID(int=11), UUID(int=12))
    reference = ResultReference(
        SOURCE,
        category="GROCERIES",
        group_by=GroupBy.MERCHANT,
        group_key="Mercadona",
        transaction_ids=evidence,
    )
    canonical = answered_with_result(Conversation(), reference)
    assert canonical.last_plan is SOURCE
    payload = conversation_mapper.to_payload(canonical)
    assert payload.last_plan is not None
    assert payload.referenced_category == "GROCERIES"
    assert payload.referenced_group_by is GroupBy.MERCHANT
    assert payload.referenced_group_key == "Mercadona"
    assert payload.referenced_transaction_ids == list(evidence)


def test_canonical_reference_is_stored_and_exact_object_is_returned() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    conversation = answered_with_result(Conversation(), reference)
    assert conversation.result_reference is reference
    assert result_reference_of(conversation, focus_of(conversation)) is reference


def test_canonical_reference_rejects_wrong_source_identity() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    with pytest.raises(InvalidValueError):
        Conversation(
            last_plan=FinancialPlan(direction=SOURCE.direction, period=SOURCE.period),
            result_reference=reference,
        )
    Conversation(last_plan=SOURCE, result_reference=reference)


def test_answered_clears_previous_canonical_reference() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    current = answered_with_result(Conversation(), reference)
    next_plan = FinancialPlan(direction=SOURCE.direction, period=RelativePeriod(Period.LAST_MONTH))
    answered = current.answered(next_plan)
    assert answered.result_reference is None


def test_focused_reference_preserves_source_and_evidence() -> None:
    evidence = (UUID(int=8),)
    reference = ResultReference(
        SOURCE, group_by=GroupBy.CATEGORY, group_key="GROCERIES", transaction_ids=evidence
    )
    focused = focused_on_result(answered_with_result(Conversation(), reference), reference)
    assert focused.result_reference is reference
    assert result_reference_of(focused, focus_of(focused)) is reference


def test_focused_on_result_reconstructs_canonical_reference() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    current = focused_on_result(
        answered_with_result(Conversation(), reference),
        ResultReference(SOURCE, group_by=GroupBy.MERCHANT, group_key="OTHER"),
    )
    assert current.result_reference is not None
    assert current.result_reference.group_key == "OTHER"


def test_focused_category_only_keeps_the_exact_reference() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    focused = focused_on_result(answered_with_result(Conversation(), reference), reference)
    assert focused.result_reference is reference


def test_canonical_reference_is_part_of_conversation_equality() -> None:
    reference = ResultReference(SOURCE, category="GROCERIES")
    canonical = answered_with_result(Conversation(), reference)
    legacy = replace(canonical, result_reference=None)
    assert canonical != legacy


def test_canonical_reference_is_reconstructed_semantically_after_wire_round_trip() -> None:
    reference = ResultReference(
        SOURCE,
        category="GROCERIES",
        group_by=GroupBy.MERCHANT,
        group_key="Mercadona",
        transaction_ids=(UUID(int=8),),
    )
    canonical = answered_with_result(Conversation(), reference)
    payload = conversation_mapper.to_payload(canonical)
    echoed = ConversationPayload.model_validate(payload.model_dump(mode="json"))
    hydrated = conversation_mapper.to_domain(echoed)

    assert hydrated.result_reference is not None
    assert hydrated.result_reference.source_query is hydrated.last_plan
    assert replace(hydrated.result_reference, source_query=SOURCE) == reference


def test_mapper_does_not_canonicalize_implicit_singleton_category_or_source_ids() -> None:
    source = replace(SOURCE, transaction_ids=(UUID(int=99),))
    hydrated = conversation_mapper.to_domain(
        conversation_mapper.to_payload(Conversation(last_plan=source))
    )

    assert hydrated.result_reference is None
    inferred = result_reference_of(hydrated, focus_of(hydrated))
    assert inferred is not None
    assert inferred.category == "GROCERIES"
    assert inferred.group_by is None
    assert inferred.group_key is None
    assert inferred.transaction_ids == (UUID(int=99),)


def test_mapper_requires_source_for_explicit_reference() -> None:
    payload = ConversationPayload(referenced_category="GROCERIES")
    with pytest.raises(InvalidValueError):
        conversation_mapper.to_domain(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "referenced_category": "LEISURE",
            "referenced_group_by": GroupBy.CATEGORY,
            "referenced_group_key": "GROCERIES",
        },
        {
            "referenced_category": "LEISURE",
            "referenced_group_by": GroupBy.MERCHANT,
            "referenced_group_key": "Mercadona",
        },
    ],
)
def test_mapper_rejects_semantically_mismatched_result_payload(changes: dict[str, object]) -> None:
    payload = conversation_mapper.to_payload(Conversation().answered(SOURCE)).model_dump()
    payload.update(changes)
    with pytest.raises(InvalidValueError):
        conversation_mapper.to_domain(ConversationPayload.model_validate(payload))


def test_answered_with_an_empty_result_clears_all_previous_reference_fields() -> None:
    previous = referenced(group_by=GroupBy.MERCHANT, group_key="Mercadona")
    next_source = FinancialPlan(Direction.INCOME, period=RelativePeriod(Period.LAST_MONTH))

    current = answered_with_result(previous, ResultReference(next_source))

    assert current.last_plan is next_source
    assert current.result_reference == ResultReference(next_source)


def test_focused_result_requires_the_identical_source_and_keeps_visible_evidence() -> None:
    evidence = (UUID(int=11), UUID(int=12))
    answered = answered_with_result(
        Conversation(), ResultReference(SOURCE, transaction_ids=evidence)
    )
    focused = focused_on_result(
        answered,
        ResultReference(
            SOURCE,
            category="GROCERIES",
            group_by=GroupBy.MERCHANT,
            group_key="Mercadona",
            transaction_ids=evidence,
        ),
    )

    assert focused.last_plan is SOURCE
    assert focused.result_reference is not None
    assert focused.result_reference.group_key == "Mercadona"
    assert focused.result_reference.transaction_ids == evidence
    with pytest.raises(InvalidValueError):
        focused_on_result(focused, ResultReference(replace_source(SOURCE)))


def replace_source(source: FinancialPlan) -> FinancialPlan:
    """Return an equal plan with distinct identity for the source-identity invariant."""
    return FinancialPlan(
        direction=source.direction,
        period=source.period,
        categories=source.categories,
        group_by=source.group_by,
    )


@pytest.mark.parametrize(
    ("group_by", "group_key", "category"),
    [
        (GroupBy.CATEGORY, "GROCERIES", "GROCERIES"),
        (GroupBy.MERCHANT, "Mercadona", "GROCERIES"),
        (GroupBy.COUNTERPARTY, "Marta", "GROCERIES"),
        (GroupBy.ACCOUNT, "Cuenta diaria", "GROCERIES"),
    ],
)
def test_referenceable_group_matrix_is_grounded_by_the_source_query(
    group_by: GroupBy, group_key: str, category: str
) -> None:
    reference = ResultReference(SOURCE, category, group_by, group_key)

    assert reference.source_query is SOURCE


def test_a_non_category_row_rejects_a_category_not_grounded_by_its_source() -> None:
    with pytest.raises(InvalidValueError):
        ResultReference(
            SOURCE,
            category="LEISURE",
            group_by=GroupBy.MERCHANT,
            group_key="Amazon",
        )


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


def test_safe_fallback_derives_only_the_source_singleton_category() -> None:
    conversation = Conversation(last_plan=SOURCE)

    result = result_reference_of(conversation, focus_of(conversation))

    assert result is not None
    assert result.category == "GROCERIES"
    assert result.group_by is None
    assert result.group_key is None


def test_canonical_refocus_replaces_previous_reference() -> None:
    category = answered_with_result(Conversation(), ResultReference(SOURCE, category="GROCERIES"))
    merchant = focused_on_result(
        category,
        ResultReference(
            SOURCE, category="GROCERIES", group_by=GroupBy.MERCHANT, group_key="Mercadona"
        ),
    )

    expected = ResultReference(SOURCE, category="GROCERIES")
    assert result_reference_of(category, focus_of(category)) == expected
    assert merchant.result_reference is not None
    assert result_reference_of(merchant, focus_of(merchant)) == merchant.result_reference


def test_canonical_refocus_drops_a_category_not_grounded_by_the_source_query() -> None:
    unfiltered = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.CATEGORY,
    )
    category = answered_with_result(
        Conversation(), ResultReference(unfiltered, category="GROCERIES")
    )
    merchant = focused_on_result(
        category, ResultReference(unfiltered, group_by=GroupBy.MERCHANT, group_key="Mercadona")
    )
    assert merchant.result_reference is not None
    assert merchant.result_reference.category is None


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
    dual = focused_on_result(
        coherent,
        ResultReference(
            SOURCE, category="GROCERIES", group_by=GroupBy.MERCHANT, group_key="Mercadona"
        ),
    )

    planned = _contextual_follow_up_plan(query("Y esa categoría el mes pasado", coherent))
    planned_from_dual = _contextual_follow_up_plan(query("Y esa categoría el mes pasado", dual))

    assert planned is not None
    assert planned.categories == ("GROCERIES",)
    assert planned_from_dual is not None
    assert planned_from_dual.categories == ("GROCERIES",)
