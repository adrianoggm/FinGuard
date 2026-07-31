from datetime import date
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.domain.constants import (
    DEFAULT_SUPPORTING_MOVEMENTS,
    MAX_CATEGORIES,
    MAX_CATEGORY_LENGTH,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric


class DateRange(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    start: date = Field(alias="from")
    end: date = Field(alias="to")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("dateRange.from cannot be later than dateRange.to")
        return self


class FinancialQuery(BaseModel):
    """A validated query plan.

    This is the intermediate representation from vision.md §9.2: the LLM proposes
    this, never SQL. Everything arriving here is untrusted input, so the model is
    deliberately strict: closed field sets, patterns and limits.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid"
    )

    account_id: UUID
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = ()
    date_range: DateRange
    group_by: GroupBy = GroupBy.NONE
    # How many movements to return as evidence for the figure.
    supporting_limit: int = Field(
        default=DEFAULT_SUPPORTING_MOVEMENTS, ge=0, le=MAX_SUPPORTING_MOVEMENTS
    )
    # The trace (compiled SQL and parameters) is opt-in: it never travels by default.
    explain: bool = False

    @field_validator("categories")
    @classmethod
    def _valid_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_CATEGORIES:
            raise ValueError("too many categories")
        for category in value:
            if not category.isascii() or not category.replace("_", "").isalnum():
                raise ValueError(f"invalid category: {category!r}")
            if len(category) > MAX_CATEGORY_LENGTH:
                raise ValueError(f"category too long: {category!r}")
        return value
