"""The plan the assistant produces and sends to banking.

Validating here is not distrust of banking — banking is the authority and validates
again. It is that a plan rejected locally is an INVALID_DOMAIN outcome we can count and
show in the evaluation, whereas the same plan sent blind comes back as a 422 that has
already cost a round trip and reads like a bug in the caller.

The invariants mirror the contract deliberately and no further: no calendar arithmetic
lives here. What `LAST_MONTH` covers is banking's business, and two implementations of
that would be one too many.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORIES,
    MAX_CATEGORY_LENGTH,
    MAX_GROUP_ROWS,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_PERIOD_DAYS,
    MAX_PERIOD_MONTHS,
    MAX_SELECTED_ACCOUNTS,
    MAX_SELECTED_TRANSACTIONS,
)
from finguard_orchestrator.domain.enums import (
    PERIODS_NEEDING_COUNT,
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.errors import InvalidValueError


@dataclass(frozen=True)
class RelativePeriod:
    """A named window. `n` belongs to the counted periods and to no other."""

    period: Period
    n: int | None = None

    def __post_init__(self) -> None:
        needs_count = self.period in PERIODS_NEEDING_COUNT
        if needs_count and self.n is None:
            raise InvalidValueError(f"{self.period} requires n")
        if not needs_count and self.n is not None:
            raise InvalidValueError(f"{self.period} does not accept n")
        if self.n is None:
            return
        if self.n < 1:
            raise InvalidValueError("n must be at least 1")
        ceiling = MAX_PERIOD_MONTHS if self.period is Period.LAST_N_MONTHS else MAX_PERIOD_DAYS
        if self.n > ceiling:
            raise InvalidValueError(f"{self.period} allows at most {ceiling}")


@dataclass(frozen=True)
class AbsoluteRange:
    """Both ends included. For what a period cannot name — "during January"."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise InvalidValueError(f"range ends before it starts: {self.start}..{self.end}")


@dataclass(frozen=True)
class FinancialPlan:
    """What the sentence turned into.

    Exactly one of `period` and `date_range`: a plan carrying both is not
    over-specified, it is two different questions, and which one wins would depend on
    the reader.
    """

    direction: Direction
    period: RelativePeriod | None = None
    date_range: AbsoluteRange | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    # Negative filters are first-class. Treating "sin alquiler" as the inclusive
    # HOUSING_RENT filter returns exactly the opposite figure, so absence cannot encode it.
    excluded_categories: tuple[str, ...] = field(default_factory=tuple)
    # The shops the question is about. Free text, unlike a category — the first field here
    # whose values the schema cannot enumerate, because the ledger's merchant names are data.
    merchants: tuple[str, ...] = field(default_factory=tuple)
    # Case-insensitive fragments of a merchant name. Kept separate from ``merchants`` because
    # "Mercadona" means one exact ledger entity while "un nombre que contenga bet" deliberately
    # describes a family such as Bet365 or Betfair. Conflating both silently changes every exact
    # merchant query into fuzzy matching.
    merchant_terms: tuple[str, ...] = field(default_factory=tuple)
    excluded_merchants: tuple[str, ...] = field(default_factory=tuple)
    counterparties: tuple[str, ...] = field(default_factory=tuple)
    excluded_counterparties: tuple[str, ...] = field(default_factory=tuple)
    counterparty_types: tuple[CounterpartyType, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM
    # Present only for rankings of aggregate groups, never individual movement rankings.
    group_limit: int | None = None
    # Money the holder moved between their own accounts. Excluded by default, which is what makes
    # "¿cuánto gasté?" mean money that left rather than money that moved (ADR-009 §2). Asked for
    # explicitly by a list, where hiding a 200 € transfer means the movements do not reconcile with
    # the balance and the person looking for it cannot find it.
    include_internal: bool = False
    # Narrow to transfers between the holder's own accounts. `include_internal` alone only widens
    # an ordinary query and therefore cannot safely answer "only my transfers".
    internal_only: bool = False
    # Every account the holder has rather than the one the credential names. It cannot widen past
    # them: banking drops its own account filter and leaves the view's, which is the scope rule.
    across_accounts: bool = False
    # Narrow to one of the holder's accounts. It is a filter and never what banking declares: the
    # credential's account is what opens the scope, and declaring a different one would open that
    # account holder's instead — a leak shaped exactly like a filter.
    only_account_id: UUID | None = None
    # A selected subset of the holder's accounts. Additive beside `only_account_id` so
    # older clients keep their one-account contract while chat can express "corriente y
    # hucha, pero no el fondo" without widening the query to every account.
    only_account_ids: tuple[UUID, ...] = field(default_factory=tuple)
    # Exact movements a conversational pronoun refers to ("esos cinco"). Banking still applies
    # holder isolation, so identifiers can narrow a query but never widen its scope.
    transaction_ids: tuple[UUID, ...] = field(default_factory=tuple)
    transaction_types: tuple[TransactionType, ...] = field(default_factory=tuple)
    explain: bool = False

    def __post_init__(self) -> None:
        if (self.period is None) == (self.date_range is None):
            raise InvalidValueError("a plan needs exactly one of period and dateRange")
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
        if self.group_limit is not None:
            if self.group_by in {GroupBy.NONE, GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
                raise InvalidValueError("groupLimit requires a non-temporal grouping")
            if not 1 <= self.group_limit <= MAX_GROUP_ROWS:
                raise InvalidValueError(f"groupLimit must be between 1 and {MAX_GROUP_ROWS}")
        check_filters(
            self.categories,
            self.merchants,
            self.excluded_categories,
            self.excluded_merchants,
            self.counterparties,
            self.excluded_counterparties,
            self.merchant_terms,
        )
        if len(set(self.counterparty_types)) != len(self.counterparty_types):
            raise InvalidValueError("counterpartyTypes cannot contain duplicates")


@dataclass(frozen=True)
class FinancialPlanDraft:
    """A plan the model had already built when it stopped to ask a question.

    That reading used to be thrown away. The planner produced a complete one, noticed the
    window was missing, and returned the question as a bare string — so the direction, the
    categories, the metric and the grouping it got right went with it, and the reply "este
    año" arrived as a two-word sentence with no history. That is why answering the
    clarification asked for the period again.

    Not a `FinancialPlan`, because a plan needs exactly one window and this has none. The
    invariant is right; the draft is the honest way to hold something that does not satisfy
    it yet, rather than relaxing the plan so half-built ones can pretend to be finished.

    **The window is the only slot that can be absent**, which is why this says
    `needs_a_window` and not `missing_slots`. Every other field in the schema has a value or
    a sentinel, so the model always names one. If a second slot ever becomes optional this
    becomes a set and the caller has to ask which — worth doing then, not now.
    """

    direction: Direction
    period: RelativePeriod | None = None
    date_range: AbsoluteRange | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    excluded_categories: tuple[str, ...] = field(default_factory=tuple)
    merchants: tuple[str, ...] = field(default_factory=tuple)
    merchant_terms: tuple[str, ...] = field(default_factory=tuple)
    excluded_merchants: tuple[str, ...] = field(default_factory=tuple)
    counterparties: tuple[str, ...] = field(default_factory=tuple)
    excluded_counterparties: tuple[str, ...] = field(default_factory=tuple)
    counterparty_types: tuple[CounterpartyType, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM
    group_limit: int | None = None
    include_internal: bool = False
    internal_only: bool = False
    across_accounts: bool = False
    only_account_id: UUID | None = None
    only_account_ids: tuple[UUID, ...] = field(default_factory=tuple)
    transaction_ids: tuple[UUID, ...] = field(default_factory=tuple)
    transaction_types: tuple[TransactionType, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Neither window is allowed — that is what a draft is for — but not both, for the same
        # reason the plan refuses it: two windows are two questions, and which one wins would
        # depend on the reader.
        if self.period is not None and self.date_range is not None:
            raise InvalidValueError("a draft cannot carry two windows")
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
        if self.metric is Metric.CUMULATIVE and self.group_by not in {
            GroupBy.DAY,
            GroupBy.WEEK,
            GroupBy.MONTH,
        }:
            raise InvalidValueError("CUMULATIVE requires DAY, WEEK or MONTH grouping")
        if self.group_limit is not None:
            if self.group_by in {GroupBy.NONE, GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
                raise InvalidValueError("groupLimit requires a non-temporal grouping")
            if not 1 <= self.group_limit <= MAX_GROUP_ROWS:
                raise InvalidValueError(f"groupLimit must be between 1 and {MAX_GROUP_ROWS}")
        # The window is the one invariant a draft relaxes. Everything else is checked exactly as
        # the plan checks it, because a draft crosses the wire and comes back: leaving the
        # filters until `with_window` meant a bad category was accepted at the edge and only
        # noticed a layer deeper, where the honest answer is no longer a 422.
        check_filters(
            self.categories,
            self.merchants,
            self.excluded_categories,
            self.excluded_merchants,
            self.counterparties,
            self.excluded_counterparties,
            self.merchant_terms,
        )
        if len(set(self.counterparty_types)) != len(self.counterparty_types):
            raise InvalidValueError("counterpartyTypes cannot contain duplicates")

    @property
    def needs_a_window(self) -> bool:
        """Whether supplying a window is all this needs to become a query.

        False is not "complete": the model can ask about something else — "¿te refieres a
        ocio?" — with the window already read. Then there is nothing to fill in from a closed
        vocabulary and the reply goes to the planner like any other sentence.
        """
        return self.period is None and self.date_range is None

    def with_window(self, window: "RelativePeriod | AbsoluteRange") -> FinancialPlan:
        """The plan this draft becomes once the window is known.

        Returns a `FinancialPlan`, so every invariant runs on the way out. The draft itself
        checks nothing about its categories: it is a half-built thing, and the place worth
        checking is the one where it becomes a query someone gets a figure from.
        """
        return FinancialPlan(
            direction=self.direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=self.categories,
            excluded_categories=self.excluded_categories,
            merchants=self.merchants,
            merchant_terms=self.merchant_terms,
            excluded_merchants=self.excluded_merchants,
            counterparties=self.counterparties,
            excluded_counterparties=self.excluded_counterparties,
            counterparty_types=self.counterparty_types,
            group_by=self.group_by,
            metric=self.metric,
            group_limit=self.group_limit,
            include_internal=self.include_internal,
            internal_only=self.internal_only,
            across_accounts=self.across_accounts,
            only_account_id=self.only_account_id,
            only_account_ids=self.only_account_ids,
            transaction_ids=self.transaction_ids,
            transaction_types=self.transaction_types,
        )

    def into_plan(self) -> FinancialPlan | None:
        """The validated plan represented by this draft, or None while its window is missing."""
        if self.needs_a_window:
            return None
        return FinancialPlan(
            direction=self.direction,
            period=self.period,
            date_range=self.date_range,
            categories=self.categories,
            excluded_categories=self.excluded_categories,
            merchants=self.merchants,
            merchant_terms=self.merchant_terms,
            excluded_merchants=self.excluded_merchants,
            counterparties=self.counterparties,
            excluded_counterparties=self.excluded_counterparties,
            counterparty_types=self.counterparty_types,
            group_by=self.group_by,
            metric=self.metric,
            group_limit=self.group_limit,
            include_internal=self.include_internal,
            internal_only=self.internal_only,
            across_accounts=self.across_accounts,
            only_account_id=self.only_account_id,
            only_account_ids=self.only_account_ids,
            transaction_ids=self.transaction_ids,
            transaction_types=self.transaction_types,
        )

    @classmethod
    def of(cls, plan: FinancialPlan) -> "FinancialPlanDraft":
        """A finished plan, read back as a draft. One shape on the wire instead of two."""
        return cls(
            direction=plan.direction,
            period=plan.period,
            date_range=plan.date_range,
            categories=plan.categories,
            excluded_categories=plan.excluded_categories,
            merchants=plan.merchants,
            merchant_terms=plan.merchant_terms,
            excluded_merchants=plan.excluded_merchants,
            counterparties=plan.counterparties,
            excluded_counterparties=plan.excluded_counterparties,
            counterparty_types=plan.counterparty_types,
            group_by=plan.group_by,
            metric=plan.metric,
            group_limit=plan.group_limit,
            include_internal=plan.include_internal,
            internal_only=plan.internal_only,
            across_accounts=plan.across_accounts,
            only_account_id=plan.only_account_id,
            only_account_ids=plan.only_account_ids,
            transaction_ids=plan.transaction_ids,
            transaction_types=plan.transaction_types,
        )


def check_filters(
    categories: tuple[str, ...],
    merchants: tuple[str, ...],
    excluded_categories: tuple[str, ...] = (),
    excluded_merchants: tuple[str, ...] = (),
    counterparties: tuple[str, ...] = (),
    excluded_counterparties: tuple[str, ...] = (),
    merchant_terms: tuple[str, ...] = (),
) -> None:
    """What a plan, a draft and a ranking check identically.

    Shared rather than copied: a type that checked less would be a way to smuggle a value past the
    plan's invariants and reach them a layer later, where the honest answer is no longer a 422.
    """
    if len(categories) > MAX_CATEGORIES or len(excluded_categories) > MAX_CATEGORIES:
        raise InvalidValueError(f"at most {MAX_CATEGORIES} categories per filter")
    for category in (*categories, *excluded_categories):
        _check_category(category)
    if set(categories) & set(excluded_categories):
        raise InvalidValueError("a category cannot be both included and excluded")
    if len(merchants) > MAX_MERCHANTS or len(excluded_merchants) > MAX_MERCHANTS:
        raise InvalidValueError(f"at most {MAX_MERCHANTS} merchants per filter")
    for merchant in (*merchants, *excluded_merchants):
        # Shape only: a name is free text and its content is banking's to match. What is
        # checked is that it is a name at all, and bounded — an empty one filters nothing
        # and would silently widen the query, which is the failure this file exists for.
        if not merchant.strip():
            raise InvalidValueError("a merchant name cannot be empty")
        if len(merchant) > MAX_MERCHANT_LENGTH:
            raise InvalidValueError(f"merchant name too long: {merchant[:20]!r}...")
    included_names = {merchant.strip().casefold() for merchant in merchants}
    excluded_names = {merchant.strip().casefold() for merchant in excluded_merchants}
    if included_names & excluded_names:
        raise InvalidValueError("a merchant cannot be both included and excluded")
    if len(merchant_terms) > MAX_MERCHANTS:
        raise InvalidValueError(f"at most {MAX_MERCHANTS} merchant terms per filter")
    for term in merchant_terms:
        if len(term.strip()) < 2:
            raise InvalidValueError("a merchant term must contain at least two characters")
        if len(term) > MAX_MERCHANT_LENGTH:
            raise InvalidValueError(f"merchant term too long: {term[:20]!r}...")
    if len(counterparties) > MAX_MERCHANTS or len(excluded_counterparties) > MAX_MERCHANTS:
        raise InvalidValueError(f"at most {MAX_MERCHANTS} counterparties per filter")
    for counterparty in (*counterparties, *excluded_counterparties):
        if not counterparty.strip():
            raise InvalidValueError("a counterparty name cannot be empty")
        if len(counterparty) > MAX_MERCHANT_LENGTH:
            raise InvalidValueError("counterparty name is too long")
    included_counterparties = {value.strip().casefold() for value in counterparties}
    excluded_counterparty_names = {value.strip().casefold() for value in excluded_counterparties}
    if included_counterparties & excluded_counterparty_names:
        raise InvalidValueError("a counterparty cannot be both included and excluded")


def _check_category(value: str) -> None:
    """A category arrives from a language model, so its shape is checked rather than
    assumed — even though the compiler on the other side passes it as a parameter and
    never as concatenated SQL. Two locks on the same door."""
    if not value:
        raise InvalidValueError("a category cannot be empty")
    if len(value) > MAX_CATEGORY_LENGTH:
        raise InvalidValueError(f"category too long: {value!r}")
    if not value.isascii() or not value.replace("_", "").isalnum():
        raise InvalidValueError(f"invalid category: {value!r}")
