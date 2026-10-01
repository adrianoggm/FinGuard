import json
import os
from dataclasses import replace
from uuid import UUID, uuid4

import asyncpg
import pytest

from finguard_orchestrator.application.metrics_executor import financial_metric_executor
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import NotAuthorisedError
from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.models.saved_metric import SavedMetric, SavedMetricDefinition
from finguard_orchestrator.domain.ports.saved_metric_repository import (
    IdempotencyConflictError,
    VersionConflictError,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.metrics.codec import (
    CodecError,
    decode_definition,
    encode_definition,
)
from finguard_orchestrator.infrastructure.metrics.memory_repository import (
    MemorySavedMetricRepository,
)
from finguard_orchestrator.infrastructure.metrics.postgres_repository import (
    PostgresSavedMetricRepository,
    _columns,
)
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def definition(*, metric: Metric = Metric.SUM) -> SavedMetricDefinition:
    return SavedMetricDefinition(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_MONTH),
            group_by=GroupBy.CATEGORY,
            metric=metric,
        ),
        ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("y", "dimension", "nominal", "Categoría"),
                ChartEncoding("x", "value_minor", "quantitative", "Importe"),
            ),
            title="Gasto por categoría",
            sort="descending",
            sort_field="value_minor",
            limit=10,
            responsive=False,
        ),
    )


def test_a_figure_metric_survives_the_two_columns_the_table_has() -> None:
    """A metric saved as a figure has no chart, and the codec *omits* the key rather than writing a
    null. The Postgres adapter read `encoded["chart"]`, so saving one crashed with a `KeyError` --
    and nothing caught it: the memory repository stores the object without encoding it, and the only
    Postgres test is an integration one whose definition always had a chart.

    This calls the adapter's own encoding, so it fails if that regresses, and it needs no database.
    """
    figure = SavedMetricDefinition(definition().plan)
    assert "chart" not in encode_definition(figure)
    plan_json, encoding_json = _columns(figure)
    # What `_row` reads back out of the two columns.
    stored = {"version": 1, "plan": json.loads(plan_json), "chart": json.loads(encoding_json)}
    assert decode_definition(stored) == figure
    # And a chart still survives the same path, so the fix did not trade one shape for the other.
    with_chart = definition()
    plan_json, encoding_json = _columns(with_chart)
    assert (
        decode_definition(
            {"version": 1, "plan": json.loads(plan_json), "chart": json.loads(encoding_json)}
        )
        == with_chart
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("chart", "mark"), "pie"),
        (("chart", "encodings", 0, "channel"), "radius"),
        (("chart", "encodings", 0, "type"), "currency"),
        (("chart", "limit"), 101),
    ],
)
def test_stored_chart_values_are_validated(path: tuple[object, ...], value: object) -> None:
    encoded = encode_definition(definition())
    target: object = encoded
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(CodecError):
        decode_definition(encoded)


def test_movement_specific_plan_is_not_saveable() -> None:
    unsafe = replace(definition().plan, transaction_ids=(uuid4(),))

    with pytest.raises(CodecError):
        encode_definition(SavedMetricDefinition(unsafe, definition().chart))


@pytest.mark.asyncio
async def test_memory_repository_isolates_holders_and_enforces_idempotency() -> None:
    repository = MemorySavedMetricRepository()
    first = SavedMetric.new("holder-a", "Uno", definition())
    saved = await repository.create(first, idempotency_key="attempt", request_hash="a" * 64)

    assert await repository.get("holder-b", saved.id) is None
    assert await repository.list("holder-b") == []
    assert (
        await repository.create(
            SavedMetric.new("holder-a", "Uno", definition()),
            idempotency_key="attempt",
            request_hash="a" * 64,
        )
    ).id == saved.id
    with pytest.raises(IdempotencyConflictError):
        await repository.create(
            SavedMetric.new("holder-a", "Otro", definition()),
            idempotency_key="attempt",
            request_hash="b" * 64,
        )


@pytest.mark.asyncio
async def test_memory_repository_versions_and_reorders_atomically() -> None:
    repository = MemorySavedMetricRepository()
    first = await repository.create(
        SavedMetric.new("holder", "Uno", definition()),
        idempotency_key="one",
        request_hash="1" * 64,
    )
    second = await repository.create(
        SavedMetric.new("holder", "Dos", definition()),
        idempotency_key="two",
        request_hash="2" * 64,
    )

    await repository.reorder("holder", [second.id, first.id])
    assert [row.id for row in await repository.list("holder")] == [second.id, first.id]
    current = await repository.get("holder", first.id)
    assert current is not None
    with pytest.raises(VersionConflictError):
        await repository.update(current, expected_version=1)
    with pytest.raises(ValueError):
        await repository.reorder("holder", [first.id, uuid4()])
    assert [row.id for row in await repository.list("holder")] == [second.id, first.id]


class ChangingBanking:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.calls += 1
        return QueryAnswer(
            currency="EUR",
            total_minor=self.calls * 100,
            movement_count=self.calls,
            groups=(Group("GROCERIES", self.calls * 100, self.calls),),
        )


@pytest.mark.asyncio
async def test_opening_a_metric_executes_banking_fresh() -> None:
    banking = ChangingBanking()
    execute = financial_metric_executor(banking, present)  # type: ignore[arg-type]
    caller = Caller("holder", UUID("08aba04f-2b30-4639-88a6-eac741153ce0"))

    first = await execute(definition(), caller)
    second = await execute(definition(), caller)

    assert banking.calls == 2
    assert first["total_minor"] == 100
    assert second["total_minor"] == 200
    assert first["visualization"]["data"] != second["visualization"]["data"]  # type: ignore[index]

    # A reopened metric has to be a whole answer, because the client renders it with the components
    # a live answer uses. `unit` was missing and the rows are minor units, so an axis drew 92000
    # where the chat drew 920,00 €; `movements` was missing and is not optional in the contract, so
    # the disclosure that reads it took the screen down with it.
    assert first["visualization"]["unit"] == "money"  # type: ignore[index]
    for promised in ("plan", "movements", "suggestions", "groups", "currency", "text"):
        assert promised in first, promised
    assert isinstance(first["movements"], list)


@pytest.mark.asyncio
async def test_a_metric_saved_as_a_figure_reopens_as_a_figure() -> None:
    """A metric with no chart is a plan, and reopening it re-runs the plan like any other.

    The visualisation it comes back with is the KPI shape the client already renders, so nothing
    downstream learns a new case -- and it is still executed rather than remembered: the figure
    moves between the two calls because banking answered twice.
    """
    banking = ChangingBanking()
    execute = financial_metric_executor(banking, present)  # type: ignore[arg-type]
    caller = Caller("holder", UUID("08aba04f-2b30-4639-88a6-eac741153ce0"))
    figure = SavedMetricDefinition(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    )

    first = await execute(figure, caller)
    second = await execute(figure, caller)

    assert banking.calls == 2
    assert "movements" in first and "plan" in first
    assert first["visualization"] == {
        "kind": "kpi",
        "value_field": "value_minor",
        "unit": "money",
        "data": [{"dimension": "total", "value_minor": 100}],
    }
    assert second["visualization"]["data"] == [  # type: ignore[index]
        {"dimension": "total", "value_minor": 200}
    ]

    # A counted metric reopens in movements rather than in money.
    counted = SavedMetricDefinition(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_MONTH),
            metric=Metric.COUNT,
        )
    )
    third = await execute(counted, caller)
    assert third["visualization"]["unit"] == "count"  # type: ignore[index]
    assert third["visualization"]["data"] == [{"dimension": "total", "value_minor": 3}]  # type: ignore[index]


# --- cross-account isolation on the saved-plan path ---------------------------------
#
# The one place `assert_may_read` is not a tautology. A saved metric's plan carries the
# account it was saved against, and a stored plan is untrusted input (ADR-011 §5): it is
# written at save time and replayed later by whichever credential opens the metric. The
# audit in `docs/audits/2026-08-28-do-the-tests-prove-anything.md` §2.2 found the guard
# could be deleted outright with 71 tests still passing, because both executor tests above
# use a plan whose account fields are unset -- so the guard was never reached.
#
# This test reaches it. It asserts refusal *and* that banking was never called: a guard
# that raises after the query has already gone out is not containment.


class RefusingBanking:
    """Records that it was asked. Reaching banking at all is the failure being pinned."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.calls += 1
        return QueryAnswer(currency="EUR", total_minor=999_00, movement_count=9)


OWN_ACCOUNT = UUID("08aba04f-2b30-4639-88a6-eac741153ce0")
SOMEONE_ELSES = UUID("f1d2c3b4-a596-4877-8899-aabbccddeeff")


@pytest.mark.asyncio
async def test_a_saved_plan_naming_an_account_the_opener_cannot_read_is_refused() -> None:
    """The singular field. A metric saved against one account, reopened by a credential
    that account does not belong to, must not answer -- and must not ask banking either."""
    banking = RefusingBanking()
    execute = financial_metric_executor(banking, present)  # type: ignore[arg-type]
    caller = Caller("ES-0001", OWN_ACCOUNT)
    stored = SavedMetricDefinition(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_MONTH),
            only_account_id=SOMEONE_ELSES,
        )
    )

    with pytest.raises(NotAuthorisedError):
        await execute(stored, caller)

    # Refused *before* the query went out. `banking.run` is called with
    # `account_id=caller.account_id`, so the request that would have left here carries the
    # caller's own account as its scope and the stored plan's account only as a filter --
    # and banking's holder-scoped view is what makes that filter safe. Asserting the call
    # count keeps containment from depending on the other service being reachable, correct,
    # or deployed: a guard that raises after the request has gone out is not containment.
    assert banking.calls == 0

    # The same stored plan, opened by the credential it was saved against, still answers:
    # the guard refuses a foreign account, not every account it is given. Without this the
    # test would also pass against a guard that refused unconditionally.
    allowed = replace(stored.plan, only_account_id=OWN_ACCOUNT)
    answered = await execute(SavedMetricDefinition(allowed), caller)
    assert answered["outcome"] == "ANSWERED"
    assert banking.calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_repository_enforces_holder_rls_and_jsonb_roundtrip() -> None:
    database_url = os.getenv("ORCHESTRATOR_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("ORCHESTRATOR_TEST_DATABASE_URL is not configured")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    assert pool is not None
    repository = PostgresSavedMetricRepository(pool)
    holder_a = f"test-a-{uuid4()}"
    holder_b = f"test-b-{uuid4()}"
    saved_a: SavedMetric | None = None
    saved_b: SavedMetric | None = None
    saved_figure: SavedMetric | None = None
    try:
        saved_a = await repository.create(
            SavedMetric.new(holder_a, "A", definition()),
            idempotency_key="a",
            request_hash="a" * 64,
        )
        saved_b = await repository.create(
            SavedMetric.new(holder_b, "B", definition()),
            idempotency_key="b",
            request_hash="b" * 64,
        )
        # A metric that is a figure: no chart, and therefore no `chart` key to write. This is the
        # shape that crashed the save.
        saved_figure = await repository.create(
            SavedMetric.new(holder_a, "Solo la cifra", SavedMetricDefinition(definition().plan)),
            idempotency_key="figure",
            request_hash="c" * 64,
        )
        assert (await repository.get(holder_a, saved_figure.id)) == saved_figure

        assert (await repository.get(holder_a, saved_a.id)) == saved_a
        assert await repository.get(holder_a, saved_b.id) is None
        assert [row.id for row in await repository.list(holder_a)] == [saved_a.id, saved_figure.id]
        assert [row.id for row in await repository.list(holder_b)] == [saved_b.id]
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT count(*) FROM assistant.saved_metric") == 0
    finally:
        if saved_figure is not None:
            await repository.delete(holder_a, saved_figure.id)
        if saved_a is not None:
            await repository.delete(holder_a, saved_a.id)
        if saved_b is not None:
            await repository.delete(holder_b, saved_b.id)
        await pool.close()
