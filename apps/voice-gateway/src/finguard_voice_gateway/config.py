from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOICE_GATEWAY_", extra="ignore")
    ticket_ttl_seconds: int = 30
    max_frame_bytes: int = 64 * 1024
    max_utterance_seconds: int = 30
    input_sample_rate: Literal[16_000] = 16_000
    output_sample_rate: Literal[24_000] = 24_000
    session_idle_seconds: int = 30 * 60
    session_absolute_seconds: int = 2 * 60 * 60
    audio_bytes_per_second: int = 16_000 * 2
    assistant_mode: Literal["fake", "orchestrator_http"] = "fake"
    stt_mode: Literal["fake", "local"] = "fake"
    stt_model: str = "small"
    stt_device: str = "auto"
    stt_compute_type: str = "int8"
    stt_language: str = "es"
    stt_beam_size: int = 5
    stt_vad_filter: bool = True
    # Words the decoder would otherwise never guess: the rail names, and anything else this
    # product says out loud that a general Spanish model has never seen. Product vocabulary,
    # so a setting. Contact names could be appended per holder; that needs the directory here,
    # which the gateway deliberately does not have.
    stt_initial_prompt: str = (
        "Banca en español. Vocabulario: bizum, bizums, transferencia, traspaso, nómina, "
        "recibo, suscripción, saldo, movimientos, hucha, fondo indexado, Mercadona, Alcampo. "
        "Nombres: Marta, Aitor, Elena, Diego, Nuria, Sergio, Carmen, Laura."
    )
    stt_timeout_seconds: float = 45.0
    stt_max_concurrency: int = 1
    stt_warmup: bool = False
    stt_local_files_only: bool = False
    tts_mode: Literal["fake", "local"] = "fake"
    tts_model_path: str = ""
    tts_config_path: str = ""
    tts_device: Literal["cpu", "cuda"] = "cpu"
    tts_speaker: int | None = None
    tts_length_scale: float | None = None
    tts_chunk_ms: int = 20
    tts_timeout_seconds: float = 60.0
    tts_warmup: bool = False
    # Browser origins allowed to call this service. Exact origins, never a wildcard: the caller's
    # token travels in a header, and `*` with credentials lets any page a holder visits spend their
    # session. The WebSocket is not covered by CORS — its gate is the one-use ticket.
    web_origins: tuple[str, ...] = (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    orchestrator_base_url: str = "http://127.0.0.1:8080"
    orchestrator_timeout_seconds: float = 60.0
    orchestrator_stream_path: str = "/v1/questions/stream"

    def model_post_init(self, __context: object) -> None:
        if self.orchestrator_timeout_seconds <= 0:
            raise ValueError("VOICE_GATEWAY_ORCHESTRATOR_TIMEOUT_SECONDS must be positive")
        if not self.orchestrator_base_url.startswith(("http://", "https://")):
            raise ValueError("VOICE_GATEWAY_ORCHESTRATOR_BASE_URL must be an HTTP URL")
        if not self.orchestrator_stream_path.startswith("/"):
            raise ValueError("VOICE_GATEWAY_ORCHESTRATOR_STREAM_PATH must start with '/'")
        if self.stt_mode == "local":
            if self.stt_device not in {"auto", "cpu", "cuda"}:
                raise ValueError("VOICE_GATEWAY_STT_DEVICE must be auto, cpu or cuda")
            if self.stt_compute_type not in {
                "int8",
                "int8_float16",
                "int8_float32",
                "float16",
                "float32",
            }:
                raise ValueError("VOICE_GATEWAY_STT_COMPUTE_TYPE is not supported")
            if not self.stt_model.strip():
                raise ValueError("VOICE_GATEWAY_STT_MODEL must not be empty")
            if not 1 <= self.stt_beam_size <= 20:
                raise ValueError("VOICE_GATEWAY_STT_BEAM_SIZE must be between 1 and 20")
            if self.stt_timeout_seconds <= 0:
                raise ValueError("VOICE_GATEWAY_STT_TIMEOUT_SECONDS must be positive")
            if self.stt_max_concurrency < 1:
                raise ValueError("VOICE_GATEWAY_STT_MAX_CONCURRENCY must be positive")
        if self.tts_mode == "local":
            if not self.tts_model_path.strip():
                raise ValueError("VOICE_GATEWAY_TTS_MODEL_PATH is required in local mode")
            if self.tts_chunk_ms <= 0:
                raise ValueError("VOICE_GATEWAY_TTS_CHUNK_MS must be positive")
            if self.tts_timeout_seconds <= 0:
                raise ValueError("VOICE_GATEWAY_TTS_TIMEOUT_SECONDS must be positive")
            if self.tts_speaker is not None and self.tts_speaker < 0:
                raise ValueError("VOICE_GATEWAY_TTS_SPEAKER must not be negative")
            if self.tts_length_scale is not None and self.tts_length_scale <= 0:
                raise ValueError("VOICE_GATEWAY_TTS_LENGTH_SCALE must be positive")
