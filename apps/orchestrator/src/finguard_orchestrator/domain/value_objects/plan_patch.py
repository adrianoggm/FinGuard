"""Immutable, typed changes to an already grounded financial plan.

Scope-preserving, with one exception that is argued at `divisible_by`: a breakdown asked of a plan
filtered to exactly one value of that breakdown's own dimension drops that one value, because the
breakdown it was asked for is one row otherwise.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)


class PatchField(StrEnum):
    WINDOW = "window"
    GROUP_BY = "groupBy"
    METRIC = "metric"
    EXCLUDED_CATEGORIES = "excludedCategories"
    EXCLUDED_MERCHANTS = "excludedMerchants"
    DIRECTION = "direction"
    COUNTERPARTIES = "counterparties"


# A breakdown by shop: `COUNTERPARTY` is what «por comercio» reads as (`explicit_facts`), and
# `MERCHANT` is the compatibility name for the same dimension.
_BY_SHOP = frozenset({GroupBy.COUNTERPARTY, GroupBy.MERCHANT})


def divisible_by[P: FinancialPlan | FinancialPlanDraft](plan: P, grouping: GroupBy) -> P:
    """`plan`, with the one value a breakdown by `grouping` would collapse on dropped (rulings
    R29, R36), or `plan` unchanged when that breakdown divides it already or must not be widened.

    «Mejor por categoría» over the groceries is one row — the groceries — and `plan_verifier`
    refuses a breakdown it cannot divide (`BREAKDOWN_ANSWERED_AS_ONE_GROUP`). The turn asked for
    the spending divided by category, so the one category goes: the window, the direction, the
    accounts, the exclusions and the row cut stay. The same for one shop and a breakdown by shop.

    Only where nothing else is unclear; otherwise the verifier's refusal stands:

    - money one way, EXPENSE or INCOME: over every movement, in and out, there is no saying which
      side the breakdown was meant to divide;
    - one value on the breakdown's *own* dimension: one category for a breakdown by category, one
      entry of `merchants` for a breakdown by shop — never `counterparties`, a person, and never
      `merchant_terms`, a fragment that already matches several shops;
    - nothing finer filtered beside it: groceries at Mercadona by category is Mercadona's
      groceries, and dropping the groceries would leave a shop filter that empties every other
      category. Nor a person or a fragment of a shop's name, for either breakdown.

    For a plan and for a draft, so that a question still being completed and a patched answer are
    read by one rule; the handler decides which of them may be widened.
    """
    if plan.direction not in {Direction.EXPENSE, Direction.INCOME}:
        return plan
    if plan.merchant_terms or plan.counterparties:
        return plan
    if grouping is GroupBy.CATEGORY and len(plan.categories) == 1 and not plan.merchants:
        return replace(plan, categories=())
    if grouping in _BY_SHOP and len(plan.merchants) == 1:
        return replace(plan, merchants=())
    return plan


@dataclass(frozen=True)
class PlanPatch:
    """A non-empty patch produced from one follow-up turn, scope-preserving but for the one
    exception `divisible_by` argues: a breakdown that would be one row."""

    window: RelativePeriod | AbsoluteRange | None = None
    group_by: GroupBy | None = None
    metric: Metric | None = None
    excluded_categories: tuple[str, ...] = ()
    excluded_merchants: tuple[str, ...] = ()
    # Which way the money went, for a follow-up that turns the question round: «¿y ella a mí?»
    # after «¿cuánto le mandé a Marta?» is the same person, the same window, money coming in.
    direction: Direction | None = None
    # The one person a turn-round named, when the answer was about several (ruling R33): «¿y Marta
    # a mí?» over Marta and Javi is what Marta sent, and Javi's money is not in it.
    counterparties: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.fields:
            raise InvalidValueError("a plan patch cannot be empty")
        if len(set(self.excluded_categories)) != len(self.excluded_categories):
            raise InvalidValueError("excluded categories in a patch must be unique")
        folded = tuple(item.casefold() for item in self.excluded_merchants)
        if len(set(folded)) != len(folded):
            raise InvalidValueError(
                "excluded merchants in a patch must be case-insensitively unique"
            )

    @property
    def fields(self) -> frozenset[PatchField]:
        return frozenset(
            field
            for field, present in (
                (PatchField.WINDOW, self.window is not None),
                (PatchField.GROUP_BY, self.group_by is not None),
                (PatchField.METRIC, self.metric is not None),
                (PatchField.EXCLUDED_CATEGORIES, bool(self.excluded_categories)),
                (PatchField.EXCLUDED_MERCHANTS, bool(self.excluded_merchants)),
                (PatchField.DIRECTION, self.direction is not None),
                (PatchField.COUNTERPARTIES, bool(self.counterparties)),
            )
            if present
        )

    @property
    def is_window_patch(self) -> bool:
        """Whether this patch carries the atomic period/date-range replacement."""
        return self.window is not None

    def apply_to(self, plan: FinancialPlan) -> FinancialPlan:
        """Return a new plan while preserving every field the patch does not name."""
        changed = plan
        if self.window is not None:
            changed = replace(
                changed,
                period=self.window if isinstance(self.window, RelativePeriod) else None,
                date_range=self.window if isinstance(self.window, AbsoluteRange) else None,
            )
        if self.group_by is not None:
            changed = replace(
                changed,
                group_by=self.group_by,
                group_limit=(
                    None
                    if self.group_by in {GroupBy.NONE, GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
                    else changed.group_limit
                ),
            )
            changed = divisible_by(changed, self.group_by)
        if self.metric is not None:
            changed = replace(changed, metric=self.metric)
        if self.excluded_categories:
            # Applied even when it can remove nothing. "Sin contar el alquiler" over a plan
            # filtered to GROCERIES drops no movement — rent was never inside a supermarket
            # filter — and the instruction is still real, so the patch keeps it: the day the
            # category filter widens, it bites. What must not happen is the answer claiming a
            # filter that cannot have moved the figure, and that is
            # `answer_writer.inert_exclusions`, not this.
            excluded = tuple(
                dict.fromkeys((*changed.excluded_categories, *self.excluded_categories))
            )
            changed = replace(
                changed,
                categories=tuple(item for item in changed.categories if item not in excluded),
                excluded_categories=excluded,
            )
        if self.excluded_merchants:
            excluded_merchants: list[str] = []
            seen_merchants: set[str] = set()
            for merchant in (*changed.excluded_merchants, *self.excluded_merchants):
                folded = merchant.casefold()
                if folded not in seen_merchants:
                    seen_merchants.add(folded)
                    excluded_merchants.append(merchant)
            changed = replace(
                changed,
                merchants=tuple(
                    item for item in changed.merchants if item.casefold() not in seen_merchants
                ),
                excluded_merchants=tuple(excluded_merchants),
            )
        if self.direction is not None:
            # Only the direction: the person, the window and every filter are the question that
            # was asked, and the turn asked it the other way round.
            changed = replace(changed, direction=self.direction)
        if self.counterparties:
            changed = replace(changed, counterparties=self.counterparties)
        return changed
