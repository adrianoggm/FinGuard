"""Stateless HMAC envelopes for save candidates."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.infrastructure.metrics.codec import decode_definition, encode_definition


class MetricTokenError(ValueError):
    """A candidate was malformed, altered, expired by key rotation, or for another holder."""


class MetricTokenCodec:
    def __init__(self, keys: dict[str, bytes], active: str) -> None:
        if not keys or active not in keys or any(len(key) < 32 for key in keys.values()):
            raise ValueError("metric signing keys need at least 32 bytes")
        self._keys = dict(keys)
        self._active = active

    def issue(self, holder_reference: str, definition: SavedMetricDefinition) -> str:
        payload = {
            "v": 1,
            "k": self._active,
            "h": holder_reference,
            "d": encode_definition(definition),
        }
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        signature = hmac.new(self._keys[self._active], body, hashlib.sha256).digest()
        return ".".join(("m1", _b64(body), _b64(signature)))

    def verify(self, token: str, holder_reference: str) -> SavedMetricDefinition:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "m1":
            raise MetricTokenError("invalid metric token")
        try:
            body = _unb64(parts[1])
            signature = _unb64(parts[2])
            raw: Any = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            raise MetricTokenError("invalid metric token") from exc
        if not isinstance(raw, dict) or set(raw) != {"v", "k", "h", "d"} or raw.get("v") != 1:
            raise MetricTokenError("invalid metric token")
        key_name = raw.get("k")
        key = self._keys.get(key_name) if isinstance(key_name, str) else None
        if key is None or not hmac.compare_digest(
            signature, hmac.new(key, body, hashlib.sha256).digest()
        ):
            raise MetricTokenError("invalid metric token")
        if raw.get("h") != holder_reference:
            raise MetricTokenError("invalid metric token")
        return decode_definition(raw.get("d"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or any(character not in alphabet for character in value):
        raise MetricTokenError("invalid metric token")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
