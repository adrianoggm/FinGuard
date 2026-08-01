"""Turns a sentence into a PlanningResult using one model, once.

One attempt on purpose. Walking the catalogue when a provider fails is application
policy and already lives in the handler; a second copy here would mean two places
decide what a failure means and they would disagree eventually.

Everything that can go wrong becomes an outcome rather than an exception. The caller
has to say something to the user whatever happened, so an exception would only move the
decision somewhere less equipped to make it.
"""

import json
from datetime import date
from typing import Any

from finguard_assistant.domain.enums import (
    PERIODS_NEEDING_COUNT,
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
)
from finguard_assistant.domain.errors import InvalidValueError, ProviderFailedError
from finguard_assistant.domain.models.planning_result import PlanningResult
from finguard_assistant.domain.ports.llm_provider import LlmProviderPort
from finguard_assistant.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_assistant.infrastructure.planning.plan_schema import (
    ABSOLUTE,
    NO_CATEGORY,
    build_schema,
)
from finguard_assistant.infrastructure.planning.prompt import build_prompt


class LlmQueryPlanner:
    """Implements QueryPlannerPort against a single model."""

    def __init__(
        self,
        provider: LlmProviderPort,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        categories: tuple[str, ...],
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._categories = categories
        self._schema = build_schema(categories)

    async def plan(
        self, sentence: str, *, today: date, previous: str | None = None
    ) -> PlanningResult:
        try:
            answer = await self._provider.complete(
                build_prompt(
                    sentence,
                    today=today,
                    categories=self._categories,
                    previous=previous,
                ),
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=self._schema,
            )
        except ProviderFailedError as exc:
            return PlanningResult.provider_error(str(exc))

        return _read(answer, today=today)


def _read(answer: str, *, today: date) -> PlanningResult:
    try:
        body = json.loads(answer)
    except ValueError as exc:
        # Under constrained decoding this is unreachable. Counting it is how we find out
        # if the constraint ever stops holding, instead of assuming it always did.
        return PlanningResult.invalid_structure(f"not JSON: {exc}")

    if not isinstance(body, dict):
        return PlanningResult.invalid_structure(f"expected an object, got {type(body).__name__}")

    try:
        outcome = PlanningOutcome(str(body.get("outcome")))
    except ValueError:
        return PlanningResult.invalid_structure(f"unknown outcome: {body.get('outcome')!r}")

    match outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            question = str(body.get("question") or "").strip()
            if not question:
                # It chose to ask and then asked nothing, which leaves the user at a
                # dead end. Treat it as malformed rather than inventing the question.
                return PlanningResult.invalid_structure("clarification with no question")
            return PlanningResult.needs_clarification(question)

        case PlanningOutcome.UNSUPPORTED:
            return PlanningResult.unsupported("the question cannot be expressed as a query")

        case PlanningOutcome.PLANNED:
            return _build_plan(body, today=today)

        case _:
            # The three remaining outcomes describe things that happen TO a model; it
            # cannot report them about itself, and the schema does not offer them.
            return PlanningResult.invalid_structure(f"{outcome} is not the model's to choose")


def _build_plan(body: dict[str, Any], *, today: date) -> PlanningResult:
    try:
        window = _window(body, today=today)
        plan = FinancialPlan(
            direction=Direction(str(body.get("direction"))),
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=_categories(body.get("categories")),
            group_by=GroupBy(str(body.get("groupBy"))),
            metric=Metric(str(body.get("metric"))),
        )
    except InvalidValueError as exc:
        return PlanningResult.invalid_domain(str(exc))
    except ValueError as exc:
        # An enum value outside the schema, or an unparseable date: well-formed JSON
        # carrying a value the domain will not take.
        return PlanningResult.invalid_domain(str(exc))

    return PlanningResult.planned(plan)


def _window(body: dict[str, Any], *, today: date) -> RelativePeriod | AbsoluteRange:
    named = str(body.get("period") or "")

    if not named:
        # The empty period exists so a model that is asking or refusing has something to
        # put there. Arriving with PLANNED means it chose to answer and then named no
        # window, which is not a date-parsing problem and should not read like one.
        raise InvalidValueError("a plan needs a window and none was named")

    if named == ABSOLUTE:
        start, end = str(body.get("from") or ""), str(body.get("to") or "")
        if not start or not end:
            raise InvalidValueError("an explicit range needs both ends")
        window = AbsoluteRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
        if window.end > today:
            # Nobody has spent money after today. A range reaching into the future is a
            # model filling in a calendar year rather than reading the question, and
            # letting it through would answer about a window that cannot have data.
            raise InvalidValueError(f"a window cannot end after today: {window.end} > {today}")
        return window

    period = Period(named)
    if period not in PERIODS_NEEDING_COUNT:
        # 0 is our sentinel for "unused", not the model getting it wrong, so it is
        # dropped rather than passed on to fail an invariant.
        return RelativePeriod(period=period)
    return RelativePeriod(period=period, n=int(body.get("n") or 0))


def _categories(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    # NONE is how the schema spells "no filter", so it never reaches the plan. Order is
    # kept and duplicates dropped: the same category twice is not two filters.
    seen: dict[str, None] = {}
    for item in raw:
        value = str(item)
        if value and value != NO_CATEGORY:
            seen.setdefault(value, None)
    return tuple(seen)
