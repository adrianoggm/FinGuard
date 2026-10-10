"""Public API of the infrastructure layer.

What the composition root is allowed to reach for: the routers to mount, the adapters to
wire and the error translation to install. Everything else — DTOs, mappers, the provider
implementations themselves — is internal to the layer and stays out of this list on
purpose, because the bootstrap has no business touching it. A provider is reached through
the registry, never imported by name.

`register_error_handlers` is spelled out here rather than re-exported as `register`: at the
call site `register(app)` alone did not say what was being registered.
"""

from finguard_orchestrator.infrastructure.banking.http_account_summary import HttpAccountSummary
from finguard_orchestrator.infrastructure.banking.http_accounts import HttpAccounts
from finguard_orchestrator.infrastructure.banking.http_cards import HttpCards
from finguard_orchestrator.infrastructure.banking.http_contacts import HttpContacts
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery
from finguard_orchestrator.infrastructure.banking.http_movement_categories import (
    HttpMovementCategories,
)
from finguard_orchestrator.infrastructure.banking.http_payments import HttpPayments
from finguard_orchestrator.infrastructure.config.toml_model_catalogue import TomlModelCatalogue
from finguard_orchestrator.infrastructure.controllers.account_controller import (
    router as account_router,
)
from finguard_orchestrator.infrastructure.controllers.graph_question_controller import (
    router as graph_question_router,
)
from finguard_orchestrator.infrastructure.controllers.payment_controller import payments_router
from finguard_orchestrator.infrastructure.controllers.runtime_controller import (
    admin_router,
    assistant_router,
)
from finguard_orchestrator.infrastructure.error_handlers import register as register_error_handlers
from finguard_orchestrator.infrastructure.llm.provider_registry import ProviderRegistry

# Preserve the public assistant router's complete screen API while keeping deterministic banking
# routes in a module that does not import conversational language readers.
assistant_router.include_router(account_router)

__all__ = [
    "HttpAccountSummary",
    "HttpAccounts",
    "HttpCards",
    "HttpContacts",
    "HttpFinancialQuery",
    "HttpMovementCategories",
    "HttpPayments",
    "ProviderRegistry",
    "TomlModelCatalogue",
    "account_router",
    "admin_router",
    "assistant_router",
    "graph_question_router",
    "payments_router",
    "register_error_handlers",
]
