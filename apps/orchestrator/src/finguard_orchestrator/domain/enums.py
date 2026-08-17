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
    """Outside what a financial query can express: a time-normalised average, a
    comparison of two periods or a forecast. Answering with the nearest expressible plan is a
    wrong answer delivered confidently."""

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
    THIS_QUARTER = "THIS_QUARTER"
    LAST_QUARTER = "LAST_QUARTER"
    THIS_SEMESTER = "THIS_SEMESTER"
    LAST_SEMESTER = "LAST_SEMESTER"
    THIS_FORTNIGHT = "THIS_FORTNIGHT"
    LAST_FORTNIGHT = "LAST_FORTNIGHT"
    THIS_WEEKEND = "THIS_WEEKEND"
    LAST_WEEKEND = "LAST_WEEKEND"
    THIS_YEAR = "THIS_YEAR"
    LAST_N_DAYS = "LAST_N_DAYS"
    LAST_N_MONTHS = "LAST_N_MONTHS"


class Action(StrEnum):
    """What kind of question a sentence turned into.

    Two, and the split is the one the review argued for: an aggregate totals or counts movements,
    a ranking picks one out by size. They are not two metrics — they answer with different shapes,
    a figure with groups against a single row with a date and a shop — so widening `Metric` would
    have produced a result whose half is meaningless whichever way it went.

    More will arrive: a balance is already answered without a plan, and a category ranking is the
    obvious next one. When the third appears this is where it goes.
    """

    AGGREGATE = "AGGREGATE"
    RANK_MOVEMENTS = "RANK_MOVEMENTS"


class Direction(StrEnum):
    EXPENSE = "EXPENSE"
    INCOME = "INCOME"
    ALL = "ALL"


class TransactionType(StrEnum):
    ACCOUNT_OPENING = "ACCOUNT_OPENING"
    SALARY = "SALARY"
    DIRECT_DEBIT = "DIRECT_DEBIT"
    CARD_PAYMENT = "CARD_PAYMENT"
    BIZUM = "BIZUM"
    TRANSFER = "TRANSFER"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"


class CounterpartyType(StrEnum):
    MERCHANT = "MERCHANT"
    PERSON = "PERSON"
    EMPLOYER = "EMPLOYER"
    BILLER = "BILLER"
    ACCOUNT = "ACCOUNT"
    BANK = "BANK"
    UNKNOWN = "UNKNOWN"


class ReceiptCategory(StrEnum):
    RENT = "RENT"
    SUBSCRIPTION = "SUBSCRIPTION"
    UTILITIES = "UTILITIES"
    INSURANCE = "INSURANCE"
    LOAN = "LOAN"
    TAX = "TAX"
    OTHER = "OTHER"


class GroupBy(StrEnum):
    NONE = "NONE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    CATEGORY = "CATEGORY"
    MERCHANT = "MERCHANT"
    COUNTERPARTY = "COUNTERPARTY"
    ACCOUNT = "ACCOUNT"


class Rail(StrEnum):
    """How the money leaves.

    Mirrored from the banking contract, like `Period` and `GroupBy`: no shared package, so this
    side states the values it will emit and banking stays the authority. A value that drifts comes
    back as a refusal rather than as a payment down the wrong rail.

    Which one a sentence means is read deterministically — "bizum" and "transferencia" are a closed
    vocabulary of two, and a model choosing between them would be a model deciding how money
    leaves an account.
    """

    BIZUM = "BIZUM"
    TRANSFER = "TRANSFER"
    # Between the holder's own accounts. Not a setting of TRANSFER: the destination is an account
    # id rather than an IBAN, and what banking books is a type the read views keep out of spending.
    # Mirrored from banking's contract like the rest of this file — no shared package.
    INTERNAL = "INTERNAL"


class Metric(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"
    # Arithmetic mean of the matching movements. Banking still returns the exact sum and
    # count; the deterministic answer writer derives the displayed mean from those two
    # integers, so no rounded model-generated number enters the response.
    AVERAGE = "AVERAGE"
    # Mean of the totals of every calendar bucket in the requested window. The
    # temporal GroupBy says whether the divisor is days, weeks or months. This is
    # deliberately distinct from AVERAGE, whose divisor is movements.
    PERIOD_AVERAGE = "PERIOD_AVERAGE"
    # Running total across ordered calendar buckets. Banking supplies exact bucket sums and the
    # orchestrator accumulates them, preserving zero-activity periods as the previous total.
    CUMULATIVE = "CUMULATIVE"
    # Cadence of matching movements. Banking returns the complete bounded evidence and the
    # orchestrator derives intervals from ledger dates; keeping this in the plan preserves the
    # question through follow-ups such as "¿y el año pasado?".
    FREQUENCY = "FREQUENCY"
    # Discovery of periodic charges. Unlike FREQUENCY, which describes one already-grounded
    # stream, RECURRENCE finds stable patterns among several counterparties and retains that
    # intent through conversational follow-ups.
    RECURRENCE = "RECURRENCE"


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


class Spoken(StrEnum):
    """Answers the assistant gives without asking the ledger anything.

    Separate from PlanningOutcome, which is about reading a sentence into a plan. Nothing here was
    planned: the answer was already in this repository or in the previous plan, so counting these as
    planning outcomes would put two different things in the metric that measures one.
    """

    # About the assistant: what it is, what it can do.
    IDENTITY = "IDENTITY"
    # «Gracias», «hola», «adiós». Not about their account and not about the assistant either, which
    # is why it is its own outcome: without one, 98 of the corpus's turns reached the planner.
    COURTESY = "COURTESY"
    # About the previous answer: which query ran, and what a category means.
    EXPLANATION = "EXPLANATION"
    # How much money there is. Read from the ledger, but through the fixed question the home
    # screen asks rather than through a plan, so no model is involved in choosing it.
    BALANCE = "BALANCE"
    # Who this account can pay. A list, from banking's own restricted role — the question
    # "¿a quién puedo hacer un bizum?" has an answer that needs no interpreting.
    CONTACTS = "CONTACTS"
    # Which product categories a financial query can filter by. Repository data, not account data.
    CATEGORIES = "CATEGORIES"
    # The wallet, and what each card has taken. Read from banking's own card views, with no plan in
    # the path: a plan has no card dimension, so the planner could only ever approximate one.
    CARDS = "CARDS"
    # A deterministic conversation control such as abandoning a saved retry. No model or ledger
    # produced it, so it belongs with the other spoken outcomes rather than planning failures.
    GUIDANCE = "GUIDANCE"
    # A transfer between the holder own accounts, understood but not staged. The chat reads the
    # sentence; preparing it is a payments:prepare scope on a different route, and a read path that
    # quietly gained the ability to stage a payment would erase the seam between asking and paying.
    TRANSFER_READY = "TRANSFER_READY"
