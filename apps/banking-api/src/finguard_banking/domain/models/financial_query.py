"""The validated query plan.

The intermediate representation of vision.md §9.2: the LLM proposes this, never SQL.
Everything arriving here is untrusted input, so the invariants are deliberately strict.

No pydantic and no aliases: `from` and `to` are the shape of the §13.2 HTTP contract,
not of the business, and §4.4 forbids the domain from depending on protocols. The DTOs
in infrastructure/dto/ own the wire format; a mapper stands between.
"""

from dataclasses import dataclass, field
from uuid import UUID

from finguard_banking.domain.constants import (
    DEFAULT_SUPPORTING_MOVEMENTS,
    MAX_CATEGORIES,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange


@dataclass(frozen=True)
class FinancialQuery:
    account_id: UUID
    date_range: DateRange
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    # Every category validated itself on construction: the plan only has to bound how
    # many of them there are.
    categories: tuple[Category, ...] = field(default_factory=tuple)
    # The shops the question is about. The first field in this contract whose values are not
    # a closed set — a category is an enum and this is whatever somebody typed — so it is
    # bounded and checked here, and it reaches SQL only as a bound parameter.
    merchants: tuple[str, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    # How many movements to return as evidence for the figure.
    supporting_limit: int = DEFAULT_SUPPORTING_MOVEMENTS
    # The trace (compiled SQL and parameters) is opt-in: it never travels by default.
    explain: bool = False
    # The zone the caller's day was measured in, when the caller said. None means the
    # deployment's own, which is the right fallback and the wrong default to rely on: a
    # date without a zone does not say what "hoy" means, and two services each using their
    # own setting can bin the same purchase on different days.
    reporting_timezone: str | None = None
    # Whether money the holder moved between their own accounts counts.
    #
    # False by default, and the default is the correctness. Moving 200 € to the piggy bank is
    # not spending 200 €, so "¿cuánto gasté?" means money that left the household — and once
    # there is a second account, counting it makes the summary report twice the movement that
    # happened, an expense here and an income there.
    #
    # True is a different question with a real answer: "¿cuánto he movido a la hucha este año?"
    # is worth asking and is not a question about spending. The balance is unaffected either
    # way — the money did leave *this* account.
    include_internal: bool = False
    # Every account this session can see, rather than the one named by `account_id`. The view
    # decides which those are, so there is no list to pass and nothing here can widen past the
    # holder — dropping the compiler's filter leaves the scope rule in exactly one place.
    #
    # Off by default, and that default is what keeps every existing question meaning what it meant.
    across_accounts: bool = False
    # Narrow to one account *within* what the session can already see. Never what gets declared:
    # `declare()` resolves the holder from the account it is given, so declaring an account opens
    # ITS holder scope — passing a stranger's here would open theirs, which is a leak and not a
    # filter. `account_id` is the credential's and is what is declared; this only narrows.
    #
    # An account that is not the holder is therefore not refused, it is absent: the balance lookup
    # finds no currency for it and the read answers 404, the same as an account that does not exist.
    only_account_id: UUID | None = None

    @property
    def scoped_to(self) -> UUID:
        """Which account the figures are about. Not which one gets declared."""
        return self.only_account_id or self.account_id

    def __post_init__(self) -> None:
        if not 0 <= self.supporting_limit <= MAX_SUPPORTING_MOVEMENTS:
            raise InvalidValueError(
                f"supportingLimit must be between 0 and {MAX_SUPPORTING_MOVEMENTS}"
            )
        if len(self.categories) > MAX_CATEGORIES:
            raise InvalidValueError(f"at most {MAX_CATEGORIES} categories")
        if len(self.merchants) > MAX_MERCHANTS:
            raise InvalidValueError(f"at most {MAX_MERCHANTS} merchants")
        for merchant in self.merchants:
            if not merchant.strip():
                raise InvalidValueError("a merchant name cannot be empty")
            if len(merchant) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError(f"merchant name too long: {merchant[:20]!r}...")

    def category_values(self) -> list[str]:
        """The plain strings, for the one place that needs them: a SQL parameter."""
        return [category.value for category in self.categories]
