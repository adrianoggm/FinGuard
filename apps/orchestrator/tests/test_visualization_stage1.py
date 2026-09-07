import dataclasses
import importlib.util
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError
from support.visual_planners import BREAKDOWN_PROFILE, default_policy

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
    _decided_visual,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ROWS_THAT_FIT,
    Channel,
    ChartEncoding,
    ChartSpec,
    PresentationContext,
    VisualContext,
    VisualField,
    Visualization,
    VisualProfile,
    derive_chart,
)
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.visual_plan import Rejection, draft_of
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.visual_interpreter import (
    apply_visual_intent,
    interpret_visual,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ConversationPayload,
    VisualContextPayload,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    _chart_suggestions,
    _remember_visual_context,
    _visualization_to_dto,
    question_to_dto,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.visualization.vega_lite import present

# Loaded the way `test_evaluate_visual.py` loads `scripts/evaluate_visual.py`: `scripts/` is not a
# package, so the exporter's own dataset+kwargs pairs -- the ones the web compile gate renders --
# are read off the file directly rather than duplicated here.
_EXPORTER_SPEC = importlib.util.spec_from_file_location(
    "export_chart_fixtures",
    Path(__file__).resolve().parents[1] / "scripts" / "export_chart_fixtures.py",
)
assert _EXPORTER_SPEC and _EXPORTER_SPEC.loader
export_chart_fixtures = importlib.util.module_from_spec(_EXPORTER_SPEC)
sys.modules["export_chart_fixtures"] = export_chart_fixtures
_EXPORTER_SPEC.loader.exec_module(export_chart_fixtures)
FAMILY_DATASETS = export_chart_fixtures.FAMILY_DATASETS

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def test_chart_references_only_result_fields_and_never_values() -> None:
    spec = derive_chart(
        group_by="CATEGORY",
        metric="SUM",
        rows=2,
        fields=frozenset({"key", "value_minor", "movement_count"}),
        value_field="value_minor",
    )
    assert spec is not None
    assert spec.valid_for(frozenset({"key", "value_minor", "movement_count"}))
    serialized = asdict(spec)
    assert not {"data", "datum", "value", "values"} & set(repr(serialized).split("'"))
    assert all(isinstance(encoding.field, str) for encoding in spec.encodings)
    assert not spec.valid_for(frozenset({"key", "other_value"}))


def test_missing_field_is_rejected() -> None:
    spec = ChartSpec(mark="line", encodings=(ChartEncoding("x", "absent", "nominal"),))
    assert not spec.valid_for(frozenset({"key"}))


def test_presenter_keeps_rows_outside_vega_spec() -> None:
    spec = derive_chart(
        group_by="DAY",
        metric="SUM",
        rows=2,
        fields=frozenset({"key", "value_minor"}),
        value_field="value_minor",
    )
    assert spec is not None
    payload = present(Visualization(spec=spec, data=({"key": "lunes", "value_minor": 1},)))
    assert "values" not in payload["spec"]
    assert payload["data"] == [{"key": "lunes", "value_minor": 1}]


def test_single_row_abstains() -> None:
    assert (
        derive_chart(
            group_by="CATEGORY",
            metric="SUM",
            rows=1,
            fields=frozenset({"key", "value_minor"}),
            value_field="value_minor",
        )
        is None
    )


def test_deterministic_defaults_distinguish_time_cumulative_and_categories() -> None:
    fields = frozenset({"dimension", "value_minor", "movement_count"})
    temporal = derive_chart(
        group_by="MONTH",
        metric="SUM",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    cumulative = derive_chart(
        group_by="MONTH",
        metric="CUMULATIVE",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    categorical = derive_chart(
        group_by="CATEGORY",
        metric="SUM",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    assert temporal is not None and temporal.mark == "line"
    assert cumulative is not None and cumulative.mark == "area"
    assert categorical is not None and categorical.mark == "bar"
    assert categorical.encodings[0].channel == "y"


async def _result(
    rows: tuple[VisualRow, ...] | None,
    *,
    metric: str = "SUM",
    group_by: str = "CATEGORY",
    group_limit: int | None = None,
    plan_metric: str | None = None,
    explicit: bool = False,
) -> AskFinancialQuestionResult:
    # Through `_decided_visual`, because the chart is no longer decided by the mapper: the
    # application decides it and the Result carries it. This is the handler's own line, so what
    # the mapper is handed here is what a live turn hands it.
    #
    # `group_limit` and `plan_metric` are the plan's own facts, threaded separately from the
    # dataset's `metric` -- the wire `_decided_visual` reads to build `plan_limited` and
    # `plan_metric` on the profile. Every other test call leaves both `None`/default, which
    # reproduces the old (bare) `SpokenPlan` exactly.
    return await _decided_visual(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Resultado",
            plan_context=SpokenPlan(
                direction="EXPENSE",
                group_by=group_by,
                metric=plan_metric if plan_metric is not None else metric,
                group_limit=group_limit,
            ),
            groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
            explicit_visualization=explicit,
            visual_dataset=VisualDataset(rows=rows, metric=metric) if rows is not None else None,
        ),
        default_policy(),
        DEFAULT_PRESENTATION,
        "",
    )


async def test_mapper_uses_typed_series_and_every_encoding_names_a_real_field() -> None:
    dto = question_to_dto(
        await _result(
            (
                VisualRow("Comida", 100, series="agosto"),
                VisualRow("Comida", 80, series="julio"),
            ),
            metric="COMPARISON",
        )
    )
    assert dto.visualization is not None
    assert dto.visualization.kind == "chart"
    assert dto.visualization.spec["encoding"]["color"]["field"] == "series"  # type: ignore[index]
    assert {row["series"] for row in dto.visualization.data} == {"agosto", "julio"}
    encoded = dto.visualization.spec["encoding"]
    fields = {
        item["field"]
        for channel in encoded.values()  # type: ignore[union-attr]
        for item in (channel if isinstance(channel, list) else [channel])
    }
    assert all(fields <= set(row) for row in dto.visualization.data)


def test_the_wire_channel_whitelist_matches_the_domain_literal() -> None:
    """`VisualContextPayload._value_free_spec` used to hand-copy the allowed channel set, and the
    copy went stale the moment `Channel` grew `xOffset`/`yOffset` -- a live categorical comparison
    could not complete a turn until the copy was replaced with `get_args(Channel)` (`1924c95`).

    The guard: every channel the domain allows must still validate here. This fails the day
    someone reintroduces a hand-written copy that omits one, on the commit that does it rather
    than the first live turn that needs the missing channel."""
    channels = get_args(Channel)
    fields = {channel: f"field_{index}" for index, channel in enumerate(channels)}

    payload = VisualContextPayload.model_validate(
        {
            "chart_id": "chart-1",
            "spec": {
                "mark": {"type": "bar"},
                "encoding": {
                    channel: {"field": field_name, "type": "nominal"}
                    for channel, field_name in fields.items()
                },
            },
            "profile": {
                "fields": [
                    {"name": field_name, "type": "nominal"} for field_name in fields.values()
                ],
                "cardinality": 2,
            },
        }
    )

    assert payload.chart_id == "chart-1"


def _visual_context_dict(
    *,
    stack: object = None,
    sort: object = None,
    sort_field: object = None,
    y_sort: object = None,
) -> dict[str, object]:
    """A minimal, otherwise-valid `VisualContextPayload` body. Each of `stack`/`sort`/
    `sort_field`/a `y`-channel `sort` is left off unless a test names it, so a test that sends
    exactly one bad value proves `_value_free_spec` refused *that* value -- not some unrelated
    shape mistake."""
    encoding: dict[str, object] = {
        "x": {"field": "value_minor", "type": "quantitative"},
        "y": {"field": "dimension", "type": "nominal"},
    }
    if y_sort is not None:
        encoding["y"] = {**encoding["y"], "sort": y_sort}  # type: ignore[dict-item]
    spec: dict[str, object] = {"mark": {"type": "bar"}, "encoding": encoding}
    if stack is not None:
        spec["stack"] = stack
    if sort is not None:
        spec["sort"] = sort
    if sort_field is not None:
        spec["sort_field"] = sort_field
    return {
        "chart_id": "chart-1",
        "spec": spec,
        "profile": {
            "fields": [
                {"name": "value_minor", "type": "quantitative"},
                {"name": "dimension", "type": "nominal"},
            ],
            "cardinality": 2,
        },
    }


def test_an_encoding_sort_with_an_unsupported_mode_is_refused_at_the_dto() -> None:
    """`conversation_mapper._encoding_sort_from_payload` used to catch `EncodingSort`'s own
    `ValueError` and return `None`, so `{"sort": {"mode": "sideways"}}` silently became "no sort"
    and the turn completed as a 200. The DTO is the guard now: it constructs `EncodingSort`
    itself, right here, and lets that same `ValueError` become a 422."""
    with pytest.raises(ValueError, match="sort mode"):
        VisualContextPayload.model_validate(_visual_context_dict(y_sort={"mode": "sideways"}))


def test_an_encoding_sort_of_input_with_a_field_is_refused_at_the_dto() -> None:
    """`input` means "keep producer order" -- naming a field to sort by contradicts it."""
    with pytest.raises(ValueError, match="input"):
        VisualContextPayload.model_validate(
            _visual_context_dict(y_sort={"mode": "input", "field": "value_minor"})
        )


def test_an_encoding_sort_field_that_is_not_an_identifier_is_refused_at_the_dto() -> None:
    with pytest.raises(ValueError, match="identifier"):
        VisualContextPayload.model_validate(
            _visual_context_dict(y_sort={"mode": "descending", "field": "not an identifier"})
        )


def test_an_unsupported_stack_is_refused_at_the_dto() -> None:
    """`_value_free_spec` checked the allowed key set, the mark, the channels and the encoding
    count -- never the *value* of `stack`. `conversation_mapper.to_domain` silently turned an
    unrecognised one into `None`, so `{"stack": "sideways"}` was a 200 that dropped the stack
    instead of refusing it."""
    with pytest.raises(ValueError, match="stack"):
        VisualContextPayload.model_validate(_visual_context_dict(stack="sideways"))


def test_an_unsupported_spec_level_sort_is_refused_at_the_dto() -> None:
    with pytest.raises(ValueError, match="sort mode"):
        VisualContextPayload.model_validate(_visual_context_dict(sort="sideways"))


def test_a_non_identifier_sort_field_is_refused_at_the_dto() -> None:
    with pytest.raises(ValueError, match="identifier"):
        VisualContextPayload.model_validate(_visual_context_dict(sort_field="42abc"))


def test_a_payload_with_none_of_the_new_keys_still_validates() -> None:
    """The old-client case: a caller that has never heard of `stack`/`sort`/`sort_field`/a
    per-encoding `sort` -- nor, since fix round 1, a field's `role`/`order` or a profile's
    `comparison`/`plan_limited` -- sends none of them, and the payload still validates: absence is
    never treated as an invalid value. `test_conversation_visual_context_round_trip_is_value_free`
    (in `test_visualization_stage2_core.py`) carries this same case all the way through
    `conversation_mapper.to_domain` and asserts the reconstructed spec's `stack`/`sort` are `None`.

    This test goes one step further for the two new keys specifically: `_visual_context_dict()`'s
    profile already has no `role`/`order`/`comparison`/`plan_limited` at all (it predates fix
    round 1), so round-tripping it through `to_domain` exercises the fallback derivation that
    round adds -- `role` guessed from `type`, `order` defaulted to `VisualField`'s own `"NONE"`,
    `comparison`/`plan_limited` defaulted to `False` -- for exactly the conversation an older
    client still holds.
    """
    payload = VisualContextPayload.model_validate(_visual_context_dict())
    assert payload.chart_id == "chart-1"
    conversation = ConversationPayload.model_validate({"visual_contexts": [_visual_context_dict()]})
    (restored,) = conversation_mapper.to_domain(conversation).visual_contexts
    by_name = {field.name: field for field in restored.profile.fields}
    assert (by_name["value_minor"].role, by_name["value_minor"].order) == ("measure", "NONE")
    assert (by_name["dimension"].role, by_name["dimension"].order) == ("dimension", "NONE")
    assert (restored.profile.comparison, restored.profile.plan_limited) == (False, False)


def test_a_rendered_specs_extra_keys_are_refused_at_the_dto() -> None:
    """I2 (fix wave): `_value_free_spec`'s allowlist used to admit `$schema`, `width` and
    `height` -- exactly the three keys `present_spec` adds to the *rendered* spec a client draws,
    never emitted by `_context_spec_payload`. A client that echoed the rendered spec instead of
    `conversation.visual_contexts[].spec` used to get a 200 whose digest could never match the
    seal -- `VISUAL_UNTRUSTED` with no diagnostic telling it why. Narrowed to exactly what the
    wire emits, the same mistake is now a 422 that names the key.
    """
    raw = _visual_context_dict()
    raw["spec"] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        **raw["spec"],  # type: ignore[dict-item]
        "width": "container",
        "height": 220,
    }
    with pytest.raises(ValueError, match="unknown visual spec key"):
        VisualContextPayload.model_validate(raw)


def test_the_servers_own_spec_vocabulary_still_validates_after_the_allowlist_narrows() -> None:
    """I2's other half: narrowing the allowlist must not refuse what the server itself emits.
    Every key `_context_spec_payload` can produce -- `mark`, `encoding`, `title`, `stack`, `sort`,
    `sort_field` and `limit` -- together on one spec, not merely the bare mark+encoding minimum
    every other DTO test already exercises.
    """
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
        title="Resultados",
        stack="zero",
        sort="descending",
        sort_field="dimension",
        limit=5,
    )
    raw = {
        "chart_id": "chart-1",
        "spec": conversation_mapper._context_spec_payload(spec),
        "profile": {
            "fields": [
                {"name": "value_minor", "type": "quantitative"},
                {"name": "dimension", "type": "nominal"},
            ],
            "cardinality": 2,
        },
    }
    payload = VisualContextPayload.model_validate(raw)
    assert set(payload.spec) == {
        "mark",
        "encoding",
        "title",
        "stack",
        "sort",
        "sort_field",
        "limit",
    }


def _old_shape_payload(*, ranked: bool) -> dict[str, object]:
    """`_visual_context_dict()`'s own shape, its profile fields optionally carrying `role`/
    `order` -- the two keys fix round 1 put on the wire. `ranked=False` is the payload an older
    client still holds (I4): a profile that predates both keys entirely, the same shape
    `test_a_payload_with_none_of_the_new_keys_still_validates` already proves still validates.
    `ranked=True` is the shape every chart built after that fix carries for a ranked breakdown."""
    payload = _visual_context_dict()
    if ranked:
        payload["profile"] = {
            "fields": [
                {"name": "value_minor", "type": "quantitative", "role": "measure"},
                {
                    "name": "dimension",
                    "type": "nominal",
                    "role": "dimension",
                    "order": "MEASURE_DESC",
                },
            ],
            "cardinality": 2,
        }
    return payload


def test_an_older_payload_validates_but_a_sort_restyle_waits_for_the_next_chart() -> None:
    """I4, decided: document, no heuristic. `to_domain`'s fallback (`role` guessed from `type`,
    `order` defaulted to `_DEFAULT_ORDER`) lets an older client's payload -- one that predates
    `role`/`order` entirely -- complete the turn instead of refusing to parse it at all. It does
    not restore what base itself could do: the fallback's `order` is `"NONE"`, and the verifier's
    own `order` rule reads `NONE` as "nothing may sort this field", so a descending-sort restyle
    asked of that chart is refused exactly as it was at base, before this fix wave existed. That
    is not a regression the fallback owes anyone: the chart still renders and the turn still
    completes, and the window closes itself the moment the conversation's next chart is built --
    every new chart carries the full profile, so the restyle resumes working from there.

    The same payload with `role`/`order` present (the shape a chart built after fix round 1 always
    carries) accepts the identical sentence, which is the contrast this test pins: not "always
    refused", but "refused only for the one chart whose payload predates these keys".
    """
    old = conversation_mapper.to_domain(
        ConversationPayload.model_validate({"visual_contexts": [_old_shape_payload(ranked=False)]})
    ).visual_contexts[0]
    intent = interpret_visual("ordénalo de mayor a menor", (old,))
    assert intent is not None and intent.patch is not None
    merged = apply_visual_intent(intent, old)
    refused = verify(draft_of(merged.spec), old.profile)
    assert isinstance(refused, Rejection)
    assert refused.rule == "order"

    ranked = conversation_mapper.to_domain(
        ConversationPayload.model_validate({"visual_contexts": [_old_shape_payload(ranked=True)]})
    ).visual_contexts[0]
    intent = interpret_visual("ordénalo de mayor a menor", (ranked,))
    assert intent is not None and intent.patch is not None
    merged = apply_visual_intent(intent, ranked)
    accepted = verify(draft_of(merged.spec), ranked.profile)
    assert isinstance(accepted, ChartSpec)


def test_the_wire_carries_derived_from_and_turn_id_and_nothing_server_side() -> None:
    """§13: no wire authority -- decision, plan and evidence never travel; origin and turn do."""
    turn = uuid4()
    decision = VisualDecision(spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic")
    context = VisualContext(
        "chart-2",
        SPEC,
        BREAKDOWN_PROFILE,
        decision=decision,
        evidence_ids=(uuid4(),),
        derived_from="chart-1",
        turn_id=turn,
    )
    conversation = IDLE.with_visual_context(context)
    payload = conversation_mapper.to_payload(conversation)
    (item,) = payload.visual_contexts
    assert (item.derived_from, item.turn_id) == ("chart-1", turn)
    assert not hasattr(item, "decision") and not hasattr(item, "source_plan")
    assert not hasattr(item, "evidence_ids")
    # `token` travels like `derived_from`/`turn_id` above it -- unlike `decision`/`source_plan`/
    # `evidence_ids`, an unsealed context's `None` still rides the wire rather than vanishing.
    assert item.token is None
    back = conversation_mapper.to_domain(payload)
    (restored,) = back.visual_contexts
    assert (restored.derived_from, restored.turn_id) == ("chart-1", turn)
    assert (restored.decision, restored.source_plan, restored.evidence_ids) == (None, None, ())
    assert (restored.token, restored.trusted) == (None, True)


def test_an_absent_origin_round_trips_as_none() -> None:
    conversation = IDLE.with_visual_context(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE))
    payload = conversation_mapper.to_payload(conversation)
    (item,) = payload.visual_contexts
    assert (item.derived_from, item.turn_id) == (None, None)
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert (restored.derived_from, restored.turn_id) == (None, None)


def test_an_invalid_origin_on_the_wire_is_refused() -> None:
    payload = conversation_mapper.to_payload(
        IDLE.with_visual_context(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE))
    )
    raw = payload.model_dump(mode="json")
    raw["visual_contexts"][0]["derived_from"] = "not a chart id!"
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate(raw)


def test_a_sealed_context_round_trips_trusted_and_a_tampered_one_does_not() -> None:
    """A context this process sealed comes back `trusted` under the holder it was sealed for,
    untrusted the moment a byte the seal covers changes underneath it, and untrusted again for a
    different holder entirely -- the seal names who it was sealed for as much as what."""
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    sealed = conversation_mapper.seal_context(context, "holder-a", codec)
    assert sealed.token is not None and sealed.trusted is True

    def verify(token: str | None, semantics: dict[str, object]) -> bool:
        return codec.verify_context(token, "holder-a", semantics)

    echoed = conversation_mapper.to_domain(
        conversation_mapper.to_payload(Conversation(visual_contexts=(sealed,))), verify=verify
    )
    assert echoed.visual_contexts[0].trusted is True
    assert echoed.visual_contexts[0].token == sealed.token

    payload = conversation_mapper.to_payload(Conversation(visual_contexts=(sealed,)))
    # `_Payload` is not frozen: mutate the wire shape in place, exactly what a tampering client's
    # request body would look like once it reaches the DTO.
    profile = payload.visual_contexts[0].profile
    profile.comparison = not profile.comparison
    tampered = conversation_mapper.to_domain(payload, verify=verify).visual_contexts[0]
    assert tampered.trusted is False and tampered.token is None

    other = conversation_mapper.to_domain(
        conversation_mapper.to_payload(Conversation(visual_contexts=(sealed,))),
        verify=lambda t, s: codec.verify_context(t, "holder-b", s),
    ).visual_contexts[0]
    assert other.trusted is False


def test_an_unsealed_echo_is_untrusted_only_when_someone_verifies() -> None:
    """No verifier: exactly today's behaviour, for every existing `to_domain(...)` call in this
    suite -- an echoed context with no seal is trusted. Only a caller that actually hands a
    verifier (Task 3's job) turns a tokenless echo untrusted."""
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)  # token None: an older client
    payload = conversation_mapper.to_payload(Conversation(visual_contexts=(context,)))
    assert conversation_mapper.to_domain(payload).visual_contexts[0].trusted is True
    untrusted = conversation_mapper.to_domain(payload, verify=lambda t, s: False)
    assert untrusted.visual_contexts[0].trusted is False


def test_the_seal_covers_the_whole_wire_context_but_not_itself() -> None:
    """`context_semantics` is the one definition of what a seal covers: the wire context minus
    the seal itself -- everything `VisualContextPayload` carries except `token`, never a
    hand-picked subset that could drift from the DTO's own field set."""
    payload = conversation_mapper.to_payload(
        Conversation(visual_contexts=(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE),))
    )
    semantics = conversation_mapper.context_semantics(payload.visual_contexts[0])
    assert "token" not in semantics
    assert set(semantics) == set(VisualContextPayload.model_fields) - {"token"}


def test_a_contradictory_field_role_is_refused_at_the_dto() -> None:
    """I2 (fix round 2): `VisualField.__post_init__` (`chart.py`) already refuses a `role` that
    contradicts `type` -- a measure that is not quantitative is a mislabelled dataset -- but
    before this fix the wire never checked the pair before handing it to `conversation_mapper.
    to_domain`, which builds the domain type unguarded and let a bare `ValueError` escape as an
    unhandled 500. `VisualFieldPayload`'s own validator now constructs the same domain type at
    the edge and lets its `ValueError` become a 422 instead -- one source of truth with
    `VisualField.__post_init__`, the way `_derived_from_is_not_self` already does for
    `derived_from` and the per-encoding sort validator already does for `EncodingSort`.
    """
    contradictory = _visual_context_dict()
    # "dimension" is nominal, not quantitative -- "measure" is the contradiction.
    contradictory["profile"]["fields"][1]["role"] = "measure"
    with pytest.raises(ValueError, match="cannot be"):
        VisualContextPayload.model_validate(contradictory)
    consistent = _visual_context_dict()
    consistent["profile"]["fields"][1]["role"] = "dimension"
    VisualContextPayload.model_validate(consistent)  # does not raise


def test_a_duplicate_field_name_is_refused_at_the_dto() -> None:
    """F4 (fix wave 2): `VisualProfile.__post_init__` (`chart.py`) already refuses a duplicate
    field name -- `VisualField.name` collides -- but before this fix nothing on the wire checked
    that first: `conversation_mapper.to_domain` built the domain `VisualProfile` straight from
    this payload's `fields`, and a bare `ValueError` there is neither a `DomainError` nor a
    pydantic `ValidationError`, so it reached the holder as an unhandled 500 instead of a 422 --
    the same collision `_role_matches_type` above already exists to close for a contradictory
    `role`. One `@model_validator` closes it here too, the same way.
    """
    duplicated = _visual_context_dict()
    # Appended, not a rename over an existing entry: renaming would also make the encoding name a
    # field the profile no longer has, tripping `_value_free_spec`'s own "encoding field absent
    # from profile" check first and proving nothing about the duplicate-name check this pins.
    duplicated["profile"]["fields"].append(dict(duplicated["profile"]["fields"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        VisualContextPayload.model_validate(duplicated)
    distinct = _visual_context_dict()
    VisualContextPayload.model_validate(distinct)  # does not raise


def test_a_chart_cannot_claim_to_derive_from_itself_on_the_wire() -> None:
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    payload = conversation_mapper.to_payload(IDLE.with_visual_context(context))
    raw = payload.model_dump(mode="json")
    raw["visual_contexts"][0]["derived_from"] = context.chart_id
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate(raw)


def test_chips_over_the_breakdown_offer_the_turn_that_changes_it() -> None:
    """Task 3: the floor's own breakdown bar (`SPEC`) has its dimension on `y` (horizontal bars).
    «Ponlo en vertical» is offered because applying it changes the chart; «Ponlo en horizontal» is
    not, because the bar is already that way -- `_chip_verifies`'s canonical-equality check drops
    it, the same gate that drops a no-op sort chip elsewhere.
    """
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    suggestions = _chart_suggestions(context)
    assert "Ponlo en vertical" in suggestions
    assert "Ponlo en horizontal" not in suggestions


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_every_field_role_and_order_survives_the_wire_and_the_floors_chart_still_verifies(
    family_name: str,
) -> None:
    """Fix round 1: `VisualField.role`/`.order` and `VisualProfile.comparison`/`.plan_limited`
    are now on the wire, so a profile that crosses it is the same profile `verify` judges a
    restyle against -- not the narrowed guess `to_domain` used to fall back to unconditionally.

    Every family the exporter renders (the same ones the web compile gate checks), because the
    property this fix exists for -- "the floor's own chart still verifies after the wire" -- is
    exactly the one a single hand-picked fixture cannot prove for every shape a real profile
    takes: `_dimension_order` gives FREQUENCY `INPUT`, RECURRENCE `NONE`, a temporal group
    `TEMPORAL`, everything else `MEASURE_DESC`, and a comparison sets `comparison=True` with a
    `series` role next to the `dimension` one. A round trip that silently narrowed any of those
    would still build *a* profile -- just not the one the floor actually used.
    """
    dataset, kwargs = FAMILY_DATASETS[family_name]
    # `total_minor` is required and keyword-only on `visual_profile_of`; the exporter's own
    # `_family()` (`export_chart_fixtures.py`) is what defaults it to `None`, since only
    # `ratio_share`'s kwargs name it. Mirrored here rather than calling `_family()` itself, which
    # renders through the presenter and returns a wire dict, not the profile/spec pair this test
    # needs.
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    conversation = IDLE.with_visual_context(VisualContext(f"chart-{family_name}", spec, profile))
    restored = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    (context,) = restored.visual_contexts
    by_name = {field.name: field for field in context.profile.fields}
    for field in profile.fields:
        assert (by_name[field.name].role, by_name[field.name].order) == (field.role, field.order)
    assert context.profile.comparison == profile.comparison
    assert context.profile.plan_limited == profile.plan_limited
    verified = verify(draft_of(context.spec), context.profile)
    assert isinstance(verified, ChartSpec), f"{family_name}: {verified}"


def _context_semantics_before_and_after(context: VisualContext) -> tuple[dict, dict]:
    """`context_semantics` of `context`'s own wire payload, and again after one full wire round
    trip -- I1's own invariant, read from the seam that has to hold it: the seal digests exactly
    this, so the payload the server re-emits for a context it just rebuilt from the wire has to
    equal the payload it emitted at birth, or a chart born trusted goes untrusted on its second
    hop with nobody having changed anything.
    """
    conversation = IDLE.with_visual_context(context)
    (before,) = conversation_mapper.to_payload(conversation).visual_contexts
    restored = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    (after_context,) = restored.visual_contexts
    (after,) = conversation_mapper.to_payload(
        IDLE.with_visual_context(after_context)
    ).visual_contexts
    return (
        conversation_mapper.context_semantics(before),
        conversation_mapper.context_semantics(after),
    )


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_the_wire_payload_is_idempotent_across_every_floor_family(family_name: str) -> None:
    """I1 (fix wave): the seal digests `context_semantics`, so the payload the server re-emits
    for a context it just rebuilt from the wire has to equal the payload it emitted at birth --
    the tooltip accumulator broke exactly that for three or more tooltips (below), and this is
    the invariant that would have caught it, run over every family the floor can actually
    produce (none of them carries more than one tooltip, so this alone would not have failed
    before the fix -- the dedicated test below is what does).
    """
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    before, after = _context_semantics_before_and_after(
        VisualContext(f"chart-{family_name}", spec, profile)
    )
    assert before == after


def test_the_wire_payload_is_idempotent_for_a_spec_with_three_tooltips() -> None:
    """I1: `_context_spec_payload`'s tooltip accumulator used to drop the first two of three
    tooltips on a second pass and, even where a survivor's shape was compared across two passes,
    emit it as a one-element list on the first and a bare dict on the second -- so a context
    sealed with three tooltips came back untrusted after one restyle, with nobody having tampered
    with anything.

    `tooltip` is the one channel `ChartSpec.__post_init__` lets repeat -- its own uniqueness check
    (`channels = [... if item.channel != "tooltip"]`) excludes it by name, `color` included -- so
    there is no equivalent two-encodings-on-one-channel case to add for any other channel.
    """
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "dimension", "nominal"),
            ChartEncoding("y", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal", "Categoría"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
            ChartEncoding("tooltip", "extra", "nominal", "Nota"),
        ),
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 3),
            VisualField("value_minor", "quantitative", "measure", 3),
            VisualField("extra", "nominal", "dimension", 3),
        ),
        metric="SUM",
        unit="money",
    )
    before, after = _context_semantics_before_and_after(
        VisualContext("chart-tooltips", spec, profile)
    )
    assert before == after


def test_the_donut_familys_role_and_claims_survive_the_wire_too() -> None:
    """The floor never produces a donut (`DeterministicVisualPlanner.plan` has no such branch), so
    the parametrised test above cannot cover it -- it is reachable only through a conversational
    transformation, per the exporter's own `_donut_family` comment. Built the same way that
    function builds it: the one family whose profile must additionally keep `positive`/
    `mutually_exclusive`/`verified_total` across the wire for the verifier's `_donut_claims` rule
    to agree with it -- flags the round trip already carried before this fix, but worth pinning
    beside the two new ones so a donut chart's whole path (role, order, and the pre-existing
    three) is proven together.
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
    conversation = IDLE.with_visual_context(VisualContext("chart-donut", spec, profile))
    restored = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    (context,) = restored.visual_contexts
    by_name = {field.name: field for field in context.profile.fields}
    for field in profile.fields:
        assert (by_name[field.name].role, by_name[field.name].order) == (field.role, field.order)
    verified = verify(draft_of(context.spec), context.profile)
    assert isinstance(verified, ChartSpec)


async def test_the_conversation_stores_the_profile_the_chart_was_chosen_from() -> None:
    """One profile per turn, and the identity check is the whole point.

    The mapper used to build a second profile from the same dataset for the conversational
    context, with two of the plan's facts missing from it -- so the profile a restyle would read
    disagreed with the one the chart was chosen from. The decision carries its own input now, and
    a second build is not something this code can express.
    """
    result = await _result((VisualRow("Comida", 100), VisualRow("Casa", 50)))

    # The two mapper halves in the order `question_to_dto` runs them. Read here rather than off
    # the response because the response carries the *payload* profile, which has never had these
    # fields on it -- the domain object this remembers is the one a restyle reads.
    remembered = _remember_visual_context(result, _visualization_to_dto(result))

    assert result.visual_decision is not None
    assert remembered.conversation.visual_contexts[0].profile is result.visual_decision.profile


async def test_mapper_never_infers_a_chart_from_public_groups() -> None:
    assert question_to_dto(await _result(None)).visualization is None


async def test_explicit_single_row_is_a_kpi_and_never_a_vega_mark() -> None:
    dto = question_to_dto(await _result((VisualRow("total", 1234),), explicit=True))
    assert dto.visualization is not None
    assert dto.visualization.kind == "kpi"
    assert dto.visualization.value_field == "value_minor"
    assert dto.visualization.data == [{"dimension": "total", "value_minor": 1234}]


async def test_share_ratio_is_a_normalized_bar_bound_to_verified_operands() -> None:
    dto = question_to_dto(
        await _result(
            (VisualRow("Parte", 25), VisualRow("Resto", 75)),
            metric="RATIO_SHARE",
        )
    )
    assert dto.visualization is not None
    assert dto.visualization.kind == "chart"
    assert dto.visualization.spec["encoding"]["x"]["stack"] == "normalize"  # type: ignore[index]


async def test_frequency_keeps_weekday_input_order_and_recurrence_is_a_timeline() -> None:
    frequency = question_to_dto(
        await _result(
            tuple(VisualRow(day, count) for day, count in (("Lun", 2), ("Mar", 0))),
            metric="FREQUENCY",
        )
    )
    assert frequency.visualization is not None
    assert frequency.visualization.kind == "chart"
    assert frequency.visualization.spec["encoding"]["x"]["sort"] is None  # type: ignore[index]

    recurrence = question_to_dto(
        await _result(
            (
                VisualRow("Netflix", 1299, series="MONTHLY", occurred_at="2026-07-01"),
                VisualRow("Netflix", 1299, series="MONTHLY", occurred_at="2026-08-01"),
            ),
            metric="RECURRENCE",
        )
    )
    assert recurrence.visualization is not None
    assert recurrence.visualization.kind == "chart"
    assert recurrence.visualization.spec["mark"] == {"type": "point"}
    assert recurrence.visualization.spec["encoding"]["x"]["field"] == "occurred_at"  # type: ignore[index]


def _spec(group_by: str, rows: int, *, plan_limited: bool = False) -> ChartSpec:
    spec = derive_chart(
        group_by=group_by,
        metric="SUM",
        rows=rows,
        fields=frozenset({"dimension", "value_minor"}),
        dimension_field="dimension",
        value_field="value_minor",
        plan_limited=plan_limited,
    )
    assert spec is not None
    return spec


def test_a_wide_breakdown_draws_what_fits_and_yields_to_a_number_somebody_said() -> None:
    """The default is what fits; anything explicit beats it.

    Thirty merchants in a plot sized by its own rows is thirty threads, so a breakdown nobody put a
    number on draws the rows that fit. But cutting is a presentation decision, and it has to lose to
    every other kind: a plan that already ranked its rows has answered this question, and cutting
    again would answer "mis top 25 comercios" with eight -- and persist an eight nobody asked for
    into the saved metric, since the spec is what gets signed.
    """
    assert _spec("MERCHANT", 30).limit == ROWS_THAT_FIT
    assert _spec("MERCHANT", 30, plan_limited=True).limit is None
    # Nothing to cut: fewer rows than fit says nothing rather than claiming a sample.
    assert _spec("MERCHANT", ROWS_THAT_FIT).limit is None
    # A temporal axis is never trimmed. A ranking tails off; a series would lose months out of its
    # middle and still look like a series.
    assert _spec("MONTH", 30).limit is None


async def test_the_wire_carries_the_limit_so_the_client_can_admit_to_it() -> None:
    """The number is in the Vega transform already, but a client should not have to parse that."""
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1000 - position)
        for position in range(30)
    )
    result = await _result(rows, group_by="MERCHANT")
    visualization = question_to_dto(result).visualization
    assert visualization is not None and visualization.kind == "chart"
    assert visualization.limit == ROWS_THAT_FIT
    # Every row still travels: the limit hides rows from the plot, never from the evidence.
    assert len(visualization.data) == 30


async def test_a_plan_limited_dataset_is_not_cut_again_through_the_mapper() -> None:
    """The wire between the plan and the floor, not a hand-built profile.

    `test_a_plan_that_already_cut_its_rows_is_not_cut_again` (deterministic_visual_planner
    tests) hands the floor a profile it built itself, so it cannot catch `_decided_visual`
    failing to read `group_limit` off the plan. This one goes through `_result` -> `_decided_visual`
    -> `question_to_dto`, the same path a live turn takes.
    """
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1000 - position)
        for position in range(30)
    )
    result = await _result(rows, group_by="MERCHANT", group_limit=25)
    visualization = question_to_dto(result).visualization
    assert visualization is not None and visualization.kind == "chart"
    assert visualization.limit is None


async def test_a_comparison_of_counts_labels_its_axis_movements_through_the_mapper() -> None:
    """The other defect the brief's `plan()` reintroduced, caught the same way.

    `test_a_comparison_of_counts_labels_its_axis_movements` (deterministic_visual_planner
    tests) also hands the floor a hand-built profile. This one drives a plan whose `metric`
    ("COUNT") disagrees with its dataset's shape marker ("COMPARISON") through the mapper, so
    it fails if `_decided_visual` stops reading `context.metric` into `plan_metric`.
    """
    rows = (VisualRow("agosto", 12), VisualRow("julio", 9))
    result = await _result(rows, metric="COMPARISON", plan_metric="COUNT")
    dto = question_to_dto(result)
    assert dto.visualization is not None and dto.visualization.kind == "chart"
    encoding = dto.visualization.spec["encoding"]
    assert encoding["x"]["title"] == "Movimientos"  # type: ignore[index]


async def test_a_figure_is_offered_for_saving_even_with_no_chart_to_hang_it_from() -> None:
    """What makes an answer keepable is a plan and a figure, not a chart.

    "Cuanto he gastado este mes" is one number, so it has no chart -- and it is the most obvious
    metric anybody would save. The offer used to be gated on a `ChartVisualizationResponse`, which
    made it the single answer with no way to keep it.
    """
    tokens = MetricTokenCodec({"active": b"z" * 32}, "active")
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    figure = replace(
        await _result(None),
        total_minor=48945,
        movement_count=12,
        metric_definition=plan,
    )

    response = question_to_dto(
        figure, holder_reference="holder", metric_tokens=tokens, offer_saving=True
    )

    assert response.visualization is None
    assert response.save_candidate is not None
    # Null rather than invented: there is no chart, so nothing a restyle could target.
    assert response.save_candidate.chart_id is None
    saved = tokens.verify(response.save_candidate.definition_token, "holder")
    assert saved.plan == plan
    assert saved.chart is None


async def test_an_answer_with_no_figure_is_not_offered_for_saving() -> None:
    """A payment preview carries the last plan too, and it is not a metric."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    tokens = MetricTokenCodec({"active": b"z" * 32}, "active")
    without_figure = replace(await _result(None), metric_definition=plan)

    response = question_to_dto(without_figure, holder_reference="holder", metric_tokens=tokens)

    assert response.save_candidate is None


async def test_offer_saving_gates_the_candidate_and_not_the_seal() -> None:
    """Fix round 1 (Task 3): sealing a chart for its holder and offering to save the figure behind
    it are two different questions. `holder_reference`/`metric_tokens` alone -- `offer_saving`
    left at its default `False` -- still seal the fresh chart (a restyle later needs no metrics
    feature to be trusted), but mint no save candidate; `offer_saving=True` does both, over the
    identical figure.
    """
    tokens = MetricTokenCodec({"active": b"z" * 32}, "active")
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    figure = replace(
        await _result((VisualRow("a", 100), VisualRow("b", 50))),
        total_minor=150,
        movement_count=2,
        metric_definition=plan,
    )

    unoffered = question_to_dto(figure, holder_reference="holder", metric_tokens=tokens)
    assert unoffered.save_candidate is None
    assert unoffered.visualization is not None
    (unoffered_context,) = unoffered.conversation.visual_contexts[-1:]
    assert unoffered_context.token is not None
    assert tokens.verify_context(
        unoffered_context.token, "holder", conversation_mapper.context_semantics(unoffered_context)
    )

    offered = question_to_dto(
        figure, holder_reference="holder", metric_tokens=tokens, offer_saving=True
    )
    assert offered.save_candidate is not None
    (offered_context,) = offered.conversation.visual_contexts[-1:]
    assert offered_context.token is not None
    assert tokens.verify_context(
        offered_context.token, "holder", conversation_mapper.context_semantics(offered_context)
    )


def test_a_profile_describes_its_dataset_and_not_a_chart() -> None:
    """The profile is the planner's input, so it cannot be derived from the chart it decides."""
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 5, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 5),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
        comparison=False,
    )

    assert profile.field_names() == frozenset({"dimension", "value_minor"})
    assert profile.field("value_minor").role == "measure"
    assert profile.role("measure")[0].name == "value_minor"
    assert profile.field("nope") is None


def test_two_fields_may_share_the_dimension_role_when_their_types_differ() -> None:
    """RECURRENCE puts `occurred_at` temporal on x and `dimension` nominal on y.

    They are told apart by type, not by a fourth role. A `timestamp` role would be the same
    fact written twice, which is the defect family this repository keeps finding.
    """
    profile = VisualProfile(
        fields=(
            VisualField("occurred_at", "temporal", "dimension", 12, "TEMPORAL"),
            VisualField("dimension", "nominal", "dimension", 3, "INPUT"),
            VisualField("value_minor", "quantitative", "measure", 12),
        ),
        metric="RECURRENCE",
        unit="money",
        positive=True,
        mutually_exclusive=False,
        verified_total=False,
        comparison=False,
    )

    assert len(profile.role("dimension")) == 2
    assert {item.type for item in profile.role("dimension")} == {"temporal", "nominal"}


def test_a_profile_refuses_a_duplicate_field_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        VisualProfile(
            fields=(
                VisualField("dimension", "nominal", "dimension", 2),
                VisualField("dimension", "temporal", "dimension", 2),
            ),
            metric="SUM",
            unit="money",
            positive=True,
            mutually_exclusive=True,
            verified_total=False,
            comparison=False,
        )


def test_a_measure_must_be_quantitative() -> None:
    """The role's type is a fact about the dataset, not a choice. A nominal measure is a
    dataset that has been mislabelled, and the planner would inherit the lie."""
    with pytest.raises(ValueError, match="measure"):
        VisualField("value_minor", "nominal", "measure", 5)


def test_a_negative_cardinality_is_not_a_count() -> None:
    with pytest.raises(ValueError, match="cardinality"):
        VisualField("dimension", "nominal", "dimension", -1)


def test_a_negative_row_count_is_not_a_count() -> None:
    """`row_count` is how many rows the chart draws, and a floor reading a negative one would be
    reading a defect rather than a dataset."""
    with pytest.raises(ValueError, match="row_count"):
        VisualProfile(
            fields=(
                VisualField("dimension", "nominal", "dimension", 2),
                VisualField("value_minor", "quantitative", "measure", 2),
            ),
            metric="SUM",
            unit="money",
            positive=True,
            mutually_exclusive=True,
            verified_total=False,
            comparison=False,
            row_count=-1,
        )


def test_presentation_is_not_part_of_the_profile() -> None:
    """The profile round-trips through the browser (`conversation.py` validates it coming back),
    so anything put in it becomes wire surface. Viewport and channel are circumstances of one
    execution, not properties of the data."""
    profile_keys = {item.name for item in dataclasses.fields(VisualProfile)}

    assert "channel" not in profile_keys
    assert "viewport" not in profile_keys
    assert "max_visible_rows" not in profile_keys
    assert "question" not in profile_keys


def test_a_request_that_names_no_viewport_still_has_one() -> None:
    """The planner cannot wait on a web deployment, so the default is part of the contract."""
    assert DEFAULT_PRESENTATION.channel == "TEXT"
    assert DEFAULT_PRESENTATION.viewport == "DESKTOP"
    assert DEFAULT_PRESENTATION.max_visible_rows == ROWS_THAT_FIT


def test_a_mobile_viewport_shows_fewer_rows_than_it_is_asked_for() -> None:
    context = PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=40)

    assert context.max_visible_rows == 40  # stated wins over guessed
    with pytest.raises(ValueError, match="max_visible_rows"):
        PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=0)
    # The other two rejections. A context is validated because it arrives from a client, and an
    # unrecognised channel or viewport reaching a rule would be decided by an `else` -- a voice
    # answer drawn for a screen, silently, because nobody said the word was wrong.
    with pytest.raises(ValueError, match="viewport"):
        PresentationContext(  # type: ignore[arg-type]
            channel="TEXT", viewport="WATCH", max_visible_rows=8
        )
    with pytest.raises(ValueError, match="channel"):
        PresentationContext(  # type: ignore[arg-type]
            channel="TELEPATHY", viewport="MOBILE", max_visible_rows=8
        )


async def test_every_row_carries_every_field_the_profile_declares() -> None:
    """The profile advertises the union of the rows' fields; the mapper used to validate the
    spec against their intersection and drop, silently, a chart the verifier had accepted.
    One contract: rows carry what the profile declares, None where a row has no value."""
    dto = question_to_dto(
        await _result(
            (
                VisualRow("Comida", 100, series="agosto"),
                VisualRow("Comida", 80, series="julio"),
                VisualRow("Transporte", 50),  # no series on this row
            ),
            metric="COMPARISON",
        )
    )
    assert dto.visualization is not None and dto.visualization.kind == "chart"
    rows = dto.visualization.data
    assert all(set(row) == {"dimension", "value_minor", "series"} for row in rows)
    assert rows[2]["series"] is None
    assert dto.visualization.spec["encoding"]["color"]["field"] == "series"  # type: ignore[index]


async def test_a_kpi_still_carries_only_the_fields_its_row_has() -> None:
    dto = question_to_dto(await _result((VisualRow("Total", 12_345),), metric="SUM", explicit=True))
    assert dto.visualization is not None and dto.visualization.kind == "kpi"
    assert set(dto.visualization.data[0]) == {"dimension", "value_minor"}
