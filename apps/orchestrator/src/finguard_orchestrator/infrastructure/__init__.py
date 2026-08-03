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
from finguard_orchestrator.infrastructure.banking.http_cards import HttpCards
from finguard_orchestrator.infrastructure.banking.http_contacts import HttpContacts
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery
from finguard_orchestrator.infrastructure.banking.http_payments import HttpPayments
from finguard_orchestrator.infrastructure.config.toml_model_catalogue import TomlModelCatalogue
from finguard_orchestrator.infrastructure.controllers.llm_controller import (
    admin_router,
    assistant_router,
)
from finguard_orchestrator.infrastructure.controllers.payment_controller import payments_router
from finguard_orchestrator.infrastructure.error_handlers import register as register_error_handlers
from finguard_orchestrator.infrastructure.llm.provider_registry import ProviderRegistry
from finguard_orchestrator.infrastructure.payments.llm_payment_reader import LlmPaymentReader
from finguard_orchestrator.infrastructure.planning.llm_query_planner import LlmQueryPlanner

__all__ = [
    "HttpAccountSummary",
    "HttpCards",
    "HttpContacts",
    "HttpFinancialQuery",
    "HttpPayments",
    "LlmPaymentReader",
    "LlmQueryPlanner",
    "ProviderRegistry",
    "TomlModelCatalogue",
    "admin_router",
    "assistant_router",
    "payments_router",
    "register_error_handlers",
]
