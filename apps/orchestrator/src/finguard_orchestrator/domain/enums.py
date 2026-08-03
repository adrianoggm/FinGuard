from enum import StrEnum


class FallbackPolicy(StrEnum):
    """What to do when the chosen model cannot answer.

    `FAIL` surfaces the error: the caller learns that the model is down. `NEXT` walks
    the rest of the catalogue in order. Which one applies is a configuration decision,
    not a code one: a demo wants NEXT, a measurement run wants FAIL so a silent
    fallback cannot skew the numbers.
    """

    FAIL = "FAIL"
    NEXT = "NEXT"


class PlanningOutcome(StrEnum):
    """How an attempt to turn a sentence into a plan ended.

    A single boolean would collapse cases that need different answers and carry very
    different risk. Three of these announce themselves — something is missing, malformed
    or rejected, and the caller finds out. Two do not: a model will answer an ambiguous
    or inexpressible question with a confident, schema-valid plan, and nothing
    downstream would notice. Those two are why this is an enum and not a flag.
    """

    PLANNED = "PLANNED"
    """A usable plan. Says nothing about whether it answers the question that was
    asked — only the evaluation dataset measures that."""

    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    """The sentence does not determine a plan. "Lately" names no window, and guessing
    one returns a real figure for a question nobody asked — worse than asking."""

    UNSUPPORTED = "UNSUPPORTED"
    """Outside what a financial query can express: an average, a comparison of two
    periods, a forecast. Answering with the nearest expressible plan is a wrong answer
    delivered confidently."""

    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    """The answer was not JSON matching the schema. Under constrained decoding this
    should be unreachable, which is exactly why it is worth counting: if it fires, the
    constraint is not doing what we believe it is."""

    INVALID_DOMAIN = "INVALID_DOMAIN"
    """Well formed but rejected by the rules — an unknown category, a backwards range,
    a count over the ceiling."""

    PROVIDER_ERROR = "PROVIDER_ERROR"
    """No answer at all: daemon down, model missing, timed out."""


class Period(StrEnum):
    """Mirrors the banking contract. The meaning and the boundaries belong to that
    service; this is only the list of names the assistant may emit.

    Declared here rather than imported because the two services share no code by
    design. The duplication is the price of that decision, and it is not silent: a
    value that drifts is rejected by banking with a 422, which arrives as
    INVALID_DOMAIN and shows up in the evaluation.
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


class Direction(StrEnum):
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


class Metric(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"


# Periods that are a count of something, and therefore need `n`. Kept beside the enum
# so a new counted period cannot be added without seeing this.
PERIODS_NEEDING_COUNT = frozenset({Period.LAST_N_DAYS, Period.LAST_N_MONTHS})


class PaymentOutcome(StrEnum):
    """Where a turn of the payment conversation ended.

    Not the same axis as PlanningOutcome, which is about reading a sentence. This is the state of
    the conversation, and the reading's outcomes map onto it: a malformed or ungrounded reading both
    become NOT_UNDERSTOOD, because the difference matters to us and not to the user.
    """

    # A preview is on the table and nothing has moved. The only state from which a yes pays.
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    # Something is missing, or two things matched. A turn in the conversation, not an error.
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    # Not a payment this assistant makes.
    UNSUPPORTED = "UNSUPPORTED"
    # The model's answer was malformed, or did not correspond to the sentence.
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"
    # The bank said no: the balance, or an intent that is no longer live.
    REFUSED = "REFUSED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    BANKING_UNAVAILABLE = "BANKING_UNAVAILABLE"
