"""I4 (fix wave): without signing keys, the seal a deployment relies on is the fallback key
committed in this repository -- forgeable by whoever reads the source. Before this segment that
key signed save candidates only, unreachable with metrics off; Task 3 made the same codec seal
every visual context unconditionally, so the same fallback now guards the whole trust boundary.

`create_app()` already refused a real deployment in one shape: `metrics_enabled` true with no
keys. What this fix adds is the *narrower* case that shape never covered -- metrics off (or
unset), no keys, and a web origin that is not loopback. `ORCHESTRATOR_WEB_ORIGINS` is the
existing, already-required setting read by both the CORS middleware and this new guard
(`bootstrap/main.py`'s own `settings.web_origins`) -- no new setting is introduced.

Loopback-only or no origins at all keeps the pre-existing warning (D-3b-7): the one signal that a
deployment's seals are forgeable, pinned here with `caplog` because after this fix it is the only
signal a `just app` developer loop ever sees.
"""

import logging

from finguard_orchestrator.bootstrap import main as bootstrap_main
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.bootstrap.main import create_app

# A valid 32-byte key, base64url-encoded the way `ORCHESTRATOR_METRICS_SIGNING_KEYS` expects
# ("name:key", a JSON list of them) -- `base64.urlsafe_b64encode(b"x" * 32)`.
_VALID_KEY = '["default:eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="]'


def _create_app_with(monkeypatch, **env: str) -> object:
    # `create_app`'s own first line is `configure_logging(...)`, which runs `logging.config.
    # dictConfig` and replaces the root logger's handlers -- caplog's own handler included. A
    # no-op stand-in is what lets `caplog` see the warning this test is about instead of losing
    # it to that reconfiguration.
    monkeypatch.setattr(bootstrap_main, "configure_logging", lambda **kwargs: None)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


def test_no_keys_and_a_public_origin_refuses_to_start(monkeypatch) -> None:
    """The case the pre-existing `metrics_enabled` guard never covered: metrics off (this
    module's own default), no signing keys, and a deployment that has told this same service --
    through the setting its own CORS middleware already reads -- that it serves a real browser
    origin rather than a loopback one."""
    try:
        _create_app_with(monkeypatch, ORCHESTRATOR_WEB_ORIGINS='["https://app.example.com"]')
    except ValueError as exc:
        assert "METRICS_SIGNING_KEYS" in str(exc)
        assert "non-loopback" in str(exc)
    else:
        raise AssertionError("a public origin with no signing keys must refuse to start")


def test_no_keys_and_loopback_only_still_warns_not_refuses(monkeypatch, caplog) -> None:
    """`just app`, unconfigured: every origin `.env.defaults` ships is loopback, so this must
    keep starting -- with the warning that has always been the one signal it is running on the
    development key (D-3b-7)."""
    with caplog.at_level(logging.WARNING, logger="finguard_orchestrator.bootstrap.main"):
        _create_app_with(monkeypatch)
    records = [r for r in caplog.records if "development key" in r.message]
    assert len(records) == 1


def test_keys_configured_warns_of_nothing(monkeypatch, caplog) -> None:
    """A deployment that has set real signing keys gets silence, whatever its origins are --
    the warning exists only to flag the fallback key, and here nothing is falling back to it."""
    with caplog.at_level(logging.WARNING, logger="finguard_orchestrator.bootstrap.main"):
        _create_app_with(
            monkeypatch,
            ORCHESTRATOR_METRICS_SIGNING_KEYS=_VALID_KEY,
            ORCHESTRATOR_WEB_ORIGINS='["https://app.example.com"]',
        )
    records = [r for r in caplog.records if "development key" in r.message]
    assert records == []
