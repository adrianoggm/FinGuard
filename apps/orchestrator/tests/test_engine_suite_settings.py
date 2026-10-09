"""The suite runs on the shipped settings, whatever a developer's own `.env` says (ruling R30).

A run once sent the suite's sentences to Groq, because the tree's `.env` switched the engine to
`model` on the `groq` provider and the settings read it. `conftest.py` now pins every setting that
chooses an engine, a model or a hosted provider to its `.env.defaults` value before anything is
imported; this checks the pin took, on whatever tree it runs.
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


def test_the_engine_and_its_model_are_the_shipped_ones() -> None:
    settings = get_settings()

    assert settings.conversation_engine == shipped("CONVERSATION_ENGINE") == "rules"
    assert settings.engine_model is None
    assert settings.planner_provider == shipped("PLANNER_PROVIDER")
    assert settings.planner_model == shipped("PLANNER_MODEL")
    assert settings.turn_interpreter == shipped("TURN_INTERPRETER")
    assert settings.visual_planner == shipped("VISUAL_PLANNER")
    assert app.state.engine is None


def test_no_hosted_provider_is_reachable() -> None:
    settings = get_settings()

    assert settings.groq_api_key is None
    assert settings.cloudflare_api_token is None
    assert settings.a2a_gateway_url is None
