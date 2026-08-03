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
    """What is being asked for.

    SUM: how much money. COUNT: how many movements. "How many times did I go to
    Mercadona" is COUNT; "how much did I spend at Mercadona" is SUM.
    """

    SUM = "SUM"
    COUNT = "COUNT"


class Direction(StrEnum):
    """Which side of the movement matters, from the account holder's point of view.

    EXPENSE: money leaving. INCOME: money arriving. ALL: both, and it is the wrong
    answer to "how much did I spend" — a question about spending that returns income
    too is a silent error, not a broader answer.
    """

    EXPENSE = "EXPENSE"
    INCOME = "INCOME"
    ALL = "ALL"


class GroupBy(StrEnum):
    """How the total is broken down. NONE means a single figure and is the default:
    a breakdown nobody asked for is as wrong as a missing one."""

    NONE = "NONE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    CATEGORY = "CATEGORY"
    MERCHANT = "MERCHANT"


class Period(StrEnum):
    """Named windows. The exact boundaries are in `relative_period.resolve`, which is
    the only thing that computes them — this list is what may be *named*.

    These four pairs are what a language model actually gets wrong, so the difference
    is spelled out rather than left to the name:

    - THIS_MONTH ends **today**, not at the end of the month. It is a partial month.
    - LAST_MONTH is the whole previous calendar month, and is **not** the same as
      LAST_N_DAYS with n=30: on the 15th of July they share only half their days.
    - LAST_N_DAYS **includes today** — "the last 7 days" is today plus the 6 before.
    - THIS_WEEK starts on Monday and ends today; LAST_WEEK is a full Monday-to-Sunday.

    An expression that names no period at all ("lately", "usually") is not a period:
    it is a question that needs clarifying before any of these can be chosen.
    """

    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_YEAR = "THIS_YEAR"
    LAST_N_DAYS = "LAST_N_DAYS"
    LAST_N_MONTHS = "LAST_N_MONTHS"
