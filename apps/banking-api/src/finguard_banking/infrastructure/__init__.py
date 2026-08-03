"""Public API of the infrastructure layer.

What the composition root is allowed to reach for: the routers to mount, the adapters to
wire and the error translation to install. Everything else — DTOs, mappers, the SQL
compiler — is internal to the layer and stays out of this list on purpose, because the
bootstrap has no business touching it.

`register_error_handlers` is spelled out here rather than re-exported as `register`: at the
call site `register(app)` alone did not say what was being registered.
"""

from finguard_banking.infrastructure.controllers.banking_controller import (
    accounts_router,
    health_router,
    queries_router,
)
from finguard_banking.infrastructure.controllers.payments_controller import payments_router
from finguard_banking.infrastructure.error_handlers import register as register_error_handlers
from finguard_banking.infrastructure.persistence.audit_log import PostgresAuditLog
from finguard_banking.infrastructure.persistence.card_repository import (
    PostgresCardRepository,
)
from finguard_banking.infrastructure.persistence.contact_repository import (
    PostgresContactRepository,
)
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.infrastructure.persistence.ledger_repository import PostgresLedgerRepository
from finguard_banking.infrastructure.persistence.movement_category_repository import (
    PostgresMovementCategoryRepository,
)
from finguard_banking.infrastructure.persistence.payment_repository import (
    PostgresPaymentRepository,
)

__all__ = [
    "PostgresAuditLog",
    "PostgresCardRepository",
    "PostgresContactRepository",
    "PostgresFinancialQueryRepository",
    "PostgresLedgerRepository",
    "PostgresMovementCategoryRepository",
    "PostgresPaymentRepository",
    "accounts_router",
    "health_router",
    "payments_router",
    "queries_router",
    "register_error_handlers",
]
