import os
from dataclasses import replace
from uuid import UUID, uuid4

import asyncpg
import pytest

from finguard_orchestrator.application.metrics_executor import financial_metric_executor
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
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
)


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
    execute = financial_metric_executor(banking)  # type: ignore[arg-type]
    caller = Caller("holder", UUID("08aba04f-2b30-4639-88a6-eac741153ce0"))

    first = await execute(definition(), caller)
    second = await execute(definition(), caller)

    assert banking.calls == 2
    assert first["total_minor"] == 100
    assert second["total_minor"] == 200
    assert first["visualization"]["data"] != second["visualization"]["data"]  # type: ignore[index]


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

        assert (await repository.get(holder_a, saved_a.id)) == saved_a
        assert await repository.get(holder_a, saved_b.id) is None
        assert [row.id for row in await repository.list(holder_a)] == [saved_a.id]
        assert [row.id for row in await repository.list(holder_b)] == [saved_b.id]
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT count(*) FROM assistant.saved_metric") == 0
    finally:
        if saved_a is not None:
            await repository.delete(holder_a, saved_a.id)
        if saved_b is not None:
            await repository.delete(holder_b, saved_b.id)
        await pool.close()
