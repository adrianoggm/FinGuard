"""Stateless HMAC envelopes for save candidates and signed visual contexts.

A sealed context binds a holder reference to the SHA-256 digest of a piece of canonical-JSON
content -- what was shown, not who asked -- so the orchestrator can hand a client a token and
later confirm that the same holder is still looking at the same thing. It carries no content of
its own (a digest only) and no expiry of its own; the only way a seal goes stale is key rotation.
`verify_context` never raises: an unverified context is a state the caller branches on, not an
error condition.
"""

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
        return "m1." + self._sign(payload)

    def verify(self, token: str, holder_reference: str) -> SavedMetricDefinition:
        raw = self._open(token, "m1")
        if raw is None or set(raw) != {"v", "k", "h", "d"}:
            raise MetricTokenError("invalid metric token")
        if raw.get("h") != holder_reference:
            raise MetricTokenError("invalid metric token")
        return decode_definition(raw.get("d"))

    def seal_context(self, holder_reference: str, semantics: dict[str, object]) -> str:
        payload = {
            "v": 1,
            "k": self._active,
            "h": holder_reference,
            "g": _digest(semantics),
        }
        return "c1." + self._sign(payload)

    def verify_context(
        self, token: str | None, holder_reference: str, semantics: dict[str, object]
    ) -> bool:
        if token is None:
            return False
        raw = self._open(token, "c1")
        if raw is None or set(raw) != {"v", "k", "h", "g"}:
            return False
        if raw.get("h") != holder_reference:
            return False
        return raw.get("g") == _digest(semantics)

    def _sign(self, body: dict[str, object]) -> str:
        encoded = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        signature = hmac.new(self._keys[self._active], encoded, hashlib.sha256).digest()
        return ".".join((_b64(encoded), _b64(signature)))

    def _open(self, token: str, prefix: str) -> dict[str, object] | None:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != prefix:
            return None
        try:
            body = _unb64(parts[1])
            signature = _unb64(parts[2])
            raw: Any = json.loads(body)
        except ValueError:
            return None
        if not isinstance(raw, dict) or raw.get("v") != 1:
            return None
        key_name = raw.get("k")
        key = self._keys.get(key_name) if isinstance(key_name, str) else None
        if key is None or not hmac.compare_digest(
            signature, hmac.new(key, body, hashlib.sha256).digest()
        ):
            return None
        return raw


def _digest(semantics: dict[str, object]) -> str:
    canonical = json.dumps(semantics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not value or any(character not in alphabet for character in value):
        raise MetricTokenError("invalid metric token")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
