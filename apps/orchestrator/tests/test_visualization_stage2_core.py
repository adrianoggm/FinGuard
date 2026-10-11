from dataclasses import replace

import pytest
from support.visual_planners import default_policy
from test_visualization_stage1 import FAMILY_DATASETS

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
)
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    EncodingPatch,
    EncodingSort,
    VisualContext,
    VisualDataset,
    VisualField,
    Visualization,
    VisualProfile,
    VisualRow,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.visual_plan import Rejection, draft_of
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_interpreter import (
    apply_visual_intent,
    interpret_visual,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    VisualizationDerivation,
)
from finguard_orchestrator.infrastructure.mappers.conversation_mapper import (
    to_payload,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    _chart_suggestions,
    question_to_dto,
)
from finguard_orchestrator.infrastructure.metrics.codec import decode_chart, encode_chart
from finguard_orchestrator.infrastructure.visualization.vega_lite import present, present_spec


async def _decided(result: AskFinancialQuestionResult) -> AskFinancialQuestionResult:
    """The Result as the graph hands it to the mapper.

    The chart is not decided inside `question_to_dto`; the graph decides it (the profile, then the
    visual policy) and the Result carries it. Decided here from the Result's own plan facts the
    same way, so the mapper is handed what a live turn hands it.
    """
    dataset, plan = result.visual_dataset, result.plan_context
    if dataset is None:
        return result
    profile = visual_profile_of(
        dataset,
        total_minor=result.total_minor,
        group_by=plan.group_by if plan is not None else None,
        plan_limited=plan is not None and plan.group_limit is not None,
        plan_metric=plan.metric if plan is not None else None,
    )
    decision = await default_policy().decide("", profile, DEFAULT_PRESENTATION)
    return result if decision is None else replace(result, visual_decision=decision)


def _base() -> tuple[ChartSpec, VisualProfile]:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
    )
    profile = VisualProfile(
        fields=(
            # `order="MEASURE_DESC"`: this is a ranked breakdown bar, the shape
            # `visual_profile_of` actually builds for one -- Task 3's verifier reads a
            # dimension's own `order` before it lets a chart-wide sort touch its axis, and
            # a plain default `"NONE"` would refuse the descending-sort restyle several tests
            # below drive over this exact fixture.
            VisualField("dimension", "nominal", "dimension", 3, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    return spec, profile


def test_patch_is_atomic_and_rejects_unknown_fields() -> None:
    spec, profile = _base()
    with pytest.raises(ValueError):
        merge_encoding_patch(spec, EncodingPatch(sort_field="missing", sort="descending"), profile)
    assert spec.mark == "bar"


def test_donut_requires_valid_profile_and_rewrites_channels() -> None:
    spec, profile = _base()
    donut = merge_encoding_patch(spec, EncodingPatch(mark="donut"), profile)
    assert donut.mark == "donut"
    assert {item.channel for item in donut.encodings} == {"theta", "color"}
    invalid = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 1),
            VisualField("value_minor", "quantitative", "measure", 1),
        ),
        metric="SUM",
        unit="money",
    )
    with pytest.raises(ValueError, match="positive, exclusive parts and a verified total"):
        merge_encoding_patch(spec, EncodingPatch(mark="donut"), invalid)
    # `invalid` above never reaches the cardinality bound at all -- it fails the
    # positive/mutually_exclusive/verified_total gate first. This one clears that gate with a
    # single part, so it is the case that actually exercises "between 2 and 5".
    one_part = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 1),
            VisualField("value_minor", "quantitative", "measure", 1),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    with pytest.raises(ValueError, match="between 2 and 5"):
        merge_encoding_patch(spec, EncodingPatch(mark="donut"), one_part)


def test_ratio_share_can_become_a_donut_and_return_to_bars() -> None:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
        stack="normalize",
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 2),
            VisualField("value_minor", "quantitative", "measure", 2),
        ),
        metric="RATIO_SHARE",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    donut = merge_encoding_patch(spec, EncodingPatch(mark="donut"), profile)
    assert donut.stack is None
    assert {item.channel for item in donut.encodings} == {"theta", "color"}
    bars = merge_encoding_patch(donut, EncodingPatch(mark="bar"), profile)
    assert {item.channel for item in bars.encodings} == {"x", "y"}


def test_a_chart_keeps_its_order_through_a_donut_and_back() -> None:
    """U-6: the mark rebuild in `merge_encoding_patch` (both the bar-to-donut and the donut-to-bar
    branches) used to build each renamed `ChartEncoding` without its `sort` -- the dimension's own
    `EncodingSort`, dropped silently on the one channel-renaming path that never carried it along.

    A donut's `color`/`theta` channels are never axes, so the missing sort was invisible there
    (`_order`'s `MEASURE_DESC` rule only requires a sort *on the axis*) -- the loss only surfaces
    on the turn back to a bar, where the dimension is an axis again and the verifier's own `order`
    rule refuses a ranked dimension whose axis carries no ranking, as an unrelated change nobody
    asked for. Driven three states deep -- ranked bar, donut, bar again -- through the interpreter
    and `apply_visual_intent`, because the bug lives in what a holder can actually ask for in two
    ordinary turns, not only in the domain function directly.
    """
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow("ALQUILER", 51_300),
            VisualRow("OCIO", 4_000),
            VisualRow("TRANSPORTE", 2_000),
        ),
    )
    total = sum(row.value_minor for row in dataset.rows)
    profile = visual_profile_of(dataset, total_minor=total, group_by="NONE")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    dimension_axis = next(item for item in spec.encodings if item.field == "dimension")
    assert dimension_axis.sort is not None  # the floor's own ranking, the thing U-6 loses

    bar = VisualContext("chart-1", spec, profile)
    intent_donut = interpret_visual("ponlo en donut", (bar,))
    assert intent_donut is not None
    donut = VisualContext("chart-2", apply_visual_intent(intent_donut, bar).spec, profile)
    assert isinstance(verify(draft_of(donut.spec), profile), ChartSpec)

    intent_bar = interpret_visual("ponlo en barras", (donut,))
    assert intent_bar is not None
    restored = apply_visual_intent(intent_bar, donut).spec
    assert isinstance(verify(draft_of(restored), profile), ChartSpec)

    restored_axis = next(item for item in restored.encodings if item.field == "dimension")
    assert restored_axis.sort == dimension_axis.sort


def test_limit_presenter_uses_valid_rank_window_and_measure_sort() -> None:
    spec, profile = _base()
    limited = merge_encoding_patch(spec, EncodingPatch(limit=3), profile)
    rendered = present(Visualization(limited, ({"dimension": "a", "value_minor": 1},)))
    assert rendered["data"] == [{"dimension": "a", "value_minor": 1}]
    assert rendered["spec"]["transform"][0]["sort"] == [
        {"field": "value_minor", "order": "descending"}
    ]
    assert rendered["spec"]["transform"][1] == {"filter": "datum._rank <= 3"}


def test_temporal_bar_sort_is_presented_as_discrete_and_keeps_measure_sort() -> None:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "dimension", "temporal", "Periodo"),
            ChartEncoding("y", "value_minor", "quantitative", "Importe"),
        ),
        sort="descending",
        sort_field="value_minor",
    )
    rendered = present_spec(spec)
    assert rendered["encoding"]["x"]["type"] == "nominal"
    assert rendered["encoding"]["x"]["sort"] == {
        "field": "value_minor",
        "order": "descending",
    }


def test_visual_derivation_dto_rejects_ids_reuse_and_embedded_rows() -> None:
    structural = {
        "mark": {"type": "bar"},
        "encoding": {"x": {"field": "dimension", "type": "nominal"}},
    }
    with pytest.raises(ValueError):
        VisualizationDerivation(source_chart_id="chart-a", chart_id="chart-a", spec=structural)
    with pytest.raises(ValueError):
        VisualizationDerivation(
            source_chart_id="chart-a",
            chart_id="chart-b",
            spec={**structural, "data": {"values": [{"amount": 10}]}},
        )


def test_context_ids_and_channels_are_bounded() -> None:
    spec, profile = _base()
    with pytest.raises(ValueError):
        VisualContext("not valid!", spec, profile)
    with pytest.raises(ValueError):
        ChartSpec("bar", (ChartEncoding("x", "a", "nominal"), ChartEncoding("x", "b", "nominal")))


def test_conversation_visual_context_payload_is_value_free() -> None:
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    payload = to_payload(conversation)
    assert "value_minor" in repr(payload)  # field names are allowed
    assert "data" not in repr(payload)


async def test_chart_response_emits_id_and_structural_context_without_rows() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Resultado",
            question="gastos por categoría",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
            groups=[SpokenGroup("Comida", 100, 1), SpokenGroup("Casa", 50, 1)],
            visual_dataset=VisualDataset((VisualRow("Comida", 100), VisualRow("Casa", 50)), "SUM"),
        )
    )
    response = question_to_dto(result)
    assert response.visualization is not None
    assert response.visualization.kind == "chart"
    assert response.visualization.unit == "money"  # type: ignore[union-attr]
    assert response.visualization.chart_id.startswith("chart-")  # type: ignore[union-attr]
    assert result.conversation.visual_contexts == ()  # result remains immutable
    assert response.conversation is not None
    assert len(response.conversation.visual_contexts) == 1


def test_visual_response_spec_is_value_free_and_references_the_verified_artifact() -> None:
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Hecho",
        currency="EUR",
        total_minor=15_000,
        plan_context=SpokenPlan(
            direction="EXPENSE",
            group_by="CATEGORY",
            metric="SUM",
            result_kind="BREAKDOWN",
            query_id="query-1",
            artifact_id="artifact-1",
        ),
        groups=[SpokenGroup("Casa", 10_000, 1), SpokenGroup("Comida", 5_000, 2)],
    )

    response = question_to_dto(result)

    assert response.visual_response is not None
    assert response.visual_response.layout == "financial_analysis"
    assert [block.type for block in response.visual_response.blocks] == ["metric", "breakdown"]
    assert all(block.artifact_id == "artifact-1" for block in response.visual_response.blocks)
    assert "15000" not in response.visual_response.model_dump_json()


async def test_visual_response_spec_composes_breakdown_and_chart_on_one_artifact() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Hecho",
            total_minor=15_000,
            plan_context=SpokenPlan(
                direction="EXPENSE",
                group_by="CATEGORY",
                metric="SUM",
                result_kind="BREAKDOWN",
                query_id="query-1",
                artifact_id="artifact-1",
                view_id="view-1",
            ),
            groups=[SpokenGroup("Casa", 10_000, 1), SpokenGroup("Comida", 5_000, 2)],
            visual_dataset=VisualDataset(
                (VisualRow("Casa", 10_000), VisualRow("Comida", 5_000)), "SUM"
            ),
        )
    )

    response = question_to_dto(result)

    assert response.visual_response is not None
    blocks = response.visual_response.blocks
    assert [block.type for block in blocks] == ["metric", "breakdown", "chart"]
    chart = blocks[-1]
    assert chart.type == "chart" and chart.view_id == "view-1"
    assert {block.artifact_id for block in blocks} == {"artifact-1"}


def test_visual_interpreter_is_value_free_and_skips_financial_changes() -> None:
    spec, profile = _base()
    context = VisualContext("chart-1", spec, profile)
    intent = interpret_visual("ponlo en barras y top 3", (context,))
    assert intent is not None and intent.patch is not None
    assert intent.patch.limit == 3
    ordered = interpret_visual("ordénalo", (context,))
    assert ordered is not None and ordered.patch is not None
    assert ordered.patch.sort == "descending"
    assert interpret_visual("por semanas", (context,)) is None
    assert interpret_visual("top 2 categorías por ticket medio", (context,)) is None
    # Wave 4.4 (design §6): the `voice` parameter is gone -- the old keyword now raises, and a
    # restyle sentence resolves the same non-refusing intent whichever channel it arrived on.
    with pytest.raises(TypeError):
        interpret_visual("ponlo en línea", (context,), voice=True)  # type: ignore[call-arg]
    resolved = interpret_visual("ponlo en horizontal", (context,))
    assert resolved is not None and resolved.explanation is None and resolved.patch is not None
    mixed = interpret_visual("por semanas y ponlo en barras", (context,))
    assert mixed is not None and mixed.patch is None and mixed.explanation is not None


async def test_identical_chart_answers_receive_distinct_ids() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="x",
            question="gastos",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
            visual_dataset=VisualDataset((VisualRow("a", 100), VisualRow("b", 50)), "SUM"),
        )
    )
    first = question_to_dto(result).visualization
    second = question_to_dto(result).visualization
    assert first is not None and second is not None
    assert first.kind == "chart" and second.kind == "chart"
    assert first.chart_id != second.chart_id


async def test_chart_unit_declares_count_and_ratio_without_changing_minor_rows() -> None:
    count = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("lunes", 12), VisualRow("martes", 5)), "FREQUENCY"
                ),
            )
        )
    ).visualization
    ratio = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("Parte", 1200), VisualRow("Resto", 8800)), "RATIO_SHARE"
                ),
            )
        )
    ).visualization
    assert count is not None and count.unit == "count" and count.data[0]["value_minor"] == 12  # type: ignore[union-attr]
    assert ratio is not None and ratio.unit == "ratio" and ratio.data[0]["value_minor"] == 1200  # type: ignore[union-attr]


async def test_a_count_comparison_uses_the_profile_unit_end_to_end() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="x",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="COUNT"),
            visual_dataset=VisualDataset(
                (
                    VisualRow("Comida", 12, series="julio"),
                    VisualRow("Comida", 8, series="junio"),
                    VisualRow("Casa", 5, series="julio"),
                    VisualRow("Casa", 4, series="junio"),
                ),
                "COMPARISON",
            ),
        )
    )

    assert result.visual_decision is not None
    assert result.visual_decision.profile.unit == "count"
    response = question_to_dto(result)
    assert response.visualization is not None
    assert response.visualization.unit == "count"


async def test_breakdown_total_verification_requires_complete_total() -> None:
    async def mapped(total: int | None, rows: tuple[VisualRow, ...]):
        return question_to_dto(
            await _decided(
                AskFinancialQuestionResult(
                    outcome="PLANNED",
                    text="x",
                    total_minor=total,
                    plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
                    visual_dataset=VisualDataset(rows, "SUM"),
                )
            )
        ).conversation

    complete = await mapped(150, (VisualRow("a", 100), VisualRow("b", 50)))
    absent = await mapped(None, (VisualRow("a", 100), VisualRow("b", 50)))
    truncated = await mapped(150, (VisualRow("a", 100), VisualRow("b", 20)))
    ratio = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("Parte", 100), VisualRow("Resto", 50)), "RATIO_SHARE"
                ),
            )
        )
    ).conversation
    assert complete is not None and complete.visual_contexts[0].profile.verified_total
    assert absent is not None and not absent.visual_contexts[0].profile.verified_total
    assert truncated is not None and not truncated.visual_contexts[0].profile.verified_total
    assert ratio is not None and ratio.visual_contexts[0].profile.verified_total


def test_the_chips_offered_all_change_the_chart() -> None:
    """F2: `_chart_suggestions` used to offer «Ordena de menor a mayor» by checking only the
    chart-wide `spec.sort` -- which stays `None` once an axis owns its own descending sort
    (F1's own shape, the floor's own shape), so the guard always passed and the chip was offered
    even when applying it changed nothing. The generator now proposes both directions and lets
    the same gate a restyle runs -- `interpret_visual` / `apply_visual_intent` /
    `verify`, plus F2's own canonical-equality check -- drop whichever one is a no-op: a
    descending bar offers the ascending chip and not the descending one, and a chart just flipped
    ascending offers the descending chip and not the ascending one.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    descending = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert descending is not None
    descending_context = VisualContext("chart-1", descending, profile)
    descending_suggestions = _chart_suggestions(descending_context)
    assert "Ordena de menor a mayor" in descending_suggestions
    assert "Ordena de mayor a menor" not in descending_suggestions

    ascending_intent = interpret_visual("ordena de menor a mayor", (descending_context,))
    assert ascending_intent is not None and ascending_intent.patch is not None
    ascending = apply_visual_intent(ascending_intent, descending_context)
    ascending_context = VisualContext("chart-2", ascending.spec, profile)
    ascending_suggestions = _chart_suggestions(ascending_context)
    assert "Ordena de mayor a menor" in ascending_suggestions
    assert "Ordena de menor a mayor" not in ascending_suggestions


def _limited() -> tuple[ChartSpec, VisualProfile]:
    spec, profile = _base()
    return replace(spec, sort="descending", limit=8), profile


def test_a_limit_the_conversation_imposes_can_also_be_lifted() -> None:
    """`EncodingPatch.clear` existed for this and nothing produced it.

    The chart cuts a wide breakdown to the rows that fit, so until now that was a limit the
    conversation could impose and never undo: every patch the interpreter built set a property, and
    none cleared one.
    """
    spec, profile = _limited()
    contexts = (VisualContext("chart-1", spec, profile),)

    intent = interpret_visual("Ver todos", contexts)
    assert intent is not None and intent.patch is not None
    assert intent.patch.changed == frozenset({"limit"})
    assert intent.target == "chart-1"
    assert merge_encoding_patch(spec, intent.patch, profile).limit is None

    for phrasing in ("ver todos", "todos", "sin límite", "muéstrame todas"):
        lifted = interpret_visual(phrasing, contexts)
        assert lifted is not None and lifted.patch is not None, phrasing


def test_asking_for_everything_is_only_a_restyle_when_that_is_all_it_says() -> None:
    """ "Todos" is a word financial questions are full of, so the pattern is anchored.

    A sentence that asks for every movement is a query. Reading it as a restyle would answer a
    question about the ledger by redrawing the last chart, which is the worst kind of wrong: it
    looks like it worked.
    """
    spec, profile = _limited()
    contexts = (VisualContext("chart-1", spec, profile),)
    for question in (
        "enséñame todos mis movimientos",
        "todos mis gastos de agosto",
        "quiero ver todos los movimientos de la cuenta",
    ):
        assert interpret_visual(question, contexts) is None, question


def test_the_chart_offers_the_follow_ups_it_can_actually_honour() -> None:
    """Every chip round-trips through the interpreter that has to read it back, and now verifies
    too (C1): `_chart_suggestions` takes the context and filters each candidate through the same
    `interpret_visual` / `apply_visual_intent` / `verify(draft_of(...), ...)` gate a restyle
    runs, so a chip offered here is a chip a restyle will accept.

    A ranked bar carries its rank on the axis encoding's own sort (`EncodingSort`, not the
    chart-wide one `_base()`/`_limited()` use) -- the shape the floor actually builds for a
    MEASURE_DESC dimension -- which is why this fixture is built by hand rather than reused from
    `_limited()`: only an axis-own sort survives a chart-wide "ascending" patch, per `_order`'s
    own per-encoding-before-chart-wide reading.
    """
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding(
                "y", "dimension", "nominal", sort=EncodingSort("descending", "value_minor")
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        limit=8,
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 3, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    context = VisualContext("chart-1", spec, profile)
    suggestions = _chart_suggestions(context)
    assert "Ver todos" in suggestions
    # A line over unordered categories is exactly what the `mark` rule exists to refuse (C1).
    assert "Ponlo en línea" not in suggestions
    assert "Ordena de menor a mayor" in suggestions
    # M-7 (Wave 4.2b fix wave): a categorical bar still offers the orientation chip that changes
    # it -- this one already draws horizontally (dimension on `y`), so turning it vertical is the
    # one `_chip_verifies` keeps.
    assert "Ponlo en vertical" in suggestions
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, chip
        merge_encoding_patch(spec, intent.patch, profile)

    # Nothing to lift when nothing was cut.
    assert "Ver todos" not in _chart_suggestions(
        VisualContext("chart-1", replace(spec, limit=None), profile)
    )
    temporal = ChartSpec(
        "line",
        (
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    temporal_profile = VisualProfile(
        fields=(
            VisualField("dimension", "temporal", "dimension", 3, "TEMPORAL"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
    )
    # A series is not reordered by its measure: no sort chip on a temporal axis.
    assert _chart_suggestions(VisualContext("chart-2", temporal, temporal_profile)) == [
        "Ponlo en barras"
    ]
    # M-7 (Wave 4.2b fix wave): a bar is not turned by its calendar axis either -- a month bar
    # turned puts the calendar down the y axis, legal but not a chart worth suggesting, so a
    # temporal bar offers neither orientation chip.
    temporal_bar = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    temporal_bar_suggestions = _chart_suggestions(
        VisualContext("chart-3", temporal_bar, temporal_profile)
    )
    assert "Ponlo en vertical" not in temporal_bar_suggestions
    assert "Ponlo en horizontal" not in temporal_bar_suggestions


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_every_chip_the_floor_offers_over_this_family_verifies_when_applied(
    family_name: str,
) -> None:
    """C1: `_chart_suggestions` used to offer chips no gate had reviewed, and on the commonest
    chart -- a plain breakdown -- one of them (a line over unordered categories) was refused the
    moment a holder tapped it. Driven through the real gate, not a mock: every chip this
    generator offers over the floor's own chart, for every family the exporter renders, must be
    a chip `apply_visual_intent` + `verify(draft_of(...), ...)` actually accepts.

    The reviewer's own probe, pinned rather than described: the plain breakdown family no longer
    offers «Ponlo en línea» (the `mark` rule refuses a line over unordered categories) while it
    still offers «Ordena de menor a mayor» (the axis already carries its own descending sort,
    which a chart-wide ascending patch cannot unrank -- see the test above).
    """
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    context = VisualContext(f"chart-{family_name}", spec, profile)
    suggestions = _chart_suggestions(context)
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, (family_name, chip)
        merged = apply_visual_intent(intent, context)
        verified = verify(draft_of(merged.spec), context.profile)
        assert isinstance(verified, ChartSpec), (family_name, chip, verified)
    if family_name == "breakdown":
        assert "Ponlo en línea" not in suggestions
        assert "Ordena de menor a mayor" in suggestions


def test_ordena_de_menor_a_mayor_reorders_the_axis_the_floor_already_ranked() -> None:
    """F1 (fix wave 2, P0): the interpreter always built a *chart-wide* `sort="ascending"`
    (`visual_interpreter.py:117-125`), and `present()` (`vega_lite.py:89-90`) lets a per-encoding
    sort win over it -- so over the floor's own ranked bar, whose dimension axis already carries
    `EncodingSort("descending", "value_minor")`, «ordena de menor a mayor» merged, verified,
    minted a new version, and Vega-Lite drew the exact same descending order. It was a no-op that
    looked like it worked.

    The fix: `merge_encoding_patch` now edits the axis that already owns the order -- the
    patch's actual meaning -- instead of a chart-wide setting nothing on the wire reads.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    before_spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert before_spec is not None
    dimension_axis = next(
        item
        for item in before_spec.encodings
        if item.channel in {"x", "y"} and item.type == "nominal"
    )
    assert dimension_axis.sort == EncodingSort("descending", "value_minor")
    context = VisualContext("chart-1", before_spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    after = apply_visual_intent(intent, context)

    # The presented spec actually changes -- the whole point of the fix.
    assert present_spec(after.spec) != present_spec(before_spec)
    after_axis = next(
        item for item in after.spec.encodings if item.channel == dimension_axis.channel
    )
    assert after_axis.sort == EncodingSort("ascending", "value_minor")
    assert after.spec.sort is None
    assert after.spec.sort_field is None
    presented = present_spec(after.spec)
    assert presented["encoding"][dimension_axis.channel]["sort"] == {
        "field": "value_minor",
        "order": "ascending",
    }

    # The mirror: «de mayor a menor» over a chart already ascending flips it straight back.
    ascending_context = VisualContext("chart-2", after.spec, profile)
    back_intent = interpret_visual("ordena de mayor a menor", (ascending_context,))
    assert back_intent is not None and back_intent.patch is not None
    restored = apply_visual_intent(back_intent, ascending_context)
    restored_axis = next(
        item for item in restored.spec.encodings if item.channel == dimension_axis.channel
    )
    assert restored_axis.sort == EncodingSort("descending", "value_minor")
    assert restored.spec.sort is None
    assert restored.spec.sort_field is None


def test_a_temporal_axis_asked_to_sort_still_takes_the_chart_wide_path_and_is_refused() -> None:
    """F1 ruling: when no axis owns a sort, the merge still takes the chart-wide path exactly as
    it did before this fix. A temporal line's own axis never carries a per-encoding sort
    (`_sort_for` returns `None` for `order="TEMPORAL"`), so «ordena de menor a mayor» over one
    still reaches the verifier's `_order` rule the same way it always did, and is refused the
    same way -- this fix changes nothing about that family.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("2026-01", 10_000), VisualRow("2026-02", 20_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="MONTH")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    dimension_axis = next(
        item for item in spec.encodings if item.channel in {"x", "y"} and item.type == "temporal"
    )
    assert dimension_axis.sort is None
    context = VisualContext("chart-1", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    merged = apply_visual_intent(intent, context)
    # No axis owned a sort, so the merge fell back to the chart-wide setting, as today.
    assert merged.spec.sort == "ascending"
    assert merged.spec.sort_field == "value_minor"
    verdict = verify(draft_of(merged.spec), profile)
    assert isinstance(verdict, Rejection)
    assert verdict.rule == "order"


# The floor family, probed against the tree before this fix: which families' dimension axis is
# already ranked (`order="MEASURE_DESC"`) and so is exactly where this fix changes *how* the sort
# is applied (`breakdown`, `categorical_comparison`), and which were already refused on the
# chart-wide path this fix leaves untouched -- `cumulative`/`temporal`/`recurrence` (their
# dimension is ordered by time, not by magnitude), `frequency` (its dimension keeps the
# producer's order), `ratio_share` (its dimension sits in `color`, off any axis, so a chart-wide
# sort has no categorical/temporal x or y to land on). No family crosses from one column to the
# other: that is the regression this test exists to catch.
_ORDENA_ASCENDING_ACCEPTED_AT_BASE: frozenset[str] = frozenset(
    {"breakdown", "categorical_comparison"}
)


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_ordena_de_menor_a_mayor_over_every_floor_family_verifies_wherever_it_did_at_base(
    family_name: str,
) -> None:
    """F1: every family the exporter renders (Task 3b's own eight, donut included below), run
    through «ordena de menor a mayor» and checked against what actually verified before this fix
    was written -- no family may newly refuse the patch, whatever its shape."""
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    context = VisualContext(f"chart-{family_name}", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    expect_accept = family_name in _ORDENA_ASCENDING_ACCEPTED_AT_BASE
    try:
        merged = apply_visual_intent(intent, context)
    except ValueError as exc:
        assert not expect_accept, f"{family_name}: newly refused by the merge ({exc})"
        return
    verdict = verify(draft_of(merged.spec), profile)
    if expect_accept:
        assert isinstance(verdict, ChartSpec), f"{family_name}: newly refused: {verdict}"
    else:
        assert isinstance(verdict, Rejection), f"{family_name}: unexpectedly accepted"


def test_ordena_de_menor_a_mayor_over_the_donut_family_is_still_refused_by_the_merge() -> None:
    """The eighth family: the floor never produces a donut, so the parametrised test above cannot
    cover it -- built the same way `test_the_donut_familys_chips_verify_too` below does. A donut
    has no x/y axis at all, so the chart-wide path this fix leaves alone still cannot find one to
    land the sort on, exactly as at BASE."""
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    dataset = VisualDataset(rows=rows, metric="SUM")
    profile = visual_profile_of(dataset, total_minor=100_000, group_by="NONE")
    context = VisualContext("chart-donut", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    with pytest.raises(ValueError, match="categorical or temporal"):
        apply_visual_intent(intent, context)


def test_the_donut_familys_chips_verify_too() -> None:
    """The floor never produces a donut (`DeterministicVisualPlanner.plan` has no such branch),
    so the parametrised test above cannot cover it. Built the same way
    `export_chart_fixtures._donut_family` and the wire test beside it do.
    """
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    dataset = VisualDataset(rows=rows, metric="SUM")
    profile = visual_profile_of(dataset, total_minor=100_000, group_by="NONE")
    context = VisualContext("chart-donut", spec, profile)
    suggestions = _chart_suggestions(context)
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, chip
        merged = apply_visual_intent(intent, context)
        verified = verify(draft_of(merged.spec), context.profile)
        assert isinstance(verified, ChartSpec), (chip, verified)


def test_a_disabled_stack_emits_an_explicit_null_and_not_an_absent_property() -> None:
    """`stack=None` omits the property, and Vega-Lite's default for a bar with a quantitative
    measure and a colour series is to **stack**. So `None` means "let Vega-Lite decide" and
    Vega-Lite decides to stack. Only an explicit null turns it off."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal"),
            ChartEncoding("yOffset", "series", "nominal"),
        ),
        stack="disabled",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["x"]["stack"] is None
    assert "yOffset" in emitted["spec"]["encoding"]


def test_an_absent_stack_is_still_absent() -> None:
    """The old behaviour has to stay reachable, because a temporal line needs no stack at all."""
    spec = ChartSpec(
        mark="line",
        encodings=(
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert "stack" not in emitted["spec"]["encoding"]["y"]


def test_an_offset_is_a_channel_the_vocabulary_knows() -> None:
    ChartEncoding("xOffset", "series", "nominal")  # must not raise
    with pytest.raises(ValueError, match="channel"):
        ChartEncoding("zOffset", "series", "nominal")


def test_a_stacked_spec_needs_a_quantitative_x_or_y_encoding() -> None:
    """`stack` only means something on the axis Vega-Lite draws bars along. The presenter's

    `next(...)` for the stacked channel has no default and raises `StopIteration` if that axis
    is missing -- so `ChartSpec` refuses the combination at construction instead, and the
    presenter's bare `next()` is provably safe. A donut cannot exercise this: `mark == "donut"`
    with a stack is already rejected by the guard above with its own message, so this uses a
    non-donut mark whose only quantitative encoding is on `theta`.
    """
    with pytest.raises(ValueError, match="quantitative"):
        ChartSpec(
            mark="point",
            encodings=(ChartEncoding("theta", "value_minor", "quantitative"),),
            stack="disabled",
        )


def test_a_chart_can_order_its_dimension_and_its_series_differently() -> None:
    """`ChartSpec` carries one `sort` and one `sort_field`, so two independent orders were not
    representable: a breakdown wants its dimension by magnitude and its legend in the order the
    producer built it. Without a per-encoding sort this design would promise two and ship one."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "y",
                "dimension",
                "nominal",
                sort=EncodingSort(mode="descending", field="value_minor"),
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal", sort=EncodingSort(mode="input")),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == {
        "field": "value_minor",
        "order": "descending",
    }
    assert emitted["spec"]["encoding"]["color"]["sort"] is None  # Vega-Lite's "keep data order"


def test_a_sort_field_needs_a_mode_that_uses_one() -> None:
    with pytest.raises(ValueError, match="input"):
        EncodingSort(mode="input", field="value_minor")


def test_an_encoding_sort_rejects_an_unsupported_mode() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        EncodingSort(mode="sideways")  # type: ignore[arg-type]


def test_an_encoding_sort_field_must_be_an_identifier() -> None:
    with pytest.raises(ValueError, match="identifiers"):
        EncodingSort(mode="ascending", field="not an identifier")


def test_a_chart_wide_sort_field_must_be_an_identifier() -> None:
    """M4: `EncodingSort.field` and `ChartEncoding.field` both run `FIELD_NAME_PATTERN`;
    `ChartSpec.sort_field` checked only that it required `sort`, not that it was an identifier."""
    with pytest.raises(ValueError, match="identifiers"):
        ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("y", "dimension", "nominal"),
                ChartEncoding("x", "value_minor", "quantitative"),
            ),
            sort="descending",
            sort_field="not an identifier",
        )


def test_a_per_encoding_sort_wins_over_the_chart_wide_sort_for_its_own_channel() -> None:
    """`ChartSpec.sort`/`sort_field` and a per-encoding `EncodingSort` can now both target the
    same Vega-Lite channel. Only one `"sort"` property can survive on that channel, and the
    encoding-level one wins -- it says something more specific about its own channel than the
    chart-wide default does. See the comment in `present()` where that precedence is decided."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "y",
                "dimension",
                "nominal",
                sort=EncodingSort(mode="ascending", field="movement_count"),
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        sort="descending",
        sort_field="value_minor",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == {
        "field": "movement_count",
        "order": "ascending",
    }


def test_a_per_encoding_input_sort_beats_a_colliding_chart_wide_sort() -> None:
    """`EncodingSort(mode="input")` writes a *present but falsy* `"sort": None`, so the
    precedence guard in `present()` has to check key membership and not truthiness -- a
    truthiness check would read that `None` as "nothing claimed this channel" and let the
    chart-wide `sort`/`sort_field` below overwrite it. This puts `input` on the very channel a
    colliding chart-wide sort targets, which the previous precedence test did not: that one used
    a *truthy* per-encoding sort, so a regression to truthiness would have passed it unchanged."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", sort=EncodingSort(mode="input")),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        sort="descending",
        sort_field="value_minor",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] is None


def test_a_fieldless_encoding_sort_emits_the_bare_order_string() -> None:
    """The per-encoding translation has three wire shapes: the object form (a field), `null`
    (`input`), and this one -- a mode with no field, which Vega-Lite takes as a bare order
    string rather than an object."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", sort=EncodingSort(mode="ascending")),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == "ascending"


def test_a_grouped_comparison_survives_the_codec_and_a_follow_up_patch() -> None:
    profile = visual_profile_of(
        VisualDataset(
            rows=(
                VisualRow("Comida", 12, series="julio"),
                VisualRow("Comida", 8, series="junio"),
                VisualRow("Casa", 5, series="julio"),
                VisualRow("Casa", 4, series="junio"),
            ),
            metric="COMPARISON",
        ),
        total_minor=None,
        group_by="CATEGORY",
        plan_metric="COUNT",
    )
    original_spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert original_spec is not None
    assert original_spec.stack == "disabled"

    restored_spec = decode_chart(encode_chart(original_spec))
    assert restored_spec == original_spec

    patched = merge_encoding_patch(restored_spec, EncodingPatch(limit=3), profile)
    quantitative = next(
        encoding.channel
        for encoding in patched.encodings
        if encoding.type == "quantitative" and encoding.channel in {"x", "y"}
    )
    assert present_spec(patched)["encoding"][quantitative]["stack"] is None
