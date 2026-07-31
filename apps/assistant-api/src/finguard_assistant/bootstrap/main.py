from fastapi import FastAPI

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.bootstrap.config import get_settings
from finguard_assistant.infrastructure import error_handlers
from finguard_assistant.infrastructure.controllers.llm_controller import admin, router
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry

DESCRIPTION = """
Conversational orchestrator of FinGuard.

The model that answers is chosen by the **active policy**, which can be swapped at
runtime over `/admin/llm-policy` without restarting. Which models exist, which provider
serves each one and what happens when one fails come from the catalogue file, not from
code.
"""


def create_app() -> FastAPI:
    """Composition root: this is the only place that wires concrete implementations."""
    app = FastAPI(
        title="FinGuard Assistant API",
        version="0.1.0",
        description=DESCRIPTION,
        summary="Conversational orchestrator",
        openapi_tags=[
            {"name": "assistant", "description": "Conversation with the assistant."},
            {"name": "admin", "description": "Runtime configuration. Needs auth before exposure."},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
    )

    catalogue = get_settings().catalogue()
    # The active policy is created at startup and lives in the application state: the
    # service is deliberately not stateless, its configuration changes at runtime.
    app.state.catalogue = catalogue
    app.state.providers = ProviderRegistry()
    app.state.policies = PolicyStore(catalogue, catalogue.default_policy())

    app.include_router(router)
    app.include_router(admin)
    error_handlers.register(app)
    return app


app = create_app()
