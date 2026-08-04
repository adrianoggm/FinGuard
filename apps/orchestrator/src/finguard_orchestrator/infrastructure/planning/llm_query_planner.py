"""Turns a sentence into a PlanningResult using one model, once.

One attempt on purpose. Walking the catalogue when a provider fails is application
policy and already lives in the handler; a second copy here would mean two places
decide what a failure means and they would disagree eventually.

Everything that can go wrong becomes an outcome rather than an exception. The caller
has to say something to the user whatever happened, so an exception would only move the
decision somewhere less equipped to make it.

**Nothing here normalises.** A field of the wrong type is INVALID_STRUCTURE, never a
best-effort reading, because the safe-looking repair is the dangerous one: `categories`
arriving as the string "GROCERIES" instead of a list used to become an empty tuple, and
an empty tuple is not a missing filter — it is *no filter at all*, so a question about
the supermarket came back as every expense there is. A wrong type cannot be repaired
into a right value when the repair widens the query.

This matters more since the benchmark found a model that breaks the constraint:
granite4:micro produced 3 invalid structures out of 79. Constrained decoding is a strong
property of the runtime, not a guarantee of the pipeline, and this layer is what makes
the difference visible instead of silent.
"""

import json
from datetime import date
from typing import Any, Final

from finguard_orchestrator.domain.enums import (
    PERIODS_NEEDING_COUNT,
    Action,
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
)
from finguard_orchestrator.domain.errors import InvalidValueError, ProviderFailedError
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.services import plan_merger
from finguard_orchestrator.domain.services.plan_merger import Field
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.planning.plan_schema import (
    ABSOLUTE,
    NO_CATEGORY,
    build_schema,
)
from finguard_orchestrator.infrastructure.planning.prompt import build_prompt


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
        self, sentence: str, *, today: date, previous: FinancialPlan | None = None
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

        return _read(answer, today=today, categories=self._categories, previous=previous)


# What to ask when the model chose to ask and did not say what. Spanish because it reaches
# the user, which is the one exception rule 14 makes.
FALLBACK_QUESTION: Final = (
    "No he entendido qué quieres consultar de tu cuenta. ¿Puedes decirlo de otra forma?"
)

# What to ask when the sentence read completely except for its window. The examples are phrases
# `window_words` can read back, which is what makes the next turn deterministic: suggesting a form
# the reader does not recognise would send the answer to the model and undo the point of asking.
WINDOW_QUESTION: Final = (
    "¿De qué periodo? Por ejemplo este mes, el mes pasado o los últimos 30 días."
)


class _MalformedError(Exception):
    """The answer did not match the schema it was constrained to. Internal to this
    module: it becomes INVALID_STRUCTURE before anything else sees it."""


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise _MalformedError(f"{key} must be a string, got {type(value).__name__}")
    return value


def _count(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    # bool is an int in Python and never a count. Excluded explicitly so `true` cannot
    # arrive as 1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise _MalformedError(f"{key} must be an integer, got {type(value).__name__}")
    return value


def _texts(body: dict[str, Any], key: str) -> list[str]:
    """A list of strings, or nothing. Never a repaired reading of something else."""
    value = body.get(key)
    if not isinstance(value, list):
        raise _MalformedError(f"{key} must be a list, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise _MalformedError(f"{key} holds a {type(item).__name__}, expected strings")
    return value


def _read(
    answer: str, *, today: date, categories: tuple[str, ...], previous: FinancialPlan | None = None
) -> PlanningResult:
    try:
        body = json.loads(answer)
    except ValueError as exc:
        # Under constrained decoding this is unreachable. Counting it is how we find out
        # if the constraint ever stops holding, instead of assuming it always did.
        return PlanningResult.invalid_structure(f"not JSON: {exc}")

    if not isinstance(body, dict):
        return PlanningResult.invalid_structure(f"expected an object, got {type(body).__name__}")

    try:
        outcome = PlanningOutcome(_text(body, "outcome"))
    except _MalformedError as exc:
        return PlanningResult.invalid_structure(str(exc))
    except ValueError:
        return PlanningResult.invalid_structure(f"unknown outcome: {body.get('outcome')!r}")

    match outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            try:
                question = _text(body, "question").strip()
            except _MalformedError as exc:
                return PlanningResult.invalid_structure(str(exc))
            if not question:
                # It chose to ask and did not say what. The JSON is valid and every field is
                # present, so this is not a structural failure — calling it one claimed the
                # constraint had broken when it had not, and a false positive in that signal
                # hides the real thing it exists to detect.
                #
                # The words are supplied instead. Inventing a *plan* would be dangerous: a
                # figure for a question nobody asked. Inventing the words of a refusal commits
                # to nothing, and the user reads the same sentence either way.
                question = FALLBACK_QUESTION
            return PlanningResult.needs_clarification(
                question,
                _draft(body, today=today, categories=categories),
                _awaiting(body),
            )

        case PlanningOutcome.UNSUPPORTED:
            return PlanningResult.unsupported("the question cannot be expressed as a query")

        case PlanningOutcome.PLANNED:
            return _build_plan(body, today=today, categories=categories, previous=previous)

        case _:
            # The three remaining outcomes describe things that happen TO a model; it
            # cannot report them about itself, and the schema does not offer them.
            return PlanningResult.invalid_structure(f"{outcome} is not the model's to choose")


def _action(body: dict[str, Any]) -> Action:
    """Which kind of query the model chose.

    Defaults to the aggregate rather than failing: it is what almost every question is, and a
    malformed tag is not worth turning a readable plan into an error. Under constrained decoding the
    value cannot be outside the enum, and if it ever is, this is where it stops mattering.
    """
    try:
        return Action(_text(body, "action"))
    except (_MalformedError, ValueError):
        return Action.AGGREGATE


def _awaiting(body: dict[str, Any]) -> Slot:
    """Which slot the question is about.

    Defaults to the window rather than failing: it is what a clarification asks for nine times out
    of ten, and a malformed slot is not worth turning a useful question into an error. A value
    outside the enum cannot arrive under constrained decoding, and if it does this is where it
    stops being interesting.
    """
    try:
        return Slot(_text(body, "awaiting"))
    except (_MalformedError, ValueError):
        return Slot.WINDOW


def _draft(
    body: dict[str, Any], *, today: date, categories: tuple[str, ...]
) -> FinancialPlanDraft | None:
    """What the model had read by the time it decided to ask a question.

    The schema requires every field on every outcome, so a clarification carries a complete
    reading of everything except the window — which is normally the thing being asked about.
    This used to be dropped on the floor, and dropping it is why answering "este año" asked
    for the period again.

    **Nothing here can fail the clarification.** A question back is useful on its own, so an
    unreadable draft becomes no draft rather than an error: turning it into INVALID_STRUCTURE
    would replace a helpful question with an apology, and the model did nothing wrong by
    asking.
    """
    try:
        direction = Direction(_text(body, "direction"))
        group_by = GroupBy(_text(body, "groupBy"))
        metric = Metric(_text(body, "metric"))
        selected_categories = _categories(_texts(body, "categories"), allowed=categories)
        merchants = tuple(dict.fromkeys(m.strip() for m in _texts(body, "merchants") if m.strip()))
    except (_MalformedError, ValueError):
        return None

    try:
        window: RelativePeriod | AbsoluteRange | None = _window(body, today=today)
    except (_MalformedError, InvalidValueError, ValueError):
        # The expected case, not an error: `period` is "" because the window is what the
        # question is about. A range that fails to parse lands here too, and no window is the
        # right reading of both — the next turn supplies one.
        window = None

    try:
        return FinancialPlanDraft(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=selected_categories,
            merchants=merchants,
            group_by=group_by,
            metric=metric,
        )
    except InvalidValueError:
        return None


def _flag(body: dict[str, Any], key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise _MalformedError(f"{key} must be a boolean, got {type(value).__name__}")
    return value


def _changed(body: dict[str, Any]) -> frozenset[Field]:
    """Which fields the follow-up changes. A name outside the set is malformed rather than
    ignored: silently dropping one means inheriting a field the sentence just changed, which
    is a real figure for the previous question."""
    names = _texts(body, "changed")
    known = {f.value for f in Field}
    unknown = set(names) - known
    if unknown:
        raise _MalformedError(f"changed holds unknown fields: {sorted(unknown)}")
    return frozenset(Field(name) for name in names)


def _build_plan(
    body: dict[str, Any],
    *,
    today: date,
    categories: tuple[str, ...],
    previous: FinancialPlan | None = None,
) -> PlanningResult:
    try:
        selected_categories = _categories(_texts(body, "categories"), allowed=categories)
        # No `allowed` list: a merchant name is not vocabulary. Blanks are dropped because an
        # empty name filters nothing, and dropping one is not the widening bug — the others
        # still narrow, and an all-blank list means the question named no shop.
        merchants = tuple(dict.fromkeys(m.strip() for m in _texts(body, "merchants") if m.strip()))
        direction, group_by, metric = (
            _text(body, "direction"),
            _text(body, "groupBy"),
            _text(body, "metric"),
        )
    except _MalformedError as exc:
        return PlanningResult.invalid_structure(str(exc))

    try:
        window = _window(body, today=today)
    except _MalformedError as exc:
        return PlanningResult.invalid_structure(str(exc))
    except (InvalidValueError, ValueError) as exc:
        # A window that is wrong rather than absent: a range ending after today, an unparseable
        # date, `n` outside its bounds. Nobody can supply what is already there and incorrect.
        return PlanningResult.invalid_domain(str(exc))

    follow_up, changed = False, frozenset[Field]()
    if previous is not None:
        try:
            follow_up, changed = _flag(body, "isFollowUp"), _changed(body)
        except _MalformedError as exc:
            return PlanningResult.invalid_structure(str(exc))

    inherits_window = follow_up and Field.WINDOW not in changed
    if window is None and previous is not None and inherits_window:
        # The prompt says "do not restate the rest", so a follow-up changing the category emits
        # no window on purpose. The plan used to be built before the merge could supply one, so
        # "¿y en gasolina?" was refused for missing the single field the model was told to leave
        # out. Taken as a pair, like the merge does: a plan carries one window or the other.
        window = previous.period or previous.date_range

    if window is None:
        # Everything else read fine and one piece is missing, which is a question rather than a
        # failure. The draft is what makes the answer to it a completion instead of a restart.
        return PlanningResult.needs_clarification(
            WINDOW_QUESTION,
            _draft(body, today=today, categories=categories),
            # The window is what is missing here by construction, whatever the model put in the
            # field: this branch runs because no window was named.
            Slot.WINDOW,
        )

    if _action(body) is Action.RANK_MOVEMENTS:
        # A ranking, and it does not go through the follow-up merge: that vocabulary is expressed
        # over an aggregate's fields, and there is nothing sensible to inherit into a maximum.
        try:
            return PlanningResult.planned(
                TopMovements(
                    direction=Direction(direction),
                    period=window if isinstance(window, RelativePeriod) else None,
                    date_range=window if isinstance(window, AbsoluteRange) else None,
                    categories=selected_categories,
                    merchants=merchants,
                    rank=_count(body, "rank") or 1,
                )
            )
        except (_MalformedError, InvalidValueError, ValueError) as exc:
            return PlanningResult.invalid_domain(str(exc))

    try:
        plan = FinancialPlan(
            direction=Direction(direction),
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=selected_categories,
            merchants=merchants,
            group_by=GroupBy(group_by),
            metric=Metric(metric),
        )
    except InvalidValueError as exc:
        return PlanningResult.invalid_domain(str(exc))
    except ValueError as exc:
        # An enum value outside the schema: well-formed JSON carrying a value the domain
        # will not take.
        return PlanningResult.invalid_domain(str(exc))

    if previous is None or not follow_up:
        # A complete question carries its own context. Inheriting here is how a breakdown
        # nobody asked for survives into the next answer.
        return PlanningResult.planned(plan)

    try:
        return PlanningResult.planned(plan_merger.merge(previous, plan, changed=changed))
    except InvalidValueError as exc:
        return PlanningResult.invalid_domain(str(exc))


def _window(body: dict[str, Any], *, today: date) -> RelativePeriod | AbsoluteRange | None:
    """The window the model named, or None if it named none.

    None is a reading, not a failure — and telling the two apart is the whole point of this
    signature. A window that is *missing* can be supplied: by the previous plan on a follow-up,
    or by the user on the next turn. A window that is *wrong* — a range ending after today, an
    unparseable date — still raises, because there is nothing for anyone to supply.

    It used to raise for both, and the missing case then arrived as INVALID_DOMAIN. The deployed
    model answers "¿cuánto suelo pagar de alquiler?" with `period: "ABSOLUTE"` and both ends
    empty, which is the only way the schema lets it choose PLANNED while having no window — so
    the reply was "no he entendido la pregunta" to a sentence it had understood entirely.
    """
    named = _text(body, "period")

    if not named:
        # The empty period exists so a model that is asking or refusing has something to put
        # there. On PLANNED it means it read the sentence and found no window in it.
        return None

    if named == ABSOLUTE:
        start, end = _text(body, "from"), _text(body, "to")
        if not start or not end:
            # ABSOLUTE with an empty end is the same statement as an empty period, spelled
            # differently. It is the spelling the deployed model actually uses.
            return None
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
    return RelativePeriod(period=period, n=_count(body, "n"))


def _categories(raw: list[str], *, allowed: tuple[str, ...]) -> tuple[str, ...]:
    """NONE is how the schema spells "no filter", so it never reaches the plan. Order is
    kept and duplicates dropped: the same category twice is not two filters.

    Takes a list that has already been type-checked. Dropping a malformed entry here
    would be the widening bug all over again, one element at a time.
    """
    seen: dict[str, None] = {}
    for value in raw:
        if value == NO_CATEGORY:
            continue
        if value not in allowed:
            # This value was outside the schema's enum. Treating an empty or invented
            # category as no filter would broaden the query, while forwarding an
            # invented one would silently return an empty result from banking.
            raise _MalformedError(f"categories holds a value outside the catalogue: {value!r}")
        seen.setdefault(value, None)
    return tuple(seen)
