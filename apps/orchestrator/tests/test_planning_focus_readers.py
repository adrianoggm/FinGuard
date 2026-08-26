from collections.abc import Callable
from datetime import date
from typing import cast
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    _account_follow_up_plan,
    _contextual_ranking_plan,
    _group_ranking_plan,
    _query_for_reader,
    _stored_percentage_pair,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.conversation import Conversation, FinancialClarification
from finguard_orchestrator.domain.models.planning_context import ContinuationSource
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 25)


def query(sentence: str, conversation: Conversation) -> AskFinancialQuestionQuery:
    return AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=conversation)


def handler_without_dependencies() -> AskFinancialQuestionHandler:
    # These readers are pure projections and do not touch any injected dependency.
    return cast(AskFinancialQuestionHandler, object())


def ratio() -> RatioPlan:
    window = RelativePeriod(Period.THIS_YEAR)
    return RatioPlan(
        part=FinancialPlan(
            direction=Direction.EXPENSE,
            period=window,
            categories=("HOUSING_RENT",),
        ),
        whole=FinancialPlan(direction=Direction.INCOME, period=window),
        kind=RatioKind.SHARE,
    )


def comparison() -> ComparisonPlan:
    return ComparisonPlan(
        earlier=FinancialPlan(
            direction=Direction.EXPENSE,
            date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
        ),
        latest=FinancialPlan(
            direction=Direction.EXPENSE,
            date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
        ),
    )


def conversation_with_special_focus(
    special: str, mirrored: FinancialPlan | TopMovements
) -> Conversation:
    fields: dict[str, object] = {"last_plan": mirrored}
    if special == "balance":
        fields["answered_the_balance"] = True
    elif special == "ratio":
        fields["last_ratio"] = ratio()
    elif special == "pending_ratio":
        fields["pending_ratio"] = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.SHARE,
        )
    else:
        fields["last_comparison"] = comparison()
    return Conversation(**fields)  # type: ignore[arg-type]


def test_contextual_ranking_reads_only_the_current_query_focus() -> None:
    current = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("FOOD",),
    )
    unrelated_prior = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("SALARY",),
    )

    result = _contextual_ranking_plan(
        query(
            "cual fue mi gasto mas grande",
            Conversation(last_plan=current, prior_plan=unrelated_prior),
        )
    )

    assert result is not None
    assert result.period == current.period
    assert result.direction is current.direction
    assert result.categories == current.categories


def test_account_follow_up_reads_only_the_current_query_focus() -> None:
    current = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.CATEGORY,
    )
    unrelated_prior = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
    )

    result = _account_follow_up_plan(
        query("en la cuenta nomina", Conversation(last_plan=current, prior_plan=unrelated_prior))
    )

    assert result is not None
    assert result.period == current.period
    assert result.direction is current.direction
    assert result.group_by is GroupBy.NONE


def test_group_ranking_follow_up_reads_only_the_current_query_focus() -> None:
    current = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("FOOD",),
        group_by=GroupBy.MERCHANT,
        metric=Metric.AVERAGE,
        group_limit=3,
    )
    unrelated_prior = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.CATEGORY,
        metric=Metric.SUM,
        group_limit=9,
    )

    result = _group_ranking_plan(
        query(
            "y por numero de operaciones",
            Conversation(last_plan=current, prior_plan=unrelated_prior),
        )
    )

    assert result is not None
    assert result.period == current.period
    assert result.categories == current.categories
    assert result.group_by is current.group_by
    assert result.metric is Metric.COUNT
    assert result.group_limit == current.group_limit


@pytest.mark.parametrize("special", ["balance", "ratio", "pending_ratio", "comparison"])
@pytest.mark.parametrize(
    ("reader", "sentence", "mirrored"),
    [
        (
            _contextual_ranking_plan,
            "cual fue mi gasto mas grande",
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.LAST_MONTH),
            ),
        ),
        (
            _account_follow_up_plan,
            "en la cuenta nomina",
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.LAST_MONTH),
            ),
        ),
        (
            _group_ranking_plan,
            "y por numero de operaciones",
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.LAST_MONTH),
                group_by=GroupBy.MERCHANT,
                group_limit=3,
            ),
        ),
    ],
)
def test_special_focus_blocks_all_three_query_mirror_readers(
    special: str,
    reader: Callable[[AskFinancialQuestionQuery], FinancialPlan | TopMovements | None],
    sentence: str,
    mirrored: FinancialPlan,
) -> None:
    result = reader(query(sentence, conversation_with_special_focus(special, mirrored)))

    assert result is None


@pytest.mark.parametrize("special", ["balance", "ratio", "pending_ratio", "comparison"])
def test_self_contained_rankings_ignore_special_focus_mirrors(special: str) -> None:
    mirrored = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("SALARY",),
    )
    conversation = conversation_with_special_focus(special, mirrored)

    movement = _contextual_ranking_plan(
        query("cual fue mi gasto mas grande de abril", conversation)
    )
    grouped = _group_ranking_plan(query("top 3 categorias por operaciones este ano", conversation))

    assert movement is not None
    assert movement.direction is Direction.EXPENSE
    assert movement.date_range == AbsoluteRange(date(2026, 4, 1), date(2026, 4, 30))
    assert movement.categories == ()
    assert grouped is not None
    assert grouped.direction is Direction.EXPENSE
    assert grouped.period == RelativePeriod(Period.THIS_YEAR)
    assert grouped.group_by is GroupBy.CATEGORY
    assert grouped.metric is Metric.COUNT
    assert grouped.group_limit == 3


@pytest.mark.parametrize(
    ("sentence", "expected_wording"),
    [
        (
            "Vuelve a la consulta anterior, cual fue mi gasto mas grande",
            "cual fue mi gasto mas grande",
        ),
        ("Volvamos a lo anterior; en la cuenta nomina", "en la cuenta nomina"),
        ("Volviendo a lo de antes: y por numero de operaciones", "y por numero de operaciones"),
    ],
)
def test_explicit_balance_return_selects_the_suspended_query(
    sentence: str, expected_wording: str
) -> None:
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    selected = _query_for_reader(
        query(sentence, Conversation(last_plan=suspended, answered_the_balance=True))
    )

    assert selected.plan is suspended
    assert selected.wording == expected_wording
    assert selected.returned_from_balance


@pytest.mark.parametrize(
    "sentence",
    [
        "Vale, vuelve a la consulta anterior: en la cuenta nomina",
        "Vale,volvamos a lo anterior; en la cuenta nomina",
        "Bueno: volvamos a lo anterior; en la cuenta nomina",
        "Y,volviendo a lo de antes: en la cuenta nomina",
    ],
)
def test_balance_return_accepts_a_closed_discourse_prefix(sentence: str) -> None:
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    selected = _query_for_reader(
        query(sentence, Conversation(last_plan=suspended, answered_the_balance=True))
    )

    assert selected.plan is suspended
    assert selected.wording == "en la cuenta nomina"
    assert selected.returned_from_balance


def test_account_reader_can_return_explicitly_from_a_balance_aside() -> None:
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.CATEGORY,
    )

    result = _account_follow_up_plan(
        query(
            "Volviendo a la consulta anterior: en la cuenta nomina",
            Conversation(last_plan=suspended, answered_the_balance=True),
        )
    )

    assert result is not None
    assert result.period == suspended.period
    assert result.direction is suspended.direction
    assert result.group_by is GroupBy.NONE


def test_group_ranking_reader_can_return_explicitly_from_a_balance_aside() -> None:
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MERCHANT,
        metric=Metric.AVERAGE,
        group_limit=3,
    )

    result = _group_ranking_plan(
        query(
            "Vuelve a la consulta anterior; y por numero de operaciones",
            Conversation(last_plan=suspended, answered_the_balance=True),
        )
    )

    assert result is not None
    assert result.period == suspended.period
    assert result.group_by is suspended.group_by
    assert result.metric is Metric.COUNT
    assert result.group_limit == suspended.group_limit


@pytest.mark.parametrize(
    "sentence",
    [
        "vale, por numero de operaciones",
        "vale,por numero de operaciones",
        "bueno: por numero de operaciones",
        "bueno:por numero de operaciones",
        "y por numero de operaciones",
        "y,por numero de operaciones",
        "y ahora por numero de operaciones",
        "vale, y por numero de operaciones",
        "ahora, por numero de operaciones",
        "mejor; por numero de operaciones",
    ],
)
def test_group_ranking_accepts_only_closed_positive_criterion_followups(sentence: str) -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MERCHANT,
        metric=Metric.SUM,
        group_limit=3,
    )

    result = _group_ranking_plan(query(sentence, Conversation(last_plan=previous)))

    assert result is not None
    assert result.metric is Metric.COUNT
    assert result.group_by is previous.group_by
    assert result.group_limit == previous.group_limit


@pytest.mark.parametrize(
    "sentence",
    [
        "no por numero de operaciones",
        "y no por numero de operaciones",
        "vale, no por numero de operaciones",
        "ahora no quiero por numero de operaciones",
    ],
)
def test_group_ranking_rejects_a_negated_criterion(sentence: str) -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MERCHANT,
        group_limit=3,
    )

    assert _group_ranking_plan(query(sentence, Conversation(last_plan=previous))) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "Vuelve a la consulta anterior cual fue mi gasto mas grande",
        "Vuelve a la consulta anterior,",
        "Por favor, vuelve a la consulta anterior: cual fue mi gasto mas grande",
        "cual fue mi gasto mas grande, volviendo a lo de antes",
        "No, vuelve a la consulta anterior: cual fue mi gasto mas grande",
        "Y no, vuelve a la consulta anterior: cual fue mi gasto mas grande",
    ],
)
def test_balance_return_requires_anchored_prefix_separator_and_request(sentence: str) -> None:
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    selected = _query_for_reader(
        query(sentence, Conversation(last_plan=suspended, answered_the_balance=True))
    )

    assert selected.plan is None
    assert selected.wording == sentence
    assert not selected.returned_from_balance


@pytest.mark.parametrize("special", ["ratio", "pending_ratio", "comparison"])
def test_explicit_return_never_authorizes_non_balance_focus(special: str) -> None:
    mirrored = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )
    sentence = "Volviendo a lo de antes: cual fue mi gasto mas grande"

    selected = _query_for_reader(
        query(sentence, conversation_with_special_focus(special, mirrored))
    )

    assert selected.plan is None
    assert not selected.returned_from_balance


@pytest.mark.parametrize("special", ["balance", "ratio", "comparison"])
def test_special_focus_blocks_ranking_from_the_last_plan_mirror(special: str) -> None:
    ranking = TopMovements(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        rank=1,
    )
    fields: dict[str, object] = {"last_plan": ranking}
    if special == "balance":
        fields["answered_the_balance"] = True
    elif special == "ratio":
        fields["last_ratio"] = ratio()
    else:
        fields["last_comparison"] = comparison()

    result = AskFinancialQuestionHandler._next_in_ranking(
        handler_without_dependencies(),
        query("y el segundo", Conversation(**fields)),  # type: ignore[arg-type]
    )

    assert result is None


@pytest.mark.parametrize("special", ["balance", "ratio", "comparison"])
def test_special_focus_blocks_year_shift_from_the_last_plan_mirror(special: str) -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 4, 1), date(2026, 4, 30)),
    )
    fields: dict[str, object] = {"last_plan": plan}
    if special == "balance":
        fields["answered_the_balance"] = True
    elif special == "ratio":
        fields["last_ratio"] = ratio()
    else:
        fields["last_comparison"] = comparison()

    result = AskFinancialQuestionHandler._a_year_earlier(
        handler_without_dependencies(),
        query("y el año anterior", Conversation(**fields)),  # type: ignore[arg-type]
    )

    assert result is None


@pytest.mark.parametrize("special", ["ratio", "pending_ratio"])
def test_ratio_focus_blocks_percentage_pair_from_query_mirrors(special: str) -> None:
    earlier = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
    )
    latest = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
    )
    fields: dict[str, object] = {"last_plan": latest, "prior_plan": earlier}
    if special == "ratio":
        fields["last_ratio"] = ratio()
    else:
        fields["pending_ratio"] = RatioDraft(
            part=FinancialPlanDraft(
                direction=Direction.EXPENSE,
                categories=("HOUSING_RENT",),
            ),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.SHARE,
        )

    result = _stored_percentage_pair(
        query("y en porcentaje", Conversation(**fields))  # type: ignore[arg-type]
    )

    assert result is None


def test_comparison_focus_is_the_source_of_a_percentage_pair() -> None:
    stored = comparison()

    result = _stored_percentage_pair(query("y en porcentaje", Conversation(last_comparison=stored)))

    assert result is stored


def test_pending_draft_produces_a_forced_active_clarification_source() -> None:
    draft = FinancialPlanDraft(
        direction=Direction.EXPENSE,
        categories=("HOUSING_RENT",),
    )

    continuation = AskFinancialQuestionHandler._pending_continuation(
        handler_without_dependencies(),
        query(
            "este año, pero cuéntalas",
            Conversation(active=FinancialClarification(draft=draft)),
        ),
    )

    assert continuation is not None
    assert continuation.source is ContinuationSource.ACTIVE_CLARIFICATION
    assert continuation.force_follow_up
    assert continuation.plan.categories == ("HOUSING_RENT",)
    assert continuation.plan.period == RelativePeriod(Period.THIS_YEAR)
