"""An amount and its currency, together.

Before this, `amount_minor: int` and `currency: str` travelled as separate fields
through Balance, Movement and QueryGroup, so nothing in Python stopped adding euros to
dollars — the only thing preventing it was the ledger trigger, which does not run until
COMMIT. Phase 5 is all arithmetic (amount against balance, subtracting a hold, issuing
a receipt), which is why this lands before it.

Minor units, always integers: the database stores `amount_minor BIGINT` and floats have
no business anywhere near money (vision.md §8.4).
"""

from dataclasses import dataclass

from finguard_banking.domain.errors import CurrencyMismatchError, InvalidValueError

CURRENCY_LENGTH = 3


@dataclass(frozen=True, order=False)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise InvalidValueError("amount_minor must be an integer number of minor units")
        if len(self.currency) != CURRENCY_LENGTH or not self.currency.isalpha():
            raise InvalidValueError(f"currency must be 3 letters, got {self.currency!r}")
        if self.currency != self.currency.upper():
            raise InvalidValueError(f"currency must be upper case, got {self.currency!r}")

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(0, currency)

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount_minor, self.currency)

    def __abs__(self) -> "Money":
        return Money(abs(self.amount_minor), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount_minor <= other.amount_minor

    @property
    def is_negative(self) -> bool:
        return self.amount_minor < 0

    def __str__(self) -> str:
        units, minor = divmod(abs(self.amount_minor), 100)
        sign = "-" if self.amount_minor < 0 else ""
        return f"{sign}{units}.{minor:02d} {self.currency}"
