import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from finguard_a2a.adapters.outbound.env_registry import EnvRegistry
from finguard_a2a.adapters.outbound.sdk_remote_agents import SdkRemoteAgents
from finguard_a2a.application.directory import Directory
from finguard_a2a.config import DEVELOPMENT_TOKEN, Settings
from finguard_a2a.domain.agents import AgentStatus

logger = logging.getLogger("finguard_a2a")


@dataclass
class Services:
    token: str
    directory: Directory
    remote: SdkRemoteAgents
    tracer: trace.Tracer


def credential_lookup(environ: Mapping[str, str]):  # type: ignore[no-untyped-def]
    """`gym-norte` -> `A2A_GATEWAY_CREDENTIAL_GYM_NORTE`; blank counts as absent."""

    def lookup(agent_id: str) -> str | None:
        return environ.get("A2A_GATEWAY_CREDENTIAL_" + agent_id.upper().replace("-", "_")) or None

    return lookup


def build_services(
    settings: Settings, http: httpx.AsyncClient, environ: Mapping[str, str] | None = None
) -> Services:
    token = settings.token.get_secret_value()
    if not token:
        logger.warning("A2A_GATEWAY_TOKEN is blank: using the development token")
        token = DEVELOPMENT_TOKEN
    tracer = _tracer(settings)

    def discovered(status: AgentStatus) -> None:
        with tracer.start_as_current_span("a2a.discover") as span:
            span.set_attribute("agent.id", status.agent.id)
            span.set_attribute("usable", status.usable)
            span.set_attribute("reason", status.reason or "")

    directory = Directory(
        EnvRegistry(settings.agents) if settings.agents.strip() else _NoAgents(),
        http,
        credential_lookup(os.environ if environ is None else environ),
        settings.card_ttl_seconds,
        on_discovered=discovered,
    )
    remote = SdkRemoteAgents(
        http,
        timeout_seconds=settings.timeout_seconds,
        max_text_bytes=settings.max_text_bytes,
        max_data_bytes=settings.max_data_bytes,
    )
    return Services(token, directory, remote, tracer)


class _NoAgents:
    def agents(self) -> tuple[()]:
        return ()


def _tracer(settings: Settings) -> trace.Tracer:
    if not settings.otel_enabled:
        return trace.get_tracer("finguard_a2a")
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint or None)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider.get_tracer("finguard_a2a")
