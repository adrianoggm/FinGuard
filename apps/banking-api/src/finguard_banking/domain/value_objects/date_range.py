"""A closed date range, both ends included.

It lives in its own module so that `relative_period` no longer has to import it from
`financial_query`, which coupled two domain modules for no reason other than where the
class happened to sit.
"""

from dataclasses import dataclass
from datetime import date

from finguard_banking.domain.errors import InvalidValueError


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidValueError(
                f"the range starts after it ends: {self.start.isoformat()} > {self.end.isoformat()}"
            )

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"
