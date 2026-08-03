"""Public API of the application layer.

Whoever uses this layer — the composition root, the controllers, the mappers — imports it
from here and not from the module each piece happens to live in today. That way moving
`get_balance.py` is an internal change, and the list below is the honest answer to what
the layer offers: if a name is not here, nobody outside was meant to use it.

Inside the layer the imports stay direct (`from ...queries.get_balance import ...`): a
package does not reach itself through its own front door.
"""

from finguard_banking.application.bus.command_bus import Command, CommandBus, CommandHandler
from finguard_banking.application.bus.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)
from finguard_banking.application.bus.query_bus import Query, QueryBus, QueryHandler
from finguard_banking.application.commands.payments import (
    CancelPaymentCommand,
    CancelPaymentHandler,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_banking.application.queries.get_balance import (
    GetBalanceHandler,
    GetBalanceQuery,
    GetBalanceResult,
)
from finguard_banking.application.queries.list_cards import (
    CardRow,
    ListCardsHandler,
    ListCardsQuery,
    ListCardsResult,
)
from finguard_banking.application.queries.list_contacts import (
    ContactRow,
    ListContactsHandler,
    ListContactsQuery,
    ListContactsResult,
)
from finguard_banking.application.queries.movement_summary import (
    MovementSummaryHandler,
    MovementSummaryQuery,
    MovementSummaryResult,
    SummaryGroup,
    SummaryMovement,
    SummaryTrace,
)

__all__ = [
    "CardRow",
    "ContactRow",
    "ListCardsHandler",
    "ListCardsQuery",
    "ListCardsResult",
    "ListContactsHandler",
    "ListContactsQuery",
    "ListContactsResult",
    "CancelPaymentCommand",
    "CancelPaymentHandler",
    "Command",
    "CommandBus",
    "CommandHandler",
    "ConfirmPaymentCommand",
    "ConfirmPaymentHandler",
    "GetBalanceHandler",
    "GetBalanceQuery",
    "GetBalanceResult",
    "HandlerAlreadyRegisteredError",
    "HandlerNotRegisteredError",
    "MovementSummaryHandler",
    "MovementSummaryQuery",
    "MovementSummaryResult",
    "PreparePaymentCommand",
    "PreparePaymentHandler",
    "Query",
    "QueryBus",
    "QueryHandler",
    "SummaryGroup",
    "SummaryMovement",
    "SummaryTrace",
]
