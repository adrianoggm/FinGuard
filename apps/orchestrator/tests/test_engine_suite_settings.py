"""The suite stays isolated from hosted providers.

A run once sent the suite's sentences to Groq, because the tree's `.env` switched the engine to
`model` on the `groq` provider and the settings read it. `conftest.py` pins the settings that choose
a model to their shipped values and blanks hosted credentials. This checks those overrides took
effect.
"""

from pathlib import Path

from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.bootstrap.main import app

SHIPPED = Path(__file__).resolve().parents[1] / ".env.defaults"


def shipped(key: str) -> str:
    for line in SHIPPED.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"ORCHESTRATOR_{key}="):
            return line.split("=", 1)[1]
    raise KeyError(key)


def test_the_suite_runs_on_the_shipped_model_settings() -> None:
    settings = get_settings()

    assert settings.engine_model is None
    assert settings.planner_provider == shipped("PLANNER_PROVIDER")
    assert settings.planner_model == shipped("PLANNER_MODEL")
    assert settings.visual_planner == shipped("VISUAL_PLANNER")
    assert app.state.engine is None


def test_no_hosted_provider_is_reachable() -> None:
    settings = get_settings()

    assert settings.groq_api_key is None
    assert settings.cloudflare_api_token is None
    assert settings.a2a_gateway_url is None
