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


# `"c"` (the chart id, I3) is the one optional body key on the `m1` envelope: every other key is
# on every token, whichever shape `issue` minted.
_METRIC_BODY_REQUIRED = frozenset({"v", "t", "k", "h", "d"})
_METRIC_BODY_ALLOWED = _METRIC_BODY_REQUIRED | {"c"}


class MetricTokenCodec:
    def __init__(self, keys: dict[str, bytes], active: str) -> None:
        if not keys or active not in keys or any(len(key) < 32 for key in keys.values()):
            raise ValueError("metric signing keys need at least 32 bytes")
        self._keys = dict(keys)
        self._active = active

    def issue(
        self,
        holder_reference: str,
        definition: SavedMetricDefinition,
        *,
        chart_id: str | None = None,
    ) -> str:
        """A save-candidate token for `holder_reference`, optionally bound to `chart_id`.

        I3 (fix wave): a token minted for one chart used to say nothing about which chart it was
        for, so the same holder's token from an unrelated restyle could be offered as another
        chart's `metric_sources` entry and `verify` had no way to refuse it -- both tokens are
        legitimately the holder's, which is exactly why the seal alone did not catch it. `chart_id`
        closes that: the two call sites that mint a *save candidate* pass the chart the candidate
        is for, and `verify(..., chart_id=...)` refuses a mismatch. Left `None` for a plain figure
        (no chart to bind) and for a definition nothing here pairs with a screen.
        """
        payload: dict[str, object] = {
            "v": 1,
            "t": "m1",
            "k": self._active,
            "h": holder_reference,
            "d": encode_definition(definition),
        }
        if chart_id is not None:
            payload["c"] = chart_id
        return "m1." + self._sign(payload)

    def verify(
        self, token: str, holder_reference: str, *, chart_id: str | None = None
    ) -> SavedMetricDefinition:
        """`token`'s definition, for `holder_reference` and -- when asked -- for `chart_id`.

        I3 (fix wave): a caller that names the chart it is pairing this token with (a restyle's
        own save candidate) gets the binding enforced -- a mismatch, or a token minted before this
        change with no chart bound to it at all, both raise. A caller that asks for no chart
        (`POST /v1/metrics`, which saves a figure rather than pairing a restyle) reads either
        shape unchanged, exactly as before this field existed.
        """
        raw = self._open(token, "m1")
        if raw is None or not _METRIC_BODY_REQUIRED <= set(raw) or set(raw) - _METRIC_BODY_ALLOWED:
            raise MetricTokenError("invalid metric token")
        if raw.get("h") != holder_reference:
            raise MetricTokenError("invalid metric token")
        if chart_id is not None and raw.get("c") != chart_id:
            raise MetricTokenError("invalid metric token")
        return decode_definition(raw.get("d"))

    def seal_context(self, holder_reference: str, semantics: dict[str, object]) -> str:
        """A token binding `holder_reference` to the digest of `semantics`, nothing else."""
        payload = {
            "v": 1,
            "t": "c1",
            "k": self._active,
            "h": holder_reference,
            "g": _digest(semantics),
        }
        return "c1." + self._sign(payload)

    def verify_context(
        self, token: str | None, holder_reference: str, semantics: dict[str, object]
    ) -> bool:
        """Whether `token` is `holder_reference`'s own seal over `semantics` -- never raises."""
        if token is None:
            return False
        raw = self._open(token, "c1")
        if raw is None or set(raw) != {"v", "t", "k", "h", "g"}:
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
            # `ValueError` here is `_unb64`'s own `MetricTokenError` (an alphabet violation) or
            # `json.loads`'s `json.JSONDecodeError` (malformed JSON) -- both load-bearing, and
            # neither visible at this line: a malformed token is exactly as unopenable as one
            # that fails the MAC below, so both collapse to the same `None` a caller branches on.
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
        # m1 (fix wave): checked only now, after the MAC -- `parts[0]` above still routes (which
        # envelope a token *claims* to be, before a hash is spent on it), but the claim used to be
        # the only check. A context seal (`c1.`) relabelled `m1.` keeps a valid signature over its
        # own untouched body, so `_open` would have handed that body to `verify()` as if it had
        # opened a metric token, and only the two envelopes' disjoint key sets stopped it reading
        # as one -- luck, not a check. The signed `"t"` is the check.
        if raw.get("t") != prefix:
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
