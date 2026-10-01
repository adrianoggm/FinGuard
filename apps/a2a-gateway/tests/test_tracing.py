"""The gateway's traces reach a collector from compose: named, and posted to the traces path."""

from collections.abc import Sequence

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from finguard_a2a import bootstrap
from finguard_a2a.config import Settings


class _Exporter(SpanExporter):
    endpoints: list[str | None] = []

    def __init__(self, endpoint: str | None = None) -> None:
        _Exporter.endpoints.append(endpoint)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS


@pytest.fixture(autouse=True)
def _recording(monkeypatch: pytest.MonkeyPatch) -> None:
    _Exporter.endpoints = []
    monkeypatch.setattr(bootstrap, "OTLPSpanExporter", _Exporter)


@pytest.mark.parametrize(
    ("given", "posted"),
    [
        ("http://otel-collector:4318", "http://otel-collector:4318/v1/traces"),
        ("http://otel-collector:4318/", "http://otel-collector:4318/v1/traces"),
        ("http://otel-collector:4318/v1/traces", "http://otel-collector:4318/v1/traces"),
        ("", None),
    ],
)
def test_a_base_url_gets_the_traces_path(given: str, posted: str | None) -> None:
    """Ruling R30: the OTLP/HTTP exporter posts to exactly the endpoint it is handed, so a base
    URL -- what compose knows -- is a 404 on every export unless the path is joined, as the
    orchestrator joins it."""
    bootstrap._tracer(Settings(otel_enabled=True, otel_endpoint=given))
    assert _Exporter.endpoints == [posted]


def test_the_spans_name_the_gateway_as_their_service() -> None:
    tracer = bootstrap._tracer(Settings(otel_enabled=True, otel_endpoint="http://c:4318"))
    resource = tracer.resource  # type: ignore[attr-defined]
    assert resource.attributes["service.name"] == "a2a-gateway"
    assert resource.attributes["service.version"] == Settings().service_version
