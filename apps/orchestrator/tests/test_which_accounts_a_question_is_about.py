"""Which of the holder's accounts a question covers.

Until there were three accounts, the figures were the one the credential named — which was also
every account the holder had, so the two were the same thing and nothing had to choose. They are
not the same thing any more, and keeping the old default answers "¿cuánto gasté?" with the spending
of one account out of three: a real figure about part of somebody's money, presented as all of it.

The rule is the one the balance answer already uses, and that is the point of it — nothing new is
being judged here. Name an account and the figures are that account's. Name none and they are all
of them. The same closed vocabulary, and the model never sees the decision.
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 8, 5)

SPENDING = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.THIS_MONTH),
    group_by=GroupBy.NONE,
    metric=Metric.SUM,
)


def summary(*holdings: Holding) -> AccountSummary:
    return AccountSummary(
        currency="EUR",
        booked_minor=227_042,
        available_minor=227_042,
        as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
        holdings=holdings
        or (
            Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
            Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550),
        ),
    )


class Planner:
    def __init__(self, plan: FinancialPlan = SPENDING) -> None:
        self._plan = plan

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        return PlanningResult(outcome=PlanningOutcome.PLANNED, plan=self._plan)


class Ledger:
    """Remembers the plan it was asked to run, which is where the scope shows up."""

    def __init__(self) -> None:
        self.plan: FinancialPlan | None = None

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plan = plan
        return QueryAnswer(currency="EUR", total_minor=0, movement_count=0, groups=(), movements=())


class Bank:
    def __init__(self, of: AccountSummary) -> None:
        self._summary = of

    async def of(self, *_: object, **__: object) -> AccountSummary:
        return self._summary


class Directory:
    def __init__(self, *holdings: Holding) -> None:
        self.holdings = holdings
        self.asked_for: uuid.UUID | None = None

    async def all_of(self, account_id: uuid.UUID) -> tuple[Holding, ...]:
        self.asked_for = account_id
        return self.holdings


async def ran(
    sentence: str,
    of: AccountSummary | None = None,
    directory: Directory | None = None,
    planned: FinancialPlan | None = None,
) -> FinancialPlan:
    ledger = Ledger()
    handler = AskFinancialQuestionHandler(
        Planner(planned or SPENDING),  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=Bank(of or summary()),  # type: ignore[arg-type]
        account_directory=directory,
        contacts=None,
        cards=NoCards(),  # type: ignore[arg-type]
    )
    await handler.handle(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    )
    assert ledger.plan is not None
    return ledger.plan


async def test_a_question_naming_no_account_covers_all_of_them() -> None:
    """The default, and the one that changed. "¿Cuánto gasté?" means everything somebody spent,
    not what left one of their three accounts."""
    plan = await ran("¿cuánto gasté este mes?")

    assert plan.across_accounts is True
    assert plan.only_account_id is None


async def test_a_question_naming_an_account_is_about_that_account() -> None:
    plan = await ran("¿cuánto gasté este mes en la hucha?")

    assert plan.only_account_id == PIGGY
    assert plan.across_accounts is False


async def test_a_question_can_use_the_holders_custom_account_name() -> None:
    vacations = uuid.UUID(int=5)
    accounts = summary(
        Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
        Holding(vacations, "SAVINGS", "Vacaciones", "EUR", 75_000),
    )

    directory = Directory(*accounts.holdings)
    plan = await ran(
        "¿cuánto gasté este mes en la cuenta Vacaciones?", accounts, directory=directory
    )

    assert plan.only_account_id == vacations
    assert plan.across_accounts is False
    assert directory.asked_for == ACCOUNT


async def test_an_unknown_custom_account_name_asks_for_a_real_account() -> None:
    directory = Directory(*summary().holdings)

    ledger = Ledger()
    handler = AskFinancialQuestionHandler(
        Planner(SPENDING),
        ledger,  # type: ignore[arg-type]
        accounts=Bank(summary()),  # type: ignore[arg-type]
        account_directory=directory,
        contacts=None,
        cards=NoCards(),  # type: ignore[arg-type]
    )
    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿cuánto gasté en la cuenta Viajes?", account_id=ACCOUNT, today=TODAY
        )
    )

    assert result.outcome == "NEEDS_CLARIFICATION"
    assert result.suggestions == ["Cuenta corriente", "Hucha", "Todas mis cuentas"]
    assert ledger.plan is None


async def test_a_kind_the_holder_does_not_have_widens_rather_than_narrowing_to_nothing() -> None:
    """Narrowing to an account that is not there would answer about nothing and look like an empty
    month. The wider answer is the honest one: the word was not understood, and the figures say so
    by covering everything."""
    only_current = summary(Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042))

    plan = await ran("¿cuánto gasté en la hucha?", only_current)

    assert plan.across_accounts is True
    assert plan.only_account_id is None


async def test_two_accounts_of_one_kind_select_both_without_widening_to_other_kinds() -> None:
    """The kind names a subset even when it contains two accounts. Widening would silently add the
    current account, while picking one would silently drop the other hucha."""
    two = summary(
        Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
        Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550),
        Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00),
    )

    plan = await ran("¿cuánto gasté en la hucha?", two)

    assert plan.across_accounts is False
    assert plan.only_account_id is None
    assert plan.only_account_ids == (PIGGY, uuid.UUID(int=4))


async def test_two_different_named_accounts_form_a_selected_subset() -> None:
    fund = uuid.UUID(int=6)
    three = summary(
        Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
        Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550),
        Holding(fund, "ASSET", "Fondo indexado", "EUR", 270_000),
    )

    plan = await ran("¿qué movimientos tuve en la cuenta corriente y la hucha este mes?", three)

    assert plan.across_accounts is False
    assert plan.only_account_id is None
    assert plan.only_account_ids == (ACCOUNT, PIGGY)


async def test_the_scope_is_not_part_of_the_plan_that_is_remembered() -> None:
    """Which accounts the figures came from is how the question was answered. The plan is what was
    *asked*, and that is what gets described and carried into a follow-up — so the two are set
    apart deliberately, and `SPENDING` here is the plan the planner produced, unscoped."""
    plan = await ran("¿cuánto gasté este mes?")

    assert plan.direction is SPENDING.direction
    assert plan.period == SPENDING.period


async def test_widening_clears_the_account_the_plan_was_already_narrowed_to() -> None:
    """The two tests above widen from a plan that named no account, which is why they pass.

    A plan arriving here with one is the case nobody wrote: `merge` rebuilds a follow-up from the
    previous plan, and scope survives that, so "¿y en el fondo?" after "¿cuánto gasté en la hucha?"
    reaches this method already narrowed to the piggy bank. Widening then set `across_accounts` and
    left the piggy bank in place — and banking reads that pair as a request for every account, so
    the answer covered all of them while `scoped_to` still said the hucha. Since the invariant
    landed on the other side it is a 422 instead, which is louder but no more correct.

    Widening means widening: the narrowing that is being abandoned has to go with it.
    """
    narrowed = replace(SPENDING, only_account_id=PIGGY)

    plan = await ran("¿cuánto gasté este mes en el fondo?", planned=narrowed)

    assert plan.across_accounts is True
    assert plan.only_account_id is None
    assert plan.only_account_ids == ()
