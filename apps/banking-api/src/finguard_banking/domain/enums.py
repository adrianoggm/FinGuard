"""Closed sets of the banking domain.

Every value that used to travel as a string literal lives here. The ledger ones
matter most: `DEBIT` appeared 28 times across migrations, the generator, the SQL
scripts and the tests, so adding a state meant grepping instead of type checking.

The migrations keep their own literals on purpose: a migration is a historical
record and must not change meaning when an enum does.
"""

from enum import StrEnum


class EntryType(StrEnum):
    """Which side of the ledger an entry sits on."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class TransactionStatus(StrEnum):
    BOOKED = "BOOKED"
    PENDING = "PENDING"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"


class TransactionType(StrEnum):
    ACCOUNT_OPENING = "ACCOUNT_OPENING"
    SALARY = "SALARY"
    DIRECT_DEBIT = "DIRECT_DEBIT"
    CARD_PAYMENT = "CARD_PAYMENT"
    BIZUM = "BIZUM"
    TRANSFER = "TRANSFER"


class Metric(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"


class Direction(StrEnum):
    """Which side of the movement matters, from the account holder's point of view."""

    EXPENSE = "EXPENSE"
    INCOME = "INCOME"
    ALL = "ALL"


class GroupBy(StrEnum):
    NONE = "NONE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    CATEGORY = "CATEGORY"
    MERCHANT = "MERCHANT"


class Period(StrEnum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_YEAR = "THIS_YEAR"
    LAST_N_DAYS = "LAST_N_DAYS"
    LAST_N_MONTHS = "LAST_N_MONTHS"
