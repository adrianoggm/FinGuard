"""What the model sees, what it may answer, and the one repair the financial planner also makes."""

import json
from typing import get_args

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    Channel,
    FieldType,
    Mark,
    SortMode,
    Stack,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.planning.llm_query_planner import STRUCTURE_REPAIR
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import LlmVisualPlanner
from finguard_orchestrator.infrastructure.planning.visual_prompt import (
    profile_payload,
    visual_schema,
)

PROFILE = visual_profile_of(
    VisualDataset(
        metric="SUM", rows=(VisualRow("Alimentación", 193_847), VisualRow("Transporte", 278_413))
    ),
    total_minor=734_291,
    group_by="NONE",
)

# One of these is non-ASCII on purpose (finding 12): `build_visual_prompt` dumps with
# `ensure_ascii=False`, so a sentinel loop that dumps with the default `ensure_ascii=True` would
# see `"Alimentación"` instead of the glyph and miss a real leak of it.
SENTINELS = ("734291", "193847", "278413", "Alimentación", "Transporte")


class RecordingProvider:
    name = "recording"

    def __init__(self, answers: list[str | BaseException]) -> None:
        self.answers, self.prompts = list(answers), []

    async def complete(self, prompt, *, model, temperature, max_output_tokens, schema=None):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_the_payload_carries_shape_and_never_values() -> None:
    """Spec B: sentinels prove three values; the key sets prove that rows, amounts and labels have
    no key to travel under, which is the strong half."""
    payload = profile_payload(PROFILE, DEFAULT_PRESENTATION)
    flat = json.dumps(payload, ensure_ascii=False)
    for sentinel in SENTINELS:
        assert sentinel not in flat
    assert set(payload) <= {
        "fields",
        "metric",
        "unit",
        "positive",
        "mutually_exclusive",
        "verified_total",
        "comparison",
        "row_count",
        "presentation",
    }
    for field in payload["fields"]:
        assert set(field) <= {"name", "type", "role", "cardinality", "order"}
    assert set(payload["presentation"]) == {"viewport", "max_visible_rows"}


def test_the_schema_is_built_from_the_domain_literals() -> None:
    schema = visual_schema()
    props = schema["properties"]
    assert None in props["mark"]["enum"]  # abstention is {"mark": null}
    assert set(props["mark"]["enum"]) - {None} == set(get_args(Mark))
    encoding = props["encodings"]["items"]["properties"]
    assert set(encoding["channel"]["enum"]) == set(get_args(Channel))
    assert set(encoding["type"]["enum"]) == set(get_args(FieldType))
    assert set(encoding["sort"]["properties"]["mode"]["enum"]) == set(get_args(SortMode))
    assert set(props["stack"]["enum"]) - {None} == set(get_args(Stack))
    assert set(props["sort"]["enum"]) - {None} == set(get_args(SortMode))


async def test_echo_yields_a_parseable_draft_the_verifier_can_refuse() -> None:
    """The floor any real model must beat: structurally valid, semantically empty."""
    planner = LlmVisualPlanner(
        EchoLlmProvider(), model="echo", temperature=0.0, max_output_tokens=256
    )
    draft = await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert draft is not None and draft.mark == get_args(Mark)[0] and draft.encodings == ()


async def test_a_valid_answer_becomes_a_draft_and_the_question_is_in_the_prompt() -> None:
    answer = json.dumps(
        {
            "mark": "bar",
            "encodings": [
                {
                    "channel": "y",
                    "field": "dimension",
                    "type": "nominal",
                    "sort": {"mode": "descending", "field": "value_minor"},
                },
                {"channel": "x", "field": "value_minor", "type": "quantitative"},
            ],
            "stack": None,
            "limit": None,
        }
    )
    provider = RecordingProvider([answer])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    draft = await planner.plan(
        "¿en qué gasto más?", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0
    )
    assert draft.mark == "bar" and draft.encodings[0].sort.field == "value_minor"
    assert "¿en qué gasto más?" in provider.prompts[0]
    assert all(s not in provider.prompts[0] for s in SENTINELS)


async def test_one_repair_then_a_provider_error() -> None:
    """Also risk 5's privacy half (finding 7): the question and the provider's own bad text must
    not reach the exception the adapter raises."""
    provider = RecordingProvider(["not json", "still not json"])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError) as raised:
        await planner.plan(
            "¿cuánto gasté en Alimentación?", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0
        )
    assert len(provider.prompts) == 2
    assert provider.prompts[1] == provider.prompts[0] + STRUCTURE_REPAIR  # the same repair, once
    assert "Alimentación" not in str(raised.value)
    assert "Alimentación" not in raised.value.reason
    assert "still not json" not in str(raised.value)  # nor the provider's own text


async def test_an_explicit_abstention_is_none() -> None:
    provider = RecordingProvider([json.dumps({"mark": None, "encodings": []})])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    assert await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0) is None


async def test_a_minimal_abstention_is_not_recorded_as_a_provider_error() -> None:
    """Section E: the four fallback reasons must stay distinguishable. A model that abstained in
    the tersest valid form must not be counted as a provider that failed (finding 9)."""
    provider = RecordingProvider([json.dumps({"mark": None})])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    assert await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0) is None
    assert len(provider.prompts) == 1  # no repair was needed


async def test_a_boolean_limit_is_never_silently_repaired() -> None:
    """Finding 1 (blocker): `isinstance(True, int)` is `True` in Python, so a JSON boolean must
    not become `limit=1` -- it is a structure failure, repaired once, then raised."""
    answer = json.dumps({"mark": "bar", "encodings": [], "limit": True})
    provider = RecordingProvider([answer, answer])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert len(provider.prompts) == 2


async def test_a_string_limit_is_never_silently_dropped() -> None:
    """Finding 1 (blocker): a `limit` the adapter cannot read must not vanish into `None` -- the
    model asked for a top-N and the verifier must see the request to be able to refuse it."""
    answer = json.dumps({"mark": "bar", "encodings": [], "limit": "5"})
    provider = RecordingProvider([answer, answer])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert len(provider.prompts) == 2


async def test_an_encoding_missing_a_required_key_is_repaired_not_fabricated() -> None:
    """Finding 13: a missing `channel` must take the repair path, never become the literal
    string `"None"` via `str(item.get("channel"))`."""
    bad = json.dumps({"mark": "bar", "encodings": [{"field": "dimension", "type": "nominal"}]})
    provider = RecordingProvider([bad, bad])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert len(provider.prompts) == 2


async def test_a_sort_missing_its_required_mode_is_repaired_not_fabricated() -> None:
    """Finding 13: the same defect, one level down -- `{"sort": {}}` must not become
    `ProposedSort(mode="None")`."""
    bad = json.dumps(
        {
            "mark": "bar",
            "encodings": [{"channel": "x", "field": "dimension", "type": "nominal", "sort": {}}],
        }
    )
    provider = RecordingProvider([bad, bad])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert len(provider.prompts) == 2


async def test_json_wrapped_in_prose_is_not_extracted() -> None:
    """Finding 6: accept-with-extraction is a repair; Section C forbids repair. Neither a code
    fence nor a friendly prefix may be stripped to find the JSON inside."""
    good = json.dumps({"mark": "bar", "encodings": []})
    provider = RecordingProvider([f"```json\n{good}\n```", f"Sure! {good}"])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)


async def test_an_unknown_channel_reaches_the_verifier_rather_than_being_dropped() -> None:
    """Finding 6: the adapter must not filter an encoding whose channel it does not recognise --
    that is the verifier's job, and dropping it here would be a silent repair."""
    answer = json.dumps(
        {
            "mark": "bar",
            "encodings": [{"channel": "radius", "field": "dimension", "type": "nominal"}],
        }
    )
    planner = LlmVisualPlanner(
        RecordingProvider([answer]), model="m", temperature=0.0, max_output_tokens=256
    )
    draft = await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert draft is not None and draft.encodings[0].channel == "radius"  # refused, never repaired
