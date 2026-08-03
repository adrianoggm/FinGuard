"""Public API of the application layer.

Whoever uses this layer — the composition root, the controllers, the mappers — imports it
from here and not from the module each piece happens to live in today. That way moving
`answer_message.py` is an internal change, and the list below is the honest answer to what
the layer offers: if a name is not here, nobody outside was meant to use it.

Inside the layer the imports stay direct (`from ...queries.answer_message import ...`): a
package does not reach itself through its own front door.
"""

from finguard_orchestrator.application.bus.command_bus import Command, CommandBus, CommandHandler
from finguard_orchestrator.application.bus.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)
from finguard_orchestrator.application.bus.query_bus import Query, QueryBus, QueryHandler
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PaymentAnswer,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_orchestrator.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_orchestrator.application.policy_store import PolicyStore
from finguard_orchestrator.application.queries.answer_message import (
    AnswerMessageHandler,
    AnswerMessageQuery,
    AnswerMessageResult,
)
from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    SpokenPlan,
    Stage,
)
from finguard_orchestrator.application.queries.get_llm_catalogue import (
    CatalogueResult,
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
    ModelRow,
    PolicyRow,
)

__all__ = [
    "ConfirmPaymentCommand",
    "ConfirmPaymentHandler",
    "PaymentAnswer",
    "PreparePaymentCommand",
    "PreparePaymentHandler",
    "AnswerMessageHandler",
    "AnswerMessageQuery",
    "AnswerMessageResult",
    "AskFinancialQuestionHandler",
    "AskFinancialQuestionQuery",
    "AskFinancialQuestionResult",
    "SpokenPlan",
    "Stage",
    "CatalogueResult",
    "Command",
    "CommandBus",
    "CommandHandler",
    "GetLlmCatalogueHandler",
    "GetLlmCatalogueQuery",
    "HandlerAlreadyRegisteredError",
    "HandlerNotRegisteredError",
    "ModelRow",
    "PolicyRow",
    "PolicyStore",
    "Query",
    "QueryBus",
    "QueryHandler",
    "ReplaceLlmPolicyCommand",
    "ReplaceLlmPolicyHandler",
    "ResetLlmPolicyCommand",
    "ResetLlmPolicyHandler",
]
