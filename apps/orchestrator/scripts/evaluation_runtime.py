"""Shared runtime for the offline evaluation scripts: the provider a run measures and the manifest
that records what was measured (commit, corpus digest, model runtime, GPU)."""

import asyncio
import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Any, Protocol

import httpx2 as httpx

from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import cloudflare_workers_ai
from finguard_orchestrator.infrastructure.llm.gemini import GeminiProvider
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)

REPOSITORY = Path(__file__).resolve().parents[3]


class EvaluationProvider(LlmProviderPort, Protocol):
    """A completion provider that can also prove it is reachable before a paid run."""

    async def ping(self) -> bool: ...


def _command_output(command: list[str], *, allow_empty: bool = False) -> str | None:
    """Best-effort provenance: measurement must still run on machines without Git/NVIDIA."""
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout.strip()
    return output if output or allow_empty else None


def _gpu_snapshot() -> list[dict[str, Any]] | None:
    raw = _command_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if raw is None:
        return None
    fields = (
        "name",
        "memory_used_mib",
        "memory_total_mib",
        "utilization_pct",
        "temperature_c",
    )
    return [
        dict(zip(fields, (part.strip() for part in line.split(",")), strict=True))
        for line in raw.splitlines()
    ]


async def _ollama_snapshot(provider: EvaluationProvider, model: str) -> dict[str, Any] | None:
    if not isinstance(provider, OllamaLlmProvider):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags_response = await client.get(f"{provider._base_url}/api/tags")
            tags_response.raise_for_status()
            ps_response = await client.get(f"{provider._base_url}/api/ps")
            ps_response.raise_for_status()
        tags = tags_response.json().get("models", [])
        running = ps_response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    installed: dict[str, Any] = next(
        (item for item in tags if item.get("name") == model or item.get("model") == model), {}
    )
    resident: dict[str, Any] = next(
        (item for item in running if item.get("name") == model or item.get("model") == model), {}
    )
    return {
        "digest": installed.get("digest"),
        "size_bytes": installed.get("size"),
        "size_vram_bytes": resident.get("size_vram"),
        "context_length": resident.get("context_length"),
    }


def build_provider(
    name: str,
    base_url: str | None,
    think: bool | str,
    timeout_seconds: float | None,
) -> EvaluationProvider:
    """Build the selected provider from the same settings used by the service.

    A benchmark-specific URL, timeout or reasoning budget would measure a deployment nobody
    runs. Command-line overrides exist for recorded endpoints and unusually slow local models,
    while every omitted value comes from ``Settings``. Missing hosted credentials fail here,
    before a corpus sentence can leave the machine.
    """
    settings = get_settings()
    if name == "groq":
        if settings.groq_api_key is None:
            raise ValueError("ORCHESTRATOR_GROQ_API_KEY is not set - see .env.defaults")
        return OpenAiCompatibleProvider(
            name=name,
            api_key=settings.groq_api_key.get_secret_value(),
            base_url=base_url or settings.groq_base_url,
            timeout_seconds=timeout_seconds or settings.groq_timeout_seconds,
            reasoning_headroom=settings.groq_reasoning_headroom,
        )
    if name == "gemini":
        if settings.gemini_api_key is None:
            raise ValueError("ORCHESTRATOR_GEMINI_API_KEY is not set - see .env.defaults")
        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            base_url=base_url or settings.gemini_base_url,
            timeout_seconds=timeout_seconds or settings.gemini_timeout_seconds,
        )
    if name == "cloudflare":
        if settings.cloudflare_api_token is None or settings.cloudflare_account_id is None:
            raise ValueError(
                "ORCHESTRATOR_CLOUDFLARE_API_TOKEN and "
                "ORCHESTRATOR_CLOUDFLARE_ACCOUNT_ID must both be set - see .env.defaults"
            )
        return cloudflare_workers_ai(
            api_token=settings.cloudflare_api_token.get_secret_value(),
            account_id=settings.cloudflare_account_id,
            api_base=base_url or settings.cloudflare_api_base,
            timeout_seconds=timeout_seconds or settings.cloudflare_timeout_seconds,
            reasoning_headroom=settings.cloudflare_reasoning_headroom,
        )
    if name != "ollama":
        raise ValueError(f"unknown provider: {name}")
    return OllamaLlmProvider(
        base_url=base_url or settings.ollama_base_url,
        keep_alive=settings.ollama_keep_alive,
        timeout_seconds=timeout_seconds or settings.ollama_timeout_seconds,
        context_tokens=settings.ollama_context_tokens,
        seed=settings.ollama_seed,
        # False is the historical CLI default. Let the deployment choose a model-specific
        # level (gpt-oss needs low/medium/high for structured output) unless the caller
        # explicitly enabled thinking for this run.
        think=settings.ollama_think if not think else think,
    )


async def run_manifest(
    dataset_path: Path, provider: EvaluationProvider, model: str, case_count: int
) -> dict[str, Any]:
    commit = _command_output(["git", "rev-parse", "HEAD"])
    dirty = _command_output(["git", "status", "--porcelain"], allow_empty=True)
    return {
        "schema": 1,
        "model": model,
        "provider": provider.name,
        "cases": case_count,
        "commit": commit,
        "dirty": bool(dirty) if dirty is not None else None,
        "corpus_sha256": hashlib.sha256(
            await asyncio.to_thread(dataset_path.read_bytes)
        ).hexdigest(),
        "python": platform.python_version(),
        "provider_settings": {
            "timeout_seconds": getattr(provider, "timeout_seconds", None),
            "context_tokens": getattr(provider, "context_tokens", None),
            "seed": getattr(provider, "seed", None),
            "think": getattr(provider, "think", None),
        },
        "model_runtime": await _ollama_snapshot(provider, model),
        "gpu": _gpu_snapshot(),
    }
