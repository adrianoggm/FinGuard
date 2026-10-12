"""Reads one turn with a model, held to the closed operations schema.

One normal call per turn, plus at most one bounded review. The review is triggered by the shape of
what the model proposed against the conversation's own state, never by reading the person's words
(ruling R18: the model is the only reader of a sentence):

- a clarification while a task is on the table, which may discard it;
- a new task that repeats a filter another task on the table holds, which may be carried over;
- a patch that keeps the active task's category beside a new one, which widens it;
- a patch that restates a direction and a window over a filtered task, which may be a new task;
- a merchant search term that is one of the selected category's own names;
- a new payment, when no payment is open: whether it asks to send money now or about one already
  made is the model's call, asked once more on purpose;
- with a chart on screen, a patch that only moves the top-N, or a patch beside a redraw: changing
  how the chart shows its data and changing what is queried look alike until the model is asked;
- compare_previous over a screen that is not the rolling window it compares: the domain would
  refuse it, and a comparison with a result on screen is a compare with from_result.

After the review the model's reading stands. If the review cannot run or be read, a risky reading
does not: a new ask is asked back keeping everything but the suspect values, and a payment is
never prepared.

A provider that cannot answer at all is different: no question to the person fixes it, so it is
raised as `InterpretationFailedError` for the handler to report. So is an answer the schema cannot
read after its one repair: it is the model failing, not missing information, and asking the person
«¿Qué quieres consultar?» would blame them for it. There is no fallback across
models here; walking the catalogue is the provider layer's policy and lives there.

**A hosted runtime with strict decoding refuses a wrong shape after generating it.** Groq's
strict `json_schema` answers it with an HTTP 400 `json_validate_failed`, usually with what the
model wrote. The provider raises that as `AnswerRefusedError`, and it is read here exactly like an
answer that cannot be read (ruling R31): the one repair, sending back what was written when the
provider returned it, and then a question back. Any other provider failure (an outage, a budget
exhausted with no content) is still `InterpretationFailedError`, with no repair call.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Any
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Clarify,
    Compare,
    ComparePrevious,
    Delegate,
    ListMovements,
    Operation,
    Patch,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import DialogueState, Missing
from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.errors import AnswerRefusedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
    TurnOperationsPort,
)
from finguard_orchestrator.domain.services import category_words, spanish
from finguard_orchestrator.infrastructure.engine.operations_prompt import (
    MAX_SENTENCE,
    build_prompt,
    repair_note,
)
from finguard_orchestrator.infrastructure.engine.operations_schema import (
    UnreadableError,
    read,
    schema_for,
)
from finguard_orchestrator.observability.decisions import Step, Verdict, record

logger = logging.getLogger(__name__)

# Told to the model with the repair when the runtime refused its answer itself, which says only
# that the shape was wrong and not where.
_REFUSED_PROBLEM = "The answer did not match the required JSON schema; answer again matching it."
_PAYMENT_OPERATIONS = (PrepareBizum, PrepareTransfer, PrepareOwnTransfer)
_FILTER_FIELDS = (
    "categories",
    "excluded_categories",
    "accounts",
    "merchants",
    "excluded_merchants",
    "merchant_terms",
    "people",
    "excluded_counterparties",
)
_PLAN_FIELDS = frozenset(field.name for field in fields(PlanFields))
_SUBJECT_FIELDS = frozenset({"categories", "merchants", "merchant_terms", "people"})
_CLARIFICATION_REVIEW = (
    "Review your clarification against the conversation state shown in the prompt. Decide again "
    "whether this message starts a new task, modifies the active task, answers its pending "
    "question, or genuinely lacks required information. Do not ask for a direction, period, "
    "account, person, or subject already supplied for the task this message refers to. A new task "
    "must not inherit unrelated fields. Return the corrected typed operation, or keep the "
    "clarification only if a required field is truly missing."
)
_INHERITED_FILTER_REVIEW = (
    "The proposed operation starts a new task with a category, account, merchant, or person "
    "filter that another task in the conversation state already holds. Decide whether the "
    "person's current message asks for that filter, or it was carried over from the other task. "
    "A new task keeps only what this message asks for. Return the corrected typed operation, or "
    "the same one if the filter is truly asked for."
)
_WIDENED_PATCH_REVIEW = (
    "The proposed patch keeps the active task's category and adds another. Decide whether the "
    "person asks to include both, or to replace the old category with the new one. Return the "
    "corrected typed operation."
)
_RESTATED_PATCH_REVIEW = (
    "The proposed patch restates a direction and a window over a task that has subject filters. "
    "Decide whether the person continues that task or asks a new, self-contained question. For a "
    "new question use ask with only what this message asks for; keep patch only when the message "
    "refers to the existing result or task. Return the corrected typed operation."
)
_CATEGORY_MERCHANT_COLLISION_REVIEW = (
    "Review whether a merchant name is actually a generic category alias. The operation combines "
    "a category with a merchant_terms value that is one of that same category's names. Keep both "
    "only when the user named a distinct specific shop; otherwise remove the redundant generic "
    "merchant filter and preserve the category query. Do not turn a category word such as "
    "supermercado into a literal shop-name search."
)
_NEW_PAYMENT_REVIEW = (
    "This proposes preparing a new payment. Decide from the person's message whether they ask to "
    "send money now, or ask about payments already made, which is a read-only query (ask with the "
    "person and the direction) and never a payment operation. Return the corrected typed "
    "operation, or the same payment operation if they do ask to send money now."
)
_CHART_PRESENTATION_REVIEW = (
    "A chart is on screen, and the proposal changes the query behind it (a top-N, or a patch "
    "beside a redraw). Decide whether the person asks to change how the chart shows the same data "
    "(order, how many items, orientation, type, stacking), which is edit_visualization alone and "
    "keeps the query and its total, or to change what is queried, which is patch. Return the "
    "corrected typed operation."
)
_ROLLING_COMPARISON_REVIEW = (
    "compare_previous only compares an active rolling LAST_N_MONTHS query with the N months before "
    "it, and the query on screen is not one. To compare a result on screen or a named period with "
    "another period, use compare with explicit sides: from_result=N for a result on screen, a "
    "window for the other. Return the corrected typed operation."
)
_REVIEWS: Mapping[str, str] = {
    "clarification": _CLARIFICATION_REVIEW,
    "inherited_filters": _INHERITED_FILTER_REVIEW,
    "widened_patch": _WIDENED_PATCH_REVIEW,
    "restated_patch": _RESTATED_PATCH_REVIEW,
    "category_merchant_collision": _CATEGORY_MERCHANT_COLLISION_REVIEW,
    "new_payment": _NEW_PAYMENT_REVIEW,
    "chart_presentation": _CHART_PRESENTATION_REVIEW,
    "rolling_comparison": _ROLLING_COMPARISON_REVIEW,
}
# What must not run unreviewed: a payment, and a new task built on a suspect filter. A
# clarification or a patch that could not be reviewed is no worse than the model's first reading.
_HOLDS_BACK = frozenset({"inherited_filters", "new_payment"})


@dataclass(frozen=True)
class _Refused:
    """An answer the provider refused against the schema after generating it, with what was
    written when the provider returned it."""

    generation: str | None


class LlmTurnOperations(TurnOperationsPort):
    """Implements TurnOperationsPort against one model."""

    def __init__(
        self,
        provider: LlmProviderPort,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        categories: Mapping[str, str],
    ) -> None:
        """`categories` maps each catalogue key to its short Spanish name: the keys close the
        schema, the names are the prompt's glossary."""
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._categories = dict(categories)
        self._schema = schema_for(self._categories)

    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        if len(sentence) > MAX_SENTENCE:
            # Past what the prompt's budget holds (ruling R16): asked back, not sent truncated.
            record(Step.PLANNER, Verdict.DECLINED, changed=False, detail="sentence_too_long")
            return (Clarify(Missing.SUBJECT),)
        prompt = build_prompt(
            sentence,
            state,
            today=today,
            categories=self._categories,
            account_names=account_names,
        )
        try:
            answer = await self._complete(prompt)
        except InterpretationFailedError:
            record(Step.PLANNER, Verdict.FAILED, changed=False, detail="model_error")
            raise
        repair_used = False
        try:
            operations = self._read(answer)
        except UnreadableError as refused:
            # The log gets types and paths; only the model, asked again, gets what it wrote.
            logger.warning("engine.operations_refused problem=%s", refused.logged)
            written = answer.generation or "" if isinstance(answer, _Refused) else answer
            try:
                answer = await self._complete(prompt + repair_note(written, refused.problem))
            except InterpretationFailedError:
                record(Step.PLANNER, Verdict.FAILED, changed=False, detail="model_error")
                raise
            repair_used = True
            try:
                operations = self._read(answer)
            except UnreadableError as refused_again:
                # Two answers the schema cannot read are the model failing, not the person being
                # unclear: answered as an outage, nothing saved, any open question left as it was.
                # Neither the person's words nor the model's reach the error: only that it failed.
                logger.warning("engine.operations_unreadable problem=%s", refused_again.logged)
                record(Step.PLANNER, Verdict.FAILED, changed=False, detail="unreadable_twice")
                raise InterpretationFailedError(
                    "the interpreter's answer could not be read twice"
                ) from None
        # One reading per turn, whatever it took to get it: the repair call is the same reading
        # asked for again, not a second decision.
        record(
            Step.PLANNER,
            Verdict.AGREED,
            changed=False,
            now=_types(operations),
            detail="repaired" if repair_used else self._model,
        )
        held = _held_by_tasks(state, account_names)
        suspicions = _suspicions(operations, state, held, self._categories)
        semantic_reviewed = False
        read_first = operations
        if suspicions and not repair_used:
            # One same-schema review. The model remains the semantic authority: what it answers
            # on review is the reading, and nothing here rewrites it.
            logger.info(
                "engine.operations_semantic_review started=true reasons=%s", ",".join(suspicions)
            )
            note = "; ".join(_REVIEWS[reason] for reason in suspicions)
            written = answer.generation or "" if isinstance(answer, _Refused) else answer
            try:
                reviewed = await self._complete(prompt + repair_note(written, note))
                operations = self._read(reviewed)
                semantic_reviewed = True
            except (InterpretationFailedError, UnreadableError) as failed:
                logger.warning(
                    "engine.operations_semantic_review_unavailable error=%s",
                    type(failed).__name__,
                )
                operations = _held_back(operations, suspicions, held)
        elif suspicions:
            # An unreadable first answer already used the one extra call this turn has. A reading
            # that needed a review does not run without one.
            logger.warning(
                "engine.operations_semantic_review_skipped_budget_exhausted reasons=%s",
                ",".join(suspicions),
            )
            operations = _held_back(operations, suspicions, held)
        if suspicions:
            _record_review(read_first, operations, suspicions, reviewed=semantic_reviewed)
        clarifications = tuple(op for op in operations if isinstance(op, Clarify))
        patches = tuple(op for op in operations if isinstance(op, Patch))
        logger.info(
            "engine.operations_read operation_count=%d operation_types=%s "
            "clarification_missing=%s clarification_draft_fields=%s patch_targets=%s "
            "has_focus=%s has_pending=%s semantic_reviewed=%s",
            len(operations),
            ",".join(type(operation).__name__ for operation in operations),
            ",".join(item.missing.value for item in clarifications) or "none",
            ",".join("+".join(sorted(item.draft.named())) or "none" for item in clarifications)
            or "none",
            ",".join(str(item.target) for item in patches) or "none",
            state.focus is not None,
            state.pending is not None,
            semantic_reviewed,
        )
        return operations

    def _read(self, answer: "str | _Refused") -> tuple[Operation, ...]:
        if isinstance(answer, _Refused):
            raise UnreadableError(_REFUSED_PROBLEM, "refused_by_the_provider_against_the_schema")
        return read(answer, self._categories)

    async def _complete(self, prompt: str) -> "str | _Refused":
        schema: dict[str, Any] = self._schema
        try:
            return await self._provider.complete(
                prompt,
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=schema,
            )
        except AnswerRefusedError as refused:
            # The model answered, in a shape the runtime refused after generating it: an answer to
            # repair, not an outage (ruling R31).
            return _Refused(refused.generation)
        except Exception as failed:
            # Whatever the adapter raised, the turn has no reading; the handler needs one error to
            # report it by, not one per runtime. Named by its type and nothing else, and not
            # chained: a hosted runtime's error can carry the generation it refused, which can be
            # the person's sentence, and a traceback logged upstream would print it.
            raise InterpretationFailedError(
                f"the interpreter's model failed: {type(failed).__name__}"
            ) from None


def _types(operations: tuple[Operation, ...]) -> str:
    """The reading as its operation types -- the shape of a decision, never its values."""
    return "+".join(type(operation).__name__ for operation in operations) or "none"


def _record_review(
    first: tuple[Operation, ...],
    final: tuple[Operation, ...],
    suspicions: tuple[str, ...],
    *,
    reviewed: bool,
) -> None:
    """What the review of a suspect reading did to it.

    `overrode` only when the reading that runs is not the one first read: the model's second
    reading replaced it, or the rules held it back as a question. A review that confirmed the
    first reading, or a held-back reading the rules let stand, is `agreed`.
    """
    changed = final != first
    reasons = suspicions[0] if len(suspicions) == 1 else f"{suspicions[0]}+{len(suspicions) - 1}"
    detail = f"{'review' if reviewed else 'held_back'}:{reasons}"
    was, now = _types(first), _types(final)
    record(
        Step.PLAN_VERIFIER,
        Verdict.OVERRODE if changed else Verdict.AGREED,
        changed=changed,
        field="reading",
        # Only when the types say it; a reading whose filters changed under the same types is
        # changed with nothing to show for it here, rather than was == now beside changed=True.
        was=was if was != now else None,
        now=now if was != now else None,
        detail=detail,
    )


def _suspicions(
    operations: tuple[Operation, ...],
    state: DialogueState,
    held: Mapping[str, set[str]],
    categories: Mapping[str, str],
) -> tuple[str, ...]:
    """Why this reading deserves a second look, read from its shape and the state alone."""
    found: list[str] = []
    task_on_the_table = (
        state.focus is not None or state.pending is not None or bool(state.suspended_pending)
    )
    if task_on_the_table and any(isinstance(operation, Clarify) for operation in operations):
        found.append("clarification")
    if any(_inherited_values(operation, held) for operation in operations):
        found.append("inherited_filters")
    if any(_widens_the_active_categories(operation, state) for operation in operations):
        found.append("widened_patch")
    if any(_restates_a_filtered_task(operation, state) for operation in operations):
        found.append("restated_patch")
    if _category_named_as_merchant(operations, state, categories):
        found.append("category_merchant_collision")
    if _opens_a_payment(operations, state):
        found.append("new_payment")
    if _queries_where_a_chart_is_shown(operations, state):
        found.append("chart_presentation")
    if any(_compares_a_window_not_on_screen(operation, state) for operation in operations):
        found.append("rolling_comparison")
    return tuple(found)


def _held_back(
    operations: tuple[Operation, ...],
    suspicions: tuple[str, ...],
    held: Mapping[str, set[str]],
) -> tuple[Operation, ...]:
    """A reading that needed a review and did not get one.

    A payment is never prepared on it, and a new ask is not run: the question keeps every field
    except the suspect values, which are the ones another task holds. Two or more suspect
    categories are catalogue keys, a closed set, and are offered back as the choice. Anything else
    stands as the model read it.
    """
    if not _HOLDS_BACK.intersection(suspicions):
        return operations
    ask = next((operation for operation in operations if isinstance(operation, Ask)), None)
    # One line per reading held back, so how many turns end in a question instead of a query can
    # be counted from the logs.
    logger.info(
        "engine.operations_held_back reasons=%s operation_types=%s kept_draft=%s",
        ",".join(suspicions),
        ",".join(type(operation).__name__ for operation in operations),
        ask is not None and "new_payment" not in suspicions,
    )
    if ask is None or "new_payment" in suspicions:
        return (Clarify(Missing.SUBJECT),)
    suspect = _inherited_values(ask, held)
    kept: dict[str, Any] = {}
    for field_name in _FILTER_FIELDS:
        values = getattr(ask.fields, field_name, None)
        if field_name not in _PLAN_FIELDS or not values:
            continue
        remaining = tuple(value for value in values if (field_name, value) not in suspect)
        kept[field_name] = remaining or None
    doubtful = tuple(value for field_name, value in suspect if field_name == "categories")
    draft = replace(ask.fields, **kept)
    return (
        Clarify(Missing.SUBJECT, candidates=doubtful if len(doubtful) > 1 else (), draft=draft),
    )


def _held_by_tasks(state: DialogueState, account_names: Mapping[UUID, str]) -> dict[str, set[str]]:
    """Every filter value a task on the table holds, folded: the screen's, the open question's and
    the suspended ones'. Values only, from the state; the sentence is not read."""
    held: dict[str, set[str]] = {field_name: set() for field_name in _FILTER_FIELDS}
    plans: list[object] = list(_focus_plans(state))
    plans.extend(pending.draft for pending in (state.pending, *state.suspended_pending) if pending)
    account_ids: list[UUID] = list(state.scope.account_ids)
    for pending in (state.pending, *state.suspended_pending):
        if pending is not None:
            account_ids.extend(pending.scope.account_ids)
    for plan in plans:
        for field_name in (
            "categories",
            "excluded_categories",
            "merchants",
            "excluded_merchants",
            "merchant_terms",
            "accounts",
            "excluded_counterparties",
        ):
            held[field_name].update(_folded(getattr(plan, field_name, None)))
        held["people"].update(_folded(getattr(plan, "people", None)))
        held["people"].update(_folded(getattr(plan, "counterparties", None)))
        account_ids.extend(getattr(plan, "account_ids", ()) or ())
        account_ids.extend(getattr(plan, "only_account_ids", ()) or ())
        if (only := getattr(plan, "only_account_id", None)) is not None:
            account_ids.append(only)
    held["accounts"].update(
        spanish.folded(label)
        for account_id in account_ids
        if (label := account_names.get(account_id))
    )
    return held


def _folded(values: object) -> set[str]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        return set()
    return {spanish.folded(value) for value in values if isinstance(value, str)}


def _inherited_values(operation: Operation, held: Mapping[str, set[str]]) -> list[tuple[str, str]]:
    """The filter values a new task repeats from a task on the table. Only new tasks: a patch or a
    reply to a question continues its task and is meant to keep its filters."""
    bundles: tuple[object, ...]
    if isinstance(operation, Ask):
        bundles = (operation.fields,)
    elif isinstance(operation, ListMovements) and not operation.continue_current:
        bundles = (operation,)
    elif isinstance(operation, Compare) and _starts_fresh_comparison(operation):
        bundles = (operation.shared, operation.earlier, operation.latest)
    else:
        return []
    found: list[tuple[str, str]] = []
    for bundle in bundles:
        for field_name in _FILTER_FIELDS:
            for value in getattr(bundle, field_name, None) or ():
                if isinstance(value, str) and spanish.folded(value) in held.get(field_name, ()):
                    found.append((field_name, value))
    return found


def _widens_the_active_categories(operation: Operation, state: DialogueState) -> bool:
    """A patch of the screen that keeps its categories and adds another: replacing «restaurantes»
    by «ocio» and keeping both look the same until the model is asked which it meant."""
    if not isinstance(operation, Patch) or operation.target != "focus":
        return False
    proposed = set(operation.changes.categories or ())
    active = _active_categories(state)
    return bool(active) and active < proposed


def _restates_a_filtered_task(operation: Operation, state: DialogueState) -> bool:
    """A patch that brings a direction and a window of its own, over a task with subject filters:
    the shape of a new, complete question read as a continuation that keeps those filters."""
    if not isinstance(operation, Patch) or operation.target != "focus":
        return False
    changes = operation.changes
    if changes.direction is None or changes.window is None:
        return False
    if changes.named() & _SUBJECT_FIELDS:
        return False
    return any(
        getattr(plan, attribute, None)
        for plan in _focus_plans(state)
        for attribute in ("categories", "merchants", "merchant_terms", "counterparties")
    )


def _category_named_as_merchant(
    operations: tuple[Operation, ...], state: DialogueState, categories: Mapping[str, str]
) -> bool:
    """A merchant search term that is one of a selected category's own names («super» beside
    GROCERIES), compared as values against the catalogue's names; no sentence is read."""
    active = _active_categories(state)
    for operation in operations:
        bundles: tuple[object, ...]
        selected: set[str]
        if isinstance(operation, Ask):
            bundles, selected = (operation.fields,), set()
        elif isinstance(operation, Patch):
            bundles = (operation.changes,)
            selected = set(active) if operation.target == "focus" else set()
        elif isinstance(operation, Compare):
            bundles = (operation.shared, operation.earlier, operation.latest)
            selected = set() if _starts_fresh_comparison(operation) else set(active)
        elif isinstance(operation, ListMovements):
            bundles, selected = (operation,), set()
        else:
            continue
        terms: set[str] = set()
        for bundle in bundles:
            selected.update(
                value for value in getattr(bundle, "categories", None) or () if value in categories
            )
            terms |= _folded(getattr(bundle, "merchant_terms", None))
        if terms & {name for category in selected for name in _names_of(category, categories)}:
            return True
    return False


def _names_of(category: str, categories: Mapping[str, str]) -> set[str]:
    """What the catalogue calls a category, folded: its key, its name, its glossary entries, and
    each word of those longer than an article («el súper» is also «super»)."""
    phrases = {category, category_words.name_of(category), categories.get(category, "")}
    gloss = category_words.words_for(category)
    if gloss:
        phrases.update(gloss.split(", "))
    folded = {spanish.folded(phrase).strip() for phrase in phrases if phrase}
    return folded | {word for phrase in folded for word in phrase.split() if len(word) > 3}


# What a top-N patch moves: its limit, and the result shape that limit reconciles.
_LIMIT_ONLY = frozenset({"limit", "clear_limit", "result_kind"})


def _queries_where_a_chart_is_shown(
    operations: tuple[Operation, ...], state: DialogueState
) -> bool:
    """A chart is on screen and the proposal re-queries it: a focus patch that only moves the
    top-N, or any focus patch beside a redraw. «Solo las cinco mayores» can mean either."""
    if state.active_visual is None:
        return False
    patches = [
        operation
        for operation in operations
        if isinstance(operation, Patch) and operation.target == "focus"
    ]
    if not patches:
        return False
    redraws = any(isinstance(operation, Visualize) for operation in operations)
    return redraws or any(patch.changes.named() <= _LIMIT_ONLY for patch in patches)


def _compares_a_window_not_on_screen(operation: Operation, state: DialogueState) -> bool:
    """compare_previous=N where the screen is not a rolling LAST_N_MONTHS of that N."""
    if not isinstance(operation, ComparePrevious):
        return False
    period = getattr(getattr(state.focus, "plan", None), "period", None)
    return (
        period is None or period.period is not Period.LAST_N_MONTHS or period.n != operation.months
    )


def _opens_a_payment(operations: tuple[Operation, ...], state: DialogueState) -> bool:
    """A payment operation, or a delegation into one, while no payment is open. A reply that
    completes or corrects an open payment is that payment's business, not a new one."""
    if state.payment is not None or state.own_transfer is not None:
        return False
    return any(
        isinstance(operation, _PAYMENT_OPERATIONS)
        or (isinstance(operation, Delegate) and operation.to in {"PAYMENT", "TRANSFER"})
        for operation in operations
    )


def _starts_fresh_comparison(operation: Compare) -> bool:
    """Mirror the domain transition's boundary between a new pair and a follow-up comparison.

    A complete pair with a stated subject owns its filters. It may inherit the active direction,
    but no prior category, merchant, or person filter. A one-sided comparison is a follow-up and
    keeps the active task's compatible scope.
    """
    earlier = operation.shared.merged(operation.earlier)
    latest = operation.shared.merged(operation.latest)
    has_subject = any(
        side.named() - {"window"}
        for side in (operation.shared, operation.earlier, operation.latest)
    )
    return earlier.window is not None and latest.window is not None and has_subject


def _focus_plans(state: DialogueState) -> list[object]:
    """The plans the screen's task holds: its query, both sides of a pair, a ratio's two parts."""
    focus = state.focus
    if focus is None:
        return []
    plans: list[object] = [plan for plan in (getattr(focus, "plan", None),) if plan is not None]
    comparison = getattr(focus, "comparison", None)
    if comparison is not None:
        plans.extend((comparison.earlier, comparison.latest))
    ratio = getattr(focus, "ratio", None)
    if ratio is not None:
        plans.extend((ratio.part, ratio.whole))
    return plans


def _active_categories(state: DialogueState) -> set[str]:
    return {
        value
        for plan in _focus_plans(state)
        for value in getattr(plan, "categories", ()) or ()
        if isinstance(value, str)
    }
