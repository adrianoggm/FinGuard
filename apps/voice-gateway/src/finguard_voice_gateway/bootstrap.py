from dataclasses import dataclass

import httpx

from finguard_voice_gateway.adapters.outbound.fake_assistant import FakeAssistantClient
from finguard_voice_gateway.adapters.outbound.fake_stt import FakeSpeechToText
from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech
from finguard_voice_gateway.adapters.outbound.local_stt import LocalSpeechToText
from finguard_voice_gateway.adapters.outbound.local_tts import LocalTextToSpeech
from finguard_voice_gateway.adapters.outbound.orchestrator_assistant import (
    OrchestratorAssistantClient,
)
from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.ports.assistant_client import AssistantClient
from finguard_voice_gateway.ports.speech_to_text import SpeechToText, SpeechToTextLifecycle
from finguard_voice_gateway.ports.text_to_speech import TextToSpeech, TextToSpeechLifecycle


@dataclass
class Services:
    settings: Settings
    stt: SpeechToText
    tts: TextToSpeech
    assistant: AssistantClient
    http_client: httpx.AsyncClient | None = None


def build_services(settings: Settings | None = None) -> Services:
    effective = settings or Settings()
    stt: SpeechToText = (
        LocalSpeechToText(effective)
        if effective.stt_mode == "local"
        else FakeSpeechToText(effective)
    )
    tts: TextToSpeech = (
        LocalTextToSpeech(effective)
        if effective.tts_mode == "local"
        else FakeTextToSpeech(effective)
    )
    if effective.assistant_mode == "orchestrator_http":
        client = httpx.AsyncClient(
            base_url=effective.orchestrator_base_url.rstrip("/"),
            timeout=httpx.Timeout(effective.orchestrator_timeout_seconds),
        )
        return Services(
            effective,
            stt,
            tts,
            OrchestratorAssistantClient(client, effective.orchestrator_stream_path),
            client,
        )
    return Services(effective, stt, tts, FakeAssistantClient())


def ensure_services_open(service_set: Services) -> None:
    """Fail deterministically instead of using an AsyncClient after lifecycle shutdown."""
    if service_set.http_client is not None and service_set.http_client.is_closed:
        raise RuntimeError("voice gateway resources are already closed")
    if isinstance(service_set.stt, SpeechToTextLifecycle) and service_set.stt.is_closed:
        raise RuntimeError("voice gateway speech-to-text resources are already closed")
    if isinstance(service_set.tts, TextToSpeechLifecycle) and service_set.tts.is_closed:
        raise RuntimeError("voice gateway text-to-speech resources are already closed")
