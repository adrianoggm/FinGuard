"""Public API of the infrastructure layer.

What the composition root is allowed to reach for: the routers to mount, the adapters to
wire and the error translation to install. Everything else — DTOs, mappers, the provider
implementations themselves — is internal to the layer and stays out of this list on
purpose, because the bootstrap has no business touching it. A provider is reached through
the registry, never imported by name.

`register_error_handlers` is spelled out here rather than re-exported as `register`: at the
call site `register(app)` alone did not say what was being registered.
"""

from finguard_assistant.infrastructure.config.toml_model_catalogue import TomlModelCatalogue
from finguard_assistant.infrastructure.controllers.llm_controller import (
    admin_router,
    assistant_router,
)
from finguard_assistant.infrastructure.error_handlers import register as register_error_handlers
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry

__all__ = [
    "ProviderRegistry",
    "TomlModelCatalogue",
    "admin_router",
    "assistant_router",
    "register_error_handlers",
]
