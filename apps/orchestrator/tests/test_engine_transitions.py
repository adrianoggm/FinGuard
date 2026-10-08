"""Transitions: typed operations applied, purely, to the canonical dialogue state.

One test per transition rule, plus Review Focus 1 (two operations in one turn), 3 (an operation
that cannot apply is a clarification, never a crash) and 4 (an unknown name is asked about by
name, never dropped).
"""

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
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Reset,
    SelectOperand,
    Smalltalk,
)
from finguard_orchestrator.domain.engine.state import (
    BalanceFocus,
    ComparisonFocus,
    DialogueState,
    DialogueTurn,
    Focus,
    Measure,
    Missing,
    Pending,
    QueryFocus,
    RatioFocus,
    Scope,
)
from finguard_orchestrator.domain.engine.transitions import (
    InvariantViolationError,
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


def test_ask_builds_a_plan_from_grounded_fields_and_it_becomes_the_focus() -> None:
    said = PlanFields(
        direction=Direction.EXPENSE, window=JULY, categories=("restaurants",), people=("marta",)
    )

    t = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    expected = plan(JULY, ["RESTAURANTS"], counterparties=("Marta Ruiz",))
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


def test_patch_on_a_query_replaces_only_the_fields_given() -> None:
    base = plan(JULY, ["RESTAURANTS"], metric=Metric.COUNT, excluded_merchants=("Lidl",))

    t = apply(
        state_with(QueryFocus(base)),
        [Patch("focus", PlanFields(categories=("LEISURE",)))],
        today=TODAY,
        grounds=G,
    )

    assert t.work == Work("QUERY", replace(base, categories=("LEISURE",)))
    assert t.state.focus == QueryFocus(replace(base, categories=("LEISURE",)))


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


def test_a_measure_on_a_query_sets_its_metric() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [ChangeMeasure(Measure.COUNT)], today=TODAY, grounds=G)

    assert t.work == Work("QUERY", plan(JULY, ["RESTAURANTS"], metric=Metric.COUNT))


def test_percent_change_on_a_single_query_asks_for_the_operand() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [ChangeMeasure(Measure.PERCENT_CHANGE)], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.OPERAND))


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


def test_balance_naming_no_account_looks_at_the_scope() -> None:
    s = replace(EMPTY, scope=Scope((CORRIENTE.account_id,)))

    t = apply(s, [Balance()], today=TODAY, grounds=WITH_ACCOUNTS)

    assert t.work == Work("BALANCE", None, (CORRIENTE.account_id,))


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


# --- The invariant ---------------------------------------------------------------------------


def test_a_patch_that_would_move_an_unnamed_field_raises_invariant_violation() -> None:
    # `merchant_terms` is a plan field no operation can carry, so rebuilding the plan from fields
    # would lose it: exactly the silent widening the invariant is there to catch.
    base = plan(JULY, ["RESTAURANTS"], merchant_terms=("bet",))

    with pytest.raises(InvariantViolationError, match="merchant_terms"):
        apply(
            state_with(QueryFocus(base)),
            [Patch("focus", PlanFields(categories=("LEISURE",)))],
            today=TODAY,
            grounds=G,
        )


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
    "reply", [Delegate("PAYMENT"), Clarify(Missing.PERSON, "Pepe")], ids=["delegate", "clarify"]
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


# --- Names that are not grounded (Review Focus 4) --------------------------------------------


def test_unknown_person_is_clarify_naming_it() -> None:
    t = apply(
        DialogueState(UUID(int=1)),
        [Ask(PlanFields(people=("Pepe",), window=JULY))],
        today=TODAY,
        grounds=G,
    )
    assert isinstance(t.reply, Clarify) and t.reply.missing is Missing.PERSON
    assert t.reply.about == "Pepe"


def test_an_unknown_name_in_a_patch_asks_and_never_drops_the_filter() -> None:
    s = state_with(QueryFocus(plan(JULY, ["RESTAURANTS"])))

    t = apply(s, [Patch("focus", PlanFields(people=("Pepe",)))], today=TODAY, grounds=G)

    assert t == Transition(s, None, Clarify(Missing.PERSON, "Pepe"))


def test_the_correction_of_an_unknown_person_completes_the_question() -> None:
    said = PlanFields(direction=Direction.EXPENSE, window=JULY, people=("Pepe",))
    first = apply(EMPTY, [Ask(said)], today=TODAY, grounds=G)

    second = apply(
        first.state, [Patch("pending", PlanFields(people=("marta",)))], today=TODAY, grounds=G
    )

    assert first.state.pending == Pending(Missing.PERSON, said)
    assert second.work == Work("QUERY", plan(JULY, [], counterparties=("Marta Ruiz",)))
    assert second.state.pending is None


# --- The state itself ------------------------------------------------------------------------


def test_the_state_remembers_only_the_last_eight_turns() -> None:
    s = EMPTY
    for n in range(10):
        s = s.remembered(DialogueTurn(f"q{n}", f"a{n}"))

    assert [turn.said for turn in s.turns] == [f"q{n}" for n in range(2, 10)]
    assert s.version == EMPTY.version
