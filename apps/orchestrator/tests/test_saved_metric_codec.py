import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.metrics.codec import (
    CodecError,
    dumps_definition,
    loads_definition,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import (
    MetricTokenCodec,
    MetricTokenError,
)


def definition() -> SavedMetricDefinition:
    return SavedMetricDefinition(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH)),
        ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("x", "dimension", "nominal"),
                ChartEncoding("y", "value_minor", "quantitative"),
            ),
        ),
    )


def test_codec_roundtrip_and_rejects_extra_fields() -> None:
    encoded = dumps_definition(definition())
    assert loads_definition(encoded) == definition()
    assert '"value":' not in encoded
    try:
        loads_definition(encoded.replace('"chart":', '"unexpected":1,"chart":'))
    except CodecError:
        pass
    else:
        raise AssertionError("extra fields must be rejected")


def test_token_is_bound_to_holder_and_signature() -> None:
    codec = MetricTokenCodec({"old": b"a" * 32, "new": b"b" * 32}, "new")
    token = codec.issue("holder-a", definition())
    assert codec.verify(token, "holder-a") == definition()
    for holder, altered in [("holder-b", token), ("holder-a", token[:-1] + "x")]:
        try:
            codec.verify(altered, holder)
        except MetricTokenError:
            pass
        else:
            raise AssertionError("tampered/foreign token accepted")


def test_a_metric_can_be_a_figure_with_no_chart_and_the_old_shape_still_reads() -> None:
    """The plan is the metric; the chart is how one answer to it was drawn.

    A single figure -- "cuanto he gastado este mes" -- is the most obvious thing anybody would keep
    and was the one thing that could not be kept, because the save offer was gated on there being a
    chart to hang it from. So the chart is nullable, and the key is omitted rather than written as
    null: `_object` accepts a subset of the keys it allows, so both shapes decode and no version
    had to move.
    """
    figure = SavedMetricDefinition(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    )
    encoded = dumps_definition(figure)
    assert '"chart"' not in encoded
    assert loads_definition(encoded) == figure
    assert loads_definition(encoded).chart is None

    # A definition written before the chart became optional reads back unchanged.
    with_chart = dumps_definition(definition())
    assert '"chart"' in with_chart
    assert loads_definition(with_chart) == definition()

    # And the token round trip carries either shape for the holder it was issued to.
    codec = MetricTokenCodec({"active": b"k" * 32}, "active")
    for subject in (figure, definition()):
        assert codec.verify(codec.issue("holder-1", subject), "holder-1") == subject
        try:
            codec.verify(codec.issue("holder-1", subject), "holder-2")
        except MetricTokenError:
            pass
        else:  # pragma: no cover - the guard is the point of the test
            raise AssertionError("a token must not verify for another holder")


def test_a_sealed_context_verifies_for_its_holder_and_content() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    token = codec.seal_context("holder-a", semantics)
    assert token.startswith("c1.")
    assert codec.verify_context(token, "holder-a", semantics) is True


@pytest.mark.parametrize(
    "tamper",
    [
        lambda t, s: (t, "holder-b", s),  # another holder
        lambda t, s: (t, "holder-a", {**s, "profile": {"comparison": False}}),  # content changed
        # signature bit
        lambda t, s: (t[:-2] + ("A" if t[-1] != "A" else "B") + t[-1], "holder-a", s),
        lambda t, s: ("m1." + t[3:], "holder-a", s),  # wrong prefix
        lambda t, s: ("not a token", "holder-a", s),
        lambda t, s: (None, "holder-a", s),
    ],
    ids=["holder", "content", "signature", "prefix", "malformed", "missing"],
)
def test_a_seal_does_not_verify_when_anything_moves(tamper) -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    token = codec.seal_context("holder-a", semantics)
    assert codec.verify_context(*tamper(token, semantics)) is False


def test_a_seal_survives_key_order_and_dies_with_key_rotation() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    token = codec.seal_context("h", {"b": 1, "a": [1, 2]})
    assert codec.verify_context(token, "h", {"a": [1, 2], "b": 1}) is True  # canonical JSON
    rotated = MetricTokenCodec({"k2": b"y" * 32}, "k2")
    assert rotated.verify_context(token, "h", {"a": [1, 2], "b": 1}) is False
