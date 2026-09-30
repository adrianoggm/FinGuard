from typing import Any

from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes
from fastapi import FastAPI

from finguard_simulated_gym.cards import card_for, legacy_card
from finguard_simulated_gym.config import Settings


def build_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    if settings.style == "legacy":
        app = FastAPI(title="Gimnasio Antiguo")
        raw = legacy_card(settings)

        @app.get("/.well-known/agent-card.json")
        async def legacy_agent_card() -> dict[str, Any]:
            return raw

    else:
        card = card_for(settings)
        app = FastAPI(title=card.name)
        add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card))
        app.state.card = card

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
