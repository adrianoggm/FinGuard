"""Stable movement semantics derived from facts already present in the ledger."""

from finguard_banking.domain.enums import CounterpartyType, ReceiptCategory, TransactionType

_RECEIPT_CATEGORIES: dict[str, ReceiptCategory] = {
    "HOUSING_RENT": ReceiptCategory.RENT,
    "SUBSCRIPTIONS": ReceiptCategory.SUBSCRIPTION,
    "UTILITIES": ReceiptCategory.UTILITIES,
    "HOUSING_UTILITIES": ReceiptCategory.UTILITIES,
    "INSURANCE": ReceiptCategory.INSURANCE,
    "LOAN": ReceiptCategory.LOAN,
    "LOAN_PAYMENT": ReceiptCategory.LOAN,
    "TAX": ReceiptCategory.TAX,
    "TAXES": ReceiptCategory.TAX,
}


def counterparty_type_of(transaction_type: TransactionType) -> CounterpartyType:
    """Classify the other side without interpreting a free-text description."""
    return {
        TransactionType.CARD_PAYMENT: CounterpartyType.MERCHANT,
        TransactionType.BIZUM: CounterpartyType.PERSON,
        TransactionType.SALARY: CounterpartyType.EMPLOYER,
        TransactionType.DIRECT_DEBIT: CounterpartyType.BILLER,
        TransactionType.TRANSFER: CounterpartyType.ACCOUNT,
        TransactionType.INTERNAL_TRANSFER: CounterpartyType.ACCOUNT,
        TransactionType.ACCOUNT_OPENING: CounterpartyType.BANK,
    }.get(transaction_type, CounterpartyType.UNKNOWN)


def receipt_category_of(
    transaction_type: TransactionType, category: str | None
) -> ReceiptCategory | None:
    """Classify actual direct debits; card subscriptions are not bank receipts."""
    if transaction_type is not TransactionType.DIRECT_DEBIT:
        return None
    return _RECEIPT_CATEGORIES.get(category or "", ReceiptCategory.OTHER)


def counterparty_name_of(merchant_name: str | None, description: str) -> str | None:
    """Prefer the structured name and retain description only as a transparent fallback."""
    candidate = merchant_name or description
    stripped = candidate.strip()
    return stripped or None
