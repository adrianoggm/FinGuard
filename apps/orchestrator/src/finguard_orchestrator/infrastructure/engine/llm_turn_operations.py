"""Reads one turn with a model, held to the closed operations schema.

One normal call per turn, plus at most one bounded review: for unreadable output, a clarification
that may discard an active task, risky filter carryover, or a payment proposal that conflicts with
an explicit past-transaction question. A repeated mismatch becomes `clarify`; it never reaches
payment preparation.

A provider that cannot answer at all is different: no question to the person fixes it, so it is
raised as `InterpretationFailedError` for the handler to report. There is no fallback across
models here; walking the catalogue is the provider layer's policy and lives there.

**A hosted runtime with strict decoding refuses a wrong shape after generating it.** Groq's
strict `json_schema` answers it with an HTTP 400 `json_validate_failed`, usually with what the
model wrote. The provider raises that as `AnswerRefusedError`, and it is read here exactly like an
answer that cannot be read (ruling R31): the one repair, sending back what was written when the
provider returned it, and then a question back. Any other provider failure (an outage, a budget
exhausted with no content) is still `InterpretationFailedError`, with no repair call.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    Clarify,
    Compare,
    Delegate,
    ListMovements,
    Operation,
    Patch,
    PatchMovementList,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
)
from finguard_orchestrator.domain.engine.state import DialogueState, Missing, MovementListFocus
from finguard_orchestrator.domain.errors import AnswerRefusedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
    TurnOperationsPort,
)
from finguard_orchestrator.domain.services import category_words, spanish, turn_arbiter
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

logger = logging.getLogger(__name__)

# Told to the model with the repair when the runtime refused its answer itself, which says only
# that the shape was wrong and not where.
_REFUSED_PROBLEM = "The answer did not match the required JSON schema; answer again matching it."
_PAST_PAYMENT = re.compile(
    r"\b(?:envie|enviaste|envio|enviaron|enviado|mande|mandaste|mando|mandaron|mandado|"
    r"pague|pagaste|pago|pagaron|pagado|hice|hiciste|hizo|hicieron|transferi|transferiste|"
    r"transfirio|transfirieron|transferido)\b"
)
_CURRENT_PAYMENT_REQUEST = re.compile(
    r"\b(?:envia(?:me|le|les)?|manda(?:me|le|les)?|haz(?:me|le)?|realiza|prepara|paga|"
    r"transfiere)\b|"
    r"\b(?:quiero|quisiera|me gustaria|voy a|vas a|puedes|podrias|quieres que|quiero que)\s+"
    r"(?:enviar|mandar|hacer|transferir|preparar|pagar|envie|mande|haga|transfiera)\b"
)
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
_PAST_PAYMENT_MISREAD = (
    "This proposes a new payment, but the person's sentence asks about a past transaction. "
    "Interpret it as a read-only financial query using ask or patch; do not prepare a payment."
)
_CLARIFICATION_REVIEW = (
    "Review your clarification against the conversation state shown in the prompt. Decide again "
    "whether this message starts a new task, modifies the active task, answers its pending "
    "question, or genuinely lacks required information. Do not ask for a direction, period, "
    "account, person, or subject already supplied for the task this message refers to. A new task "
    "must not inherit unrelated fields. Return the corrected typed operation, or keep the "
    "clarification only if a required field is truly missing."
)
_UNANCHORED_FILTER_REVIEW = (
    "Review the proposed operation against the person's current words and the conversation state. "
    "It introduces one or more category, account, merchant, or person filters that are not stated "
    "in this message. Decide whether those values are truly implied by this turn or were carried "
    "over from an unrelated task. A self-contained new task starts without filters the person did "
    "not mention. A follow-up keeps compatible filters from its owning task, but must not broaden "
    "or replace them without a user instruction. Do not invent a filter to represent a vague word; "
    "use the correct typed operation for account scope, task continuation, or clarification. "
    "Return "
    "the corrected operation, or ask one focused clarification if the intended scope is ambiguous."
)
_COMPLETE_QUESTION_PATCH_REVIEW = (
    "The current message states a complete financial question, but the proposed operation patches "
    "a previously filtered task. Decide again whether the user is continuing that task or asking "
    "a new question. For a new question, use ask and include only filters stated in this message; "
    "do not carry over an old category, merchant, person, or account filter. Keep patch only when "
    "the wording clearly refers to the existing result or task. "
    "Return the corrected typed operation."
)
_CATEGORY_MERCHANT_COLLISION_REVIEW = (
    "Review whether a merchant name is actually a generic category alias. The operation combines "
    "a category with a merchant_terms value that itself names that same category. Keep both only "
    "when the user named a distinct specific shop; otherwise remove the redundant generic merchant "
    "filter and preserve the category query. Do not turn a category word such as supermercado into "
    "a literal shop-name search."
)


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
            return (Clarify(Missing.SUBJECT),)
        prompt = build_prompt(
            sentence,
            state,
            today=today,
            categories=self._categories,
            account_names=account_names,
        )
        answer = await self._complete(prompt)
        repair_used = False
        try:
            operations = self._read(answer)
        except UnreadableError as refused:
            # The log gets types and paths; only the model, asked again, gets what it wrote.
            logger.warning("engine.operations_refused problem=%s", refused.logged)
            written = answer.generation or "" if isinstance(answer, _Refused) else answer
            answer = await self._complete(prompt + repair_note(written, refused.problem))
            repair_used = True
            try:
                operations = self._read(answer)
            except UnreadableError as refused_again:
                logger.warning("engine.operations_unreadable problem=%s", refused_again.logged)
                return (Clarify(Missing.SUBJECT),)
        semantic_reviewed = False
        clarification_needs_review = (
            state.focus is not None or state.pending is not None or bool(state.suspended_pending)
        ) and any(isinstance(operation, Clarify) for operation in operations)
        unanchored_filters = _has_unanchored_filter_values(
            operations,
            sentence,
            state,
            categories=self._categories,
            account_names=account_names,
        )
        complete_question_patch = _has_complete_question_patch(
            operations,
            sentence,
            state,
            today=today,
            categories=self._categories,
            account_names=account_names,
        )
        category_merchant_collision = _has_category_alias_merchant_collision(
            operations, state, categories=self._categories
        )
        if not repair_used and (
            clarification_needs_review
            or unanchored_filters
            or complete_question_patch
            or category_merchant_collision
        ):
            # A single same-schema review handles an unnecessary clarification, a proposed scope
            # expansion, or a complete question represented as a patch over prior filters. The
            # model remains the semantic authority: this layer never rewrites a valid operation.
            # If a grounding or complete-question review cannot run, its risky interpretation is
            # not safe to execute and is replaced with a focused clarification below.
            review_note = "; ".join(
                note
                for condition, note in (
                    (clarification_needs_review, _CLARIFICATION_REVIEW),
                    (unanchored_filters, _UNANCHORED_FILTER_REVIEW),
                    (complete_question_patch, _COMPLETE_QUESTION_PATCH_REVIEW),
                    (category_merchant_collision, _CATEGORY_MERCHANT_COLLISION_REVIEW),
                )
                if condition
            )
            logger.info(
                "engine.operations_semantic_review started=true clarification=%s "
                "unanchored_filters=%s complete_question_patch=%s "
                "category_merchant_collision=%s",
                clarification_needs_review,
                unanchored_filters,
                complete_question_patch,
                category_merchant_collision,
            )
            try:
                reviewed = await self._complete(prompt + repair_note(answer, review_note))
            except InterpretationFailedError as failed:
                logger.warning(
                    "engine.operations_semantic_review_unavailable error=%s",
                    type(failed).__name__,
                )
                if unanchored_filters or complete_question_patch or category_merchant_collision:
                    operations = (Clarify(Missing.SUBJECT),)
            else:
                try:
                    operations = self._read(reviewed)
                    semantic_reviewed = True
                    still_unanchored = _has_unanchored_filter_values(
                        operations,
                        sentence,
                        state,
                        categories=self._categories,
                        account_names=account_names,
                    )
                    still_complete_question_patch = _has_complete_question_patch(
                        operations,
                        sentence,
                        state,
                        today=today,
                        categories=self._categories,
                        account_names=account_names,
                    )
                    still_colliding = _has_category_alias_merchant_collision(
                        operations, state, categories=self._categories
                    )
                    if still_unanchored or still_complete_question_patch or still_colliding:
                        # The review is bounded. Do not let a second valid-looking but still
                        # contradictory interpretation reach Banking; ask instead of guessing.
                        logger.warning(
                            "engine.operations_semantic_review_rejected "
                            "unanchored_filters=%s complete_question_patch=%s "
                            "category_merchant_collision=%s",
                            still_unanchored,
                            still_complete_question_patch,
                            still_colliding,
                        )
                        operations = (Clarify(Missing.SUBJECT),)
                except UnreadableError as refused_review:
                    logger.warning(
                        "engine.operations_semantic_review_unreadable problem=%s",
                        refused_review.logged,
                    )
                    if unanchored_filters or complete_question_patch or category_merchant_collision:
                        operations = (Clarify(Missing.SUBJECT),)
            repair_used = True
        elif repair_used and (
            unanchored_filters or complete_question_patch or category_merchant_collision
        ):
            # An unreadable first answer may already have consumed the one allowed repair. In
            # that path, a valid but still ungrounded repair must not reach Banking without the
            # semantic review that the budget no longer permits.
            logger.warning(
                "engine.operations_semantic_review_skipped_budget_exhausted "
                "unanchored_filters=%s complete_question_patch=%s "
                "category_merchant_collision=%s",
                unanchored_filters,
                complete_question_patch,
                category_merchant_collision,
            )
            operations = (Clarify(Missing.SUBJECT),)
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
        if not _past_payment_was_misread(sentence, operations):
            return operations

        # A plausible payment operation is still unsafe when the sentence asks about a completed
        # transaction. Give the planner one structured correction chance; if it repeats the
        # mismatch, stop before any payment or contact-resolution node sees the operation.
        logger.warning("engine.operations_consistency_mismatch problem=past_activity_as_payment")
        if repair_used:
            return (Clarify(Missing.SUBJECT),)
        written = answer.generation or "" if isinstance(answer, _Refused) else answer
        repaired = await self._complete(prompt + repair_note(written, _PAST_PAYMENT_MISREAD))
        try:
            operations = self._read(repaired)
        except UnreadableError as refused:
            logger.warning(
                "engine.operations_consistency_repair_unreadable problem=%s", refused.logged
            )
            return (Clarify(Missing.SUBJECT),)
        if _past_payment_was_misread(sentence, operations):
            logger.warning(
                "engine.operations_consistency_rejected problem=past_activity_as_payment"
            )
            return (Clarify(Missing.SUBJECT),)
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


def _past_payment_was_misread(sentence: str, operations: tuple[Operation, ...]) -> bool:
    """Detect a payment proposal for a past-tense transaction without authorizing or routing
    anything. The check can only trigger one bounded repair or a safe clarification."""
    proposes_payment = any(
        isinstance(operation, _PAYMENT_OPERATIONS)
        or (isinstance(operation, Delegate) and operation.to in {"PAYMENT", "TRANSFER"})
        for operation in operations
    )
    if not proposes_payment:
        return False
    words = spanish.folded(sentence)
    return bool(_PAST_PAYMENT.search(words)) and not bool(_CURRENT_PAYMENT_REQUEST.search(words))


def _has_complete_question_patch(
    operations: tuple[Operation, ...],
    sentence: str,
    state: DialogueState,
    *,
    today: date,
    categories: Mapping[str, str],
    account_names: Mapping[UUID, str],
) -> bool:
    """Review an LLM patch when a complete question could accidentally carry old filters.

    This does not decide whether the person meant a new task. It only recognizes a risky
    combination of the model's typed patch, a self-contained turn, and an existing filter, then
    lets the same planner reconsider that semantic choice before any banking read.
    """
    if state.focus is None or not turn_arbiter.states_a_question_of_its_own(sentence, today=today):
        return False
    for operation in operations:
        if not isinstance(operation, Patch) or operation.target != "focus":
            continue
        current = _current_filter_values(state, account_names, operation, categories=categories)
        if any(current.values()):
            return True
    return False


def _has_unanchored_filter_values(
    operations: tuple[Operation, ...],
    sentence: str,
    state: DialogueState,
    *,
    categories: Mapping[str, str],
    account_names: Mapping[UUID, str],
) -> bool:
    """Whether an operation adds a filter absent from the turn and its owning task.

    This is a trigger for a model review, not a phrase parser: the planner still decides what an
    ambiguous phrase means. It catches cross-task filter leakage before a valid but broader query
    reaches the bank, including a fresh ask that accidentally copied a prior merchant/category.
    """
    words = spanish.folded(sentence)
    mentioned_categories = set(category_words.named_in(sentence))
    for operation in operations:
        bundles: tuple[object, ...]
        inherits_task = isinstance(operation, (Patch, PatchMovementList)) or (
            isinstance(operation, Compare) and not _starts_fresh_comparison(operation)
        )
        current = (
            _current_filter_values(state, account_names, operation, categories=categories)
            if inherits_task
            else {}
        )
        if isinstance(operation, (Ask, Patch)):
            bundles = (operation.fields if isinstance(operation, Ask) else operation.changes,)
        elif isinstance(operation, Compare):
            bundles = (operation.shared, operation.earlier, operation.latest)
        elif isinstance(operation, (ListMovements, PatchMovementList)):
            bundles = (operation,)
        elif isinstance(operation, Balance):
            bundles = (operation,)
        else:
            continue
        for bundle in bundles:
            for field_name in _FILTER_FIELDS:
                values = getattr(bundle, field_name, None)
                if not values:
                    continue
                if field_name == "categories":
                    proposed_categories = _category_ids(values, categories)
                    current_categories = {
                        value for value in current.get("categories", set()) if value in categories
                    }
                    carried_unstated = proposed_categories.intersection(
                        current_categories
                    ).difference(mentioned_categories)
                    added_stated = proposed_categories.difference(current_categories).intersection(
                        mentioned_categories
                    )
                    if carried_unstated and added_stated:
                        # A newly named category replaces an old category filter. Keeping both
                        # would widen the query even though each value is individually valid.
                        return True
                for value in values:
                    if not isinstance(value, str) or not value.strip():
                        continue
                    normalized_values = {spanish.folded(value)}
                    if field_name in {"categories", "excluded_categories"}:
                        category_ids = _category_ids((value,), categories)
                        if mentioned_categories.intersection(category_ids):
                            continue
                        normalized_values.update(category_ids)
                    if normalized_values.intersection(current.get(field_name, set())):
                        continue
                    surfaces = [value]
                    if field_name in {"categories", "excluded_categories"}:
                        label = categories.get(value)
                        if label:
                            surfaces.append(label)
                    if field_name == "accounts" and _account_name_is_anchored(
                        value, words, account_names
                    ):
                        continue
                    if not any(_surface_in_words(surface, words) for surface in surfaces):
                        return True
    return False


def _has_category_alias_merchant_collision(
    operations: tuple[Operation, ...],
    state: DialogueState,
    *,
    categories: Mapping[str, str],
) -> bool:
    """Catch a category's own alias duplicated as a literal merchant filter.

    This is a review trigger, not a deterministic rewrite: the model decides whether the user
    meant a category or a particular shop. It prevents a generic word such as ``super`` from
    silently narrowing a GROCERIES query to ledger names containing that word.
    """
    active_categories = _active_category_ids(state, categories)
    for operation in operations:
        bundles: tuple[object, ...]
        inherited_categories: set[str] = set()
        if isinstance(operation, Ask):
            bundles = (operation.fields,)
        elif isinstance(operation, Patch):
            bundles = (operation.changes,)
            if operation.target == "focus":
                inherited_categories = active_categories
            elif operation.target in {"earlier", "latest"} and state.focus is not None:
                comparison = getattr(state.focus, "comparison", None)
                side = (
                    getattr(comparison, operation.target, None) if comparison is not None else None
                )
                if side is not None:
                    inherited_categories = _category_ids(
                        getattr(side, "categories", ()) or (), categories
                    )
            elif operation.target == "pending" and state.pending is not None:
                inherited_categories = _category_ids(
                    state.pending.draft.categories or (), categories
                )
            elif operation.target == "suspended":
                pending = next(
                    (item for item in state.suspended_pending if item.task_id == operation.task_id),
                    None,
                )
                if pending is not None:
                    inherited_categories = _category_ids(pending.draft.categories or (), categories)
        elif isinstance(operation, Compare):
            bundles = (operation.shared, operation.earlier, operation.latest)
            if not _starts_fresh_comparison(operation):
                inherited_categories = active_categories
        elif isinstance(operation, ListMovements):
            bundles = (operation,)
        elif isinstance(operation, PatchMovementList):
            bundles = (operation,)
            inherited_categories = active_categories
        else:
            continue

        selected_categories = set(inherited_categories)
        merchant_terms: set[str] = set()
        for bundle in bundles:
            selected_categories.update(
                _category_ids(getattr(bundle, "categories", ()) or (), categories)
            )
            merchant_terms.update(
                value
                for value in (getattr(bundle, "merchant_terms", ()) or ())
                if isinstance(value, str)
            )
        for merchant_term in merchant_terms:
            named_categories = selected_categories.intersection(
                category_words.precisely_named_in(merchant_term)
            )
            if any(
                category_words.is_precise_alias(merchant_term, category)
                for category in named_categories
            ):
                return True
    return False


def _starts_fresh_comparison(operation: Compare) -> bool:
    """Mirror the domain transition's boundary between a new pair and a follow-up comparison.

    A complete pair with a stated subject owns its filters. It may inherit the active direction,
    but no prior category, merchant, or person filter. A one-sided comparison is a follow-up and
    keeps the active task's compatible scope.
    """
    earlier = operation.shared.merged(operation.earlier)
    latest = operation.shared.merged(operation.latest)
    has_subject = any(
        fields.named() - {"window"}
        for fields in (operation.shared, operation.earlier, operation.latest)
    )
    return earlier.window is not None and latest.window is not None and has_subject


def _active_category_ids(state: DialogueState, categories: Mapping[str, str]) -> set[str]:
    plans: list[object] = []
    focus = state.focus
    if focus is not None:
        plan = getattr(focus, "plan", None)
        if plan is not None:
            plans.append(plan)
        comparison = getattr(focus, "comparison", None)
        if comparison is not None:
            plans.extend((comparison.earlier, comparison.latest))
        ratio = getattr(focus, "ratio", None)
        if ratio is not None:
            plans.extend((ratio.part, ratio.whole))
    return {
        category
        for plan in plans
        for category in _category_ids(getattr(plan, "categories", ()) or (), categories)
    }


def _category_ids(values: object, categories: Mapping[str, str]) -> set[str]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        return set()
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        if value in categories:
            result.add(value)
        else:
            result.update(category_words.precisely_named_in(value))
    return result


def _current_filter_values(
    state: DialogueState,
    account_names: Mapping[UUID, str],
    operation: Operation,
    *,
    categories: Mapping[str, str],
) -> dict[str, set[str]]:
    """Return only filters owned by the task the operation explicitly continues."""
    values: dict[str, set[str]] = {field_name: set() for field_name in _FILTER_FIELDS}
    plans: list[object] = []
    scopes = []
    if isinstance(operation, Patch) and operation.target == "pending":
        if state.pending is not None:
            plans.append(state.pending.draft)
            scopes.append(state.pending.scope)
    elif isinstance(operation, Patch) and operation.target == "suspended":
        pending = next(
            (item for item in state.suspended_pending if item.task_id == operation.task_id),
            None,
        )
        if pending is not None:
            plans.append(pending.draft)
            scopes.append(pending.scope)
    else:
        focus = state.focus
        if isinstance(operation, PatchMovementList):
            if isinstance(focus, MovementListFocus):
                plans.append(focus.plan)
        elif focus is not None:
            plan = getattr(focus, "plan", None)
            if plan is not None:
                plans.append(plan)
            comparison = getattr(focus, "comparison", None)
            if comparison is not None:
                plans.extend((comparison.earlier, comparison.latest))
            ratio = getattr(focus, "ratio", None)
            if ratio is not None:
                plans.extend((ratio.part, ratio.whole))
        scopes.append(state.scope)
    for plan in plans:
        for field_name in (
            "categories",
            "excluded_categories",
            "accounts",
            "merchants",
            "excluded_merchants",
            "merchant_terms",
        ):
            for value in getattr(plan, field_name, ()) or ():
                if isinstance(value, str):
                    values[field_name].add(spanish.folded(value))
                    if field_name in {"categories", "excluded_categories"}:
                        values[field_name].update(_category_ids((value,), categories))
        for field_name, attribute in (
            ("people", "people"),
            ("people", "counterparties"),
            ("excluded_counterparties", "excluded_counterparties"),
        ):
            for value in getattr(plan, attribute, ()) or ():
                if isinstance(value, str):
                    values[field_name].add(spanish.folded(value))
        account_ids = list(getattr(plan, "account_ids", ()) or ())
        account_ids.extend(getattr(plan, "only_account_ids", ()) or ())
        only_account_id = getattr(plan, "only_account_id", None)
        if only_account_id is not None:
            account_ids.append(only_account_id)
        for account_id in account_ids:
            label = account_names.get(account_id)
            if label:
                values["accounts"].add(spanish.folded(label))
    for scope in scopes:
        for account_id in scope.account_ids:
            label = account_names.get(account_id)
            if label:
                values["accounts"].add(spanish.folded(label))
    return values


def _surface_in_words(surface: str, words: str) -> bool:
    normalized = spanish.folded(surface).strip()
    return (
        bool(normalized) and re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", words) is not None
    )


def _account_name_is_anchored(value: str, words: str, account_names: Mapping[UUID, str]) -> bool:
    """Accept a distinctive partial account name only when it identifies one catalogue entry.

    Banking grounding resolves whole-word account-name fragments. Mirror its useful shorthand
    ("corriente" for "Cuenta corriente") without treating generic words such as "cuenta" as a
    reliable scope selector.
    """
    if _surface_in_words(value, words):
        return True
    generic = {"cuenta", "cuentas", "de", "del", "la", "el", "mi", "mis"}
    parts = tuple(re.findall(r"[a-z0-9]+", spanish.folded(value)))
    distinctive = tuple(part for part in parts if len(part) > 2 and part not in generic)
    names = tuple(
        tuple(re.findall(r"[a-z0-9]+", spanish.folded(name))) for name in account_names.values()
    )
    for part in distinctive:
        matching_accounts = sum(part in name for name in names)
        if matching_accounts == 1 and _surface_in_words(part, words):
            return True
    return False
