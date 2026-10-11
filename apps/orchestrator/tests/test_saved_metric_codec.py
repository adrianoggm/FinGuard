from uuid import uuid4

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


def test_a_sealed_context_verifies_for_its_holder_conversation_position_and_content() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    conversation_id = uuid4()
    token = codec.seal_context("holder-a", conversation_id, 0, semantics)
    assert token.startswith("c2.")
    assert codec.verify_context(token, "holder-a", conversation_id, 0, semantics) == "ok"


@pytest.mark.parametrize(
    "tamper,reason",
    [
        (lambda t, c, s: (t, "holder-b", c, 0, s), "holder"),  # another holder
        (lambda t, c, s: (t, "holder-a", uuid4(), 0, s), "conversation"),  # another conversation
        (lambda t, c, s: (t, "holder-a", c, 1, s), "position"),  # a different index in the list
        (
            lambda t, c, s: (t, "holder-a", c, 0, {**s, "profile": {"comparison": False}}),
            "content",
        ),
        (
            # signature bit
            lambda t, c, s: (t[:-2] + ("A" if t[-2] != "A" else "B") + t[-1], "holder-a", c, 0, s),
            "format",
        ),
        (lambda t, c, s: ("m1." + t[3:], "holder-a", c, 0, s), "format"),  # wrong prefix
        (lambda t, c, s: ("c1." + t[3:], "holder-a", c, 0, s), "format"),  # pre-hardening prefix
        (lambda t, c, s: ("not a token", "holder-a", c, 0, s), "format"),
        (lambda t, c, s: (None, "holder-a", c, 0, s), "format"),
    ],
    ids=[
        "holder",
        "conversation",
        "position",
        "content",
        "signature",
        "prefix",
        "pre-hardening-prefix",
        "malformed",
        "missing",
    ],
)
def test_a_seal_names_why_it_does_not_verify_when_anything_moves(tamper, reason) -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    semantics = {"chart_id": "chart-1", "spec": ["bar"], "profile": {"comparison": True}}
    conversation_id = uuid4()
    token = codec.seal_context("holder-a", conversation_id, 0, semantics)
    assert codec.verify_context(*tamper(token, conversation_id, semantics)) == reason


def test_a_seal_survives_key_order_and_dies_with_key_rotation() -> None:
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    conversation_id = uuid4()
    token = codec.seal_context("h", conversation_id, 0, {"b": 1, "a": [1, 2]})
    # canonical JSON
    assert codec.verify_context(token, "h", conversation_id, 0, {"a": [1, 2], "b": 1}) == "ok"
    rotated = MetricTokenCodec({"k2": b"y" * 32}, "k2")
    assert rotated.verify_context(token, "h", conversation_id, 0, {"a": [1, 2], "b": 1}) == "format"


def test_the_type_discriminator_is_signed_not_only_the_prefix() -> None:
    """m1 (fix wave): before this fix, `_open` compared the unsigned `parts[0]` against the
    prefix its caller asked for and never asked the signed body what it thought it was. A context
    seal (`c2.`) relabelled `m1.` still carries a valid signature over its own, untouched body --
    `_open("m1." + body_and_signature, "m1")` matched the structural check and would have handed
    that body back to `verify()` as if it had opened a metric token. Only the two envelopes'
    disjoint key sets (`{v,k,h,c,p,g}` vs `{v,t,k,h,d}`) stopped `verify()` from accepting it --
    luck, not a check. The signed `"t"` field closes it: the relabelled token's body still says
    `"t": "c2"`, so `_open` refuses it before either side ever asks about keys.
    """
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    context_token = codec.seal_context("holder-a", uuid4(), 0, {"chart_id": "chart-1"})
    relabelled = "m1." + context_token.split(".", 1)[1]
    assert codec._open(relabelled, "m1") is None


def test_a_metric_token_names_the_chart_it_was_issued_for() -> None:
    """I3 (fix wave): `issue` now takes the chart id it is minting a save candidate for, and
    `verify` checks it back when the caller demands one. A caller that does not ask (`POST
    /v1/metrics`, which saves a figure rather than pairing a restyle) still reads either shape --
    the whole point of leaving `chart_id` optional on both ends.
    """
    codec = MetricTokenCodec({"active": b"k" * 32}, "active")
    bound = codec.issue("holder-1", definition(), chart_id="chart-a")
    assert codec.verify(bound, "holder-1", chart_id="chart-a") == definition()
    with pytest.raises(MetricTokenError):
        codec.verify(bound, "holder-1", chart_id="chart-b")
    # `POST /v1/metrics`'s own call: no chart_id demanded, so a bound token still reads.
    assert codec.verify(bound, "holder-1") == definition()

    unbound = codec.issue("holder-1", definition())
    assert codec.verify(unbound, "holder-1") == definition()
    with pytest.raises(MetricTokenError):
        codec.verify(unbound, "holder-1", chart_id="chart-a")
