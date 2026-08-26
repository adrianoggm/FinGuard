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
from finguard_orchestrator.domain.services import window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
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


class NoContacts:
    """An address book with nobody in it, which is a real state and not a missing dependency.

    This was `None` until a reader started consulting the directory about sentences in the past
    tense — "¿cuántas compras hice?" is one — and three tests here raised `AttributeError` on a
    build no deployment can make: the port is required precisely so an unwired one is found by
    mypy rather than by a user.
    """

    async def all_of(self, account_id, **kwargs):
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


class AsksAnyway(GivesUp):
    """A model asking for detail the sentence already states."""

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        return PlanningResult.needs_clarification(
            "¿Te refieres a supermercados en general o a un tipo específico de gasto?",
            awaiting=Slot.CATEGORY,
        )


class Ledger:
    def __init__(self) -> None:
        self.plan: FinancialPlan | None = None
        self.ranking: TopMovements | None = None

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
        contacts=NoContacts(),  # type: ignore[arg-type]
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


async def test_a_redundant_model_clarification_is_rescued_from_the_complete_sentence() -> None:
    planner = AsksAnyway()
    ledger = Ledger()

    answer = await asked(
        "¿Cuánto gasté en supermercados el mes pasado sin Mercadona?",
        planner,
        ledger,
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 1
    assert ledger.plan is not None
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.excluded_merchants == ("mercadona",)


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

    planner = GivesUp()
    ledger = Ledger()
    answer = await asked(
        "¿Y si no contamos el alquiler?", planner, ledger, Conversation(last_plan=previous)
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.categories == ()
    assert ledger.plan.excluded_categories == ("HOUSING_RENT",)
    assert answer.conversation.last_plan == replace(previous, excluded_categories=("HOUSING_RENT",))


async def test_a_natural_category_comparison_asks_for_its_missing_period() -> None:
    answer = await asked(
        "¿Voy gastando más en el súper o comiendo fuera?", GivesUp(), ComparisonLedger()
    )

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert answer.conversation.awaiting is Slot.WINDOW
    assert isinstance(answer.conversation.active, FinancialClarification)
    assert answer.conversation.active.draft.categories == ("GROCERIES", "RESTAURANTS")


async def test_a_courtesy_prefix_does_not_reset_the_comparison_window() -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES", "RESTAURANTS"),
        group_by=GroupBy.CATEGORY,
    )
    ledger = Ledger()

    await asked(
        "Vale, quédate solo con el súper",
        GivesUp(),
        ledger,
        Conversation(last_plan=previous),
    )

    assert ledger.plan is not None
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.period is not None
    assert ledger.plan.period.period is Period.THIS_YEAR


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
    assert listed.conversation.referenced_transaction_ids == selected

    broken_down = await asked("Sepáralos por cuenta", planner, ledger, listed.conversation)

    assert planner.asked == 0
    assert broken_down.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.group_by is GroupBy.ACCOUNT
    assert ledger.plan.transaction_ids == selected


async def test_those_movements_select_the_exact_rows_that_were_shown() -> None:
    planner = GivesUp()
    ledger = ListingLedger()
    listed = await asked("Muéstrame los cinco últimos movimientos", planner, ledger)
    selected = listed.conversation.referenced_transaction_ids

    answer = await asked("¿Cuánto suman esos movimientos?", planner, ledger, listed.conversation)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.transaction_ids == selected
    assert ledger.plan.group_by is GroupBy.NONE


async def test_that_merchant_reuses_the_highlighted_breakdown_row() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.MERCHANT,
    )
    conversation = Conversation(
        last_plan=previous,
        referenced_group_by=GroupBy.MERCHANT,
        referenced_group_key="Mercadona",
    )

    await asked("¿Cuánto gasté en ese comercio?", planner, ledger, conversation)

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.merchants == ("Mercadona",)
    assert ledger.plan.group_by is GroupBy.NONE


async def test_that_account_reuses_the_highlighted_breakdown_row() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.ACCOUNT,
        across_accounts=True,
    )
    conversation = Conversation(
        last_plan=previous,
        referenced_group_by=GroupBy.ACCOUNT,
        referenced_group_key="Hucha",
    )

    await asked("¿Cuántos ingresos tuvo esa cuenta?", planner, ledger, conversation)

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.only_account_id == uuid.UUID(int=2)
    assert ledger.plan.across_accounts is False
    assert ledger.plan.group_by is GroupBy.NONE
    assert ledger.plan.metric is Metric.COUNT


@pytest.mark.parametrize(
    ("sentence", "excluded_categories", "excluded_merchants"),
    [
        ("Todos menos alquiler", ("HOUSING_RENT",), ()),
        ("Lo mismo, pero sin Mercadona", (), ("mercadona",)),
    ],
)
async def test_a_negative_filter_patches_the_query_without_the_model(
    sentence: str,
    excluded_categories: tuple[str, ...],
    excluded_merchants: tuple[str, ...],
) -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
    )

    answer = await asked(sentence, planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.excluded_categories == excluded_categories
    assert ledger.plan.excluded_merchants == excluded_merchants
    assert "sin" in answer.text.lower()


@pytest.mark.parametrize("sentence", ["Vuelve a la consulta anterior", "Quita ese filtro"])
async def test_a_user_can_restore_the_query_before_the_last_filter(sentence: str) -> None:
    planner = GivesUp()
    ledger = Ledger()
    original = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
    )
    filtered = replace(original, excluded_categories=("HOUSING_RENT",))
    conversation = Conversation(last_plan=filtered, prior_plan=original)

    answer = await asked(sentence, planner, ledger, conversation)

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.excluded_categories == ()
    assert ledger.plan.categories == original.categories
    assert ledger.plan.period == original.period
    assert answer.text.startswith("He vuelto a la consulta anterior.")
    assert answer.conversation.last_plan == original
    assert answer.conversation.prior_plan == filtered


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


async def test_a_natural_monthly_average_follow_up_keeps_the_salary_query() -> None:
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("SALARY",),
    )

    await asked("¿Eso cuánto sale al mes?", GivesUp(), ledger, Conversation(last_plan=previous))

    assert ledger.plan is not None
    assert ledger.plan.categories == ("SALARY",)
    assert ledger.plan.metric is Metric.PERIOD_AVERAGE
    assert ledger.plan.group_by is GroupBy.MONTH


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

    planner = GivesUp()
    ledger = ComparisonLedger()
    answer = await asked(
        "¿Y después de eso?",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert answer.text.startswith("El siguiente es restaurantes: 10,00 €")
    assert answer.conversation.referenced_group_by is GroupBy.CATEGORY
    assert answer.conversation.referenced_group_key == "RESTAURANTS"
    assert answer.conversation.result_reference is not None
    assert answer.conversation.result_reference.source_query is answer.conversation.last_plan
    assert answer.conversation.result_reference.category == "RESTAURANTS"

    selected = await asked(
        "¿Cuánto gasté en esa categoría?",
        planner,
        ledger,
        answer.conversation,
    )

    assert planner.asked == 0
    assert selected.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.categories == ("RESTAURANTS",)


async def test_next_merchant_keeps_the_category_scope_and_visible_evidence() -> None:
    evidence = (uuid.UUID(int=71), uuid.UUID(int=72))

    class MerchantBreakdownLedger(Ledger):
        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plan = plan
            movements = tuple(
                Movement(
                    occurred_at=datetime(2026, 8, 5 - index, 10, 0, tzinfo=UTC),
                    description=merchant,
                    amount_minor=-amount,
                    direction="EXPENSE",
                    merchant_name=merchant,
                    category="GROCERIES",
                    currency="EUR",
                    transaction_id=str(transaction_id),
                )
                for index, (merchant, amount, transaction_id) in enumerate(
                    zip(("Mercadona", "Carrefour"), (6_000, 1_000), evidence, strict=True)
                )
            )
            return QueryAnswer(
                currency="EUR",
                total_minor=7_000,
                movement_count=2,
                groups=(Group("Mercadona", 6_000, 1), Group("Carrefour", 1_000, 1)),
                movements=movements,
            )

    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        categories=("GROCERIES",),
        group_by=GroupBy.MERCHANT,
    )
    ledger = MerchantBreakdownLedger()

    answer = await asked(
        "¿Y después de eso?",
        GivesUp(),
        ledger,
        Conversation(last_plan=previous),
    )

    reference = answer.conversation.result_reference
    assert reference is not None
    assert reference.source_query is answer.conversation.last_plan
    assert reference.category == "GROCERIES"
    assert reference.group_by is GroupBy.MERCHANT
    assert reference.group_key == "Carrefour"
    assert reference.transaction_ids == evidence


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


async def test_a_ranked_movement_can_be_reused_as_an_exact_ledger_row() -> None:
    class IdentifiedRankingLedger(Ledger):
        async def ranked(self, ranking: TopMovements, **kwargs: object) -> Movement:
            found = await super().ranked(ranking, **kwargs)
            return replace(found, transaction_id=str(uuid.UUID(int=81)))

    planner = GivesUp()
    ledger = IdentifiedRankingLedger()
    ranked = await asked("¿Cuál fue mi gasto más alto este año?", planner, ledger)

    await asked("¿Cuánto fue ese movimiento?", planner, ledger, ranked.conversation)

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.transaction_ids == (uuid.UUID(int=81),)


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


@pytest.mark.parametrize(
    ("sentence", "earlier_period", "latest_period"),
    [
        (
            "\u00bfGast\u00e9 m\u00e1s este mes que el mes pasado?",
            Period.LAST_MONTH,
            Period.THIS_MONTH,
        ),
        ("Compara esta semana con la semana pasada", Period.LAST_WEEK, Period.THIS_WEEK),
    ],
)
async def test_two_periods_can_be_compared_in_one_natural_sentence(
    sentence: str, earlier_period: Period, latest_period: Period
) -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=3)

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(sentence, planner, ledger)

    assert planner.asked == 0
    assert [plan.period.period for plan in ledger.plans if plan.period] == [
        earlier_period,
        latest_period,
    ]
    assert answer.conversation.prior_plan is not None
    assert answer.conversation.last_plan is not None


@pytest.mark.parametrize(
    ("sentence", "called_starts", "first_label", "second_label"),
    [
        (
            "\u00bfGast\u00e9 m\u00e1s en julio que en junio?",
            [date(2026, 6, 1), date(2026, 7, 1)],
            "julio de 2026",
            "junio de 2026",
        ),
        (
            "Compara mis ingresos de 2025 con 2026",
            [date(2026, 1, 1), date(2025, 1, 1)],
            "en 2025",
            "en 2026",
        ),
    ],
)
async def test_two_new_named_periods_are_compared_without_the_model(
    sentence: str,
    called_starts: list[date],
    first_label: str,
    second_label: str,
) -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(
                currency="EUR",
                total_minor=10_000 + len(self.plans) * 1_000,
                movement_count=3,
            )

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(sentence, planner, ledger)

    assert planner.asked == 0
    assert [plan.date_range.start for plan in ledger.plans if plan.date_range] == called_starts
    assert first_label in answer.text
    assert second_label in answer.text
    assert answer.conversation.prior_plan is not None
    assert answer.conversation.last_plan is not None


async def test_an_elliptical_two_month_comparison_reuses_the_validated_query_scope() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=3)

    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("GROCERIES",),
        excluded_merchants=("mercadona",),
        only_account_id=ACCOUNT,
        group_by=GroupBy.MERCHANT,
        metric=Metric.AVERAGE,
    )
    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    await asked(
        "Comparame julio y junio",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    assert all(plan.categories == ("GROCERIES",) for plan in ledger.plans)
    assert all(plan.excluded_merchants == ("mercadona",) for plan in ledger.plans)
    assert all(plan.only_account_id == ACCOUNT for plan in ledger.plans)
    assert all(
        plan.group_by is GroupBy.NONE and plan.metric is Metric.AVERAGE for plan in ledger.plans
    )


async def test_an_arbitrary_range_can_be_compared_with_the_same_period_last_year() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            total = 15_000 if plan.date_range.start.year == 2026 else 12_000
            return QueryAnswer(currency="EUR", total_minor=total, movement_count=4)

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Compara mis gastos del 1 de marzo al 15 de abril de 2026 "
        "con el mismo periodo del año pasado",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert [plan.date_range for plan in ledger.plans] == [
        AbsoluteRange(date(2025, 3, 1), date(2025, 4, 15)),
        AbsoluteRange(date(2026, 3, 1), date(2026, 4, 15)),
    ]
    assert "30,00 € mayor" in answer.text
    assert answer.conversation.last_comparison is not None


async def test_changing_amount_to_count_only_changes_the_range_comparison_metric() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=3)

    ranges = (
        "del 1 de marzo al 15 de abril de 2026 con el periodo del 1 de mayo al 15 de junio de 2026"
    )
    amount_ledger = PeriodLedger()
    count_ledger = PeriodLedger()

    await asked(f"Compara mis gastos {ranges}", GivesUp("COMPARISON"), amount_ledger)
    count_answer = await asked(
        f"Compara cuántos movimientos de gasto tuve {ranges}",
        GivesUp("COMPARISON"),
        count_ledger,
    )

    assert [plan.date_range for plan in amount_ledger.plans] == [
        plan.date_range for plan in count_ledger.plans
    ]
    assert all(plan.metric is Metric.SUM for plan in amount_ledger.plans)
    assert all(plan.metric is Metric.COUNT for plan in count_ledger.plans)
    assert all(
        replace(plan, metric=Metric.SUM) == amount
        for plan, amount in zip(count_ledger.plans, amount_ledger.plans, strict=True)
    )

    repeated = PeriodLedger()
    await asked(
        "Compáralos otra vez",
        GivesUp("COMPARISON"),
        repeated,
        count_answer.conversation,
    )

    assert all(plan.metric is Metric.COUNT for plan in repeated.plans)


async def test_an_explicit_salary_comparison_does_not_inherit_an_old_expense_filter() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=244_287, movement_count=1)

    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("RESTAURANTS",),
        merchants=("bar manolo",),
    )
    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Compara mi nomina de julio con junio",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert all(plan.direction is Direction.INCOME for plan in ledger.plans)
    assert all(plan.categories == ("SALARY",) for plan in ledger.plans)
    assert all(not plan.merchants for plan in ledger.plans)
    assert all(plan.across_accounts and plan.only_account_id is None for plan in ledger.plans)
    assert "ingreso" in answer.text


async def test_a_direct_comparison_remembers_the_named_account_that_actually_ran() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=3)

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Compara mis gastos de la cuenta principal en julio y junio",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert all(plan.only_account_id == ACCOUNT for plan in ledger.plans)
    assert answer.conversation.prior_plan is not None
    assert answer.conversation.last_plan is not None
    assert answer.conversation.prior_plan.only_account_id == ACCOUNT
    assert answer.conversation.last_plan.only_account_id == ACCOUNT


async def test_compare_it_with_the_previous_month_reuses_the_whole_query_scope() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=3)

    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        categories=("GROCERIES",),
        excluded_merchants=("mercadona",),
        only_account_id=ACCOUNT,
        metric=Metric.AVERAGE,
        group_by=GroupBy.MERCHANT,
    )
    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Comp\u00e1ralo con el mes anterior",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    earlier, latest = ledger.plans
    assert earlier.period == RelativePeriod(Period.LAST_MONTH)
    assert latest.period == RelativePeriod(Period.THIS_MONTH)
    assert earlier.categories == latest.categories == ("GROCERIES",)
    assert earlier.excluded_merchants == latest.excluded_merchants == ("mercadona",)
    assert earlier.only_account_id == latest.only_account_id == ACCOUNT
    assert all(
        plan.metric is Metric.AVERAGE and plan.group_by is GroupBy.NONE for plan in ledger.plans
    )
    assert answer.conversation.prior_plan is not None


async def test_a_direct_period_comparison_counts_movements_instead_of_money() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            if plan.date_range.start.month == 6:
                return QueryAnswer(currency="EUR", total_minor=90_000, movement_count=9)
            return QueryAnswer(currency="EUR", total_minor=10_000, movement_count=12)

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "¿En qué mes hice más compras, julio o junio?",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    assert all(plan.metric is Metric.COUNT for plan in ledger.plans)
    assert "En julio de 2026: 12 movimientos" in answer.text
    assert "junio de 2026: 9 movimientos" in answer.text
    assert "3 movimientos más en julio de 2026" in answer.text
    assert answer.total_minor is None
    assert isinstance(answer.conversation.last_plan, FinancialPlan)
    assert answer.conversation.last_plan.metric is Metric.COUNT


async def test_a_direct_period_comparison_uses_the_mean_not_the_total() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            if plan.date_range.start.month == 6:
                return QueryAnswer(currency="EUR", total_minor=12_000, movement_count=6)
            return QueryAnswer(currency="EUR", total_minor=9_000, movement_count=3)

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "¿En qué mes fue mayor mi gasto medio por movimiento, julio o junio?",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert all(plan.metric is Metric.AVERAGE for plan in ledger.plans)
    assert "En julio de 2026: 30,00 €" in answer.text
    assert "junio de 2026: 20,00 €" in answer.text
    assert "importe medio por movimiento fue 10,00 € mayor" in answer.text


async def test_a_direct_period_comparison_preserves_the_calendar_average() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            total = 1_200_000 if plan.date_range.start.year == 2025 else 600_000
            return QueryAnswer(
                currency="EUR",
                total_minor=total,
                movement_count=24,
                period_start=plan.date_range.start,
                period_end=plan.date_range.end,
            )

    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Compara mi gasto medio mensual de 2025 con 2026",
        planner,
        ledger,
    )

    assert planner.asked == 0
    assert all(plan.metric is Metric.PERIOD_AVERAGE for plan in ledger.plans)
    assert all(plan.group_by is GroupBy.MONTH for plan in ledger.plans)
    assert "En 2025: 1.000,00 €" in answer.text
    assert "en 2026: 500,00 €" in answer.text
    assert "gasto medio mensual fue 500,00 € mayor en 2025" in answer.text


async def test_where_payroll_landed_keeps_the_account_question_through_period_clarification() -> (
    None
):
    planner = GivesUp()

    first = await asked(
        "Sabrias decirme si he cobrado mi nomina en que cuenta?",
        planner,
    )

    assert planner.asked == 0
    assert isinstance(first.conversation.active, FinancialClarification)
    assert "n\u00f3mina" in first.text
    assert "cuenta" in first.text
    draft = first.conversation.active.draft
    assert draft.categories == ("SALARY",)
    assert draft.direction is Direction.INCOME
    assert draft.group_by is GroupBy.ACCOUNT

    ledger = Ledger()
    second = await asked(
        "Pues prueba el mes 7 y si no el 8",
        planner,
        ledger,
        first.conversation,
    )

    assert second.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.date_range is not None
    assert ledger.plan.date_range.start == date(2026, 7, 1)
    assert ledger.plan.date_range.end == date(2026, 7, 31)
    assert ledger.plan.group_by is GroupBy.ACCOUNT
    assert ledger.plan.categories == ("SALARY",)


async def test_a_second_numbered_month_is_only_queried_when_the_first_has_no_payroll() -> None:
    class FallbackLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            if plan.date_range.start.month == 7:
                return QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
            return QueryAnswer(
                currency="EUR",
                total_minor=244_287,
                movement_count=1,
                groups=(Group("Cuenta corriente", 244_287, 1),),
            )

    planner = GivesUp()
    first = await asked(
        "Sabrias decirme si he cobrado mi nomina en que cuenta?",
        planner,
    )
    ledger = FallbackLedger()

    answer = await asked(
        "Pues prueba el mes 7 y si no el 8",
        planner,
        ledger,
        first.conversation,
    )

    assert [plan.date_range.start.month for plan in ledger.plans if plan.date_range] == [7, 8]
    assert "julio de 2026" in answer.text
    assert "agosto de 2026" in answer.text
    assert answer.conversation.last_plan is not None
    assert answer.conversation.last_plan.date_range == window_words.whole_month(2026, 8)


async def test_check_it_against_previous_month_is_anchored_to_the_payroll_month_in_view() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            amount = 246_562 if plan.date_range and plan.date_range.start.month == 6 else 244_287
            return QueryAnswer(currency="EUR", total_minor=amount, movement_count=1)

    july = FinancialPlan(
        direction=Direction.INCOME,
        date_range=window_words.whole_month(2026, 7),
        categories=("SALARY",),
        group_by=GroupBy.ACCOUNT,
        across_accounts=True,
    )
    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Vale compruebalo con el mes anterior",
        planner,
        ledger,
        Conversation(last_plan=july),
    )

    assert planner.asked == 0
    assert [plan.date_range.start.month for plan in ledger.plans if plan.date_range] == [6, 7]
    assert all(plan.categories == ("SALARY",) for plan in ledger.plans)
    assert "junio de 2026" in answer.text
    assert "julio de 2026" in answer.text


async def test_this_payroll_can_be_compared_with_all_the_previous_ones() -> None:
    class PayrollLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
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
    conversation = Conversation(last_plan=july)
    planner = GivesUp("COMPARISON")
    ledger = PayrollLedger()

    answer = await asked(
        "He cobrado mas en esa nomina que en las anteriores?",
        planner,
        ledger,
        conversation,
    )

    assert planner.asked == 0
    assert [plan.group_by for plan in ledger.plans] == [GroupBy.NONE, GroupBy.MONTH]
    assert "junio de 2026" in answer.text
    assert "2.465,62" in answer.text
    # Everything the next turn branches on is untouched. The log is not: every turn appends to it,
    # including this one, which is the one part of a conversation that is never left alone.
    assert replace(answer.conversation, turns=()) == conversation
    assert len(answer.conversation.turns) == 1


async def test_which_payroll_was_higher_compares_the_two_months_in_memory() -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.date_range is not None
            amount = 246_562 if plan.date_range.start.month == 6 else 244_287
            return QueryAnswer(currency="EUR", total_minor=amount, movement_count=1)

    july = FinancialPlan(
        direction=Direction.INCOME,
        date_range=window_words.whole_month(2026, 7),
        categories=("SALARY",),
        across_accounts=True,
    )
    june = replace(july, date_range=window_words.whole_month(2026, 6))
    planner = GivesUp("COMPARISON")
    ledger = PeriodLedger()

    answer = await asked(
        "Entonces cual he cobrado mas?",
        planner,
        ledger,
        Conversation(last_plan=june, prior_plan=july),
    )

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    assert "junio de 2026" in answer.text
    assert "mayor" in answer.text


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
    ledger = ComparisonLedger()

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
    assert result.conversation.referenced_group_by is GroupBy.CATEGORY
    assert result.conversation.referenced_group_key == "GROCERIES"


async def test_the_highlighted_category_can_be_reused_by_pronoun() -> None:
    planner = GivesUp()
    ledger = ComparisonLedger()

    first = await asked(
        "En qu\u00e9 categor\u00eda gast\u00e9 m\u00e1s este a\u00f1o?",
        planner,
        ledger,
    )
    assert first.conversation.referenced_category == "GROCERIES"

    second = await asked(
        "Y esa categor\u00eda el mes pasado?",
        planner,
        ledger,
        first.conversation,
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.period == RelativePeriod(Period.LAST_MONTH)
    assert ledger.plan.group_by is GroupBy.NONE
    assert second.conversation.referenced_category == "GROCERIES"


async def test_frustration_acknowledges_the_failure_without_opening_an_unrelated_slot() -> None:
    planner = GivesUp()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_WEEK),
        group_by=GroupBy.WEEK,
    )

    result = await asked("Que te pasa ?", planner, conversation=Conversation(last_plan=previous))

    assert planner.asked == 0
    assert result.outcome == "GUIDANCE"
    assert result.conversation.last_plan == previous
    assert result.conversation.active is None


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


async def test_a_natural_account_correction_keeps_the_financial_question() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("GROCERIES",),
        only_account_id=uuid.UUID(int=2),
    )

    await asked(
        "No, quer\u00eda decir la cuenta principal",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.only_account_id == ACCOUNT
    assert ledger.plan.period == previous.period
    assert ledger.plan.categories == previous.categories


async def test_a_complete_account_question_does_not_inherit_an_old_category() -> None:
    class FreshPlanner(GivesUp):
        async def plan(self, *_: object, **kwargs: object) -> PlanningResult:
            self.asked += 1
            assert kwargs.get("previous") is None
            return PlanningResult(
                outcome=PlanningOutcome.PLANNED,
                plan=FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                    # A model may confuse the noun "cuenta" with a request to group by account.
                    # The explicit singular account in the sentence must still win.
                    group_by=GroupBy.ACCOUNT,
                ),
            )

    planner = FreshPlanner()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("RESTAURANTS",),
    )

    await asked(
        "Cuanto gaste en la cuenta principal este mes?",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 1
    assert ledger.plan is not None
    assert ledger.plan.categories == ()
    assert ledger.plan.only_account_id == ACCOUNT
    assert ledger.plan.group_by is GroupBy.NONE


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


@pytest.mark.parametrize(
    "sentence",
    [
        "No, me refer\u00eda a supermercados",
        "Quer\u00eda decir supermercados",
        "Mejor solo supermercados",
    ],
)
async def test_more_natural_category_corrections_are_deterministic(sentence: str) -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("RESTAURANTS",),
    )

    await asked(sentence, planner, ledger, Conversation(last_plan=previous))

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.categories == ("GROCERIES",)
    assert ledger.plan.period == previous.period


async def test_a_natural_direction_correction_keeps_the_rest_of_the_query() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        merchants=("FinGuard",),
    )

    await asked(
        "No, quer\u00eda decir solo ingresos",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.direction is Direction.INCOME
    assert ledger.plan.period == previous.period
    assert ledger.plan.merchants == previous.merchants


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


async def test_a_superlative_inherits_the_average_window_and_filter() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("HOUSING_RENT",),
        metric=Metric.AVERAGE,
    )

    result = await asked(
        "Cual ha sido el gasto mas alto?", planner, ledger, Conversation(last_plan=previous)
    )

    assert planner.asked == 0
    assert ledger.ranking is not None
    assert ledger.ranking.period == previous.period
    assert ledger.ranking.categories == previous.categories
    assert result.outcome == PlanningOutcome.PLANNED.value


async def test_the_ranked_income_can_be_classified_without_starting_a_payment() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = TopMovements(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("SALARY",),
    )

    result = await asked(
        "Eso fue nomina o un bizum?", planner, ledger, Conversation(last_plan=previous)
    )

    assert planner.asked == 0
    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "nómina" in result.text
    assert "no un bizum" in result.text


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


async def test_other_accounts_are_the_complement_of_the_previous_selection() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(Period.THIS_MONTH),
        only_account_id=ACCOUNT,
    )

    result = await asked(
        "Y las dem\u00e1s cuentas?",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.only_account_id == uuid.UUID(int=2)
    assert ledger.plan.only_account_ids == ()
    assert ledger.plan.include_internal is True
    assert result.conversation.last_plan is not None


async def test_other_accounts_do_not_widen_a_query_that_already_included_all() -> None:
    planner = GivesUp()
    ledger = Ledger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        across_accounts=True,
    )

    result = await asked(
        "Ahora las otras cuentas",
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert planner.asked == 0
    assert ledger.plan is None
    assert result.outcome == "GUIDANCE"
    assert "ya inclu\u00eda todas" in result.text


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
