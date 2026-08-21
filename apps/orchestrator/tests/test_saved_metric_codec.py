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
