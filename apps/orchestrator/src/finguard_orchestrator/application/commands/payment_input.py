"""Read values a holder explicitly supplied while completing an own-account transfer.

This module only grounds values already selected by a typed transfer operation. It does not pick
the payment flow, authorize it, resolve permissions, or call banking; those remain in the graph and
the existing payment command handlers.
"""

from finguard_orchestrator.domain.engine.operations import PrepareBizum
from finguard_orchestrator.domain.engine.state import OwnTransferDraftPending
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import account_words, payment_words


def destination_candidates(
    sentence: str,
    accounts: tuple[Holding, ...],
    prior: OwnTransferDraftPending | None,
) -> tuple[Holding, ...]:
    names = tuple(holding.display_name for holding in accounts)
    if prior is not None and prior.candidate_account_ids:
        selected = account_words.chosen_among(sentence, names)
        return tuple(
            holding
            for holding in accounts
            if holding.account_id in prior.candidate_account_ids
            and holding.display_name in selected
        )
    custom = account_words.custom_destination_among(sentence, names)
    if not custom and prior is not None and prior.to_account_id is None:
        custom = account_words.chosen_among(sentence, names)
    if custom:
        return tuple(holding for holding in accounts if holding.display_name in custom)
    kinds = account_words.destination_in(sentence)
    return tuple(holding for holding in accounts if holding.kind in kinds)


def amount(sentence: str) -> int | None:
    """Return the closed-grammar amount, if there is one."""
    return payment_words.amount_in(sentence)


def mentions_amount(sentence: str) -> bool:
    """Distinguish an omitted amount from one the closed grammar must reject."""
    return payment_words.mentions_amount(sentence)


def grounded_bizum(
    sentence: str, operation: PrepareBizum, contacts: tuple[Contact, ...]
) -> tuple[Contact, int] | None:
    """Require the operation's recipient and amount to match the message and account directory."""
    amount_minor = payment_words.amount_in(sentence)
    mentioned = payment_words.contacts_in(sentence, contacts)
    selected_by_model = payment_words.contacts_in(operation.recipient, contacts)
    if (
        amount_minor != operation.amount_minor
        or len(mentioned) != 1
        or len(selected_by_model) != 1
        or mentioned[0].contact_id != selected_by_model[0].contact_id
    ):
        return None
    return mentioned[0], amount_minor
