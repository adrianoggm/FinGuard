"""Transitions: typed operations applied, purely, to the canonical dialogue state.

One test per transition rule, plus Review Focus 1 (two operations in one turn), 3 (an operation
that cannot apply is a clarification, never a crash) and 4 (an unknown name is asked about by
name, never dropped).
"""

import typing
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    ComparePrevious,
    Delegate,
    InvestigateComparison,
    Operation,
    Patch,
    PlanFields,
    Reset,
    RestorePrevious,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import (
    BalanceFocus,
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    Focus,
    FocusHistoryEntry,
    Measure,
    Missing,
    Pending,
    QueryFocus,
    RatioFocus,
    Scope,
)
from finguard_orchestrator.domain.engine.transitions import (
    Transition,
    Work,
    apply,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

TODAY = date(2026, 10, 8)
MAY = AbsoluteRange(date(2026, 5, 1), date(2026, 5, 31))
JUNE = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
AUGUST = AbsoluteRange(date(2026, 8, 1), date(2026, 8, 31))
SEPTEMBER = AbsoluteRange(date(2026, 9, 1), date(2026, 9, 30))
G = Grounds(
    categories=frozenset({"RESTAURANTS", "LEISURE", "TRANSPORT_FUEL"}),
    accounts=(),
    contacts=(Contact(UUID(int=7), "Marta Ruiz", "600"),),
)
CORRIENTE = Holding(UUID(int=11), "CURRENT", "Cuenta corriente", "EUR", 0)
HUCHA = Holding(UUID(int=12), "SAVINGS", "Hucha", "EUR", 0)
WITH_ACCOUNTS = replace(G, accounts=(CORRIENTE, HUCHA))
EMPTY = DialogueState(UUID(int=1))


def plan(
    window: RelativePeriod | AbsoluteRange, categories: list[str], **rest: object
) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=tuple(categories),
        **rest,
    )


def pair(
    earlier: RelativePeriod | AbsoluteRange,
    latest: RelativePeriod | AbsoluteRange,
    categories: tuple[str, ...] = ("RESTAURANTS",),
) -> ComparisonPlan:
    comparison = ComparisonPlan.between(plan(earlier, [*categories]), plan(latest, [*categories]))
    assert comparison is not None
    return comparison


def state_with(focus: Focus, **rest: object) -> DialogueState:
    return DialogueState(UUID(int=1), focus=focus, **rest)


# --- Ask -------------------------------------------------------------------------------------


def test_ask_keeps_the_counterparty_as_ledger_text_and_becomes_the_focus() -> None:
    said = PlanFields(
        direction=Direction.EXPENSE, window=JULY, categories=("restaurants",), people=("marta",)
    )

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    expected = plan(JULY, ["RESTAURANTS"], counterparties=("marta",))
    assert t.work == Work("QUERY", expected)
    assert t.state.focus == QueryFocus(expected)
    assert t.reply is None


def test_ask_without_a_window_opens_a_pending_question_for_it() -> None:
    said = PlanFields(direction=Direction.EXPENSE, categories=("restaurants",))

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    assert t.work is None and t.reply == Clarify(Missing.WINDOW)
    assert t.state.pending == Pending(
        Missing.WINDOW, PlanFields(direction=Direction.EXPENSE, categories=("RESTAURANTS",))
    )


def test_ask_without_a_direction_asks_for_the_subject_and_keeps_what_was_said() -> None:
    said = PlanFields(window=JULY, categories=("RESTAURANTS",))

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    assert t.work is None and t.reply == Clarify(Missing.SUBJECT)
    assert t.state.pending == Pending(Missing.SUBJECT, said)


def test_a_window_that_has_not_begun_is_asked_for_again() -> None:
    november = AbsoluteRange(date(2026, 11, 1), date(2026, 11, 30))

    t = apply(
        EMPTY,
        [Ask(PlanFields(direction=Direction.EXPENSE, window=november))],
        today=TODAY,
        grounds=G,
    )

    assert t.work is None and t.reply == Clarify(Missing.WINDOW)
    assert t.state.pending is not None and t.state.pending.missing is Missing.WINDOW


def test_ask_with_a_limit_and_no_grouping_is_a_ranking() -> None:
    said = PlanFields(
        direction=Direction.EXPENSE, window=JULY, categories=("RESTAURANTS",), limit=3
    )

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    ranking = TopMovements(
        direction=Direction.EXPENSE, date_range=JULY, categories=("RESTAURANTS",), rank=3
    )
    assert t.work == Work("QUERY", ranking)
    assert t.state.focus == QueryFocus(ranking)


def test_ask_with_a_limit_and_a_grouping_limits_the_groups() -> None:
    said = PlanFields(direction=Direction.EXPENSE, window=JULY, group_by=GroupBy.CATEGORY, limit=3)

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    assert t.work == Work("QUERY", plan(JULY, [], group_by=GroupBy.CATEGORY, group_limit=3))


def test_a_ranking_cannot_carry_a_person_so_it_asks_rather_than_drop_one() -> None:
    said = PlanFields(direction=Direction.EXPENSE, window=JULY, people=("Marta Ruiz",), limit=1)

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    assert t.work is None and t.reply == Clarify(Missing.SUBJECT)


# --- Patch -----------------------------------------------------------------------------------


# Every field an operation can carry, set away from its default, so a patch that moved a field it
# did not name could not hide behind the default. The state around it is just as full.
RICH_STATE: dict[str, object] = {
    "version": 5,
    "scope": Scope((HUCHA.account_id,)),
    "turns": (DialogueTurn("¿y en julio?", "tus gastos en julio"),),
}


def rich(window: AbsoluteRange, **over: object) -> FinancialPlan:
    fields: dict[str, object] = {
        "direction": Direction.INCOME,
        "date_range": window,
        "categories": ("RESTAURANTS",),
        "excluded_categories": ("LEISURE",),
        "merchants": ("Casa Pepe",),
        "excluded_merchants": ("Lidl",),
        "counterparties": ("Marta Ruiz",),
        "metric": Metric.COUNT,
        **over,
    }
    return FinancialPlan(**fields)


@pytest.mark.parametrize(
    ("changes", "moved"),
    [
        (PlanFields(direction=Direction.EXPENSE), {"direction": Direction.EXPENSE}),
        (PlanFields(window=JUNE), {"date_range": JUNE}),
        (PlanFields(categories=("TRANSPORT_FUEL",)), {"categories": ("TRANSPORT_FUEL",)}),
        (PlanFields(excluded_categories=()), {"excluded_categories": ()}),
        (PlanFields(merchants=("Bar Manolo",)), {"merchants": ("Bar Manolo",)}),
        (PlanFields(excluded_merchants=("Dia",)), {"excluded_merchants": ("Dia",)}),
        (PlanFields(people=()), {"counterparties": ()}),
        (PlanFields(metric=Metric.SUM), {"metric": Metric.SUM}),
        (PlanFields(group_by=GroupBy.CATEGORY), {"group_by": GroupBy.CATEGORY}),
        (PlanFields(limit=5), {"group_limit": 5}),
    ],
    ids=[
        "direction",
        "window",
        "categories",
        "excluded_categories",
        "merchants",
        "excluded_merchants",
        "people",
        "metric",
        "group_by",
        "limit",
    ],
)
def test_patch_on_a_query_replaces_only_the_fields_given(
    changes: PlanFields, moved: dict[str, object]
) -> None:
    base = rich(JULY, group_by=GroupBy.MERCHANT, group_limit=3)
    s = state_with(QueryFocus(base), **RICH_STATE)

    t = apply(s, [Patch("focus", changes)], today=TODAY, grounds=G)

    patched = replace(base, **moved)
    assert t == Transition(
        replace(s, focus=QueryFocus(patched)), Work("QUERY", patched, (HUCHA.account_id,)), None
    )


def test_patch_on_a_comparison_replaces_only_the_fields_given_on_both_sides() -> None:
    before = ComparisonPlan.between(rich(JUNE), rich(JULY))
    assert before is not None
    s = state_with(ComparisonFocus(before, Measure.COUNT), **RICH_STATE)

    t = apply(
        s, [Patch("focus", PlanFields(categories=("TRANSPORT_FUEL",)))], today=TODAY, grounds=G
    )

    after = ComparisonPlan.between(
        rich(JUNE, categories=("TRANSPORT_FUEL",)), rich(JULY, categories=("TRANSPORT_FUEL",))
    )
    assert after is not None
    assert t == Transition(
        replace(s, focus=ComparisonFocus(after, Measure.COUNT)),
        Work("COMPARE", after, (HUCHA.account_id,)),
        None,
    )


def test_patch_on_a_ratio_replaces_only_the_fields_given_on_part_and_whole() -> None:
    part = rich(JULY, metric=Metric.SUM)
    whole = replace(part, categories=(), merchants=(), counterparties=())
    s = state_with(RatioFocus(RatioPlan(part, whole, RatioKind.SHARE)), **RICH_STATE)

    t = apply(
        s,
        [Patch("focus", PlanFields(window=JUNE, excluded_merchants=("Dia",)))],
        today=TODAY,
        grounds=G,
    )

    moved: dict[str, object] = {"date_range": JUNE, "excluded_merchants": ("Dia",)}
    after = RatioPlan(
        replace(part, **moved),
        replace(whole, **moved),
        RatioKind.SHARE,
    )
    assert t == Transition(
        replace(s, focus=RatioFocus(after)), Work("RATIO", after, (HUCHA.account_id,)), None
    )


def test_a_limit_on_a_share_cannot_turn_its_part_into_a_ranking() -> None:
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)
    s = state_with(RatioFocus(share))

    t = apply(s, [Patch("focus", PlanFields(limit=3))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


def test_a_patch_on_a_ranking_moves_its_rank() -> None:
    first = TopMovements(direction=Direction.EXPENSE, date_range=JULY, rank=1)

    t = apply(
        state_with(QueryFocus(first)), [Patch("focus", PlanFields(limit=2))], today=TODAY, grounds=G
    )

    assert t.work == Work("QUERY", replace(first, rank=2))


def test_patch_on_comparison_changes_subject_on_both_sides_and_keeps_months() -> None:
    s = state_with(
        ComparisonFocus(
            ComparisonPlan.between(plan(JUNE, ["RESTAURANTS"]), plan(JULY, ["RESTAURANTS"]))
        )
    )
    t = apply(s, [Patch("focus", PlanFields(categories=("LEISURE",)))], today=TODAY, grounds=G)
    assert t.work is not None and isinstance(t.work.plan, ComparisonPlan)
    assert t.work.kind == "COMPARE"
    assert [p.categories for p in (t.work.plan.earlier, t.work.plan.latest)] == [
        ("LEISURE",),
        ("LEISURE",),
    ]
    assert (t.work.plan.earlier.date_range, t.work.plan.latest.date_range) == (JUNE, JULY)


def test_patch_on_one_side_of_a_comparison_changes_only_that_side() -> None:
    t = apply(
        state_with(ComparisonFocus(pair(JUNE, JULY))),
        [Patch("earlier", PlanFields(window=MAY))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(MAY, JULY))


@pytest.mark.parametrize("side", ["earlier", "latest"])
def test_accounts_said_about_one_side_of_a_comparison_are_clarify_operand(side: str) -> None:
    # One pair has one scope, so accounts said about one side would re-scope both.
    s = state_with(ComparisonFocus(pair(JUNE, JULY)), scope=Scope((CORRIENTE.account_id,)))

    t = apply(
        s,
        [Patch(side, PlanFields(accounts=("hucha",)))],
        today=TODAY,
        grounds=WITH_ACCOUNTS,
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_window_patch_on_a_comparison_moves_both_sides_by_the_shift_of_the_latest() -> None:
    t = apply(
        state_with(ComparisonFocus(pair(JUNE, JULY))),
        [Patch("focus", PlanFields(window=SEPTEMBER))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(AUGUST, SEPTEMBER))


def test_a_window_patch_across_a_new_year_keeps_the_calendar_order() -> None:
    december_2025 = AbsoluteRange(date(2025, 12, 1), date(2025, 12, 31))
    january_2026 = AbsoluteRange(date(2026, 1, 1), date(2026, 1, 31))
    december_2024 = AbsoluteRange(date(2024, 12, 1), date(2024, 12, 31))
    january_2025 = AbsoluteRange(date(2025, 1, 1), date(2025, 1, 31))

    t = apply(
        state_with(ComparisonFocus(pair(december_2025, january_2026))),
        [Patch("focus", PlanFields(window=january_2025))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(december_2024, january_2025))


def test_a_window_patch_on_a_comparison_of_days_moves_by_days() -> None:
    first_week = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 7))
    second_week = AbsoluteRange(date(2026, 6, 8), date(2026, 6, 14))

    t = apply(
        state_with(ComparisonFocus(pair(first_week, second_week))),
        [Patch("focus", PlanFields(window=AbsoluteRange(date(2026, 6, 22), date(2026, 6, 28))))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work(
        "COMPARE",
        pair(
            AbsoluteRange(date(2026, 6, 15), date(2026, 6, 21)),
            AbsoluteRange(date(2026, 6, 22), date(2026, 6, 28)),
        ),
    )


def test_a_comparison_of_relative_periods_cannot_be_moved_and_asks_for_the_operand() -> None:
    s = state_with(
        ComparisonFocus(pair(RelativePeriod(Period.LAST_MONTH), RelativePeriod(Period.THIS_MONTH)))
    )

    t = apply(s, [Patch("focus", PlanFields(window=JULY))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


@pytest.mark.parametrize(
    ("earlier", "latest", "window"),
    [
        (JUNE, JULY, AbsoluteRange(date(1, 1, 1), date(1, 1, 31))),
        (
            AbsoluteRange(date(2026, 6, 1), date(2026, 6, 7)),
            AbsoluteRange(date(2026, 6, 8), date(2026, 6, 14)),
            AbsoluteRange(date(1, 1, 3), date(1, 1, 9)),
        ),
    ],
    ids=["months-before-year-1", "week-pair-before-year-1"],
)
def test_a_shift_that_would_leave_the_calendar_asks_for_the_operand(
    earlier: AbsoluteRange, latest: AbsoluteRange, window: AbsoluteRange
) -> None:
    s = state_with(ComparisonFocus(pair(earlier, latest)))

    t = apply(s, [Patch("focus", PlanFields(window=window))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


@pytest.mark.parametrize(
    ("earlier", "latest", "window"),
    [
        (JUNE, JULY, AbsoluteRange(date(2026, 9, 1), date(2026, 9, 15))),
        (JUNE, JULY, AbsoluteRange(date(2026, 7, 1), date(2026, 9, 30))),
        (
            AbsoluteRange(date(2026, 6, 1), date(2026, 6, 7)),
            AbsoluteRange(date(2026, 6, 8), date(2026, 6, 14)),
            AbsoluteRange(date(2026, 6, 15), date(2026, 6, 24)),
        ),
    ],
    ids=["half-a-month-over-months", "a-quarter-over-months", "ten-days-over-weeks"],
)
def test_a_window_of_another_length_than_the_pair_asks_for_the_operand(
    earlier: AbsoluteRange, latest: AbsoluteRange, window: AbsoluteRange
) -> None:
    # Moving the pair by such a window has no one right answer (half of June? which quarter?),
    # so it asks rather than shift into windows nobody named.
    s = state_with(ComparisonFocus(pair(earlier, latest)))

    t = apply(s, [Patch("focus", PlanFields(window=window))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_patch_on_a_comparison_shown_as_a_percentage_stays_a_percentage() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY), Measure.PERCENT_CHANGE))

    t = apply(s, [Patch("focus", PlanFields(categories=("LEISURE",)))], today=TODAY, grounds=G)

    leisure = pair(JUNE, JULY, ("LEISURE",))
    assert t.work == Work(
        "RATIO",
        RatioPlan(part=leisure.latest, whole=leisure.earlier, kind=RatioKind.PERCENT_CHANGE),
    )
    assert t.state.focus == ComparisonFocus(leisure, Measure.PERCENT_CHANGE)


def test_patch_on_a_ratio_patches_part_and_whole_together() -> None:
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)

    t = apply(
        state_with(RatioFocus(share)),
        [Patch("focus", PlanFields(window=JUNE))],
        today=TODAY,
        grounds=G,
    )

    moved = RatioPlan(part=plan(JUNE, ["RESTAURANTS"]), whole=plan(JUNE, []), kind=RatioKind.SHARE)
    assert t.work == Work("RATIO", moved)
    assert t.state.focus == RatioFocus(moved)


def test_a_subject_patch_on_a_share_changes_the_part_only() -> None:
    # «¿y en ocio?» after «qué porcentaje de mis gastos fue en restaurantes»: a new part of the
    # same whole. Patching the whole too would make it a share of itself.
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)

    t = apply(
        state_with(RatioFocus(share)),
        [Patch("focus", PlanFields(categories=("LEISURE",), people=("Marta Ruiz",)))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work(
        "RATIO",
        RatioPlan(
            part=plan(JULY, ["LEISURE"], counterparties=("Marta Ruiz",)),
            whole=plan(JULY, []),
            kind=RatioKind.SHARE,
        ),
    )


@pytest.mark.parametrize(
    "changes",
    [
        PlanFields(categories=()),
        PlanFields(categories=(), merchants=(), people=()),
    ],
    ids=["categories", "every-subject-field"],
)
def test_emptying_a_shares_subject_asks_for_one(changes: PlanFields) -> None:
    # Without a subject the part is the whole, and «100 %» would be a share of itself: a figure
    # nobody asked for. Asked back, as `_share_of` does for a query with no subject.
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)
    s = state_with(RatioFocus(share))

    t = apply(s, [Patch("focus", changes)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


@pytest.mark.parametrize(
    ("changes", "moved"),
    [
        (PlanFields(window=JUNE), {"date_range": JUNE}),
        (PlanFields(direction=Direction.INCOME), {"direction": Direction.INCOME}),
    ],
    ids=["window", "direction"],
)
def test_window_and_direction_patches_on_a_share_move_part_and_whole(
    changes: PlanFields, moved: dict[str, object]
) -> None:
    part, whole = plan(JULY, ["RESTAURANTS"]), plan(JULY, [])
    share = RatioPlan(part=part, whole=whole, kind=RatioKind.SHARE)

    t = apply(state_with(RatioFocus(share)), [Patch("focus", changes)], today=TODAY, grounds=G)

    assert t.work == Work(
        "RATIO",
        RatioPlan(part=replace(part, **moved), whole=replace(whole, **moved), kind=RatioKind.SHARE),
    )


def test_an_accounts_patch_on_a_share_rescopes_it_and_keeps_both_plans() -> None:
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)

    t = apply(
        state_with(RatioFocus(share)),
        [Patch("focus", PlanFields(accounts=("hucha",)))],
        today=TODAY,
        grounds=WITH_ACCOUNTS,
    )

    assert t.work == Work("RATIO", share, (HUCHA.account_id,))
    assert t.state.scope == Scope((HUCHA.account_id,))


def test_patch_on_pending_merges_into_the_draft_and_runs_as_an_ask_once_complete() -> None:
    draft = PlanFields(direction=Direction.EXPENSE, categories=("RESTAURANTS",))
    s = replace(EMPTY, pending=Pending(Missing.WINDOW, draft))

    t = apply(s, [Patch("pending", PlanFields(window=JULY))], today=TODAY, grounds=G)

    assert t.work == Work("QUERY", plan(JULY, ["RESTAURANTS"]))
    assert t.state.pending is None
    assert t.state.focus == QueryFocus(plan(JULY, ["RESTAURANTS"]))


def test_patch_on_pending_that_is_still_incomplete_keeps_the_question_open() -> None:
    s = replace(EMPTY, pending=Pending(Missing.WINDOW, PlanFields(categories=("RESTAURANTS",))))

    t = apply(s, [Patch("pending", PlanFields(window=JULY))], today=TODAY, grounds=G)

    assert t.work is None and t.reply == Clarify(Missing.SUBJECT)
    assert t.state.pending == Pending(
        Missing.SUBJECT, PlanFields(window=JULY, categories=("RESTAURANTS",))
    )


def test_patch_on_pending_with_nothing_pending_is_clarify() -> None:
    t = apply(EMPTY, [Patch("pending", PlanFields(window=JULY))], today=TODAY, grounds=G)

    assert t == Transition(EMPTY, None, Clarify(Missing.SUBJECT))


# --- Compare ---------------------------------------------------------------------------------


def test_compare_builds_the_pair_in_calendar_order() -> None:
    shared = PlanFields(direction=Direction.EXPENSE, categories=("RESTAURANTS",))

    t = apply(
        EMPTY,
        [Compare(shared, PlanFields(window=JULY), PlanFields(window=JUNE))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(JUNE, JULY))
    assert t.state.focus == ComparisonFocus(pair(JUNE, JULY), Measure.AMOUNT)


def test_compare_previous_resolves_equal_rolling_windows_from_active_query() -> None:
    active = plan(
        RelativePeriod(Period.LAST_N_MONTHS, n=3), ["RESTAURANTS"], group_by=GroupBy.CATEGORY
    )
    s = state_with(QueryFocus(active))

    t = apply(s, [ComparePrevious(3)], today=TODAY, grounds=G)

    expected = pair(
        AbsoluteRange(date(2026, 4, 8), date(2026, 7, 7)),
        AbsoluteRange(date(2026, 7, 8), date(2026, 10, 8)),
    )
    assert t.work == Work("COMPARE", expected)
    assert t.state.focus == ComparisonFocus(expected, Measure.AMOUNT)


def test_compare_previous_refuses_a_window_that_does_not_match_the_active_query() -> None:
    s = state_with(QueryFocus(plan(RelativePeriod(Period.LAST_N_MONTHS, n=3), [])))

    t = apply(s, [ComparePrevious(2)], today=TODAY, grounds=G)

    assert t.work is None and t.reply == Clarify(Missing.OPERAND)
    assert t.state.focus == s.focus and t.state.pending is not None


def test_restore_from_a_comparison_follow_up_question_returns_to_the_prior_query() -> None:
    prior = plan(JULY, ["RESTAURANTS"])
    compared = pair(JUNE, JULY)
    s = state_with(
        ComparisonFocus(compared),
        pending=Pending(Missing.OPERAND, PlanFields()),
        focus_history=(FocusHistoryEntry(QueryFocus(prior), Scope()),),
        turns=(DialogueTurn("A que se debe?", "elige un periodo"),),
    )

    t = apply(s, [RestorePrevious()], today=TODAY, grounds=G)

    assert isinstance(t.state.focus, QueryFocus) and t.state.focus.plan == prior
    assert t.state.pending is None
    assert t.work == Work("QUERY", prior)


def test_investigate_comparison_reuses_its_exact_pair() -> None:
    compared = pair(JUNE, JULY)
    s = state_with(ComparisonFocus(compared))

    t = apply(s, [InvestigateComparison()], today=TODAY, grounds=G)

    assert t.work == Work("COMPARE", compared)
    assert t.state.focus == ComparisonFocus(compared, Measure.AMOUNT)


def test_compare_inherits_what_it_does_not_name_from_the_focus() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s, [Compare(PlanFields(), PlanFields(window=JUNE), PlanFields())], today=TODAY, grounds=G
    )

    assert t.work == Work("COMPARE", pair(JUNE, JULY))


def test_a_comparison_the_domain_refuses_is_clarify_operand() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Compare(PlanFields(), PlanFields(window=JULY), PlanFields(window=JULY))],
        today=TODAY,
        grounds=G,
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_compare_with_nothing_on_screen_and_a_side_without_a_window_asks_for_it() -> None:
    shared = PlanFields(direction=Direction.EXPENSE)

    t = apply(
        EMPTY, [Compare(shared, PlanFields(window=JUNE), PlanFields())], today=TODAY, grounds=G
    )

    assert t == Transition(EMPTY, None, Clarify(Missing.OPERAND))


def test_a_compare_with_accounts_on_one_side_is_clarify_operand() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Compare(PlanFields(), PlanFields(window=JUNE, accounts=("hucha",)), PlanFields())],
        today=TODAY,
        grounds=WITH_ACCOUNTS,
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_compare_over_a_comparison_takes_what_it_does_not_name_from_its_latest_side() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(
        s,
        [Compare(PlanFields(categories=("LEISURE",)), PlanFields(window=MAY), PlanFields())],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(MAY, JULY, ("LEISURE",)))


def test_a_compare_over_a_share_takes_what_it_does_not_name_from_its_part() -> None:
    share = RatioPlan(part=plan(JULY, ["RESTAURANTS"]), whole=plan(JULY, []), kind=RatioKind.SHARE)

    t = apply(
        state_with(RatioFocus(share)),
        [Compare(PlanFields(), PlanFields(window=JUNE), PlanFields())],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("COMPARE", pair(JUNE, JULY))


def test_a_compare_over_a_ranking_is_clarify_operand() -> None:
    s = state_with(QueryFocus(TopMovements(direction=Direction.EXPENSE, date_range=JULY, rank=1)))

    t = apply(
        s, [Compare(PlanFields(), PlanFields(window=JUNE), PlanFields())], today=TODAY, grounds=G
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


# --- ChangeMeasure ---------------------------------------------------------------------------


def test_percent_change_on_a_comparison_is_a_ratio_over_the_same_pair() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(s, [ChangeMeasure(Measure.PERCENT_CHANGE)], today=TODAY, grounds=G)

    assert t.work == Work(
        "RATIO",
        RatioPlan(
            part=plan(JULY, ["RESTAURANTS"]),
            whole=plan(JUNE, ["RESTAURANTS"]),
            kind=RatioKind.PERCENT_CHANGE,
        ),
    )
    assert t.state.focus == ComparisonFocus(pair(JUNE, JULY), Measure.PERCENT_CHANGE)


def test_amount_after_a_percentage_shows_the_comparison_again() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY), Measure.PERCENT_CHANGE))

    t = apply(s, [ChangeMeasure(Measure.AMOUNT)], today=TODAY, grounds=G)

    assert t.work == Work("COMPARE", pair(JUNE, JULY))
    assert t.state.focus == ComparisonFocus(pair(JUNE, JULY), Measure.AMOUNT)


def test_count_on_a_comparison_sets_the_metric_on_both_sides() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(s, [ChangeMeasure(Measure.COUNT)], today=TODAY, grounds=G)

    counted = ComparisonPlan.between(
        plan(JUNE, ["RESTAURANTS"], metric=Metric.COUNT),
        plan(JULY, ["RESTAURANTS"], metric=Metric.COUNT),
    )
    assert counted is not None
    assert t.work == Work("COMPARE", counted)
    assert t.state.focus == ComparisonFocus(counted, Measure.COUNT)


def test_a_comparison_of_period_averages_is_labelled_as_one() -> None:
    # Not AMOUNT, and not AVERAGE either: that divides by movements, this by months.
    monthly = {"metric": Metric.PERIOD_AVERAGE, "group_by": GroupBy.MONTH}
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"], **monthly)))

    t = apply(
        s, [Compare(PlanFields(), PlanFields(window=JUNE), PlanFields())], today=TODAY, grounds=G
    )

    averaged = ComparisonPlan.between(
        plan(JUNE, ["RESTAURANTS"], **monthly), plan(JULY, ["RESTAURANTS"], **monthly)
    )
    assert averaged is not None
    assert t.work == Work("COMPARE", averaged)
    assert t.state.focus == ComparisonFocus(averaged, Measure.PERIOD_AVERAGE)


def test_a_period_average_without_a_calendar_grouping_is_clarify() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(s, [ChangeMeasure(Measure.PERIOD_AVERAGE)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


def test_a_measure_on_a_query_sets_its_metric() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [ChangeMeasure(Measure.COUNT)], today=TODAY, grounds=G)

    assert t.work == Work("QUERY", plan(JULY, ["RESTAURANTS"], metric=Metric.COUNT))


def test_percent_change_on_a_single_query_asks_for_the_operand() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [ChangeMeasure(Measure.PERCENT_CHANGE)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_share_of_a_query_is_its_part_of_the_same_question_without_the_subject() -> None:
    # «¿Qué porcentaje de mis gastos fue en restaurantes?» The whole keeps everything that is not
    # the subject — the window, the direction, what was excluded — and the scope stays the same.
    base = plan(
        JULY,
        ["RESTAURANTS"],
        merchants=("Casa Pepe",),
        counterparties=("Marta Ruiz",),
        excluded_categories=("LEISURE",),
    )
    s = state_with(QueryFocus(base), scope=Scope((HUCHA.account_id,)))

    t = apply(s, [ChangeMeasure(Measure.SHARE)], today=TODAY, grounds=G)

    share = RatioPlan(
        part=base,
        whole=replace(base, categories=(), merchants=(), counterparties=()),
        kind=RatioKind.SHARE,
    )
    assert t.work == Work("RATIO", share, (HUCHA.account_id,))
    assert t.state.focus == RatioFocus(share)


def test_share_of_a_query_without_a_subject_asks_for_it() -> None:
    s = state_with(QueryFocus(plan(JULY, [], excluded_categories=("LEISURE",))))

    t = apply(s, [ChangeMeasure(Measure.SHARE)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


def test_share_of_a_comparison_asks_which_side() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(s, [ChangeMeasure(Measure.SHARE)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


@pytest.mark.parametrize(
    "focus",
    [
        None,
        BalanceFocus(),
        RatioFocus(
            RatioPlan(
                replace(plan(JULY, []), direction=Direction.INCOME),
                plan(JULY, []),
                RatioKind.SAVINGS_RATE,
            )
        ),
    ],
    ids=["nothing", "balance", "savings-rate"],
)
def test_a_measure_with_no_figure_on_screen_to_return_to_is_clarify(focus: Focus | None) -> None:
    s = replace(EMPTY, focus=focus)

    t = apply(s, [ChangeMeasure(Measure.COUNT)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


@pytest.mark.parametrize(
    ("measure", "metric"), [(Measure.AMOUNT, Metric.SUM), (Measure.COUNT, Metric.COUNT)]
)
def test_a_figure_after_a_share_is_its_part_with_that_metric(
    measure: Measure, metric: Metric
) -> None:
    # «En euros» after «¿qué porcentaje de mis gastos fue en restaurantes?»: the restaurants
    # figure for the same window, which is what the share was built from.
    part = plan(JULY, ["RESTAURANTS"])
    s = state_with(RatioFocus(RatioPlan(part, plan(JULY, []), RatioKind.SHARE)))

    t = apply(s, [ChangeMeasure(measure)], today=TODAY, grounds=G)

    figure = replace(part, metric=metric)
    assert t == Transition(replace(s, focus=QueryFocus(figure)), Work("QUERY", figure), None)


def test_euros_after_a_percentage_change_held_as_a_ratio_is_the_pair_again() -> None:
    # No transition leaves a percentage change as a `RatioFocus` (it is a `ComparisonFocus` with
    # that measure, see `test_amount_after_a_percentage_shows_the_comparison_again`), but a state
    # that holds one still returns to its two figures rather than asking.
    change = RatioPlan(
        part=plan(JULY, ["RESTAURANTS"]),
        whole=plan(JUNE, ["RESTAURANTS"]),
        kind=RatioKind.PERCENT_CHANGE,
    )
    s = state_with(RatioFocus(change))

    t = apply(s, [ChangeMeasure(Measure.AMOUNT)], today=TODAY, grounds=G)

    assert t == Transition(
        replace(s, focus=ComparisonFocus(pair(JUNE, JULY), Measure.AMOUNT)),
        Work("COMPARE", pair(JUNE, JULY)),
        None,
    )


# --- SelectOperand ---------------------------------------------------------------------------


def test_select_operand_turns_the_comparison_into_a_query_on_that_side() -> None:
    s = state_with(ComparisonFocus(pair(JUNE, JULY), Measure.PERCENT_CHANGE))

    t = apply(s, [SelectOperand("earlier")], today=TODAY, grounds=G)

    assert t.work == Work("QUERY", plan(JUNE, ["RESTAURANTS"]))
    assert t.state.focus == QueryFocus(plan(JUNE, ["RESTAURANTS"]))


def test_select_operand_without_a_comparison_is_clarify_operand() -> None:
    t = apply(EMPTY, [SelectOperand("latest")], today=TODAY, grounds=G)

    assert t == Transition(EMPTY, None, Clarify(Missing.OPERAND))


# --- Reset and Balance -----------------------------------------------------------------------


def test_reset_clears_focus_and_pending_and_keeps_scope() -> None:
    s = state_with(
        QueryFocus(plan(JULY, ["RESTAURANTS"])),
        pending=Pending(Missing.WINDOW, PlanFields()),
        scope=Scope((HUCHA.account_id,)),
    )

    t = apply(s, [Reset()], today=TODAY, grounds=G)

    assert t == Transition(replace(EMPTY, scope=Scope((HUCHA.account_id,))), None, None)


def test_balance_is_work_on_the_accounts_named_and_leaves_focus_and_scope_alone() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])), scope=Scope((CORRIENTE.account_id,)))

    t = apply(s, [Balance(("hucha",))], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t == Transition(s, Work("BALANCE", None, (HUCHA.account_id,)), None)


def test_balance_naming_no_account_lists_every_account_whatever_the_scope() -> None:
    # «¿Cuánto tengo?» is about all the money, as on the rules route (ruling R11); a scope narrowed
    # for spending questions does not hide an account from the balance.
    s = replace(EMPTY, scope=Scope((CORRIENTE.account_id,)))

    t = apply(s, [Balance()], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t == Transition(s, Work("BALANCE", None, ()), None)


def test_balance_on_an_unknown_account_asks_naming_it() -> None:
    t = apply(EMPTY, [Balance(("Nómina",))], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t == Transition(EMPTY, None, Clarify(Missing.ACCOUNT, "Nómina"))


# --- Scope -----------------------------------------------------------------------------------


def test_accounts_named_in_an_ask_scope_the_work_and_stay_in_the_state() -> None:
    said = PlanFields(direction=Direction.EXPENSE, window=JULY, accounts=("hucha",))

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t.work == Work("QUERY", plan(JULY, []), (HUCHA.account_id,))
    assert t.state.scope == Scope((HUCHA.account_id,))


def test_a_question_that_names_no_accounts_keeps_the_scope() -> None:
    s = replace(EMPTY, scope=Scope((HUCHA.account_id,)))

    t = apply(
        s,
        [Ask(PlanFields(direction=Direction.EXPENSE, window=JULY))],
        today=TODAY,
        grounds=WITH_ACCOUNTS,
    )

    assert t.work == Work("QUERY", plan(JULY, []), (HUCHA.account_id,))
    assert t.state.scope == s.scope


def test_a_patch_naming_every_account_widens_the_scope_and_keeps_the_plan() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])), scope=Scope((HUCHA.account_id,)))

    t = apply(s, [Patch("focus", PlanFields(accounts=()))], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t.work == Work("QUERY", plan(JULY, ["RESTAURANTS"]), ())
    assert t.state.scope == Scope()


# --- Operations that cannot apply (Review Focus 3) -------------------------------------------


def test_patch_without_focus_is_clarify() -> None:
    t = apply(
        DialogueState(UUID(int=1)),
        [Patch("focus", PlanFields(categories=("LEISURE",)))],
        today=TODAY,
        grounds=G,
    )
    assert t.work is None and isinstance(t.reply, Clarify) and t.reply.missing is Missing.SUBJECT


@pytest.mark.parametrize(
    "op",
    [Patch("earlier", PlanFields(window=JUNE)), SelectOperand("latest")],
    ids=["patch-a-side", "select-a-side"],
)
def test_an_operation_on_a_side_of_a_single_query_is_clarify_operand(op: Operation) -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [op], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_patch_on_a_balance_focus_is_clarify() -> None:
    s = state_with(BalanceFocus((HUCHA.account_id,)))

    t = apply(s, [Patch("focus", PlanFields(window=JULY))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


def test_a_plan_the_domain_refuses_is_clarify_not_an_exception() -> None:
    said = PlanFields(direction=Direction.EXPENSE, window=JULY, metric=Metric.PERIOD_AVERAGE)

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    assert t == Transition(EMPTY, None, Clarify(Missing.SUBJECT))


def test_a_turn_with_no_operations_is_clarify() -> None:
    assert apply(EMPTY, [], today=TODAY, grounds=G) == Transition(
        EMPTY, None, Clarify(Missing.SUBJECT)
    )


@pytest.mark.parametrize(
    ("focus", "target"),
    [
        (QueryFocus(plan(JULY, ["RESTAURANTS"])), "focus"),
        (ComparisonFocus(pair(JUNE, JULY)), "focus"),
        (ComparisonFocus(pair(JUNE, JULY)), "latest"),
        (
            RatioFocus(RatioPlan(plan(JULY, ["RESTAURANTS"]), plan(JULY, []), RatioKind.SHARE)),
            "focus",
        ),
    ],
    ids=["query", "comparison", "comparison-side", "share"],
)
def test_a_patch_that_changes_nothing_is_clarify_not_the_same_answer_again(
    focus: Focus, target: str
) -> None:
    s = state_with(focus)

    t = apply(s, [Patch(target, PlanFields())], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.SUBJECT))


def test_an_empty_reply_to_an_open_question_asks_it_again() -> None:
    draft = PlanFields(direction=Direction.EXPENSE, categories=("RESTAURANTS",))
    s = replace(EMPTY, pending=Pending(Missing.WINDOW, draft))

    t = apply(s, [Patch("pending", PlanFields())], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.WINDOW))


@pytest.mark.parametrize("target", ["focus", "latest"])
def test_a_grouping_a_comparison_cannot_keep_is_clarify_not_the_same_pair_again(
    target: str,
) -> None:
    # A comparison is one figure per side, so `between` folds a presentation grouping away.
    s = state_with(ComparisonFocus(pair(JUNE, JULY)))

    t = apply(s, [Patch(target, PlanFields(group_by=GroupBy.CATEGORY))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_a_grouping_a_comparison_keeps_is_applied() -> None:
    t = apply(
        state_with(ComparisonFocus(pair(JUNE, JULY))),
        [Patch("focus", PlanFields(group_by=GroupBy.ACCOUNT))],
        today=TODAY,
        grounds=G,
    )

    by_account = ComparisonPlan.between(
        plan(JUNE, ["RESTAURANTS"], group_by=GroupBy.ACCOUNT),
        plan(JULY, ["RESTAURANTS"], group_by=GroupBy.ACCOUNT),
    )
    assert by_account is not None and by_account.latest.group_by is GroupBy.ACCOUNT
    assert t.work == Work("COMPARE", by_account)


def test_a_compare_that_names_a_grouping_it_cannot_keep_is_clarify() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Compare(PlanFields(group_by=GroupBy.MERCHANT), PlanFields(window=JUNE), PlanFields())],
        today=TODAY,
        grounds=G,
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


# --- The invariant ---------------------------------------------------------------------------


def test_a_patch_keeps_an_unnamed_merchant_term() -> None:
    base = plan(JULY, ["RESTAURANTS"], merchant_terms=("bet",))

    transition = apply(
        state_with(QueryFocus(base)),
        [Patch("focus", PlanFields(categories=("LEISURE",)))],
        today=TODAY,
        grounds=G,
    )

    assert transition.state.focus == QueryFocus(plan(JULY, ["LEISURE"], merchant_terms=("bet",)))


# --- Several operations in one turn (Review Focus 1) -----------------------------------------


def test_two_operations_apply_in_order() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))
    t = apply(
        s,
        [
            Compare(
                PlanFields(categories=("LEISURE",)),
                PlanFields(window=JUNE),
                PlanFields(window=JULY),
            ),
            ChangeMeasure(Measure.PERCENT_CHANGE),
        ],
        today=TODAY,
        grounds=G,
    )
    assert t.work is not None and isinstance(t.work.plan, RatioPlan)
    assert t.work.kind == "RATIO" and t.work.plan.kind is RatioKind.PERCENT_CHANGE
    assert t.state.focus == ComparisonFocus(pair(JUNE, JULY, ("LEISURE",)), Measure.PERCENT_CHANGE)


def test_one_operation_that_cannot_apply_makes_the_whole_turn_a_question() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Patch("focus", PlanFields(categories=("LEISURE",))), SelectOperand("earlier")],
        today=TODAY,
        grounds=G,
    )

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


def test_an_aside_answered_last_keeps_the_start_focus_not_an_unrun_question() -> None:
    # The turn answers the balance; the question before it never ran, so it cannot become what is
    # on screen, and neither can the accounts it named.
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])), scope=Scope((CORRIENTE.account_id,)))
    question = PlanFields(
        direction=Direction.EXPENSE, window=JUNE, categories=("LEISURE",), accounts=("hucha",)
    )

    t = apply(s, [Ask(question), Balance(("hucha",))], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t == Transition(s, Work("BALANCE", None, (HUCHA.account_id,)), None)


def test_a_question_after_an_aside_is_answered_and_becomes_the_focus() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Balance(("hucha",)), Patch("focus", PlanFields(categories=("LEISURE",)))],
        today=TODAY,
        grounds=WITH_ACCOUNTS,
    )

    assert t.work == Work("QUERY", plan(JULY, ["LEISURE"]))
    assert t.state.focus == QueryFocus(plan(JULY, ["LEISURE"]))


def test_a_reset_then_a_question_without_a_window_leaves_only_the_new_question_open() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])), scope=Scope((HUCHA.account_id,)))
    said = PlanFields(direction=Direction.EXPENSE, categories=("leisure",))

    t = apply(s, [Reset(), Ask(said)], today=TODAY, grounds=G)

    grounded = PlanFields(direction=Direction.EXPENSE, categories=("LEISURE",))
    assert t == Transition(
        replace(EMPTY, scope=s.scope, pending=Pending(Missing.WINDOW, grounded)),
        None,
        Clarify(Missing.WINDOW),
    )


def test_a_reset_then_a_question_from_the_model_keeps_the_reset() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [Reset(), Clarify(Missing.WINDOW)], today=TODAY, grounds=G)

    assert t == Transition(EMPTY, None, Clarify(Missing.WINDOW))


def test_courtesy_beside_a_question_does_not_replace_its_answer() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Smalltalk("THANKS"), Patch("focus", PlanFields(categories=("LEISURE",)))],
        today=TODAY,
        grounds=G,
    )

    assert t.reply is None and t.work == Work("QUERY", plan(JULY, ["LEISURE"]))


def test_smalltalk_alone_is_the_reply_and_changes_nothing() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [Smalltalk("GREETING")], today=TODAY, grounds=G)

    assert t == Transition(s, None, Smalltalk("GREETING"))


@pytest.mark.parametrize(
    "reply", [Delegate("PAYMENT"), Clarify(Missing.PERSON)], ids=["delegate", "clarify"]
)
def test_a_delegation_or_a_question_from_the_model_ends_the_turn_and_keeps_the_state(
    reply: Delegate | Clarify,
) -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(
        s,
        [Patch("focus", PlanFields(categories=("LEISURE",))), reply],
        today=TODAY,
        grounds=G,
    )

    assert t == Transition(s, None, reply)


def test_a_question_from_the_model_keeps_only_what_it_asks_for() -> None:
    # Ruling R21: `about` and `candidates` are names grounding found in the holder's catalogues.
    # Written by the model they are free text, and quoting them would put the model's words, a
    # figure among them, in front of the person.
    said = Clarify(Missing.SUBJECT, "gastaste 999,99 €", ("Marta Ruiz", "999,99 €"))

    t = apply(EMPTY, [said], today=TODAY, grounds=G)

    assert t == Transition(EMPTY, None, Clarify(Missing.SUBJECT))


# --- Historical counterparties versus saved payment contacts -------------------------------


def test_an_unknown_counterparty_is_queried_against_ledger_data_not_contacts() -> None:
    t = apply(
        EMPTY,
        [Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, people=("Pepe",)))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("QUERY", plan(JULY, [], counterparties=("Pepe",)))
    assert t.state.pending is None


@pytest.mark.parametrize(
    "op",
    [
        Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, people=("Marta",))),
        Patch("focus", PlanFields(people=("Marta",))),
    ],
    ids=["ask", "patch"],
)
def test_an_ambiguous_contact_name_is_still_a_counterparty_filter(op: Operation) -> None:
    two_martas = replace(
        G,
        contacts=(
            Contact(UUID(int=7), "Marta Ruiz", "600"),
            Contact(UUID(int=8), "Marta Solis", "601"),
        ),
    )
    state = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(state, [op], today=TODAY, grounds=two_martas)

    expected = (
        plan(JULY, [], counterparties=("Marta",))
        if isinstance(op, Ask)
        else plan(JULY, ["RESTAURANTS"], counterparties=("Marta",))
    )
    assert t.work == Work("QUERY", expected)
    assert t.state.pending is None


# --- The state itself ------------------------------------------------------------------------


def test_the_state_remembers_only_the_last_eight_turns() -> None:
    s = EMPTY
    for n in range(10):
        s = s.remembered(DialogueTurn(f"q{n}", f"a{n}"))

    assert [turn.said for turn in s.turns] == [f"q{n}" for n in range(2, 10)]
    assert s.version == EMPTY.version


@pytest.mark.parametrize(
    "cls",
    [
        DialogueState,
        Pending,
        PlanFields,
        QueryFocus,
        ComparisonFocus,
        RatioFocus,
        BalanceFocus,
        Ask,
        Patch,
        Compare,
        Work,
        Transition,
    ],
)
def test_every_engine_type_resolves_its_own_annotations(cls: type) -> None:
    # A codec or a schema builder reads these with `get_type_hints`; a forward reference to a
    # name another module defines would make that raise.
    assert typing.get_type_hints(cls)
