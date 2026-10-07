"""A reply to the question left open fills every slot it names, whichever one was asked.

Measured live on 2026-10-07: «en todas mis cuentas» given to «¿De qué periodo?» was answered with
the balance, and «este año» given to «¿En qué cuenta?» was asked about again with the period lost;
after an answered question, «este año» to a new question's «¿De qué periodo?» completed the
*previous* plan. The arbitration now reads what the reply fills before any reader of the query in
view sees it, and the open question absorbs all of it — the account and the window, in either
order — asking only for what is still missing (spec §3.1, §3.2).

Driven over HTTP on the episodes stack, with the planner scripted to ask for the window the way the
real one does (`support.pending`).
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import (
    SETTLED,
    KeepsTheBase,
    account_question_stack,
    ask,
    asking_for_the_window_stack,
)

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_the_account_given_to_a_period_question_is_kept_and_the_period_asked_again() -> None:
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "cuánto he gastado en el gimnasio últimamente")  # «¿De qué periodo?»
    told = ask(stack, "en todas mis cuentas", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION" and told["outcome"] != "BALANCE"
    assert told["conversation"]["account_selection_mode"] == "ALL"
    # Recorded as asked (ruling R23), so a reply that again answers nothing is the repeat.
    assert told["conversation"]["last_asked"] == "WINDOW"
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED" and stack.ledger.plans[-1].across_accounts


def test_the_period_given_to_an_account_question_is_kept() -> None:
    stack = account_question_stack()  # «¿En qué cuenta?»
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    told = ask(stack, "este año", asked["conversation"])
    assert told["question"] == "¿En qué cuenta quieres consultarlo?"
    ask(stack, "Todas mis cuentas", told["conversation"])
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR  # not today..today


def test_a_reply_to_a_new_question_does_not_complete_the_previous_answer() -> None:
    stack = asking_for_the_window_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    asked = ask(stack, "¿Cuánto gasté en el gimnasio?", first["conversation"])  # a window question
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    ask(stack, "este año", asked["conversation"])
    assert stack.ledger.plans[-1].categories != ("GROCERIES",)


def test_a_reply_that_fills_the_slot_and_names_a_subject_keeps_both() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "este año pero en gasolina", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.period.period is Period.THIS_YEAR and plan.categories == ("TRANSPORT_FUEL",)


# --- beyond the brief's four: the other spellings of the same property --------------------------


def test_a_kind_of_account_given_to_a_period_question_is_kept_for_the_answer() -> None:
    """«En la hucha» chooses as surely as «en todas mis cuentas»: kept in the draft and as the
    conversation's choice, so the reply that brings the window runs the question there."""
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, "en la hucha", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION", told["text"]
    assert told["conversation"]["workflow"] == "FINANCIAL_CLARIFICATION"
    assert told["conversation"]["account_selection_mode"] == "SELECTED"
    # Recorded as asked (ruling R23), so a reply that again answers nothing is the repeat.
    assert told["conversation"]["last_asked"] == "WINDOW"
    assert told["conversation"]["draft"]["only_account_id"] == str(episodes.PIGGY)
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.only_account_id == episodes.PIGGY and plan.categories == ("RESTAURANTS",)
    assert plan.period.period is Period.THIS_YEAR


def test_the_window_and_the_account_in_one_reply_answer_the_account_question() -> None:
    stack = account_question_stack()
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    answered = ask(stack, "la hucha este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.only_account_id == episodes.PIGGY and plan.period.period is Period.THIS_YEAR


def test_the_kept_window_travels_with_the_account_question() -> None:
    """Kept in the plan that waits, on the wire, and asked about with the question's own chips —
    so the next reply is a chip, and the chip is all it needs to be."""
    stack = account_question_stack()
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    told = ask(stack, "este año", asked["conversation"])
    assert told["conversation"]["workflow"] == "ACCOUNT_SCOPE"
    assert told["conversation"]["pending_plan"]["period"] == "THIS_YEAR"
    assert told["suggestions"] == [
        "Cuenta corriente",
        "Hucha",
        "Fondo indexado",
        "Todas mis cuentas",
    ]
    assert len(stack.ledger.plans) == 0, "nothing ran before the account was chosen"


def test_a_question_of_its_own_is_not_the_period_the_open_one_was_missing() -> None:
    """A whole question names a window too. It is answered as itself, not as the restaurants
    question's missing period (the fourth condition `_finish_pending` already kept)."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "¿cuánto gasté en gasolina el mes pasado?", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("TRANSPORT_FUEL",) and plan.period.period is Period.LAST_MONTH


@pytest.mark.parametrize(
    "reply",
    [
        "¿cuánto ingresé en la hucha?",
        "¿cuánto he gastado en la hucha?",
        "¿qué movimientos tengo en la hucha?",
        "muéstrame los movimientos de la cuenta corriente",
        "dame los gastos de la hucha por categoría",
    ],
)
def test_a_new_question_naming_an_account_never_runs_the_question_left_open(reply: str) -> None:
    """Ruling R21, the wrong figure the review found: «¿cuánto ingresé en la hucha?» while
    «¿cuánto gasté en restaurantes?» waited for its period had its account folded into the
    restaurants draft, and «este año» then ran restaurant *spending* in the hucha for a question
    about income. A question of its own is answered as itself, whatever account it names."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, reply, asked["conversation"])
    if told["outcome"] == "NEEDS_CLARIFICATION":
        ask(stack, "este año", told["conversation"])
    assert all(plan.categories != ("RESTAURANTS",) for plan in stack.ledger.plans), [
        (plan.direction, plan.categories) for plan in stack.ledger.plans
    ]


# --- ruling R22: the category a reply names is absorbed, without the model -----------------------
#
# On `KeepsTheBase`, a planner that hands a forced base back untouched: the stub planner rebuilds
# every plan from the reply's own words, so on it a category reached the figure whether or not the
# handler absorbed it. Here only the handler can have put it there.


def test_the_category_beside_the_window_replaces_the_subject_without_the_model() -> None:
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(planner=planner)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    answered = ask(stack, "este año pero en gasolina", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("TRANSPORT_FUEL",) and plan.period.period is Period.THIS_YEAR
    assert "este año pero en gasolina" not in planner.asked, "the plan was completed in code"


def test_the_category_beside_the_account_is_kept_for_the_window() -> None:
    """«En todas mis cuentas pero en gasolina» lost the fuel: the account was kept and the subject
    it named was not. Both go into the draft, and the window is all that is asked again."""
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(settled=False, planner=planner)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, "en todas mis cuentas pero en gasolina", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION", told["text"]
    assert told["conversation"]["draft"]["categories"] == ["TRANSPORT_FUEL"]
    assert told["conversation"]["account_selection_mode"] == "ALL"
    # Recorded as asked (ruling R23), so a reply that again answers nothing is the repeat.
    assert told["conversation"]["last_asked"] == "WINDOW"
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("TRANSPORT_FUEL",) and plan.across_accounts
    assert plan.period.period is Period.THIS_YEAR


def test_a_category_the_reply_excludes_is_excluded() -> None:
    """«Sin gasolina» names the fuel too, and including it would answer the opposite question."""
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(planner=planner)
    asked = ask(stack, "¿cuánto gasté?")
    answered = ask(stack, "este año sin gasolina", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.excluded_categories == ("TRANSPORT_FUEL",) and plan.categories == ()
    assert plan.period.period is Period.THIS_YEAR
    assert "este año sin gasolina" not in planner.asked


def test_a_shop_gives_way_to_the_category_that_replaces_the_subject() -> None:
    """The draft held Mercadona; the reply's subject is fuel, and Mercadona's fuel is nothing."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuánto gasté en el super Mercadona?")
    assert asked["conversation"]["draft"]["merchants"] == ["Mercadona"]
    ask(stack, "este año pero en gasolina", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("TRANSPORT_FUEL",)
    assert plan.merchants == () and plan.merchant_terms == ()


def test_a_subject_that_is_income_answers_as_income() -> None:
    """The payroll put in place of the restaurants is money that came in: run as spending, it was
    a figure about nothing anyone asked."""
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(planner=planner)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    answered = ask(stack, "este año, la nómina", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("SALARY",) and plan.direction is Direction.INCOME
    assert "este año, la nómina" not in planner.asked


def test_the_window_asked_again_is_recorded_even_where_the_first_ask_left_no_slot() -> None:
    """Ruling R23. A reader's own window question opens its draft with `with_active`, which records
    no `last_asked`; the re-ask records it itself, so the turn after it knows what was asked."""
    stack = asking_for_the_window_stack(settled=False, planner=KeepsTheBase())
    asked = ask(stack, "¿cuál es mi gasto medio mensual en restaurantes?")
    assert asked["conversation"]["workflow"] == "FINANCIAL_CLARIFICATION"
    assert asked["conversation"].get("last_asked") is None
    told = ask(stack, "en la hucha", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION", told["text"]
    assert told["conversation"]["last_asked"] == "WINDOW"
    ask(stack, "este año", told["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.only_account_id == episodes.PIGGY and plan.categories == ("RESTAURANTS",)
    assert plan.metric is Metric.PERIOD_AVERAGE and plan.period.period is Period.THIS_YEAR


# --- carried to Task 7: a breakdown said beside the window ----------------------------------------
#
# «¿Cuánto gasté en restaurantes?» → «¿De qué periodo?» → «este año por comercio» completed the
# draft with the window alone, and the breakdown it asked for was never drawn. On `KeepsTheBase`
# again, so only the handler can have put the grouping there.


@pytest.mark.parametrize(
    ("question", "reply", "grouping"),
    [
        ("¿cuánto gasté en restaurantes?", "este año por comercio", GroupBy.COUNTERPARTY),
        ("¿cuánto gasté?", "este año, mejor por categoría", GroupBy.CATEGORY),
        ("¿cuánto gasté?", "este año agrupado por categorías", GroupBy.CATEGORY),
        ("¿cuánto gasté en restaurantes?", "este año por meses", GroupBy.MONTH),
    ],
)
def test_a_breakdown_beside_the_window_is_drawn_without_the_model(
    question: str, reply: str, grouping: GroupBy
) -> None:
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(planner=planner)
    asked = ask(stack, question)
    answered = ask(stack, reply, asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.group_by is grouping and plan.period.period is Period.THIS_YEAR
    assert reply not in planner.asked, "the plan was completed in code"


def test_the_subject_a_breakdown_replies_with_is_kept_too() -> None:
    """The breakdown is one more thing the reply says, not instead of the rest of it: the
    restaurants the question was about stay, and so does the account question it never asked."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "este año por comercio", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("RESTAURANTS",) and plan.direction is Direction.EXPENSE


def test_a_breakdown_the_draft_cannot_divide_is_not_put_into_it() -> None:
    """Restaurants by category is one row. The verifier refuses that only when the sentence it
    checks asks to divide, and the reply that would later complete a draft holding it («este año»)
    does not — so it stays out, and the reply completes the question it answered, as before."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    answered = ask(stack, "este año por categoría", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.group_by is GroupBy.NONE and plan.categories == ("RESTAURANTS",)


def test_a_breakdown_the_drafts_measure_cannot_take_is_left_out() -> None:
    """A monthly average spends its grouping on the months; by shop as well has no representation,
    so the draft refuses it and the reply completes the average it answered."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuál es mi gasto medio mensual en restaurantes?")
    answered = ask(stack, "este año por comercio", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.metric is Metric.PERIOD_AVERAGE and plan.group_by is GroupBy.MONTH
    assert plan.period.period is Period.THIS_YEAR


def test_a_breakdown_by_shop_of_a_draft_about_one_shop_is_not_put_into_it() -> None:
    """M-2. Mercadona by shop is one row, like restaurants by category: a question still being
    completed is not widened (R36), so the breakdown stays out and the reply completes it."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuánto gasté en el super Mercadona?")
    assert asked["conversation"]["draft"]["merchants"] == ["Mercadona"]
    answered = ask(stack, "este año por comercio", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.group_by is GroupBy.NONE and plan.merchants == ("Mercadona",)
