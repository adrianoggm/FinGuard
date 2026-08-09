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
    """Where a transaction is in its life, and the database enforces the transitions.

    PENDING is where every transaction starts and the only state that accepts entries.
    BOOKED and CANCELLED are terminal: a correction is a compensating transaction, which
    leaves the mistake and the fix both in the record. Migration 0004 has the triggers, and
    the read views only ever show BOOKED.
    """

    PENDING = "PENDING"
    BOOKED = "BOOKED"
    CANCELLED = "CANCELLED"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"


class AccountKind(StrEnum):
    """What an account is for, which decides what a figure about it means.

    The three of migration 0014, whose CHECK constraint is the other copy of this list. Written
    out here as well because the constraint cannot be imported: what it protects is the table, and
    what this protects is every read path that would otherwise pass the kind around as a string.

    CURRENT is money to spend, SAVINGS is money set aside, ASSET is a holding whose worth is not
    its cost — and that last one is why this is not cosmetic. Adding an ASSET balance into a total
    labelled "what you have" reports a purchase price as if it were a valuation.
    """

    CURRENT = "CURRENT"
    SAVINGS = "SAVINGS"
    ASSET = "ASSET"


class TransactionType(StrEnum):
    ACCOUNT_OPENING = "ACCOUNT_OPENING"
    SALARY = "SALARY"
    DIRECT_DEBIT = "DIRECT_DEBIT"
    CARD_PAYMENT = "CARD_PAYMENT"
    BIZUM = "BIZUM"
    TRANSFER = "TRANSFER"
    # Money between two accounts the same person owns. Its own type rather than a TRANSFER
    # with a flag beside it: what makes it different is not how it moved but that it never
    # left the household, and every question about spending has to know that.
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"


class CounterpartyType(StrEnum):
    """What role the other side plays, derived from the ledger transaction type."""

    MERCHANT = "MERCHANT"
    PERSON = "PERSON"
    EMPLOYER = "EMPLOYER"
    BILLER = "BILLER"
    ACCOUNT = "ACCOUNT"
    BANK = "BANK"
    UNKNOWN = "UNKNOWN"


class ReceiptCategory(StrEnum):
    """A direct debit's purpose when the ledger category supports that claim."""

    RENT = "RENT"
    SUBSCRIPTION = "SUBSCRIPTION"
    UTILITIES = "UTILITIES"
    INSURANCE = "INSURANCE"
    LOAN = "LOAN"
    TAX = "TAX"
    OTHER = "OTHER"


class Metric(StrEnum):
    """What is being asked for.

    SUM: how much money. COUNT: how many movements. "How many times did I go to
    Mercadona" is COUNT; "how much did I spend at Mercadona" is SUM.
    """

    SUM = "SUM"
    COUNT = "COUNT"
    # Average amount per matching movement. Aggregate rows deliberately keep carrying
    # both their sum and their count; callers derive the mean without losing either fact.
    AVERAGE = "AVERAGE"
    # The aggregate still returns exact sums per temporal bucket. The caller uses
    # the resolved date range to include empty buckets in the divisor.
    PERIOD_AVERAGE = "PERIOD_AVERAGE"
    # Exact per-period sums used by the assistant to produce a running total.
    CUMULATIVE = "CUMULATIVE"
    # The aggregate and evidence shape is unchanged; the caller derives cadence from the complete
    # bounded movement dates. Keeping it in the contract preserves the requested semantics.
    FREQUENCY = "FREQUENCY"
    RECURRENCE = "RECURRENCE"


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
    COUNTERPARTY = "COUNTERPARTY"
    ACCOUNT = "ACCOUNT"


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


class Rail(StrEnum):
    """How the money moves.

    Three, and they are not three settings of one operation: a Bizum names a person by phone and
    the bank finds the account, a transfer names the account and there may be no person behind it
    at all, and an internal transfer names another account of the same holder. They differ in what
    has to be on the preview, in what the intent has to remember, and in what can go wrong — which
    is why the destination check in the schema is written per rail rather than as a set of nullable
    columns and a convention.
    """

    BIZUM = "BIZUM"
    TRANSFER = "TRANSFER"
    # Between the holder's own accounts. Not a setting of TRANSFER: the destination is an account
    # id rather than an IBAN, no clearing account is involved because the money does not leave the
    # household, and what it books is `INTERNAL_TRANSFER` — the type the read views already keep
    # out of spending. Calling it a transfer would make "¿cuánto gasté?" wrong by every euro
    # somebody moved into their savings.
    INTERNAL = "INTERNAL"
