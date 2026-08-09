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
    MAX_SELECTED_ACCOUNTS,
    MAX_SELECTED_TRANSACTIONS,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    TransactionType,
)
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
    excluded_categories: tuple[Category, ...] = field(default_factory=tuple)
    # The shops the question is about. The first field in this contract whose values are not
    # a closed set — a category is an enum and this is whatever somebody typed — so it is
    # bounded and checked here, and it reaches SQL only as a bound parameter.
    merchants: tuple[str, ...] = field(default_factory=tuple)
    excluded_merchants: tuple[str, ...] = field(default_factory=tuple)
    # The other side of any ledger movement. Unlike ``merchants``, this also represents people,
    # employers, billers and accounts, and can therefore be narrowed by its ledger-native type.
    counterparties: tuple[str, ...] = field(default_factory=tuple)
    excluded_counterparties: tuple[str, ...] = field(default_factory=tuple)
    counterparty_types: tuple[CounterpartyType, ...] = field(default_factory=tuple)
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
    # Unlike `include_internal`, this narrows the result to movements between the holder's own
    # accounts. It powers explicit transfer history and contributions to an asset account.
    internal_only: bool = False
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
    # A bounded-by-holder subset. The compiler binds these identifiers as one array and
    # the read view still applies holder isolation underneath, so this can narrow but
    # never widen the session's scope.
    only_account_ids: tuple[UUID, ...] = field(default_factory=tuple)
    # Exact rows from a previously displayed selection. The holder-scoped view remains the
    # authority, so foreign or invented identifiers simply match nothing.
    transaction_ids: tuple[UUID, ...] = field(default_factory=tuple)
    # Ledger-native types, used by deterministic questions such as "recibos domiciliados".
    transaction_types: tuple[TransactionType, ...] = field(default_factory=tuple)

    @property
    def scoped_to(self) -> UUID:
        """Which account the figures are about. Not which one gets declared."""
        return self.only_account_id or next(iter(self.only_account_ids), self.account_id)

    def __post_init__(self) -> None:
        if self.internal_only and not self.include_internal:
            raise InvalidValueError("internalOnly requires includeInternal")
        if self.only_account_id is not None and self.only_account_ids:
            raise InvalidValueError("use onlyAccountId or onlyAccountIds, not both")
        if len(set(self.only_account_ids)) != len(self.only_account_ids):
            raise InvalidValueError("onlyAccountIds cannot contain duplicates")
        if len(self.only_account_ids) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError(f"at most {MAX_SELECTED_ACCOUNTS} selected accounts")
        if len(set(self.transaction_ids)) != len(self.transaction_ids):
            raise InvalidValueError("transactionIds cannot contain duplicates")
        if len(self.transaction_ids) > MAX_SELECTED_TRANSACTIONS:
            raise InvalidValueError(f"at most {MAX_SELECTED_TRANSACTIONS} selected transactions")
        if len(set(self.transaction_types)) != len(self.transaction_types):
            raise InvalidValueError("transactionTypes cannot contain duplicates")
        if self.metric is Metric.PERIOD_AVERAGE and self.group_by not in {
            GroupBy.DAY,
            GroupBy.WEEK,
            GroupBy.MONTH,
        }:
            raise InvalidValueError("PERIOD_AVERAGE requires DAY, WEEK or MONTH grouping")
        if self.metric is Metric.CUMULATIVE and self.group_by not in {
            GroupBy.DAY,
            GroupBy.WEEK,
            GroupBy.MONTH,
        }:
            raise InvalidValueError("CUMULATIVE requires DAY, WEEK or MONTH grouping")
        if not 0 <= self.supporting_limit <= MAX_SUPPORTING_MOVEMENTS:
            raise InvalidValueError(
                f"supportingLimit must be between 0 and {MAX_SUPPORTING_MOVEMENTS}"
            )
        if len(self.categories) > MAX_CATEGORIES or len(self.excluded_categories) > MAX_CATEGORIES:
            raise InvalidValueError(f"at most {MAX_CATEGORIES} categories per filter")
        included_categories = {category.value for category in self.categories}
        excluded_categories = {category.value for category in self.excluded_categories}
        if included_categories & excluded_categories:
            raise InvalidValueError("a category cannot be both included and excluded")
        if len(self.merchants) > MAX_MERCHANTS or len(self.excluded_merchants) > MAX_MERCHANTS:
            raise InvalidValueError(f"at most {MAX_MERCHANTS} merchants per filter")
        for merchant in (*self.merchants, *self.excluded_merchants):
            if not merchant.strip():
                raise InvalidValueError("a merchant name cannot be empty")
            if len(merchant) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError(f"merchant name too long: {merchant[:20]!r}...")
        included_merchants = {merchant.strip().casefold() for merchant in self.merchants}
        excluded_merchants = {merchant.strip().casefold() for merchant in self.excluded_merchants}
        if included_merchants & excluded_merchants:
            raise InvalidValueError("a merchant cannot be both included and excluded")
        if (
            len(self.counterparties) > MAX_MERCHANTS
            or len(self.excluded_counterparties) > MAX_MERCHANTS
        ):
            raise InvalidValueError(f"at most {MAX_MERCHANTS} counterparties per filter")
        for counterparty in (*self.counterparties, *self.excluded_counterparties):
            if not counterparty.strip():
                raise InvalidValueError("a counterparty name cannot be empty")
            if len(counterparty) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError("counterparty name is too long")
        included_counterparties = {value.strip().casefold() for value in self.counterparties}
        excluded_counterparties = {
            value.strip().casefold() for value in self.excluded_counterparties
        }
        if included_counterparties & excluded_counterparties:
            raise InvalidValueError("a counterparty cannot be both included and excluded")
        if len(set(self.counterparty_types)) != len(self.counterparty_types):
            raise InvalidValueError("counterpartyTypes cannot contain duplicates")

    def category_values(self) -> list[str]:
        """The plain strings, for the one place that needs them: a SQL parameter."""
        return [category.value for category in self.categories]

    def excluded_category_values(self) -> list[str]:
        return [category.value for category in self.excluded_categories]
