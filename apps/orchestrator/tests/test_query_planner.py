"""Reading a model's answer, and every way that can go wrong.

A fake provider returns canned strings, so these run without a daemon and can hold the
answer still — including answers a constrained decoder should never produce. Those are
the ones worth pinning: the whole design assumes the constraint holds, and code that
only works while an assumption holds is code nobody has tested.
"""

import json
from datetime import date
from typing import Any

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.infrastructure.planning.llm_query_planner import LlmQueryPlanner
from finguard_orchestrator.infrastructure.planning.plan_schema import (
    ABSOLUTE,
    NO_CATEGORY,
    build_schema,
)

CATEGORIES = ("GROCERIES", "TRANSPORT_FUEL", "HOUSING_RENT")
TODAY = date(2026, 7, 15)

FULL = {
    "outcome": "PLANNED",
    "question": "",
    "period": "LAST_MONTH",
    "n": 0,
    "from": "",
    "to": "",
    "direction": "EXPENSE",
    "categories": [NO_CATEGORY],
    "merchants": [],
    "groupBy": "NONE",
    "metric": "SUM",
}


class FakeProvider:
    """Answers with whatever it was handed, or raises."""

    name = "fake"

    def __init__(self, answer: str | None = None, failure: str | None = None) -> None:
        self._answer = answer
        self._failure = failure
        self.schema: dict[str, Any] | None = None

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> str:
        self.schema = schema
        if self._failure is not None:
            raise ProviderFailedError(self.name, self._failure)
        return self._answer or ""


def planner_for(provider: FakeProvider) -> LlmQueryPlanner:
    return LlmQueryPlanner(
        provider, model="m", temperature=0.0, max_output_tokens=256, categories=CATEGORIES
    )


async def plan_from(body: dict[str, Any] | str) -> Any:
    raw = body if isinstance(body, str) else json.dumps(body)
    return await planner_for(FakeProvider(answer=raw)).plan("da igual", today=TODAY)


# --- the schema itself -------------------------------------------------------------


def test_the_schema_lets_the_model_refuse() -> None:
    """The load-bearing one. If the schema only described a plan, a constrained decoder
    could not emit anything else — the model would be physically unable to say "I don't
    know", so every ambiguous question would come back as a confident guess."""
    outcomes = build_schema(CATEGORIES)["properties"]["outcome"]["enum"]

    assert PlanningOutcome.NEEDS_CLARIFICATION in outcomes
    assert PlanningOutcome.UNSUPPORTED in outcomes


def test_the_schema_does_not_offer_the_outcomes_that_happen_to_a_model() -> None:
    # A model cannot report its own output as malformed, or the daemon as down.
    outcomes = build_schema(CATEGORIES)["properties"]["outcome"]["enum"]

    assert PlanningOutcome.INVALID_STRUCTURE not in outcomes
    assert PlanningOutcome.PROVIDER_ERROR not in outcomes


def test_categories_are_closed_by_the_schema() -> None:
    # Constrained decoding cannot emit a token sequence outside the enum, so an
    # invented category is unreachable rather than caught after the fact.
    allowed = build_schema(CATEGORIES)["properties"]["categories"]["items"]["enum"]

    assert set(allowed) == {*CATEGORIES, NO_CATEGORY}


def test_the_periods_come_from_the_enum() -> None:
    # Generated, not copied: a tenth period must not leave the schema behind.
    allowed = build_schema(CATEGORIES)["properties"]["period"]["enum"]

    assert {p.value for p in Period} <= set(allowed)


async def test_the_schema_reaches_the_provider() -> None:
    provider = FakeProvider(answer=json.dumps(FULL))

    await planner_for(provider).plan("¿cuánto gasté?", today=TODAY)

    assert provider.schema is not None, "the request went out unconstrained"


# --- reading a well-formed answer --------------------------------------------------


async def test_a_named_period_becomes_a_plan() -> None:
    result = await plan_from(FULL)

    assert result.outcome is PlanningOutcome.PLANNED
    assert result.plan.period.period is Period.LAST_MONTH
    assert result.plan.direction is Direction.EXPENSE
    assert result.plan.group_by is GroupBy.NONE
    assert result.plan.categories == ()


async def test_the_unused_count_sentinel_is_dropped() -> None:
    # 0 is our convention for "not applicable", not the model getting it wrong, so it
    # must not be passed on to fail the invariant that LAST_MONTH takes no count.
    result = await plan_from({**FULL, "n": 0})

    assert result.plan.period.n is None


async def test_a_counted_period_keeps_its_count() -> None:
    result = await plan_from({**FULL, "period": "LAST_N_DAYS", "n": 30})

    assert result.plan.period.n == 30


async def test_an_absolute_window_becomes_a_range() -> None:
    result = await plan_from({**FULL, "period": ABSOLUTE, "from": "2026-01-01", "to": "2026-01-31"})

    assert result.plan.period is None
    assert result.plan.date_range.start == date(2026, 1, 1)
    assert result.plan.date_range.end == date(2026, 1, 31)


async def test_the_no_category_marker_never_becomes_a_filter() -> None:
    result = await plan_from({**FULL, "categories": [NO_CATEGORY, "GROCERIES", "GROCERIES"]})

    # NONE dropped, and the same category twice is one filter, not two.
    assert result.plan.categories == ("GROCERIES",)


@pytest.mark.parametrize("category", ["", "CRYPTO_ASSETS"])
async def test_a_category_outside_the_schema_is_structural(category: str) -> None:
    """An invalid category must never become no filter or a guaranteed empty result."""
    result = await plan_from({**FULL, "categories": [category]})

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE
    assert result.plan is None


async def test_categories_with_the_wrong_container_are_structural() -> None:
    result = await plan_from({**FULL, "categories": "GROCERIES"})

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE
    assert result.plan is None


async def test_a_question_is_carried_back() -> None:
    result = await plan_from(
        {**FULL, "outcome": "NEEDS_CLARIFICATION", "question": "¿De qué mes?", "period": ""}
    )

    assert result.outcome is PlanningOutcome.NEEDS_CLARIFICATION
    assert result.question == "¿De qué mes?"
    assert not result.is_answerable


async def test_unsupported_does_not_fall_back_to_a_plan() -> None:
    result = await plan_from({**FULL, "outcome": "UNSUPPORTED", "period": ""})

    assert result.outcome is PlanningOutcome.UNSUPPORTED
    assert result.plan is None


# --- answers a constrained decoder should never produce ----------------------------


async def test_text_that_is_not_json_is_structural() -> None:
    result = await plan_from("lo siento, no puedo ayudarte con eso")

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE


async def test_a_json_array_is_structural() -> None:
    result = await plan_from("[1, 2, 3]")

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE


async def test_an_unknown_outcome_is_structural() -> None:
    result = await plan_from({**FULL, "outcome": "MAYBE"})

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE


async def test_asking_nothing_is_structural_not_a_clarification() -> None:
    # It chose to ask and then asked nothing, which leaves the user at a dead end.
    # Inventing the question here would hide that the model produced nothing usable.
    result = await plan_from({**FULL, "outcome": "NEEDS_CLARIFICATION", "question": "   "})

    assert result.outcome is PlanningOutcome.INVALID_STRUCTURE


async def test_a_period_outside_the_enum_is_a_domain_failure() -> None:
    result = await plan_from({**FULL, "period": "LAST_FORTNIGHT"})

    assert result.outcome is PlanningOutcome.INVALID_DOMAIN


async def test_a_counted_period_without_its_count_is_a_domain_failure() -> None:
    result = await plan_from({**FULL, "period": "LAST_N_DAYS", "n": 0})

    assert result.outcome is PlanningOutcome.INVALID_DOMAIN


async def test_a_backwards_absolute_window_is_a_domain_failure() -> None:
    result = await plan_from({**FULL, "period": ABSOLUTE, "from": "2026-01-31", "to": "2026-01-01"})

    assert result.outcome is PlanningOutcome.INVALID_DOMAIN


async def test_an_unparseable_date_is_a_domain_failure() -> None:
    result = await plan_from({**FULL, "period": ABSOLUTE, "from": "enero", "to": "2026-01-31"})

    assert result.outcome is PlanningOutcome.INVALID_DOMAIN


# --- the provider itself failing ---------------------------------------------------


async def test_a_dead_provider_is_an_outcome_not_an_exception() -> None:
    """The caller has to tell the user something whichever way it went, so raising here
    would only move the decision somewhere with less to say."""
    result = await planner_for(FakeProvider(failure="daemon down")).plan("hola", today=TODAY)

    assert result.outcome is PlanningOutcome.PROVIDER_ERROR
    assert not result.is_answerable
