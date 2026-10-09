"""Ground payment slots against utterances and the holder's account-scoped catalogues.

This module only grounds values already selected by typed operations. It does not pick or authorize
payment flows and never calls banking; those remain in the graph and existing command handlers.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import PrepareBizum, PrepareTransfer
from finguard_orchestrator.domain.engine.state import OwnTransferDraftPending
from finguard_orchestrator.domain.enums import Rail
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import account_words, payment_words
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft


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


@dataclass(frozen=True)
class ContactPaymentDraftResolution:
    draft: PaymentDraft
    candidate_contact_ids: tuple[UUID, ...] = ()
    rejected: bool = False


def resolve_contact_payment_draft(
    sentence: str,
    operation: PrepareBizum | PrepareTransfer,
    contacts: tuple[Contact, ...],
    *,
    previous: PaymentDraft | None = None,
    candidate_contact_ids: tuple[UUID, ...] = (),
) -> ContactPaymentDraftResolution:
    """Merge only slots explicit in this utterance into a saved draft.

    The typed operation selects Bizum or bank transfer. A transfer rail must also be present in
    this utterance, except when continuing an already grounded transfer draft. Closed readers bind
    the amount and contact to the user's words and account directory. The model cannot resolve two
    matching contacts or supply a value that was not spoken or already in the draft.
    """
    prior = previous or PaymentDraft()
    planned_rail = Rail.TRANSFER if isinstance(operation, PrepareTransfer) else Rail.BIZUM
    names_rail = payment_words.names_a_rail(sentence)
    # A reply that fills a pending slot is silent about the rail. The canonical draft, not an
    # imperfect re-interpretation of "20 euros", decides whether the payment remains a transfer.
    rail = previous.rail if previous is not None and not names_rail else planned_rail
    if names_rail and payment_words.rail_in(sentence) is not rail:
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    if (
        rail is Rail.TRANSFER
        and not payment_words.names_transfer(sentence)
        and (previous is None or previous.rail is not Rail.TRANSFER)
    ):
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    amount_minor = payment_words.amount_in(sentence)
    spoken_iban = payment_words.iban_in(sentence)
    if payment_words.mentions_iban(sentence) and spoken_iban is None:
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    recipient_iban = spoken_iban or prior.recipient_iban
    if payment_words.mentions_amount(sentence) and amount_minor is None:
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    if amount_minor is not None and operation.amount_minor not in (None, amount_minor):
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    if amount_minor is None:
        amount_minor = prior.amount_minor
        if operation.amount_minor is not None and operation.amount_minor != amount_minor:
            return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)

    spoken = payment_words.contacts_in(sentence, contacts)
    if candidate_contact_ids:
        spoken = tuple(c for c in spoken if c.contact_id in candidate_contact_ids)
    modeled = (
        payment_words.contacts_in(operation.recipient, contacts)
        if operation.recipient is not None
        else ()
    )
    contact: Contact | None = None
    typed_recipient: str | None = (
        payment_words.transfer_recipient_for_iban(sentence)
        if rail is Rail.TRANSFER and recipient_iban is not None
        else None
    )
    if (
        typed_recipient is None
        and previous is not None
        and previous.recipient_iban is not None
        and previous.recipient_name is not None
    ):
        typed_recipient = previous.recipient_name
    candidates: tuple[Contact, ...] = ()
    if len(spoken) > 1:
        candidates = spoken
    elif len(spoken) == 1:
        contact = spoken[0]
        if operation.recipient is not None and (
            len(modeled) != 1 or modeled[0].contact_id != contact.contact_id
        ):
            return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    elif operation.recipient is not None and typed_recipient is None:
        if len(modeled) != 1 or prior.recipient_contact_id != modeled[0].contact_id:
            return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
        contact = modeled[0]
    elif prior.recipient_contact_id is not None:
        contact = next((c for c in contacts if c.contact_id == prior.recipient_contact_id), None)
        if contact is None:
            return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
    if typed_recipient is not None:
        typed_matches = payment_words.contacts_in(typed_recipient, contacts)
        if len(typed_matches) > 1:
            return ContactPaymentDraftResolution(
                prior, tuple(contact.contact_id for contact in typed_matches)
            )
        if typed_matches:
            if contact is not None and contact.contact_id != typed_matches[0].contact_id:
                return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)
            contact = typed_matches[0]
        elif contact is not None:
            # A known contact elsewhere in the sentence must not override the explicit recipient
            # label that was written beside the typed destination.
            return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)

    if (
        operation.recipient is not None
        and contact is None
        and not candidates
        and typed_recipient is None
    ):
        # An unknown name cannot be passed to banking for a fuzzy guess.
        return ContactPaymentDraftResolution(prior, candidate_contact_ids, rejected=True)

    draft = PaymentDraft(
        recipient_name=contact.display_name if contact else typed_recipient,
        recipient_contact_id=contact.contact_id if contact else None,
        recipient_iban=recipient_iban,
        amount_minor=amount_minor,
        currency=prior.currency,
        concept=prior.concept,
        rail=rail,
    )
    return ContactPaymentDraftResolution(
        draft,
        tuple(candidate.contact_id for candidate in candidates),
    )
