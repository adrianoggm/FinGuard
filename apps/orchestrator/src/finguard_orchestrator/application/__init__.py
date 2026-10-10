"""Lazy public API for the application layer.

The package keeps its stable re-export names without importing every use case at process startup.

"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "Command": ("bus.command_bus", "Command"),
    "CommandBus": ("bus.command_bus", "CommandBus"),
    "CommandHandler": ("bus.command_bus", "CommandHandler"),
    "HandlerAlreadyRegisteredError": ("bus.errors", "HandlerAlreadyRegisteredError"),
    "HandlerNotRegisteredError": ("bus.errors", "HandlerNotRegisteredError"),
    "Query": ("bus.query_bus", "Query"),
    "QueryBus": ("bus.query_bus", "QueryBus"),
    "QueryHandler": ("bus.query_bus", "QueryHandler"),
    "CategoriseMovementCommand": ("commands.categorise", "CategoriseMovementCommand"),
    "CategoriseMovementHandler": ("commands.categorise", "CategoriseMovementHandler"),
    "ConfirmPaymentCommand": ("commands.pay", "ConfirmPaymentCommand"),
    "ConfirmPaymentHandler": ("commands.pay", "ConfirmPaymentHandler"),
    "PaymentAnswer": ("commands.pay", "PaymentAnswer"),
    "PrepareChosenPaymentCommand": ("commands.pay", "PrepareChosenPaymentCommand"),
    "PrepareChosenPaymentHandler": ("commands.pay", "PrepareChosenPaymentHandler"),
    "PrepareOwnTransferCommand": ("commands.pay", "PrepareOwnTransferCommand"),
    "PrepareOwnTransferHandler": ("commands.pay", "PrepareOwnTransferHandler"),
    "ReplaceLlmPolicyCommand": ("commands.replace_llm_policy", "ReplaceLlmPolicyCommand"),
    "ReplaceLlmPolicyHandler": ("commands.replace_llm_policy", "ReplaceLlmPolicyHandler"),
    "ResetLlmPolicyCommand": ("commands.replace_llm_policy", "ResetLlmPolicyCommand"),
    "ResetLlmPolicyHandler": ("commands.replace_llm_policy", "ResetLlmPolicyHandler"),
    "PolicyStore": ("policy_store", "PolicyStore"),
    "AnswerMessageHandler": ("queries.answer_message", "AnswerMessageHandler"),
    "AnswerMessageQuery": ("queries.answer_message", "AnswerMessageQuery"),
    "AnswerMessageResult": ("queries.answer_message", "AnswerMessageResult"),
    "BreakdownResult": ("queries.browse_account", "BreakdownResult"),
    "GetBreakdownHandler": ("queries.browse_account", "GetBreakdownHandler"),
    "GetBreakdownQuery": ("queries.browse_account", "GetBreakdownQuery"),
    "GetMovementsHandler": ("queries.browse_account", "GetMovementsHandler"),
    "GetMovementsQuery": ("queries.browse_account", "GetMovementsQuery"),
    "MovementsResult": ("queries.browse_account", "MovementsResult"),
    "AskFinancialQuestionQuery": ("queries.financial_turn", "AskFinancialQuestionQuery"),
    "AskFinancialQuestionResult": ("queries.financial_turn", "AskFinancialQuestionResult"),
    "SpokenPlan": ("queries.financial_turn", "SpokenPlan"),
    "AccountSummaryResult": ("queries.get_account_summary", "AccountSummaryResult"),
    "GetAccountSummaryHandler": ("queries.get_account_summary", "GetAccountSummaryHandler"),
    "GetAccountSummaryQuery": ("queries.get_account_summary", "GetAccountSummaryQuery"),
    "CatalogueResult": ("queries.get_llm_catalogue", "CatalogueResult"),
    "GetLlmCatalogueHandler": ("queries.get_llm_catalogue", "GetLlmCatalogueHandler"),
    "GetLlmCatalogueQuery": ("queries.get_llm_catalogue", "GetLlmCatalogueQuery"),
    "ModelRow": ("queries.get_llm_catalogue", "ModelRow"),
    "PolicyRow": ("queries.get_llm_catalogue", "PolicyRow"),
    "CardRow": ("queries.list_cards", "CardRow"),
    "ChargesResult": ("queries.list_cards", "ChargesResult"),
    "GetCardChargesHandler": ("queries.list_cards", "GetCardChargesHandler"),
    "GetCardChargesQuery": ("queries.list_cards", "GetCardChargesQuery"),
    "ListCardsHandler": ("queries.list_cards", "ListCardsHandler"),
    "ListCardsQuery": ("queries.list_cards", "ListCardsQuery"),
    "WalletResult": ("queries.list_cards", "WalletResult"),
    "ListContactsHandler": ("queries.list_contacts", "ListContactsHandler"),
    "ListContactsQuery": ("queries.list_contacts", "ListContactsQuery"),
    "ListContactsResult": ("queries.list_contacts", "ListContactsResult"),
}

__all__ = [
    "AccountSummaryResult",
    "AnswerMessageHandler",
    "AnswerMessageQuery",
    "AnswerMessageResult",
    "AskFinancialQuestionQuery",
    "AskFinancialQuestionResult",
    "BreakdownResult",
    "CardRow",
    "CatalogueResult",
    "CategoriseMovementCommand",
    "CategoriseMovementHandler",
    "ChargesResult",
    "Command",
    "CommandBus",
    "CommandHandler",
    "ConfirmPaymentCommand",
    "ConfirmPaymentHandler",
    "GetAccountSummaryHandler",
    "GetAccountSummaryQuery",
    "GetBreakdownHandler",
    "GetBreakdownQuery",
    "GetCardChargesHandler",
    "GetCardChargesQuery",
    "GetLlmCatalogueHandler",
    "GetLlmCatalogueQuery",
    "GetMovementsHandler",
    "GetMovementsQuery",
    "HandlerAlreadyRegisteredError",
    "HandlerNotRegisteredError",
    "ListCardsHandler",
    "ListCardsQuery",
    "ListContactsHandler",
    "ListContactsQuery",
    "ListContactsResult",
    "ModelRow",
    "MovementsResult",
    "PaymentAnswer",
    "PolicyRow",
    "PolicyStore",
    "PrepareChosenPaymentCommand",
    "PrepareChosenPaymentHandler",
    "PrepareOwnTransferCommand",
    "PrepareOwnTransferHandler",
    "Query",
    "QueryBus",
    "QueryHandler",
    "ReplaceLlmPolicyCommand",
    "ReplaceLlmPolicyHandler",
    "ResetLlmPolicyCommand",
    "ResetLlmPolicyHandler",
    "SpokenPlan",
    "WalletResult",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))


if TYPE_CHECKING:
    # Keep static type checkers aware of the public re-exports without eagerly importing them at
    # runtime. These imports deliberately mirror `_EXPORTS` and preserve the package API.
    from finguard_orchestrator.application.bus.command_bus import (
        Command,
        CommandBus,
        CommandHandler,
    )
    from finguard_orchestrator.application.bus.errors import (
        HandlerAlreadyRegisteredError,
        HandlerNotRegisteredError,
    )
    from finguard_orchestrator.application.bus.query_bus import Query, QueryBus, QueryHandler
    from finguard_orchestrator.application.commands.categorise import (
        CategoriseMovementCommand,
        CategoriseMovementHandler,
    )
    from finguard_orchestrator.application.commands.pay import (
        ConfirmPaymentCommand,
        ConfirmPaymentHandler,
        PaymentAnswer,
        PrepareChosenPaymentCommand,
        PrepareChosenPaymentHandler,
        PrepareOwnTransferCommand,
        PrepareOwnTransferHandler,
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
    from finguard_orchestrator.application.queries.browse_account import (
        BreakdownResult,
        GetBreakdownHandler,
        GetBreakdownQuery,
        GetMovementsHandler,
        GetMovementsQuery,
        MovementsResult,
    )
    from finguard_orchestrator.application.queries.financial_turn import (
        AskFinancialQuestionQuery,
        AskFinancialQuestionResult,
        SpokenPlan,
    )
    from finguard_orchestrator.application.queries.get_account_summary import (
        AccountSummaryResult,
        GetAccountSummaryHandler,
        GetAccountSummaryQuery,
    )
    from finguard_orchestrator.application.queries.get_llm_catalogue import (
        CatalogueResult,
        GetLlmCatalogueHandler,
        GetLlmCatalogueQuery,
        ModelRow,
        PolicyRow,
    )
    from finguard_orchestrator.application.queries.list_cards import (
        CardRow,
        ChargesResult,
        GetCardChargesHandler,
        GetCardChargesQuery,
        ListCardsHandler,
        ListCardsQuery,
        WalletResult,
    )
    from finguard_orchestrator.application.queries.list_contacts import (
        ListContactsHandler,
        ListContactsQuery,
        ListContactsResult,
    )
