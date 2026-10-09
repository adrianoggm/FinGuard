"""Operations applied to the dialogue state: the only place a turn changes what is on screen.

Pure: no I/O, no model, no sentence. Each rule is one an operation states. A patch moves the fields
it names, a comparison keeps its pair and its calendar order, a reset keeps the scope. So what the
rules engine needed a reader for is, here, a matter of which operation came in.

An operation that cannot apply becomes a clarification, never an exception and never a guess. The
one exception that escapes is `InvariantViolationError`: it means this module is wrong, not the
model, and the handler reports it as that.
"""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Final, Literal, assert_never
from uuid import UUID

from finguard_orchestrator.domain.engine.grounding import Grounds, Unknown, ground
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Reset,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import (
    ComparisonFocus,
    DialogueState,
    Focus,
    Measure,
    Missing,
    Pending,
    QueryFocus,
    RatioFocus,
    Scope,
)
from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.errors import DomainError, InvalidValueError
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

type Plan = FinancialPlan | TopMovements
type Window = RelativePeriod | AbsoluteRange
type Applied = tuple[DialogueState, Work | None]


class InvariantViolationError(DomainError):
    """A transition broke a rule it exists to keep. A bug in this module, never the model's."""

    code = "invariant_violation"


@dataclass(frozen=True)
class Work:
    """What the turn asks the bank. Empty `account_ids` means every account."""

    kind: Literal["QUERY", "COMPARE", "RATIO", "BALANCE"]
    plan: FinancialPlan | TopMovements | ComparisonPlan | RatioPlan | None
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
        self, missing: Missing, about: str | None = None, *, pending: Pending | None = None
    ) -> None:
        super().__init__(missing)
        self.clarify = Clarify(missing, about)
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
    for op in ops:
        if isinstance(op, Smalltalk):
            courtesy = courtesy or op
            continue
        if isinstance(op, Delegate):
            # The turn belongs to another flow, and the conversation must be exactly as it was
            # when that flow hands it back.
            return Transition(state, None, op)
        if isinstance(op, Clarify):
            return Transition(resting, None, op)
        try:
            current, work = _applied(current, op, today, grounds)
        except _CannotApplyError as question:
            # One operation that cannot apply makes the whole turn a question. Half a turn run
            # would answer something nobody asked; only the question it opens is kept.
            pending = question.pending or resting.pending
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
    return Transition(current, work, None if work else courtesy)


def _applied(
    state: DialogueState,
    op: Ask | Patch | Compare | ChangeMeasure | SelectOperand | Reset | Balance,
    today: date,
    grounds: Grounds,
) -> Applied:
    match op:
        case Ask(fields=said):
            return _asked(state, said, today, grounds)
        case Patch(target="pending", changes=changes):
            if state.pending is None:
                raise _CannotApplyError(Missing.SUBJECT)
            return _asked(state, state.pending.draft.merged(changes), today, grounds)
        case Patch():
            return _patched_focus(state, op, today, grounds)
        case Compare():
            return _compared(state, op, today, grounds)
        case ChangeMeasure(measure=measure):
            return _measured(state, measure, today)
        case SelectOperand(which=which):
            if not isinstance(state.focus, ComparisonFocus):
                raise _CannotApplyError(Missing.OPERAND)
            pair = state.focus.comparison
            return _on_query(state, pair.earlier if which == "earlier" else pair.latest)
        case Reset():
            return replace(state, focus=None, pending=None), None
        case Balance(accounts=names):
            # An aside: it leaves the focus, the open question and the scope as they were. With no
            # account named it is about all the money (ruling R11), as on the rules route: a scope
            # narrowed for spending questions must not hide an account from the balance.
            if not names:
                return state, Work("BALANCE", None, ())
            accounts = _grounded(PlanFields(accounts=names), grounds).accounts or ()
            return state, Work("BALANCE", None, _account_ids(accounts, grounds))
        case _:
            assert_never(op)


def _asked(state: DialogueState, said: PlanFields, today: date, grounds: Grounds) -> Applied:
    grounded = ground(said, grounds)
    if isinstance(grounded, Unknown):
        pending = Pending(grounded.kind, said)
        raise _CannotApplyError(grounded.kind, grounded.name, pending=pending)
    try:
        plan = _built(grounded, today)
    except _CannotApplyError as gap:
        # Kept with everything already said, so «en julio» alone completes it.
        missing = gap.clarify.missing
        raise _CannotApplyError(missing, pending=Pending(missing, grounded)) from None
    return _on_query(_scoped(state, grounded, grounds), plan)


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
            whole_changes = replace(changes, categories=None, merchants=None, people=None)
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
    anchor = _anchor(state.focus)
    sides: list[Plan] = []
    for side in (earlier, latest):
        said = shared.merged(side)
        if anchor is not None:
            sides.append(_patched(anchor, said, today))
        elif said.window is None:
            raise _CannotApplyError(Missing.OPERAND)
        else:
            sides.append(_built(said, today))
    pair = _as_said(_paired(*sides), shared, earlier, latest)
    return _on_pair(_scoped(state, shared, grounds), pair, percent=False)


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
    whole = replace(plan, categories=(), merchants=(), counterparties=())
    return RatioPlan(part=plan, whole=whole, kind=RatioKind.SHARE)


def _has_subject(plan: FinancialPlan) -> bool:
    """Whether the plan narrows to something a share can be a part of."""
    return bool(plan.categories or plan.merchants or plan.counterparties)


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
        raise _CannotApplyError(grounded.kind, grounded.name)
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
    patched = _built(_fields_of(base).merged(changes), today)
    _check_kept(base, patched, changes.named())
    return patched


# Which plan attributes each operation field may move. Written out rather than derived from
# `_built`, so a builder that starts moving more than it was told is caught instead of trusted.
_MOVES: Final[dict[str, frozenset[str]]] = {
    "direction": frozenset({"direction"}),
    "window": frozenset({"period", "date_range"}),
    "categories": frozenset({"categories"}),
    "merchants": frozenset({"merchants"}),
    "people": frozenset({"counterparties"}),
    "accounts": frozenset(),  # the scope, which is not in the plan
    "excluded_categories": frozenset({"excluded_categories"}),
    "excluded_merchants": frozenset({"excluded_merchants"}),
    "metric": frozenset({"metric"}),
    "group_by": frozenset({"group_by"}),
    "limit": frozenset({"group_limit", "rank"}),
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
        excluded_categories=plan.excluded_categories,
        excluded_merchants=plan.excluded_merchants,
    )
    if isinstance(plan, TopMovements):
        return replace(fields, people=(), limit=plan.rank)
    return replace(
        fields,
        people=plan.counterparties,
        metric=plan.metric,
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
    period = window if isinstance(window, RelativePeriod) else None
    date_range = window if isinstance(window, AbsoluteRange) else None
    if said.limit is not None and said.group_by in (None, GroupBy.NONE):
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
        excluded_merchants=said.excluded_merchants or (),
        counterparties=said.people or (),
        metric=said.metric or Metric.SUM,
        group_by=said.group_by or GroupBy.NONE,
        group_limit=said.limit,
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
