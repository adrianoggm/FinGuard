from datetime import date
from typing import cast
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    _stored_percentage_pair,
)
from finguard_orchestrator.domain.enums import Direction, Period
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
