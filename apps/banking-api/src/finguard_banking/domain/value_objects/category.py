"""A spending category, validated.

It used to be an inline loop inside `FinancialQuery`, which meant the rule lived where
it was consumed rather than where it belongs: a category is a value with invariants, and
the plan should be able to assume every one it holds is already valid.

The pattern matters because these arrive from the LLM. `t.metadata ->> 'category'` is
compared against them, and although the compiler passes them as parameters — never as
concatenated SQL — a closed shape is the second lock on that door.
"""

from dataclasses import dataclass

from finguard_banking.domain.constants import MAX_CATEGORY_LENGTH
from finguard_banking.domain.errors import InvalidValueError


@dataclass(frozen=True)
class Category:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvalidValueError("a category cannot be empty")
        if len(self.value) > MAX_CATEGORY_LENGTH:
            raise InvalidValueError(f"category too long: {self.value!r}")
        if not self.value.isascii() or not self.value.replace("_", "").isalnum():
            raise InvalidValueError(f"invalid category: {self.value!r}")

    def __str__(self) -> str:
        return self.value
