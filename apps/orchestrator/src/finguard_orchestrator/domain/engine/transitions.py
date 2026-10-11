"""Operations applied to the dialogue state: the only place a turn changes what is on screen.

Pure: no I/O, no model, no sentence. Each rule is one an operation states. A patch moves the fields
it names, a comparison keeps its pair and its calendar order, a reset keeps the scope. So what the
rules engine needed a reader for is, here, a matter of which operation came in.

An operation that cannot apply becomes a clarification, never an exception and never a guess. The
one exception that escapes is `InvariantViolationError`: it means this module is wrong, not the
model, and the handler reports it as that.
"""

import dataclasses
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Final, Literal, assert_never
from uuid import UUID, uuid4

from finguard_orchestrator.domain.engine.grounding import Grounds, Unknown, ground
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    ComparePrevious,
    Delegate,
    InvestigateComparison,
    ListMovements,
    Operation,
    Patch,
    PatchMovementList,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    Reset,
    RestorePrevious,
    ResumeFocus,
    SelectOperand,
    Smalltalk,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import (
    BalanceFocus,
    ComparisonFocus,
    DialogueState,
    Focus,
    FocusHistoryEntry,
    Measure,
    Missing,
    MovementListFocus,
    Pending,
    PreviousFocus,
    QueryFocus,
    RatioFocus,
    Scope,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, ResultKind
from finguard_orchestrator.domain.errors import DomainError, InvalidValueError
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.movement_list_plan import MovementListPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

type Plan = FinancialPlan | TopMovements
type Window = RelativePeriod | AbsoluteRange
type Applied = tuple[DialogueState, Work | None]


class InvariantViolationError(DomainError):
    """A transition broke a rule it exists to keep. A bug in this module, never the model's."""

    code = "invariant_violation"


class SuspendedTaskLimitError(DomainError):
    """A new task cannot take the floor without silently losing an older clarification."""

    code = "suspended_task_limit"


@dataclass(frozen=True)
class Work:
    """What the turn asks the bank. Empty `account_ids` means every account."""

    kind: Literal["QUERY", "COMPARE", "RATIO", "BALANCE", "MOVEMENT_LIST", "NO_MORE_MOVEMENTS"]
    plan: FinancialPlan | TopMovements | ComparisonPlan | RatioPlan | MovementListPlan | None
    account_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Transition:
    """A turn's outcome.

    `state` already holds the focus `work` makes, so it is the state to save once the work has
    run, and only then: work the bank never answered must not become what is on screen. That is
    how the focus becomes the executed work without the caller rebuilding a focus from a work,
    which would lose the comparison behind a percentage.
    """

    state: DialogueState
    work: Work | None
    reply: Clarify | Smalltalk | Delegate | None


class _CannotApplyError(Exception):
    """An operation that cannot apply as it stands, so the turn ends as a question back. Raised
    where the gap is found and caught once in `apply`, so no helper carries a reply back up."""

    def __init__(
        self,
        missing: Missing,
        about: str | None = None,
        *,
        pending: Pending | None = None,
        candidates: tuple[str, ...] = (),
    ) -> None:
        super().__init__(missing)
        self.clarify = Clarify(missing, about, candidates)
        self.pending = pending


def apply(
    state: DialogueState, ops: Sequence[Operation], *, today: date, grounds: Grounds
) -> Transition:
    """Apply `ops` in order, each to what the previous ones made.

    A turn gives one answer: the last work, so «junio vs julio, en porcentaje» is the comparison
    then its percentage. What the state keeps is what that answer and any reset made; a question
    the turn does not answer never becomes the focus. Courtesy beside a question does not replace
    the question's answer. `today` only refuses windows that have not begun.
    """
    if not ops:
        return Transition(state, None, Clarify(Missing.SUBJECT))
    # `resting` is the state with only the resets applied: what is left when no work of this turn
    # so far is the one answered.
    current = resting = state
    work: Work | None = None
    courtesy: Smalltalk | None = None
    restored_previous = False
    for op in ops:
        if isinstance(op, Smalltalk):
            courtesy = courtesy or op
            continue
        if isinstance(op, Delegate):
            # The turn belongs to another flow, and the conversation must be exactly as it was
            # when that flow hands it back.
            return Transition(state, None, op)
        if isinstance(op, Visualize):
            # The legacy route accepts the operation through its existing chart adapter. The
            # LangGraph route intercepts it earlier and executes it against canonical artifacts.
            return Transition(state, None, Delegate("CHART"))
        # «Back to what I had» while a question is open dismisses that question (`_restored`): it
        # is not a new task, so the question is not kept aside to come back to.
        dismisses_question = (
            isinstance(op, RestorePrevious) and op.steps_back == 1 and current.focus is not None
        )
        if (
            current.pending is not None
            and not dismisses_question
            and isinstance(
                op,
                (
                    Ask,
                    Compare,
                    ComparePrevious,
                    ListMovements,
                    PatchMovementList,
                    RestorePrevious,
                    ResumeFocus,
                ),
            )
        ):
            # These operations take the conversational floor. Balance and courtesy are asides;
            # patches that answer the current clarification remain attached to their owner.
            try:
                current = suspend_pending_task(current)
            except SuspendedTaskLimitError:
                return Transition(resting, None, Clarify(Missing.TASK_LIMIT))
            resting = current
        if isinstance(op, (RestorePrevious, ResumeFocus)):
            try:
                current, work = (
                    _restored(current, op.steps_back)
                    if isinstance(op, RestorePrevious)
                    else _resumed_focus(current, op.task_id)
                )
            except _CannotApplyError as question:
                return Transition(resting, None, question.clarify)
            restored_previous = True
            continue
        if isinstance(op, (PrepareBizum, PrepareOwnTransfer)):
            raise InvariantViolationError("payment operation escaped graph dispatch")
        if isinstance(op, Clarify):
            # Only what it asks for (ruling R21). A name to quote comes from grounding, which
            # found it in the holder's catalogues; from the model it is free text, and quoting
            # it would show the person the model's words, a figure among them. Persist the open
            # question even when it came from an empty answer to an existing clarification.
            # A clarification belongs to the task already open, when there is one. Otherwise
            # it follows the visible focus whose correction/continuation is being discussed.
            active_pending = resting.pending
            if active_pending is not None and not op.draft.named():
                # A malformed/empty model answer is a recovery question, not authority to erase
                # the unfinished task's fields or change which answer it is waiting for.
                open_question = active_pending
                clarification = Clarify(active_pending.missing, draft=active_pending.draft)
            elif active_pending is not None:
                # A further clarification is still the same task. Keep its captured account
                # scope and stable identity, and merge only the fields this operation supplied.
                open_question = Pending(
                    op.missing,
                    active_pending.draft.merged(op.draft),
                    active_pending.scope,
                    active_pending.task_id,
                )
                clarification = Clarify(op.missing, draft=open_question.draft)
            else:
                open_question = Pending(op.missing, op.draft, resting.scope)
                clarification = Clarify(op.missing, draft=op.draft)
            return Transition(replace(resting, pending=open_question), None, clarification)
        if isinstance(op, (Smalltalk, Delegate, Visualize, Clarify)):
            raise InvariantViolationError("non-domain operation escaped transition dispatch")
        if not isinstance(
            op,
            (
                Ask,
                Patch,
                Compare,
                ComparePrevious,
                InvestigateComparison,
                ChangeMeasure,
                SelectOperand,
                Reset,
                Balance,
                ListMovements,
                PatchMovementList,
            ),
        ):
            raise InvariantViolationError("unsupported operation escaped transition dispatch")
        try:
            current, work = _applied(current, op, today, grounds)
        except _CannotApplyError as question:
            # One operation that cannot apply makes the whole turn a question. Half a turn run
            # would answer something nobody asked; only the question it opens is kept.
            pending = question.pending or (
                Pending(question.clarify.missing, PlanFields(), Scope())
                if isinstance(op, Ask)
                else resting.pending
                or Pending(question.clarify.missing, PlanFields(), resting.scope)
            )
            return Transition(replace(resting, pending=pending), None, question.clarify)
        except InvalidValueError:
            # The fields were understood but make a plan the domain refuses: say what is missing
            # rather than send the bank something else.
            return Transition(resting, None, Clarify(Missing.SUBJECT))
        if work is None:
            resting = current  # a reset: what it cleared stays cleared whatever follows
        elif work.kind == "BALANCE":
            # An aside answered after a question: that question is not answered, so it must not
            # become what is on screen either.
            current = resting
    if (
        work is not None
        and work.kind != "BALANCE"
        and not restored_previous
        and (current.focus != resting.focus or current.scope != resting.scope)
    ):
        history = _history_after(resting)
        if resting.focus is not None:
            previous_focus = PreviousFocus(resting.focus, resting.scope)
        elif history:
            previous = history[-1]
            previous_focus = PreviousFocus(previous.focus, previous.scope)
        else:
            previous_focus = None
        current = replace(
            current,
            focus_history=history,
            previous_focus=previous_focus,
        )
    return Transition(current, work, None if work else courtesy)


def _applied(
    state: DialogueState,
    op: (
        Ask
        | Patch
        | Compare
        | ComparePrevious
        | InvestigateComparison
        | ChangeMeasure
        | SelectOperand
        | Reset
        | Balance
        | ListMovements
        | PatchMovementList
    ),
    today: date,
    grounds: Grounds,
) -> Applied:
    match op:
        case Ask(fields=said):
            return _asked(state, said, today, grounds)
        case Patch(target=target, changes=changes, task_id=task_id) if target in {
            "pending",
            "suspended",
        }:
            if target == "pending":
                pending = state.pending
                if pending is None:
                    raise _CannotApplyError(Missing.SUBJECT)
            else:
                if not state.suspended_pending:
                    raise _CannotApplyError(Missing.SUBJECT)
                if task_id is None:
                    # Choosing the newest suspended task by recency would silently guess which
                    # user goal the model meant. The prompt exposes stable IDs for explicit
                    # resumption, so an omitted ID is an incomplete operation.
                    raise _CannotApplyError(Missing.SUBJECT)
                task_index = next(
                    (
                        index
                        for index, candidate in enumerate(state.suspended_pending)
                        if candidate.task_id == task_id
                    ),
                    -1,
                )
                if task_index < 0:
                    raise _CannotApplyError(Missing.SUBJECT)
                pending = state.suspended_pending[task_index]
                suspended = (
                    *state.suspended_pending[:task_index],
                    *state.suspended_pending[task_index + 1 :],
                )
                if state.pending is not None:
                    if len(suspended) >= DialogueState.MAX_SUSPENDED_PENDING:
                        # Never make room by evicting a different unfinished clarification.
                        # Keep the original state and ask the user to complete or resume one.
                        raise _CannotApplyError(Missing.TASK_LIMIT)
                    suspended = (*suspended, state.pending)
                state = replace(state, pending=None, suspended_pending=suspended)
            return _asked(
                state,
                pending.draft.merged(changes),
                today,
                grounds,
                task_scope=pending.scope,
                task_id=pending.task_id,
            )
        case Patch():
            return _patched_focus(state, op, today, grounds)
        case Compare():
            return _compared(state, op, today, grounds)
        case ComparePrevious():
            return _compared_previous(state, op, today)
        case InvestigateComparison():
            if not isinstance(state.focus, ComparisonFocus):
                raise _CannotApplyError(Missing.OPERAND)
            return _on_pair(state, state.focus.comparison, percent=False)
        case ChangeMeasure(measure=measure):
            return _measured(state, measure, today)
        case SelectOperand(which=which):
            if not isinstance(state.focus, ComparisonFocus):
                raise _CannotApplyError(Missing.OPERAND)
            pair = state.focus.comparison
            return _on_query(state, pair.earlier if which == "earlier" else pair.latest)
        case Reset():
            return replace(
                state,
                focus=None,
                pending=None,
                previous_focus=None,
                focus_history=(),
                suspended_pending=(),
                own_transfer=None,
            ), None
        case Balance(accounts=names):
            # An aside: it leaves the focus, the open question and the scope as they were. With no
            # account named it is about all the money (ruling R11), as on the rules route: a scope
            # narrowed for spending questions must not hide an account from the balance.
            if not names:
                return state, Work("BALANCE", None, ())
            accounts = _grounded(PlanFields(accounts=names), grounds).accounts or ()
            return state, Work("BALANCE", None, _account_ids(accounts, grounds))
        case ListMovements() as operation:
            return _listed_movements(state, operation, today, grounds)
        case PatchMovementList() as operation:
            return _patched_movement_list(state, operation, today, grounds)
        case _:
            assert_never(op)


def _asked(
    state: DialogueState,
    said: PlanFields,
    today: date,
    grounds: Grounds,
    *,
    task_scope: Scope | None = None,
    task_id: str | None = None,
) -> Applied:
    if said.clear_limit:
        raise _CannotApplyError(Missing.SUBJECT)
    # A new Ask starts a new task with an unscoped default. A pending patch resumes the scope
    # captured by that task, independently of the older result still visible on screen.
    owner_scope = task_scope if task_scope is not None else Scope()
    grounded = ground(said, grounds)
    if isinstance(grounded, Unknown):
        raise _CannotApplyError(
            grounded.kind,
            grounded.name,
            pending=Pending(grounded.kind, said, owner_scope, task_id or uuid4().hex),
            candidates=grounded.candidates,
        )
    task_state = _scoped(replace(state, scope=owner_scope), grounded, grounds)
    try:
        plan = _built(grounded, today)
    except _CannotApplyError as gap:
        # Kept with everything already said, so «en julio» alone completes it.
        missing = gap.clarify.missing
        raise _CannotApplyError(
            missing,
            pending=Pending(missing, grounded, task_state.scope, task_id or uuid4().hex),
        ) from None
    return _on_query(task_state, plan)


def _patched_focus(state: DialogueState, op: Patch, today: date, grounds: Grounds) -> Applied:
    if not op.changes.named():
        # Nothing said to change: running the same question again would read as an answer to
        # something the person did not ask.
        raise _CannotApplyError(Missing.SUBJECT)
    if op.target != "focus" and op.changes.accounts is not None:
        # A pair has one scope: accounts said about one side would re-scope both.
        raise _CannotApplyError(Missing.OPERAND)
    changes = _grounded(op.changes, grounds)
    state = _scoped(state, changes, grounds)
    focus = state.focus
    if isinstance(focus, ComparisonFocus):
        pair = _repaired(focus.comparison, op.target, changes, today)
        return _on_pair(state, pair, percent=focus.measure is Measure.PERCENT_CHANGE)
    if op.target != "focus":
        raise _CannotApplyError(Missing.OPERAND)
    if isinstance(focus, QueryFocus):
        return _on_query(state, _patched(focus.plan, changes, today))
    if isinstance(focus, RatioFocus):
        whole_changes = changes
        if focus.ratio.kind is RatioKind.SHARE:
            # A new subject is a new part of the same whole; patching the whole too would make it
            # a share of itself. Everything else (window, direction) moves both.
            whole_changes = replace(
                changes, categories=None, merchants=None, merchant_terms=None, people=None
            )
        part = _patched(focus.ratio.part, changes, today)
        whole = _patched(focus.ratio.whole, whole_changes, today)
        if not isinstance(part, FinancialPlan) or not isinstance(whole, FinancialPlan):
            raise _CannotApplyError(Missing.SUBJECT)
        if focus.ratio.kind is RatioKind.SHARE and not _has_subject(part):
            # A patch that emptied the subject left the part equal to the whole: «100 %» would be
            # a share of itself, as in `_share_of`.
            raise _CannotApplyError(Missing.SUBJECT)
        return _on_ratio(state, RatioPlan(part=part, whole=whole, kind=focus.ratio.kind))
    raise _CannotApplyError(Missing.SUBJECT)


def _repaired(
    pair: ComparisonPlan, target: str, changes: PlanFields, today: date
) -> ComparisonPlan:
    """The pair with `changes` on the side `target` names, or on both."""
    earlier: Plan = pair.earlier
    latest: Plan = pair.latest
    if target == "earlier":
        earlier = _patched(earlier, changes, today)
    elif target == "latest":
        latest = _patched(latest, changes, today)
    else:
        earlier_changes = changes
        if changes.window is not None:
            # A window said over a pair moves the pair: the latest side becomes the window said
            # and the earlier one moves as far, so the gap between them and their order are kept.
            moved = _shifted(pair.earlier.date_range, pair.latest.date_range, changes.window)
            if moved is None:
                raise _CannotApplyError(Missing.OPERAND)
            earlier_changes = replace(changes, window=moved)
        earlier = _patched(earlier, earlier_changes, today)
        latest = _patched(latest, changes, today)
    return _as_said(_paired(earlier, latest), changes)


def _compared(state: DialogueState, op: Compare, today: date, grounds: Grounds) -> Applied:
    shared, earlier, latest = (_grounded(f, grounds) for f in (op.shared, op.earlier, op.latest))
    if earlier.accounts is not None or latest.accounts is not None:
        # A comparison has one scope. Accounts per side would compare accounts, which no plan here
        # can say, and keeping only one of them would drop the other without a word.
        raise _CannotApplyError(Missing.OPERAND)
    # A fully specified pair is a new comparison. Reusing the visible query as its base silently
    # carried old merchant/category filters into unrelated requests (for example, after checking
    # Bizum history). A follow-up comparison leaves at least one window open and inherits the
    # active focus, as in «y comparado con junio?» or a category change on an existing pair.
    requested_earlier, requested_latest = shared.merged(earlier), shared.merged(latest)
    explicit_pair = requested_earlier.window is not None and requested_latest.window is not None
    has_subject = any(replace(fields, window=None).named() for fields in (shared, earlier, latest))
    fresh_comparison = explicit_pair and has_subject
    active_anchor = _anchor(state.focus)
    anchor = None if fresh_comparison else active_anchor
    inherited_direction = (
        active_anchor.direction
        if fresh_comparison and isinstance(active_anchor, FinancialPlan)
        else None
    )
    base = replace(state, scope=Scope()) if fresh_comparison and shared.accounts is None else state
    sides: list[Plan] = []
    for side in (earlier, latest):
        said = shared.merged(side)
        if fresh_comparison and said.direction is None and inherited_direction is not None:
            said = replace(said, direction=inherited_direction)
        if anchor is not None:
            sides.append(_patched(anchor, said, today))
        elif said.window is None:
            raise _CannotApplyError(Missing.OPERAND)
        else:
            sides.append(_built(said, today))
    pair = _as_said(_paired(*sides), shared, earlier, latest)
    return _on_pair(_scoped(base, shared, grounds), pair, percent=False)


def _compared_previous(state: DialogueState, op: ComparePrevious, today: date) -> Applied:
    """Resolve a structured previous-window relation against the active rolling query."""
    focus = state.focus
    if not isinstance(focus, QueryFocus) or not isinstance(focus.plan, FinancialPlan):
        raise _CannotApplyError(Missing.OPERAND)
    plan = focus.plan
    if (
        plan.period is None
        or plan.period.period is not Period.LAST_N_MONTHS
        or plan.period.n != op.months
    ):
        raise _CannotApplyError(Missing.OPERAND)
    latest_start = _months_before(today, op.months)
    earlier_end = latest_start - timedelta(days=1)
    earlier_start = _months_before(today, op.months * 2)
    earlier = replace(plan, period=None, date_range=AbsoluteRange(earlier_start, earlier_end))
    latest = replace(plan, period=None, date_range=AbsoluteRange(latest_start, today))
    pair = ComparisonPlan.between(earlier, latest)
    if pair is None:
        raise _CannotApplyError(Missing.OPERAND)
    return _on_pair(state, pair, percent=False)


def _months_before(reference: date, months: int) -> date:
    zero_based = reference.year * 12 + reference.month - 1 - months
    year, month = divmod(zero_based, 12)
    month += 1
    return date(year, month, min(reference.day, monthrange(year, month)[1]))


def _measured(state: DialogueState, measure: Measure, today: date) -> Applied:
    focus = state.focus
    if isinstance(focus, ComparisonFocus):
        if measure is Measure.PERCENT_CHANGE:
            return _on_pair(state, focus.comparison, percent=True)
        if measure is Measure.SHARE:
            # A share is of one figure, and a pair has two.
            raise _CannotApplyError(Missing.OPERAND)
        to_metric = PlanFields(metric=_METRIC_OF[measure])
        pair = focus.comparison
        return _on_pair(
            state,
            _paired(
                _patched(pair.earlier, to_metric, today), _patched(pair.latest, to_metric, today)
            ),
            percent=False,
        )
    if isinstance(focus, QueryFocus):
        if measure is Measure.PERCENT_CHANGE:
            # A change is between two figures, and one query has one.
            raise _CannotApplyError(Missing.OPERAND)
        if measure is Measure.SHARE:
            return _on_ratio(state, _share_of(focus.plan))
        return _on_query(state, _patched(focus.plan, PlanFields(metric=_METRIC_OF[measure]), today))
    if isinstance(focus, RatioFocus) and measure in _METRIC_OF:
        # «En euros» after a percentage goes back to the figures it was built from: the part of a
        # share, the pair of a change. No transition leaves a change in a `RatioFocus`, but a
        # state holding one is answered the same way rather than asked about.
        to_metric = PlanFields(metric=_METRIC_OF[measure])
        ratio = focus.ratio
        if ratio.kind is RatioKind.SHARE:
            return _on_query(state, _patched(ratio.part, to_metric, today))
        if ratio.kind is RatioKind.PERCENT_CHANGE:
            pair = _paired(
                _patched(ratio.whole, to_metric, today), _patched(ratio.part, to_metric, today)
            )
            return _on_pair(state, pair, percent=False)
    raise _CannotApplyError(Missing.SUBJECT)


def _share_of(plan: Plan) -> RatioPlan:
    """«¿Qué porcentaje de mis gastos fue en restaurantes?»: the plan on screen as the part, and
    the same question without its subject as the whole. Exclusions, window and direction stay on
    both, because they say what the whole is."""
    if not isinstance(plan, FinancialPlan) or not _has_subject(plan):
        # Without a subject the part is the whole, and a share of itself says nothing.
        raise _CannotApplyError(Missing.SUBJECT)
    whole = replace(plan, categories=(), merchants=(), merchant_terms=(), counterparties=())
    return RatioPlan(part=plan, whole=whole, kind=RatioKind.SHARE)


def _has_subject(plan: FinancialPlan) -> bool:
    """Whether the plan narrows to something a share can be a part of."""
    return bool(plan.categories or plan.merchants or plan.merchant_terms or plan.counterparties)


# --- what a work puts on screen ---------------------------------------------------------------

# One label per metric a comparison can carry (`COMPARABLE_METRICS`), so a pair always shows what
# it measures.
_MEASURE_OF: Final = {
    Metric.SUM: Measure.AMOUNT,
    Metric.COUNT: Measure.COUNT,
    Metric.AVERAGE: Measure.AVERAGE,
    Metric.PERIOD_AVERAGE: Measure.PERIOD_AVERAGE,
}
_METRIC_OF: Final = {measure: metric for metric, measure in _MEASURE_OF.items()}


def _on_query(state: DialogueState, plan: Plan) -> Applied:
    state = replace(state, focus=QueryFocus(plan), pending=None)
    return state, Work("QUERY", plan, state.scope.account_ids)


def suspend_pending_task(state: DialogueState) -> DialogueState:
    """Move the clarification into bounded history or refuse to discard an existing task."""
    if state.pending is None:
        return state
    if len(state.suspended_pending) >= DialogueState.MAX_SUSPENDED_PENDING:
        raise SuspendedTaskLimitError("all suspended clarification slots are occupied")
    suspended = (*state.suspended_pending, state.pending)
    return replace(state, pending=None, suspended_pending=suspended)


def _on_ratio(state: DialogueState, ratio: RatioPlan) -> Applied:
    state = replace(state, focus=RatioFocus(ratio), pending=None)
    return state, Work("RATIO", ratio, state.scope.account_ids)


def _on_pair(state: DialogueState, pair: ComparisonPlan, *, percent: bool) -> Applied:
    """A pair on screen, shown as its two figures or as the change between them.

    A percentage keeps the comparison as the focus and only changes how it is measured, so «en
    euros» afterwards finds the pair and not a ratio it would have to take apart.
    """
    measure = Measure.PERCENT_CHANGE if percent else _MEASURE_OF[pair.metric]
    state = replace(state, focus=ComparisonFocus(pair, measure), pending=None)
    if percent:
        ratio = RatioPlan(part=pair.latest, whole=pair.earlier, kind=RatioKind.PERCENT_CHANGE)
        return state, Work("RATIO", ratio, state.scope.account_ids)
    return state, Work("COMPARE", pair, state.scope.account_ids)


def _history_after(state: DialogueState) -> tuple[FocusHistoryEntry, ...]:
    """Keep prior typed foci, migrating schema-6's single slot before appending current focus."""
    history = state.focus_history
    if not history and state.previous_focus is not None:
        previous = state.previous_focus
        history = _append_history(history, previous.focus, previous.scope)
    if state.focus is not None:
        history = _append_history(history, state.focus, state.scope)
    return history


def _append_history(
    history: tuple[FocusHistoryEntry, ...], focus: Focus, scope: Scope
) -> tuple[FocusHistoryEntry, ...]:
    """Move a focus to the newest history position without keeping duplicate plans."""
    entry = next(
        (existing for existing in history if existing.focus == focus and existing.scope == scope),
        None,
    ) or FocusHistoryEntry(focus, scope)
    return (*tuple(existing for existing in history if existing != entry), entry)[
        -DialogueState.MAX_FOCUS_HISTORY :
    ]


def _resumed_focus(state: DialogueState, task_id: str) -> Applied:
    """Resume one explicitly named answered task, never whichever task happens to be newest."""
    task_index = next(
        (index for index, entry in enumerate(state.focus_history) if entry.task_id == task_id),
        -1,
    )
    if task_index < 0:
        raise _CannotApplyError(Missing.SUBJECT)
    selected = state.focus_history[task_index]
    remaining = (
        *state.focus_history[:task_index],
        *state.focus_history[task_index + 1 :],
    )
    if state.focus is not None:
        remaining = _append_history(remaining, state.focus, state.scope)
    restored = replace(
        state,
        focus=selected.focus,
        scope=selected.scope,
        focus_history=remaining,
        previous_focus=PreviousFocus(remaining[-1].focus, remaining[-1].scope)
        if remaining
        else None,
        pending=None,
    )
    return restored, _work_for_focus(selected.focus, selected.scope.account_ids)


def _restored(state: DialogueState, steps_back: int) -> Applied:
    # A clarification is only an unfinished turn. If the holder says to go back while one is open,
    # the most recent answered query is still the current focus; discard the draft and show that
    # result again. Remove the dismissed clarification from recent dialogue too, so its question
    # cannot capture the next follow-up. The ordinary history below is for going back from one
    # answered focus to another.
    if steps_back == 1 and state.pending is not None and state.focus is not None:
        restored = replace(state, pending=None, turns=state.turns[:-1])
        # A comparison is a new answered focus. If its follow-up opens a question, "previous
        # query" means the query that led to that comparison; an unanswered follow-up to an
        # ordinary query still repeats that current query.
        if not isinstance(state.focus, ComparisonFocus) or not state.focus_history:
            return restored, _work_for_focus(restored.focus, restored.scope.account_ids)
        state = restored

    history = state.focus_history
    if len(history) >= steps_back:
        selected_index = len(history) - steps_back
        previous = history[selected_index]
        remaining = (*history[:selected_index], *history[selected_index + 1 :])
    elif steps_back == 1 and not history and state.previous_focus is not None:
        # Schema-5 canonical state only retained this one entry; keep it restorable once.
        previous = FocusHistoryEntry(state.previous_focus.focus, state.previous_focus.scope)
        remaining = ()
    else:
        raise _CannotApplyError(Missing.SUBJECT)

    if state.focus is not None:
        remaining = _append_history(remaining, state.focus, state.scope)

    focus = previous.focus
    restored = replace(
        state,
        focus=focus,
        scope=previous.scope,
        focus_history=remaining,
        previous_focus=PreviousFocus(remaining[-1].focus, remaining[-1].scope)
        if remaining
        else None,
        pending=None,
    )
    return restored, _work_for_focus(focus, restored.scope.account_ids)


def _work_for_focus(focus: Focus | None, account_ids: tuple[UUID, ...]) -> Work:
    if focus is None:
        raise _CannotApplyError(Missing.SUBJECT)
    match focus:
        case QueryFocus(plan=plan):
            return Work("QUERY", plan, account_ids)
        case MovementListFocus(plan=plan):
            return Work("MOVEMENT_LIST", plan, account_ids)
        case ComparisonFocus(comparison=pair, measure=Measure.PERCENT_CHANGE):
            ratio = RatioPlan(part=pair.latest, whole=pair.earlier, kind=RatioKind.PERCENT_CHANGE)
            return Work("RATIO", ratio, account_ids)
        case ComparisonFocus(comparison=pair):
            return Work("COMPARE", pair, account_ids)
        case RatioFocus(ratio=ratio):
            return Work("RATIO", ratio, account_ids)
        case BalanceFocus(account_ids=ids):
            return Work("BALANCE", None, ids)
    assert_never(focus)


def _listed_movements(
    state: DialogueState, operation: ListMovements, today: date, grounds: Grounds
) -> Applied:
    if operation.continue_current:
        focus = state.focus
        if not isinstance(focus, MovementListFocus):
            raise _CannotApplyError(Missing.SUBJECT)
        if focus.plan.cursor is None:
            return state, Work("NO_MORE_MOVEMENTS", None, focus.plan.account_ids)
        # Pagination advances the saved query. Its direction, window, account and filters come
        # from the canonical focus; repeated/default operation fields cannot rewrite that query.
        plan = replace(focus.plan, page_size=operation.page_size or focus.plan.page_size)
        continued = replace(state, focus=MovementListFocus(plan), pending=None)
        return continued, Work("MOVEMENT_LIST", plan, plan.account_ids)

    fields = _grounded(
        PlanFields(
            direction=operation.direction,
            accounts=operation.accounts or None,
            categories=operation.categories or None,
            merchant_terms=operation.merchant_terms or None,
        ),
        grounds,
    )
    scoped = _scoped(state, fields, grounds)
    window = operation.window
    if isinstance(window, AbsoluteRange) and window.start > today:
        raise _CannotApplyError(Missing.WINDOW)
    if fields.accounts is None:
        # A new list starts a new task; a prior account filter is not silently inherited.
        scoped = replace(scoped, scope=Scope())
    plan = MovementListPlan(
        direction=fields.direction or Direction.ALL,
        page_size=operation.page_size or 20,
        period=window if isinstance(window, RelativePeriod) else None,
        period_reference=today if isinstance(window, RelativePeriod) else None,
        date_from=window.start if isinstance(window, AbsoluteRange) else None,
        date_to=window.end if isinstance(window, AbsoluteRange) else None,
        account_ids=scoped.scope.account_ids,
        across_accounts=not bool(scoped.scope.account_ids),
        categories=fields.categories or (),
        merchant_terms=fields.merchant_terms or (),
    )
    listed = replace(scoped, focus=MovementListFocus(plan), pending=None)
    return listed, Work("MOVEMENT_LIST", plan, plan.account_ids)


def _patched_movement_list(
    state: DialogueState, operation: PatchMovementList, today: date, grounds: Grounds
) -> Applied:
    focus = state.focus
    if not isinstance(focus, MovementListFocus):
        raise _CannotApplyError(Missing.SUBJECT)
    fields = _grounded(
        PlanFields(
            direction=operation.direction,
            accounts=operation.accounts,
            categories=operation.categories,
            merchant_terms=operation.merchant_terms,
        ),
        grounds,
    )
    previous = focus.plan
    if isinstance(operation.window, AbsoluteRange) and operation.window.start > today:
        raise _CannotApplyError(Missing.WINDOW)
    period: RelativePeriod | None
    period_reference: date | None
    date_from: date | None
    date_to: date | None
    if operation.clear_window:
        period = period_reference = date_from = date_to = None
    elif isinstance(operation.window, RelativePeriod):
        period, period_reference, date_from, date_to = operation.window, today, None, None
    elif isinstance(operation.window, AbsoluteRange):
        period, period_reference, date_from, date_to = (
            None,
            None,
            operation.window.start,
            operation.window.end,
        )
    else:
        period, period_reference, date_from, date_to = (
            previous.period,
            previous.period_reference,
            previous.date_from,
            previous.date_to,
        )
    account_ids = (
        previous.account_ids if fields.accounts is None else _account_ids(fields.accounts, grounds)
    )
    plan = replace(
        previous,
        direction=fields.direction or previous.direction,
        page_size=operation.page_size or previous.page_size,
        cursor=None,
        period=period,
        period_reference=period_reference,
        date_from=date_from,
        date_to=date_to,
        account_ids=account_ids,
        across_accounts=(
            previous.across_accounts if fields.accounts is None else not bool(account_ids)
        ),
        categories=previous.categories if fields.categories is None else fields.categories,
        merchant_terms=(
            previous.merchant_terms if fields.merchant_terms is None else fields.merchant_terms
        ),
    )
    scope = state.scope if fields.accounts is None else Scope(account_ids)
    updated = replace(state, scope=scope, focus=MovementListFocus(plan), pending=None)
    return updated, Work("MOVEMENT_LIST", plan, plan.account_ids)


def _anchor(focus: Focus | None) -> Plan | None:
    """The plan a comparison takes what it does not name from: the one on screen."""
    if isinstance(focus, QueryFocus):
        return focus.plan
    if isinstance(focus, ComparisonFocus):
        return focus.comparison.latest
    if isinstance(focus, RatioFocus):
        return focus.ratio.part
    return None


def _paired(earlier: Plan, latest: Plan) -> ComparisonPlan:
    """The two sides as a comparison in calendar order, or the question which two to compare."""
    if not isinstance(earlier, FinancialPlan) or not isinstance(latest, FinancialPlan):
        # A ranking answers with one movement, not with a figure there is a change between.
        raise _CannotApplyError(Missing.OPERAND)
    if (
        earlier.date_range is not None
        and latest.date_range is not None
        and latest.date_range.start < earlier.date_range.start
    ):
        # The sign of the change is read off this order; a pair the wrong way round says
        # «disminuyó» about a rise.
        earlier, latest = latest, earlier
    pair = ComparisonPlan.between(earlier, latest)
    if pair is None:
        raise _CannotApplyError(Missing.OPERAND)
    return pair


def _as_said(pair: ComparisonPlan, *said: PlanFields) -> ComparisonPlan:
    """`pair`, unless it lost a grouping that was said.

    A comparison is one figure per side, so `ComparisonPlan.between` folds a presentation grouping
    away. Said explicitly, that grouping would vanish and the pair be answered as if applied.
    """
    if any(fields.group_by not in (None, pair.latest.group_by) for fields in said):
        raise _CannotApplyError(Missing.OPERAND)
    return pair


# --- plans from fields, and the invariant -----------------------------------------------------


def _grounded(fields: PlanFields, grounds: Grounds) -> PlanFields:
    grounded = ground(fields, grounds)
    if isinstance(grounded, Unknown):
        raise _CannotApplyError(grounded.kind, grounded.name, candidates=grounded.candidates)
    return grounded


def _scoped(state: DialogueState, fields: PlanFields, grounds: Grounds) -> DialogueState:
    """Accounts named move the scope, which then stays until accounts are named again."""
    if fields.accounts is None:
        return state
    return replace(state, scope=Scope(_account_ids(fields.accounts, grounds)))


def _account_ids(names: tuple[str, ...], grounds: Grounds) -> tuple[UUID, ...]:
    """The ids of accounts already grounded, so every name is a display name held exactly once."""
    by_name = {holding.display_name: holding.account_id for holding in grounds.accounts}
    return tuple(by_name[name] for name in names)


def _patched(base: Plan, changes: PlanFields, today: date) -> Plan:
    """`base` with the fields `changes` names and, checked rather than assumed, nothing else."""
    base_fields = _fields_of(base)
    fields = base_fields.merged(changes)
    derived_result_kind = False
    derived_limit_clear = False
    if changes.group_by is not None:
        # The user chose the dimension; the compatible result shape follows from that domain
        # choice. The model may omit or stale-copy result_kind; neither should turn a clear
        # regrouping into an incomplete query. A time series has no top-N contract, so drop an
        # inherited limit. An explicitly changed limit on a calendar dimension is a ranking.
        group_by = fields.group_by or GroupBy.NONE
        if group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
            if changes.limit is not None and fields.limit is not None:
                result_kind = ResultKind.RANKING
            else:
                result_kind = ResultKind.TIME_SERIES
                derived_limit_clear = fields.limit is not None
                fields = replace(fields, limit=None)
        elif group_by is GroupBy.NONE:
            result_kind = ResultKind.RANKING if fields.limit is not None else ResultKind.SCALAR
        else:
            result_kind = ResultKind.RANKING if fields.limit is not None else ResultKind.BREAKDOWN
        fields = replace(fields, result_kind=result_kind)
        derived_result_kind = True
    # Removing top-N from a grouped ranking changes its result shape to the complete breakdown.
    # This is a mechanical contract repair: `clear_limit` already expresses the user's choice;
    # without it, the retained RANKING kind would be invalid once the rank is gone.
    if (
        changes.clear_limit
        and fields.result_kind is ResultKind.RANKING
        and fields.group_by
        in {
            GroupBy.CATEGORY,
            GroupBy.MERCHANT,
            GroupBy.COUNTERPARTY,
            GroupBy.ACCOUNT,
        }
    ):
        fields = replace(fields, result_kind=ResultKind.BREAKDOWN)
    patched = _built(fields, today)
    allowed_changes = changes.named()
    if derived_result_kind or fields.result_kind != base_fields.result_kind:
        allowed_changes = allowed_changes | {"result_kind"}
    if derived_limit_clear or fields.limit != base_fields.limit:
        allowed_changes = allowed_changes | {"clear_limit"}
    _check_kept(base, patched, allowed_changes)
    return patched


# Which plan attributes each operation field may move. Written out rather than derived from
# `_built`, so a builder that starts moving more than it was told is caught instead of trusted.
_MOVES: Final[dict[str, frozenset[str]]] = {
    "direction": frozenset({"direction"}),
    "window": frozenset({"period", "date_range"}),
    "categories": frozenset({"categories"}),
    "merchants": frozenset({"merchants"}),
    "merchant_terms": frozenset({"merchant_terms"}),
    "people": frozenset({"counterparties"}),
    "accounts": frozenset(),  # the scope, which is not in the plan
    "excluded_categories": frozenset({"excluded_categories"}),
    "excluded_merchants": frozenset({"excluded_merchants"}),
    "metric": frozenset({"metric"}),
    "result_kind": frozenset({"result_kind"}),
    "group_by": frozenset({"group_by"}),
    "limit": frozenset({"group_limit", "rank"}),
    "clear_limit": frozenset({"group_limit", "rank", "result_kind"}),
}


def _check_kept(base: Plan, patched: Plan, named: frozenset[str]) -> None:
    free = {attribute for name in named for attribute in _MOVES[name]}
    common = {f.name for f in dataclasses.fields(base)} & {
        f.name for f in dataclasses.fields(patched)
    }
    moved = sorted(name for name in common - free if getattr(base, name) != getattr(patched, name))
    if moved:
        raise InvariantViolationError(f"a patch moved {', '.join(moved)} without naming it")


def _fields_of(plan: Plan) -> PlanFields:
    fields = PlanFields(
        direction=plan.direction,
        window=plan.period or plan.date_range,
        categories=plan.categories,
        merchants=plan.merchants,
        merchant_terms=plan.merchant_terms,
        excluded_categories=plan.excluded_categories,
        excluded_merchants=plan.excluded_merchants,
    )
    if isinstance(plan, TopMovements):
        return replace(fields, people=(), limit=plan.rank, result_kind=ResultKind.RANKING)
    return replace(
        fields,
        people=plan.counterparties,
        metric=plan.metric,
        result_kind=plan.result_kind,
        group_by=plan.group_by,
        limit=plan.group_limit,
    )


def _built(said: PlanFields, today: date) -> Plan:
    """The plan complete fields make: a ranking for a limit without groups, an aggregate else."""
    window, direction = said.window, said.direction
    if window is None or (isinstance(window, AbsoluteRange) and window.start > today):
        # Nothing has happened in a window that has not begun, and a zero for it would be a
        # confident answer to a question nobody asked.
        raise _CannotApplyError(Missing.WINDOW)
    if direction is None:
        raise _CannotApplyError(Missing.SUBJECT)
    group_by = said.group_by or GroupBy.NONE
    result_kind = said.result_kind
    if result_kind in {ResultKind.COMPARISON, ResultKind.INVESTIGATION}:
        raise _CannotApplyError(Missing.OPERAND)
    if result_kind is ResultKind.SCALAR and (
        group_by is not GroupBy.NONE or said.limit is not None
    ):
        raise _CannotApplyError(Missing.SUBJECT)
    if result_kind is ResultKind.RECORDS and (
        group_by is not GroupBy.NONE or said.limit is not None or said.metric is not Metric.COUNT
    ):
        raise _CannotApplyError(Missing.SUBJECT)
    if result_kind is ResultKind.RANKING and said.limit is None:
        raise _CannotApplyError(Missing.SUBJECT)
    if result_kind is ResultKind.BREAKDOWN and (
        group_by not in {GroupBy.CATEGORY, GroupBy.MERCHANT, GroupBy.COUNTERPARTY, GroupBy.ACCOUNT}
        or said.limit is not None
    ):
        raise _CannotApplyError(Missing.SUBJECT)
    if result_kind is ResultKind.TIME_SERIES and (
        group_by not in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH} or said.limit is not None
    ):
        raise _CannotApplyError(Missing.SUBJECT)
    period = window if isinstance(window, RelativePeriod) else None
    date_range = window if isinstance(window, AbsoluteRange) else None
    if said.limit is not None and group_by is GroupBy.NONE:
        if said.people or said.metric not in (None, Metric.SUM):
            # A ranking carries neither, and building one would drop them without a word.
            raise _CannotApplyError(Missing.SUBJECT)
        return TopMovements(
            direction=direction,
            period=period,
            date_range=date_range,
            categories=said.categories or (),
            excluded_categories=said.excluded_categories or (),
            merchants=said.merchants or (),
            merchant_terms=said.merchant_terms or (),
            excluded_merchants=said.excluded_merchants or (),
            rank=said.limit,
        )
    return FinancialPlan(
        direction=direction,
        period=period,
        date_range=date_range,
        categories=said.categories or (),
        excluded_categories=said.excluded_categories or (),
        merchants=said.merchants or (),
        merchant_terms=said.merchant_terms or (),
        excluded_merchants=said.excluded_merchants or (),
        counterparties=said.people or (),
        metric=said.metric or Metric.SUM,
        group_by=said.group_by or GroupBy.NONE,
        group_limit=said.limit,
        result_kind=result_kind,
    )


# --- moving a window --------------------------------------------------------------------------


def _shifted(
    window: AbsoluteRange | None, latest: AbsoluteRange | None, new: Window
) -> AbsoluteRange | None:
    """`window` moved as far as `latest` moved to become `new`, or None when there is no such move.

    Only between explicit ranges: a relative period would have to be resolved to be moved, and
    what LAST_MONTH covers is banking's business. Whole months move by months, because June to
    July is one month and not 30 days; anything else moves by days. `new` must be as long as
    `latest`: half a month or a quarter said over a pair of months is no shift of that pair, and
    moving it anyway would compare windows nobody named.
    """
    if window is None or latest is None or not isinstance(new, AbsoluteRange):
        return None
    try:
        if all(_whole_months(each) for each in (window, latest, new)):
            if _months_in(new) != _months_in(latest):
                return None
            months = _month_index(new.start) - _month_index(latest.start)
            return AbsoluteRange(
                _first_day(_month_index(window.start) + months),
                _first_day(_month_index(window.end) + months + 1) - timedelta(days=1),
            )
        if new.end - new.start != latest.end - latest.start:
            return None
        days = new.start - latest.start
        return AbsoluteRange(window.start + days, window.end + days)
    except (ValueError, OverflowError):
        # Moved past the first or the last day the calendar holds (`_first_day` and the date
        # arithmetic both raise there): there is no such window to compare with.
        return None


def _whole_months(window: AbsoluteRange) -> bool:
    return window.start.day == 1 and (window.end + timedelta(days=1)).day == 1


def _months_in(window: AbsoluteRange) -> int:
    return _month_index(window.end) - _month_index(window.start) + 1


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _first_day(month_index: int) -> date:
    return date(month_index // 12, month_index % 12 + 1, 1)
