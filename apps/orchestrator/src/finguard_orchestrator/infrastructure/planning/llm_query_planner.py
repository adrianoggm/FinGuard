"""Turns a sentence into a PlanningResult using one model.

One ordinary attempt, plus one schema-repair attempt when the provider returned bytes
that cannot be read as its constrained contract. Walking the catalogue when a provider
fails remains application policy and lives in the handler; the repair is narrower and
never changes, widens, or guesses a financial plan.

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
import logging
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
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.services import plan_merger, window_words
from finguard_orchestrator.domain.services.plan_merger import Field
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
    metric_without_a_bucket,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.planning.plan_schema import (
    ABSOLUTE,
    NO_CATEGORY,
    build_schema,
)
from finguard_orchestrator.infrastructure.planning.prompt import build_prompt
from finguard_orchestrator.observability import Step, Verdict, record

logger = logging.getLogger(__name__)

STRUCTURE_REPAIR: Final = (
    "\n\nYour previous response did not satisfy the required JSON schema. "
    "Return only one JSON object that matches the supplied schema exactly."
)

# How each planning outcome reads as a verdict. A table rather than a chain of `if`s because the
# mapping is data: a seam that has to branch to work out what it decided is a seam whose telemetry
# can disagree with its behaviour, and there is nothing here to disagree about.
#
# A clarification is `DECLINED` beside `UNSUPPORTED` because the shape of the decision is the same
# from the outside — the model looked and did not produce a plan — and the exact outcome is in
# `now` for anybody who needs the difference. `FAILED` is kept for the two that are not this
# service choosing: bytes that did not match the schema, and a provider that said nothing at all.
_VERDICT: Final = {
    PlanningOutcome.PLANNED: Verdict.AGREED,
    PlanningOutcome.NEEDS_CLARIFICATION: Verdict.DECLINED,
    PlanningOutcome.UNSUPPORTED: Verdict.DECLINED,
    PlanningOutcome.INVALID_DOMAIN: Verdict.DECLINED,
    PlanningOutcome.INVALID_STRUCTURE: Verdict.FAILED,
    PlanningOutcome.PROVIDER_ERROR: Verdict.FAILED,
}


def _settled(group_by: GroupBy, metric: Metric) -> Metric:
    """`metric_without_a_bucket`, recorded.

    Both places that settle the pair go through here — the plan and the clarification's draft —
    so the seam is one `record` call and neither call site grew a telemetry block. `changed` is
    the pair actually moving, which is exactly the case the domain used to refuse: a cumulative
    metric with no bucket to accumulate over was an `InvalidValueError`, and a refusal becoming
    an answer is the whole reason this step is worth counting.
    """
    now = metric_without_a_bucket(group_by, metric)
    record(
        Step.METRIC_WITHOUT_A_BUCKET,
        Verdict.OVERRODE if now is not metric else Verdict.AGREED,
        changed=now is not metric,
        field="metric",
        was=metric.value,
        now=now.value,
        detail=group_by.value,
    )
    return now


class LlmQueryPlanner:
    """Implements ContextualQueryPlannerPort against a single model."""

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
        self,
        sentence: str,
        *,
        context: PlanningContext,
    ) -> PlanningResult:
        """One planning decision, recorded once, whatever route inside produced it.

        `changed` is the repair. The other two facts the seam carries have somewhere of their own
        to go — the outcome in `now`, the model in `detail` — and what is left for "did this alter
        what the turn answered" is the one thing that did: a first answer that failed the schema
        and a second that did not. Without the repair those turns are `INVALID_STRUCTURE`, so the
        rate of `changed=True` here is the rate at which constrained decoding stopped holding and
        this layer covered for it. `planner.invalid_structure` says the same thing to a log; this
        makes it a figure beside every other step's.
        """
        planned, repaired = await self._planned(sentence, context=context)
        record(
            Step.PLANNER,
            _VERDICT[planned.outcome],
            changed=repaired,
            now=planned.outcome.value,
            detail=self._model,
        )
        return planned

    async def _planned(
        self,
        sentence: str,
        *,
        context: PlanningContext,
    ) -> tuple[PlanningResult, bool]:
        """The result, and whether it came from the schema-repair attempt rather than the first."""
        if window_words.has_invalid_counted_window(sentence):
            return (
                PlanningResult.needs_clarification(
                    "Ese periodo queda fuera del intervalo admitido. "
                    "¿Cuántos días o meses quieres consultar?"
                ),
                False,
            )
        today = context.today
        previous = context.continuation.plan if context.continuation else None
        force_follow_up = context.continuation.force_follow_up if context.continuation else False
        prompt = build_prompt(
            sentence,
            today=today,
            categories=self._categories,
            previous=previous,
            force_follow_up=force_follow_up,
        )
        try:
            answer = await self._complete(prompt)
        except ProviderFailedError as exc:
            return PlanningResult.provider_error(str(exc)), False

        planned = _read(
            answer,
            today=today,
            categories=self._categories,
            previous=previous,
            force_follow_up=force_follow_up,
        )
        if planned.outcome is not PlanningOutcome.INVALID_STRUCTURE:
            return planned, False

        # Never echo malformed provider text into the repair prompt. The original question and the
        # exact schema are sufficient, and the second result is still validated without coercion.
        logger.warning("planner.invalid_structure retrying=true")
        try:
            repaired = await self._complete(prompt + STRUCTURE_REPAIR)
        except ProviderFailedError as exc:
            return PlanningResult.provider_error(str(exc)), True
        return (
            _read(
                repaired,
                today=today,
                categories=self._categories,
                previous=previous,
                force_follow_up=force_follow_up,
            ),
            True,
        )

    async def _complete(self, prompt: str) -> str:
        return await self._provider.complete(
            prompt,
            model=self._model,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            schema=self._schema,
        )


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
    answer: str,
    *,
    today: date,
    categories: tuple[str, ...],
    previous: FinancialPlan | None = None,
    force_follow_up: bool = False,
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
            draft = _draft(body, today=today, categories=categories)
            if previous is not None and force_follow_up and draft is not None:
                try:
                    draft = plan_merger.merge_draft(previous, draft, changed=_changed(body))
                except (_MalformedError, InvalidValueError) as exc:
                    return PlanningResult.invalid_structure(str(exc))
            return PlanningResult.needs_clarification(
                question,
                draft,
                _awaiting(body),
            )

        case PlanningOutcome.UNSUPPORTED:
            return PlanningResult.unsupported("the question cannot be expressed as a query")

        case PlanningOutcome.PLANNED:
            return _build_plan(
                body,
                today=today,
                categories=categories,
                previous=previous,
                force_follow_up=force_follow_up,
            )

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
        # The same reading `_build_plan` makes, for the same pair. A draft that lost it came back
        # as no draft at all, and the draft is the only thing the reply to the question builds on.
        metric = _settled(group_by, Metric(_text(body, "metric")))
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
    force_follow_up: bool = False,
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
            follow_up, changed = (
                force_follow_up or _flag(body, "isFollowUp"),
                _changed(body),
            )
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
        grouping = GroupBy(group_by)
        plan = FinancialPlan(
            direction=Direction(direction),
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=selected_categories,
            merchants=merchants,
            group_by=grouping,
            # A running total the model gave no buckets to. This is not the normalising the module
            # docstring refuses: that rule is about repairs which *widen* a query, and every filter
            # here is left exactly as read. The pair is settled rather than dropped, and only where
            # settling it changes no figure — `metric_without_a_bucket` argues which pair that is.
            metric=_settled(grouping, Metric(metric)),
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
