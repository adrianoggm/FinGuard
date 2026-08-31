"""Calendar accumulations and configurable rankings stay typed through conversation."""

import uuid
from datetime import date, datetime

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers import dimensions, metrics, rankings
from finguard_orchestrator.domain import constants
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import cumulative_calculator, explicit_facts, spanish
from finguard_orchestrator.domain.value_objects import financial_plan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 9)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("a closed accumulation or group ranking must not reach the model")


class Ledger:
    def __init__(self, answer: QueryAnswer) -> None:
        self.answer = answer
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return self.answer


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 100_000, 100_000, datetime(2026, 8, 9, 10))


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(sentence: str, ledger: Ledger, conversation: Conversation = IDLE):  # type: ignore[no-untyped-def]
    planner = ModelMustNotPlan()
    result = await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )
    return result, planner


def temporal_answer() -> QueryAnswer:
    return QueryAnswer(
        currency="EUR",
        total_minor=15_000,
        movement_count=3,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 4, 30),
        groups=(Group("2026-01", 10_000, 2), Group("2026-03", 5_000, 1)),
    )


async def test_monthly_accumulation_fills_empty_months_without_inventing_money() -> None:
    ledger = Ledger(temporal_answer())

    result, planner = await ask("¿Cuánto llevaba gastado al acabar cada mes este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[0].metric is Metric.CUMULATIVE
    assert ledger.plans[0].group_by is GroupBy.MONTH
    assert ledger.plans[0].direction is Direction.EXPENSE
    assert [(group.key, group.total_minor, group.movement_count) for group in result.groups] == [
        ("2026-01", 10_000, 2),
        ("2026-02", 10_000, 2),
        ("2026-03", 15_000, 3),
        ("2026-04", 15_000, 3),
    ]
    assert "gasto acumulado" in result.text
    assert planner.calls == 0


async def test_accumulation_keeps_its_metric_when_the_user_changes_calendar_unit() -> None:
    ledger = Ledger(temporal_answer())
    first, _ = await ask("Muéstrame mi gasto acumulado por mes este año", ledger)

    second, planner = await ask("¿Y por semana?", ledger, first.conversation)

    assert ledger.plans[-1].metric is Metric.CUMULATIVE
    assert ledger.plans[-1].group_by is GroupBy.WEEK
    assert "gasto acumulado" in second.text
    assert planner.calls == 0


def test_weekly_accumulation_uses_calendar_mondays_and_carries_gaps() -> None:
    answer = QueryAnswer(
        currency="EUR",
        total_minor=3_000,
        movement_count=2,
        period_start=date(2026, 1, 7),
        period_end=date(2026, 1, 25),
        groups=(Group("2026-01-05", 1_000, 1), Group("2026-01-19", 2_000, 1)),
    )

    accumulated = cumulative_calculator.accumulate(answer, GroupBy.WEEK)

    assert [(group.key, group.total_minor) for group in accumulated.groups] == [
        ("2026-01-05", 1_000),
        ("2026-01-12", 1_000),
        ("2026-01-19", 3_000),
    ]


def ranked_answer() -> QueryAnswer:
    return QueryAnswer(
        currency="EUR",
        total_minor=50_000,
        movement_count=20,
        groups=(
            Group("Mercadona", 18_000, 6),
            Group("Carrefour", 13_000, 5),
            Group("Lidl", 9_000, 4),
            Group("Dia", 6_000, 3),
            Group("Alcampo", 4_000, 2),
        ),
    )


async def test_top_three_merchants_by_spend_is_an_aggregate_ranking() -> None:
    ledger = Ledger(ranked_answer())

    result, planner = await ask("Los tres comercios donde más gasté este año", ledger)

    plan = ledger.plans[0]
    assert plan.group_by is GroupBy.MERCHANT
    assert plan.metric is Metric.SUM
    assert plan.group_limit == 3
    assert [group.key for group in result.groups] == ["Mercadona", "Carrefour", "Lidl"]
    assert result.total_minor == 40_000
    assert result.movement_count == 15
    assert result.movements == []
    assert "3 comercios con mayor importe total" in result.text
    assert planner.calls == 0


async def test_category_ranking_by_operations_preserves_the_requested_unit() -> None:
    ledger = Ledger(ranked_answer())

    result, planner = await ask("Top 5 categorías con más operaciones este año", ledger)

    assert ledger.plans[0].group_by is GroupBy.CATEGORY
    assert ledger.plans[0].metric is Metric.COUNT
    assert ledger.plans[0].group_limit == 5
    assert "más operaciones" in result.text
    assert "Mercadona: 6" in result.text
    assert planner.calls == 0


async def test_ranking_criterion_can_change_naturally_in_the_next_turn() -> None:
    ledger = Ledger(ranked_answer())
    first, _ = await ask("Top 3 comercios por mayor ticket medio este año", ledger)

    second, planner = await ask("¿Y por número de operaciones?", ledger, first.conversation)

    assert ledger.plans[0].metric is Metric.AVERAGE
    assert ledger.plans[-1].metric is Metric.COUNT
    assert ledger.plans[-1].group_by is GroupBy.MERCHANT
    assert ledger.plans[-1].group_limit == 3
    assert "más operaciones" in second.text
    assert planner.calls == 0


def test_a_named_dimension_is_not_overruled_by_the_one_before_it() -> None:
    """Two turns about comercios, then one about categorías, read by the recogniser that loses.

    Found by measuring the cascade rather than by a report: two recognisers claim
    "Ahora top 2 categorías por ticket medio" and read it differently — the ranking one by the
    word the sentence uses, the average one by inheriting the grouping of the turn before — and
    only their order in `_steps` decided which ran. The episode whose stated rule is
    "merchant/category must alter only the named dimension" was passing on that order.

    So this calls the losing one directly. End to end the turn was always right, which is exactly
    why the misreading survived: it is unreachable, and one moved branch from being on the wire.
    """
    about_merchants = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.MERCHANT,
        metric=Metric.SUM,
        group_limit=3,
    )

    reading = metrics._average_plan(
        AskFinancialQuestionQuery(
            sentence="Ahora top 2 categorías por ticket medio",
            account_id=ACCOUNT,
            today=TODAY,
            conversation=Conversation(last_plan=about_merchants),
        )
    )

    assert isinstance(reading, FinancialPlan)
    assert reading.group_by is GroupBy.CATEGORY, "the sentence says categorías, not comercios"
    assert reading.metric is Metric.AVERAGE


def test_cumulative_and_group_rankings_reject_meaningless_shapes() -> None:
    with pytest.raises(InvalidValueError, match="CUMULATIVE requires"):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_YEAR),
            metric=Metric.CUMULATIVE,
        )
    with pytest.raises(InvalidValueError, match="groupLimit requires"):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_YEAR),
            group_limit=3,
        )


def _ranking_for(sentence: str) -> FinancialPlan | None:
    return rankings._group_ranking_plan(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    )


def test_a_ranking_shows_the_number_of_rows_it_was_asked_for() -> None:
    """The number the sentence says wins, and asking for too many is not a failure.

    Both of these used to be wrong, in opposite directions. "top 25 gastos por categoría" put the
    count in a different half of the sentence from the dimension, missed the pattern that wanted
    them glued together, and fell through to the default of five: a question about twenty-five
    answered with five rows and not a word about it. "top 25 comercios" did match, and then
    ``FinancialPlan`` raised on it, because the row ceiling was the same constant that keeps a
    spoken list of individual movements short.
    """
    unglued = _ranking_for("dame mis top 25 gastos por categoria")
    assert unglued is not None and unglued.group_by is GroupBy.CATEGORY
    assert unglued.group_limit == 25

    glued = _ranking_for("dame una comparativa de mis top 25 comercios")
    assert glued is not None and glued.group_by is GroupBy.MERCHANT
    assert glued.group_limit == 25

    # Clamped rather than refused: the slice yields whatever exists, so an account with twelve
    # merchants answers "top 250" with twelve and hides nothing.
    assert (plan := _ranking_for("mis top 250 comercios")) is not None
    assert plan.group_limit == 100

    # The phrasings the suite already covered read exactly as before.
    assert (three := _ranking_for("Los tres comercios donde mas gaste este anyo")) is not None
    assert three.group_limit == 3
    # And a plain breakdown still asks for no ranking at all.
    assert _ranking_for("cuanto gasto por comercio") is None


def test_the_all_accounts_scope_is_not_a_grouping_instruction() -> None:
    """«en todas mis cuentas» says where to look, not what to group by.

    Measured against the running service on 2026-08-28, token `demo-token-laura-pays`: "cuál fue mi
    mayor gasto de julio en todas mis cuentas" came back `group_by=ACCOUNT group_limit=5` and «La
    cuenta con mayor importe total del 2026-07-01 al 2026-07-31 es: Cuenta corriente…» — a ranking
    of accounts offered as the answer to a question about one charge. `_GROUP_RANKING_DIMENSION` was
    a bare noun alternation, so the noun *inside the scope phrase* supplied the dimension, and this
    recogniser runs ahead of the planner: the superlative was never read by anything.

    The asymmetry is why a thousand turns of corpus never saw it. "cuánto gasté en julio en todas
    mis cuentas" carries the same noun and no ranking signal, so it was always a plain total. Only a
    superlative *plus* the scope phrase changed meaning.
    """
    hijacked = _ranking_for("¿cuál fue mi mayor gasto de julio en todas mis cuentas?")

    assert hijacked is None, (
        "the scope phrase supplied the dimension: "
        f"group_by={hijacked.group_by if hijacked else None}, "
        f"group_limit={hijacked.group_limit if hijacked else None}"
    )
    named = dimensions._named_group_dimension
    assert named(spanish.folded("¿cuál fue mi mayor gasto de julio en todas mis cuentas?")) is None
    # The widening half of the same vocabulary, for the same reason: "todas las categorías" asks
    # for a filter to disappear, which is the opposite of asking for one row each.
    assert named(spanish.folded("mi mayor gasto de julio en todas las categorías")) is None


def test_a_genuine_grouping_instruction_still_ranks_accounts() -> None:
    """The other half, and the whole risk of the guard above.

    «por cuenta», «en qué cuenta», «de cada cuenta» and «las cuentas con más gastos» are all
    instructions to group, and every one of them has to survive a guard that refuses «todas mis
    cuentas». What makes that line narrow is that the scope phrase puts a possessive directly in
    front of the noun, exactly as two of the grouping phrasings do — so the discriminator is the
    universal quantifier, not the determiner, and it cannot be "the noun needs a preposition in
    front of it": four of the phrasings below put a number or an article there instead.
    """
    named = dimensions._named_group_dimension

    assert named(spanish.folded("mis mayores gastos por cuenta")) is GroupBy.ACCOUNT
    assert named(spanish.folded("¿en qué cuenta gasté más?")) is GroupBy.ACCOUNT
    assert named(spanish.folded("desglósalo por cada cuenta")) is GroupBy.ACCOUNT

    ranked = _ranking_for("¿cuáles son las cuentas con más gastos?")
    assert ranked is not None and ranked.group_by is GroupBy.ACCOUNT
    assert ranked.metric is Metric.SUM

    # The two dimensions that share the pattern read exactly as before.
    merchants = _ranking_for("Los tres comercios donde más gasté este año")
    assert merchants is not None and merchants.group_by is GroupBy.MERCHANT
    categories = _ranking_for("Top 5 categorías con más operaciones este año")
    assert categories is not None and categories.group_by is GroupBy.CATEGORY


def test_the_row_ceiling_is_not_the_spoken_ranking_ceiling() -> None:
    """A hundred aggregate rows is a chart; a hundred read-aloud movements is a list dump."""
    assert constants.MAX_GROUP_ROWS == 100
    assert constants.MAX_GROUP_RANK == 10
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.MERCHANT,
        group_limit=constants.MAX_GROUP_ROWS,
    )
    assert plan.group_limit == 100
    with pytest.raises(InvalidValueError, match="groupLimit must be between 1 and 100"):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_YEAR),
            group_by=GroupBy.MERCHANT,
            group_limit=constants.MAX_GROUP_ROWS + 1,
        )


# `qwen3.5:9b` answers "¿Qué llevo gastado en la semana en curso?" with `metric: CUMULATIVE` and
# `groupBy: NONE` — the accumulation words are real Spanish and the model reads them, then states
# that nothing buckets the result. The window and the direction are right; the pair is not a plan.
# Four held-out cases died on it as INVALID_DOMAIN, and `enforce` never saw them, because the
# constructor raises before there is any plan for it to correct.


@pytest.mark.parametrize("group_by", [GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH])
def test_an_accumulation_over_real_buckets_is_left_alone(group_by: GroupBy) -> None:
    """The buckets are there, so there is a build-up to show and nothing to settle."""
    assert financial_plan.metric_without_a_bucket(group_by, Metric.CUMULATIVE) is Metric.CUMULATIVE


def test_an_accumulation_over_no_buckets_is_the_total_itself() -> None:
    """One undifferentiated bucket makes the running total a sequence of one — the total."""
    assert financial_plan.metric_without_a_bucket(GroupBy.NONE, Metric.CUMULATIVE) is Metric.SUM


@pytest.mark.parametrize(
    "group_by",
    [GroupBy.CATEGORY, GroupBy.MERCHANT, GroupBy.COUNTERPARTY, GroupBy.ACCOUNT],
)
def test_an_accumulation_beside_a_second_axis_is_still_refused(group_by: GroupBy) -> None:
    """«Gasto acumulado por comercio» needs two grouping axes, and a plan has one.

    `_cumulative_plan` and `explicit_facts.reshaped` both decline this rather than keeping a half.
    Settling it here would quietly answer what they refuse to, and answer it with a figure — a
    running total whose breakdown was dropped on the way — so the pair stays forbidden and the
    constructor stays the thing that says so.
    """
    assert financial_plan.metric_without_a_bucket(group_by, Metric.CUMULATIVE) is Metric.CUMULATIVE
    with pytest.raises(InvalidValueError, match="CUMULATIVE requires"):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_YEAR),
            group_by=group_by,
            metric=Metric.CUMULATIVE,
        )


def test_a_period_average_over_no_buckets_is_not_the_total() -> None:
    """The asymmetry this rule is narrow for.

    «Gasto medio mensual» over an unbucketed year is not the year's total — it is that total over
    a divisor the sentence named and the plan then failed to carry. No figure settles that
    reading, so it stays a refusal rather than becoming a number twelve times too large.
    """
    assert (
        financial_plan.metric_without_a_bucket(GroupBy.NONE, Metric.PERIOD_AVERAGE)
        is Metric.PERIOD_AVERAGE
    )
    with pytest.raises(InvalidValueError, match="PERIOD_AVERAGE requires"):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_YEAR),
            metric=Metric.PERIOD_AVERAGE,
        )


@pytest.mark.parametrize("metric", [Metric.SUM, Metric.COUNT, Metric.AVERAGE, Metric.FREQUENCY])
def test_a_metric_that_needs_no_bucket_is_never_touched(metric: Metric) -> None:
    assert financial_plan.metric_without_a_bucket(GroupBy.NONE, metric) is metric


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuál es mi gasto acumulado este año?",
        "¿cuánto llevaba gastado en mayo?",
        "¿cómo ha ido mi gasto este año?",
    ],
)
def test_an_accumulation_the_sentence_states_survives_the_settled_pair(sentence: str) -> None:
    """**The bound on what settling the pair can cost.**

    `enforce` runs after the planner in both the handler and the evaluation harness, and there the
    deterministic reader outranks the model. So wherever the sentence really does say "acumulado",
    the metric comes straight back — with the MONTH bucket the reader supplies — and the settled
    SUM never survives to be shown. What this rule can reach is only the sentences `_CUMULATIVE`
    does not know, and there the figure it produces is the one the running total would have ended
    on.
    """
    settled = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        metric=financial_plan.metric_without_a_bucket(GroupBy.NONE, Metric.CUMULATIVE),
    )
    assert settled.metric is Metric.SUM

    checked = explicit_facts.enforce(settled, explicit_facts.read(sentence, today=TODAY))

    assert checked.metric is Metric.CUMULATIVE
    assert checked.group_by is GroupBy.MONTH
