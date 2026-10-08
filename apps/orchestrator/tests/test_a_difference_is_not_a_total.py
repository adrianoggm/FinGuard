"""The figure beside the prose is a figure the prose says.

`total_minor` is what a client renders next to the answer text, and `answer_writer.leading_figure`
is where that contract is written down: the field and the sentence are two representations of one
decision, so a client showing 2.156,48 € under a sentence saying 8.571,98 € is what happens when
they disagree.

Three answer shapes were disagreeing. All three subtract two figures and put the *difference* in
the field named for a total, beside `groups` holding the two *operands*:

    «¿He gastado más este mes que el anterior?»
        text         → "En lo que va de mes: 0,00 €; el mes pasado: 1.895,97 €.
                        El gasto fue 1.895,97 € mayor el mes pasado."
        total_minor  → −189597
        groups       → [{en lo que va de mes, 0}, {el mes pasado, 189597}]

That is the case `evaluation/planning/RESULTS-E2E.md` measured over HTTP, where it failed the
`GROUPS_DO_NOT_SUM_TO_TOTAL` property and gate 1's *Evidence reconciliation* row with it. A client
rendering the leading figure shows **−1.895,97 € for a question about spending**. The two windows'
movement counts reconcile in the same payload — 28 = 28 — which is what makes it a defect in one
field rather than a shrug about the shape.

The property these tests pin is deliberately stated over the *text* rather than over the groups:
whatever a comparison's rows turn out to mean, a figure the answer never says is not the figure to
put beside it. `difference_minor` carries the change, and its presence is what tells a consumer the
rows are operands.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 8)


class ModelMustNotPlan:
    """Every sentence here is answered from a vocabulary this service owns."""

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        raise AssertionError("this turn must not reach the model")


class ModelGivesUp:
    """A refusal the guard rescues, which is how the payroll comparison is reached."""

    def __init__(self) -> None:
        self.asked = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        return PlanningResult(outcome=PlanningOutcome.UNSUPPORTED, reason="COMPARISON")


class Ledger:
    def __init__(self, answer_for: Callable[[FinancialPlan], QueryAnswer]) -> None:
        self.answer_for = answer_for
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return self.answer_for(plan)


class Bank:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=215_648,
            available_minor=215_648,
            as_of=datetime(2026, 8, 8, 9, 30, tzinfo=UTC),
            holdings=(Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 215_648),),
        )


class NoContacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class NoCards:
    async def all_of(self, *_: object, **__: object):  # type: ignore[no-untyped-def]
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(
    sentence: str,
    ledger: Ledger,
    *,
    conversation: Conversation = IDLE,
    planner: object | None = None,
) -> AskFinancialQuestionResult:
    return await AskFinancialQuestionHandler(
        planner or ModelMustNotPlan(),  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=Bank(),  # type: ignore[arg-type]
        contacts=NoContacts(),  # type: ignore[arg-type]
        cards=NoCards(),  # type: ignore[arg-type]
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )


def leading_figure_the_answer_never_says(result: AskFinancialQuestionResult) -> str | None:
    """The complaint, or `None`. A missing figure is no complaint: the sentence is the answer."""
    if result.total_minor is None:
        return None
    figure = answer_writer.money(result.total_minor, result.currency)
    if figure in result.text:
        return None
    return f"total_minor renders as {figure!r} and the answer says: {result.text!r}"


# --- the measured case ---------------------------------------------------------------------


def spending_this_month_and_last(plan: FinancialPlan) -> QueryAnswer:
    """Nothing yet this month, 1.895,97 € over 28 movements last month — the recorded figures."""
    if plan.period is not None and plan.period.period.value == "THIS_MONTH":
        return QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
    return QueryAnswer(currency="EUR", total_minor=189_597, movement_count=28)


async def test_a_period_comparison_does_not_offer_its_difference_as_the_leading_figure() -> None:
    ledger = Ledger(spending_this_month_and_last)

    result = await ask("¿He gastado más este mes que el anterior?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    # The sentence measured over HTTP, unchanged: this fix moves a field, not an answer. The
    # accounts it was read from are said under it since the final review (I-6), as under every
    # other answer read across them.
    assert result.text == (
        "En lo que va de mes: 0,00 €; el mes pasado: 1.895,97 €. "
        "El gasto fue 1.895,97 € mayor el mes pasado.\n"
        "Consultado en todas tus cuentas."
    )
    assert leading_figure_the_answer_never_says(result) is None
    # And it is absent rather than rewritten: a comparison states two figures and the change
    # between them, so there is no single one, and a client that gets `None` renders the sentence.
    assert result.total_minor is None
    assert result.difference_minor == -189_597


async def test_a_period_comparison_still_carries_both_operands_and_their_counts() -> None:
    """The evidence is untouched. Whoever renders the two windows keeps reading `groups`."""
    ledger = Ledger(spending_this_month_and_last)

    result = await ask("¿He gastado más este mes que el anterior?", ledger)

    assert [(group.key, group.total_minor, group.movement_count) for group in result.groups] == [
        ("en lo que va de mes", 0, 0),
        ("el mes pasado", 189_597, 28),
    ]
    assert result.movement_count == 28
    assert sum(group.movement_count for group in result.groups) == result.movement_count


# --- the same defect on the payroll comparison ---------------------------------------------


async def test_a_payroll_comparison_leads_with_the_payroll_and_names_the_change() -> None:
    """«¿He cobrado más?» answered −22,75 € under a sentence saying "22,75 € más"."""

    def payrolls(plan: FinancialPlan) -> QueryAnswer:
        if plan.group_by is GroupBy.MONTH:
            return QueryAnswer(
                currency="EUR",
                total_minor=476_562,
                movement_count=2,
                groups=(Group("2026-06", 246_562, 1), Group("2026-05", 230_000, 1)),
            )
        return QueryAnswer(currency="EUR", total_minor=244_287, movement_count=1)

    july = FinancialPlan(
        direction=Direction.INCOME,
        date_range=window_words.whole_month(2026, 7),
        categories=("SALARY",),
        group_by=GroupBy.ACCOUNT,
        across_accounts=True,
    )
    planner = ModelGivesUp()

    result = await ask(
        "He cobrado mas en esa nomina que en las anteriores?",
        Ledger(payrolls),
        conversation=Conversation(last_plan=july),
        planner=planner,
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert leading_figure_the_answer_never_says(result) is None
    # Here the sentence does lead with one figure — the payroll asked about — so the field carries
    # it. The thirteen month rows beside it were never parts of it.
    assert result.total_minor == 244_287
    assert result.difference_minor == 244_287 - 246_562


# --- net cash flow, where the leading figure legitimately is the difference ------------------


async def test_net_cash_flow_keeps_its_net_and_says_the_rows_are_operands() -> None:
    """Income minus expenses is what the sentence leads with, so `total_minor` is right as it is.

    What was missing is any way for a consumer to know that `Ingresos` and `Gastos` are the two
    operands of that subtraction rather than parts of it, which is the other half of the same
    contract defect.
    """
    ledger = Ledger(
        lambda plan: (
            QueryAnswer(currency="EUR", total_minor=180_000, movement_count=12)
            if plan.direction is Direction.EXPENSE
            else QueryAnswer(currency="EUR", total_minor=250_000, movement_count=3)
        )
    )

    result = await ask("¿Cuál fue mi flujo neto este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert leading_figure_the_answer_never_says(result) is None
    assert result.total_minor == 70_000
    assert result.difference_minor == 70_000
    assert [(group.key, group.total_minor) for group in result.groups] == [
        ("Ingresos", 250_000),
        ("Gastos", 180_000),
    ]


# --- and the shape this contract was always right about --------------------------------------


async def test_an_ordinary_breakdown_still_leads_with_the_total_its_groups_add_up_to() -> None:
    """The control. Nothing about a difference changes the answer that really has a total."""
    ledger = Ledger(
        lambda _: QueryAnswer(
            currency="EUR",
            total_minor=44_962,
            movement_count=8,
            groups=(Group("GROCERIES", 30_000, 5), Group("RESTAURANTS", 14_962, 3)),
        )
    )

    class Plans:
        async def plan(self, *_: object, **__: object) -> PlanningResult:
            return PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.LAST_MONTH),
                    group_by=GroupBy.CATEGORY,
                )
            )

    result = await ask("¿En qué gasté el mes pasado por categoría?", ledger, planner=Plans())

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert leading_figure_the_answer_never_says(result) is None
    assert result.total_minor == 44_962
    assert sum(group.total_minor for group in result.groups) == result.total_minor
    assert result.difference_minor is None
