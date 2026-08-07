"""A refusal is this product's decision, not the model's mood.

`capability_guard` is what declines a question — averages, ratios, comparisons, forecasts — and it
runs *before* the model, deterministically. So an `UNSUPPORTED` naming a family the guard does not
have is not a refusal this service makes. It is the model failing, wearing the authority of a rule.

That is the worst kind of wrong answer this pipeline can give. A wrong figure can be checked against
a statement; "no puedo" about something the product does teaches somebody not to ask again, and
nothing ever contradicts it.

Ollama returned exactly that for "¿qué intereses me ha dado la hucha?" — a question whose every part
is in a closed vocabulary this service reads itself.

The recovery is bounded on purpose. The sentence is re-read deterministically and a plan is built
only from what it states outright: a precise category, and a window. It never guesses at a plan the
sentence does not contain, and it never overrides a refusal the guard actually made.
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    FinancialClarification,
    Slot,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


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
TODAY = date(2026, 8, 5)


class GivesUp:
    """A planner that refuses without naming a family the guard owns."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.asked = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        return PlanningResult(outcome=PlanningOutcome.UNSUPPORTED, reason=self.reason)


class Ledger:
    def __init__(self) -> None:
        self.plan: FinancialPlan | None = None

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plan = plan
        return QueryAnswer(
            currency="EUR",
            total_minor=5_250,
            movement_count=7,
            groups=(Group(key="total", total_minor=5_250, movement_count=7),),
            movements=(),
        )

    async def ranked(self, ranking: TopMovements, **_: object) -> Movement:
        self.ranking = ranking
        return Movement(
            occurred_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            description="Arrendamiento Centro",
            amount_minor=92_000,
            direction=ranking.direction.value,
            merchant_name="Arrendamiento Centro",
            category=ranking.categories[0] if ranking.categories else "HOUSING_RENT",
            currency="EUR",
        )


class ListingLedger(Ledger):
    def __init__(self) -> None:
        super().__init__()
        self.requested_movements: int | None = None

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self.plan = plan
        self.requested_movements = movements
        available = tuple(
            Movement(
                occurred_at=datetime(2026, 8, day, 10, 0, tzinfo=UTC),
                description=f"Movimiento {day}",
                amount_minor=day * 100,
                direction="EXPENSE",
                merchant_name=f"Comercio {day}",
                transaction_id=str(uuid.UUID(int=day)),
            )
            for day in range(5, 0, -1)
        )
        return QueryAnswer(
            currency="EUR",
            total_minor=1_500,
            movement_count=100,
            movements=available[:movements],
        )


class ComparisonLedger(Ledger):
    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plan = plan
        return QueryAnswer(
            currency="EUR",
            total_minor=7_000,
            movement_count=5,
            groups=(Group("GROCERIES", 6_000, 3), Group("RESTAURANTS", 1_000, 2)),
        )


class Bank:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=(
                Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
                Holding(uuid.UUID(int=2), "SAVINGS", "Hucha", "EUR", 548_550),
            ),
        )


async def asked(
    sentence: str,
    planner: GivesUp,
    ledger: Ledger | None = None,
    conversation: Conversation = IDLE,
):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger or Ledger(),  # type: ignore[arg-type]
        accounts=Bank(),  # type: ignore[arg-type]
        contacts=None,
        cards=NoCards(),  # type: ignore[arg-type]
    )
    return await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
    )


# --- what gets rescued ------------------------------------------------------------------


async def test_a_sentence_that_states_a_plan_is_answered_despite_the_model() -> None:
    """The reported case. Every part of it is in a vocabulary this service owns, so a refusal was
    never the truth about it."""
    ledger = Ledger()

    answer = await asked("¿qué intereses me ha dado la hucha este año?", GivesUp(), ledger)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert answer.total_minor == 5_250
    assert ledger.plan is not None
    assert ledger.plan.categories == ("INTEREST",)


async def test_interest_is_read_as_money_coming_in() -> None:
    """From what the category *is*, not from a verb. "¿Qué intereses me ha dado?" carries no word
    of spending or earning that this service reads, and filing interest under EXPENSE would answer
    zero — a real figure, about the opposite of what was asked."""
    ledger = Ledger()

    await asked("¿qué intereses me ha dado la hucha este año?", GivesUp(), ledger)

    assert ledger.plan is not None
    assert ledger.plan.direction is Direction.INCOME


async def test_the_window_the_sentence_named_is_the_window_it_runs_over() -> None:
    ledger = Ledger()

    await asked("¿cuánto gasté en gasolina el mes pasado?", GivesUp(), ledger)

    assert ledger.plan is not None
    assert ledger.plan.period is not None
    assert ledger.plan.period.period is Period.LAST_MONTH
    assert ledger.plan.direction is Direction.EXPENSE


async def test_a_category_with_no_period_is_asked_about_rather_than_refused() -> None:
    """What is missing is a window, and saying "no puedo calcularlo" instead is a capability limit
    that does not exist — it teaches somebody the product cannot do something it does."""
    answer = await asked("¿qué intereses me ha dado la hucha?", GivesUp())

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "periodo" in answer.text
    assert answer.conversation.awaiting is Slot.WINDOW


# --- what must still be refused ----------------------------------------------------------


@pytest.mark.parametrize("family", ["AVERAGE", "RATIO", "COMPARISON"])
async def test_a_refusal_the_guard_owns_is_left_alone(family: str) -> None:
    """The rescue reads the model's reason and stops when it names a real family. Overriding one of
    these would be the pipeline talking itself out of a decision the product made on purpose."""
    answer = await asked("¿cuánto gasté en gasolina el mes pasado?", GivesUp(family))

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value


async def test_a_plain_average_is_planned_before_the_legacy_guard_or_model() -> None:
    """A bare category average now has one useful meaning: amount per matching movement."""
    planner = GivesUp()
    ledger = Ledger()

    answer = await asked("¿cuál es mi gasto medio en gasolina el mes pasado?", planner, ledger)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE


async def test_excluding_a_category_is_never_reversed_into_only_that_category() -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    answer = await asked(
        "¿Y si no contamos el alquiler?", GivesUp(), Ledger(), Conversation(last_plan=previous)
    )

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value
    assert "resultado contrario" in answer.text
    assert answer.conversation.last_plan == previous


async def test_a_natural_category_comparison_asks_for_its_missing_period() -> None:
    answer = await asked(
        "¿Voy gastando más en el súper o comiendo fuera?", GivesUp(), ComparisonLedger()
    )

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert answer.conversation.awaiting is Slot.WINDOW
    assert isinstance(answer.conversation.active, FinancialClarification)
    assert answer.conversation.active.draft.categories == ("GROCERIES", "RESTAURANTS")


async def test_latest_five_movements_means_five_rows_not_five_days() -> None:
    planner = GivesUp()
    ledger = ListingLedger()

    answer = await asked(
        "Perdona, ¿cuáles son los utlimos 5 movimientos de mis cuentas?", planner, ledger
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.requested_movements == 5
    assert answer.movement_count == 5
    assert len(answer.movements) == 5
    assert "últimos 5 movimientos" in answer.text


async def test_a_breakdown_by_account_keeps_exactly_the_five_rows_just_shown() -> None:
    planner = GivesUp()
    ledger = ListingLedger()
    listed = await asked("Muéstrame los cinco últimos movimientos", planner, ledger)

    assert isinstance(listed.conversation.last_plan, FinancialPlan)
    selected = listed.conversation.last_plan.transaction_ids
    assert selected == tuple(uuid.UUID(int=day) for day in range(5, 0, -1))

    broken_down = await asked("Sepáralos por cuenta", planner, ledger, listed.conversation)

    assert planner.asked == 0
    assert broken_down.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.group_by is GroupBy.ACCOUNT
    assert ledger.plan.transaction_ids == selected


async def test_five_last_is_also_a_row_count_when_the_noun_is_elliptical() -> None:
    ledger = ListingLedger()

    answer = await asked("Los cinco últimos, da igual cuándo", GivesUp(), ledger)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.requested_movements == 5
    assert "últimos 5 movimientos" in answer.text


async def test_show_me_a_few_recent_movements_defaults_to_a_short_list() -> None:
    ledger = ListingLedger()

    answer = await asked(
        "Enséñame un poco lo último que se ha movido en mis cuentas", GivesUp(), ledger
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.requested_movements == 5


@pytest.mark.parametrize(
    "sentence",
    ["Dame un ejemplo de esos movimientos", "Dame un ejemlo de esos movimientos"],
)
async def test_an_example_reuses_the_previous_query(sentence: str) -> None:
    planner = GivesUp()
    ledger = ListingLedger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_N_MONTHS, n=3),
        group_by=GroupBy.MERCHANT,
        metric=Metric.COUNT,
    )

    answer = await asked(
        sentence,
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.requested_movements == 1
    assert answer.movement_count == 1
    assert answer.text.startswith("Por ejemplo: Comercio 5")
    assert answer.conversation.last_plan == previous


async def test_a_natural_example_without_repeating_movements_reuses_the_query() -> None:
    ledger = ListingLedger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES",),
    )

    answer = await asked(
        "Ponme un par de ejemplos", GivesUp(), ledger, Conversation(last_plan=previous)
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.requested_movements == 2
    assert answer.movement_count == 2
    assert answer.text.startswith("Por ejemplo:")


async def test_a_difference_question_reads_the_two_groups_already_on_screen() -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES", "RESTAURANTS"),
        group_by=GroupBy.CATEGORY,
    )

    answer = await asked(
        "¿Cuánta diferencia hay?",
        GivesUp(),
        ComparisonLedger(),
        Conversation(last_plan=previous),
    )

    assert answer.text == "Hay 50,00 € de diferencia: supermercado supera a restaurantes."


async def test_after_that_names_the_second_group_in_the_previous_breakdown() -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES", "RESTAURANTS"),
        group_by=GroupBy.CATEGORY,
    )

    answer = await asked(
        "¿Y después de eso?",
        GivesUp(),
        ComparisonLedger(),
        Conversation(last_plan=previous),
    )

    assert answer.text.startswith("El siguiente es restaurantes: 10,00 €")


async def test_a_pronoun_can_ask_for_the_ranked_movements_date() -> None:
    previous = TopMovements(
        direction=Direction.INCOME,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("SALARY",),
        rank=2,
    )

    answer = await asked(
        "Esa, ¿de qué fecha es?", GivesUp(), Ledger(), Conversation(last_plan=previous)
    )

    assert answer.text == "Fue el 2 de julio."


@pytest.mark.parametrize("sentence", ["Compáralos", "¿En cuál gasté más?"])
async def test_two_consecutive_period_queries_can_be_compared_naturally(sentence: str) -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.period is not None
            total = 8_000 if plan.period.period is Period.LAST_MONTH else 11_500
            return QueryAnswer(currency="EUR", total_minor=total, movement_count=4)

    earlier = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("RESTAURANTS",),
        group_by=GroupBy.MERCHANT,
    )
    latest = replace(earlier, period=RelativePeriod(Period.THIS_MONTH), group_by=GroupBy.NONE)
    conversation = Conversation(last_plan=latest, prior_plan=earlier)
    planner = GivesUp()
    ledger = PeriodLedger()

    answer = await asked(sentence, planner, ledger, conversation)

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    assert all(plan.metric is Metric.SUM and plan.group_by is GroupBy.NONE for plan in ledger.plans)
    assert "115,00 €" in answer.text
    assert "80,00 €" in answer.text
    assert "35,00 € mayor" in answer.text
    assert answer.conversation.last_plan == latest
    assert answer.conversation.prior_plan == earlier


async def test_a_model_cannot_refuse_an_explicit_supported_average_per_movement() -> None:
    ledger = Ledger()

    answer = await asked(
        "¿Cuál fue el importe medio de las compras de gasolina el mes pasado?",
        GivesUp("AVERAGE"),
        ledger,
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE


async def test_a_category_average_comparison_answers_the_comparison_not_the_combined_mean() -> None:
    class ComparisonLedger(Ledger):
        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plan = plan
            return QueryAnswer(
                currency="EUR",
                total_minor=5_028,
                movement_count=8,
                groups=(Group("GROCERIES", 3_600, 6), Group("RESTAURANTS", 1_428, 2)),
            )

    planner = GivesUp()
    ledger = ComparisonLedger()

    answer = await asked(
        "¿Cuál tiene mayor gasto medio, supermercados o restaurantes, este año?",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.group_by is GroupBy.CATEGORY
    assert "más alta fue la de restaurantes" in answer.text
    assert "6,29 €" not in answer.text


@pytest.mark.parametrize(
    ("sentence", "expected_group"),
    [("¿Y al mes?", GroupBy.MONTH), ("¿Y por semana?", GroupBy.WEEK)],
)
async def test_a_short_period_follow_up_changes_the_average_divisor_without_the_model(
    sentence: str, expected_group: GroupBy
) -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("GROCERIES",),
        metric=Metric.AVERAGE,
    )

    await asked(sentence, planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE
    assert ledger.plan.group_by is expected_group
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.period == previous.period


async def test_the_reported_weekly_follow_up_keeps_the_year_and_the_average() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked("Y el semanal ?", planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.period == previous.period
    assert ledger.plan.group_by is GroupBy.WEEK
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE


async def test_weekly_income_average_keeps_its_account_scope_and_valid_grouping() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("SALARY",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
        only_account_id=ACCOUNT,
    )

    result = await asked("Y semanal?", planner, ledger, Conversation(last_plan=previous))

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.group_by is GroupBy.WEEK
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE
    assert ledger.plan.only_account_id == ACCOUNT


async def test_the_reported_highest_category_is_planned_without_the_model() -> None:
    planner = GivesUp()
    ledger = Ledger()

    result = await asked(
        "Vale en que categoria es la que he gastado mas a lo largo de este año?",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.direction is Direction.EXPENSE
    assert ledger.plan.group_by is GroupBy.CATEGORY
    assert ledger.plan.metric is Metric.SUM
    assert "categoría" in result.text.lower()


async def test_the_reported_account_breakdown_preserves_the_previous_query() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=4),
    )

    result = await asked(
        "Me harias el desglose por cuenta ? o esta todo de la cuenta nomina ?",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.direction is Direction.ALL
    assert ledger.plan.period == previous.period
    assert ledger.plan.group_by is GroupBy.ACCOUNT
    assert ledger.plan.across_accounts is True
    assert result.conversation.last_plan is not None


async def test_principal_and_salary_account_replies_focus_the_previous_query() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=4),
        group_by=GroupBy.ACCOUNT,
        across_accounts=True,
    )

    principal = await asked(
        "La principal",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.group_by is GroupBy.NONE
    assert ledger.plan.only_account_id == ACCOUNT
    assert principal.conversation.last_plan is not None

    salary_account = await asked(
        "En la que ingreso la nomina vaya ç",
        planner,
        ledger,
        principal.conversation,
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.only_account_id == ACCOUNT
    assert salary_account.outcome == PlanningOutcome.PLANNED.value


async def test_a_category_follow_up_keeps_the_year_instead_of_defaulting_to_last_month() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
    )

    await asked("Y en gasolina?", planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.period == previous.period
    assert ledger.plan.categories == ("TRANSPORT_FUEL",)


async def test_a_category_correction_replaces_only_the_category() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("TRANSPORT_FUEL",),
    )

    await asked(
        "Perdona queria decir restaurantes",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.period == previous.period
    assert ledger.plan.categories == ("RESTAURANTS",)


async def test_only_one_of_two_compared_categories_becomes_a_single_filter() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("GROCERIES", "RESTAURANTS"),
        group_by=GroupBy.CATEGORY,
        metric=Metric.AVERAGE,
    )

    await asked("Ahora solo supermercados", planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.group_by is GroupBy.NONE
    assert ledger.plan.metric is Metric.AVERAGE


async def test_short_count_and_sum_questions_patch_the_last_query() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("GROCERIES",),
        metric=Metric.AVERAGE,
    )

    counted = await asked(
        "Cuantas compras hice?", planner, ledger, Conversation(last_plan=previous)
    )
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.COUNT
    assert ledger.plan.period == previous.period

    await asked("Y cuanto gaste?", planner, ledger, counted.conversation)
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.SUM
    assert ledger.plan.categories == ("GROCERIES",)


async def test_a_comparison_reference_reuses_the_two_categories() -> None:
    planner = GivesUp()
    ledger = ComparisonLedger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("GROCERIES", "RESTAURANTS"),
        group_by=GroupBy.CATEGORY,
        metric=Metric.AVERAGE,
    )

    result = await asked(
        "Cual de las dos es mayor?", planner, ledger, Conversation(last_plan=previous)
    )

    assert planner.asked == 0
    assert ledger.plan == replace(previous, across_accounts=True)
    assert "medias por movimiento" in result.text.lower()


async def test_account_selection_can_include_two_and_exclude_the_fund() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(Period.THIS_MONTH),
        only_account_id=uuid.UUID(int=2),
    )

    await asked(
        "Solo cuenta corriente y hucha, no el fondo",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.only_account_id is None
    assert ledger.plan.only_account_ids == (ACCOUNT, uuid.UUID(int=2))


async def test_an_example_can_refer_to_income_instead_of_saying_movements() -> None:
    planner = GivesUp()
    ledger = ListingLedger()
    previous = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("SALARY",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    result = await asked(
        "Ponme un ejemplo de esos ingresos",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert result.outcome == PlanningOutcome.PLANNED.value
    assert result.movement_count == 1


async def test_per_movement_follow_up_clears_the_old_temporal_divisor() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("GROCERIES",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked("¿Y por movimiento?", planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE
    assert ledger.plan.group_by is GroupBy.NONE


async def test_ticket_average_does_not_inherit_a_monthly_period_divisor() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("GROCERIES",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked("¿Y el ticket medio?", planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE
    assert ledger.plan.group_by is GroupBy.NONE


async def test_a_complete_weekly_average_outranks_the_previous_monthly_context() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        categories=("HOUSING_RENT",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked(
        "¿Y el coste semanal medio en restaurantes?",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE
    assert ledger.plan.group_by is GroupBy.WEEK
    assert ledger.plan.categories == ("RESTAURANTS",)


@pytest.mark.parametrize(
    ("sentence", "previous_direction", "previous_category", "expected_direction"),
    [
        ("¿Cuánto suelo cobrar al mes?", Direction.EXPENSE, "GROCERIES", Direction.INCOME),
        ("¿Cuánto suelo gastar al mes?", Direction.INCOME, "SALARY", Direction.EXPENSE),
    ],
)
async def test_changing_between_income_and_spending_drops_an_incompatible_average_filter(
    sentence: str,
    previous_direction: Direction,
    previous_category: str,
    expected_direction: Direction,
) -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=previous_direction,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        categories=(previous_category,),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked(sentence, planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.direction is expected_direction
    assert ledger.plan.categories == ()
    assert ledger.plan.merchants == ()
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE


@pytest.mark.parametrize(
    ("sentence", "category", "period"),
    [
        ("¿Y en alquiler?", "HOUSING_RENT", Period.LAST_N_MONTHS),
        ("¿Y este mes?", "GROCERIES", Period.THIS_MONTH),
    ],
)
async def test_an_average_keeps_context_through_short_filter_and_window_follow_ups(
    sentence: str, category: str, period: Period
) -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        categories=("GROCERIES",),
        group_by=GroupBy.MONTH,
        metric=Metric.PERIOD_AVERAGE,
    )

    await asked(sentence, planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE
    assert ledger.plan.categories == (category,)
    assert ledger.plan.period is not None
    assert ledger.plan.period.period is period


async def test_a_model_cannot_refuse_a_supported_category_comparison() -> None:
    ledger = Ledger()

    answer = await asked(
        "Compara gasolina frente a restaurantes el mes pasado",
        GivesUp("COMPARISON"),
        ledger,
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.categories == ("TRANSPORT_FUEL", "RESTAURANTS")
    assert ledger.plan.group_by is GroupBy.CATEGORY


async def test_a_sentence_stating_nothing_certain_keeps_the_refusal() -> None:
    """No category this service is sure of, so there is no plan in the sentence to rescue. The
    decline stands, which is the honest outcome — this recovers a plan the words contain and never
    invents one."""
    answer = await asked("¿qué tal va la cosa este mes?", GivesUp())

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value
