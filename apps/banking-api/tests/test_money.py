"""Money exists to make one class of bug impossible: mixing currencies in memory."""

import pytest

from finguard_banking.domain.errors import CurrencyMismatchError, InvalidValueError
from finguard_banking.domain.value_objects.money import Money

EUR = "EUR"
USD = "USD"


def test_amounts_in_the_same_currency_add_up() -> None:
    assert Money(2_500, EUR) + Money(1_000, EUR) == Money(3_500, EUR)
    assert Money(2_500, EUR) - Money(1_000, EUR) == Money(1_500, EUR)


def test_two_currencies_cannot_be_combined() -> None:
    # The database catches this per transaction, but only at COMMIT. The arithmetic
    # happens here.
    with pytest.raises(CurrencyMismatchError) as raised:
        Money(2_500, EUR) + Money(2_500, USD)

    assert raised.value.left == EUR
    assert raised.value.right == USD


def test_two_currencies_cannot_be_compared() -> None:
    with pytest.raises(CurrencyMismatchError):
        assert Money(1, EUR) < Money(2, USD)


def test_a_negative_amount_is_valid() -> None:
    # A debited account goes negative; that is a balance, not an error.
    assert (-Money(2_500, EUR)).amount_minor == -2_500
    assert Money(-2_500, EUR).is_negative
    assert abs(Money(-2_500, EUR)) == Money(2_500, EUR)


@pytest.mark.parametrize("currency", ["EU", "EURO", "eur", "E1R", ""])
def test_a_currency_that_is_not_three_upper_case_letters_is_rejected(currency: str) -> None:
    with pytest.raises(InvalidValueError):
        Money(100, currency)


@pytest.mark.parametrize("amount", [10.5, "100", True])
def test_only_whole_minor_units_are_money(amount: object) -> None:
    # Floats have no business near money (vision.md §8.4), and a bool is not an amount.
    with pytest.raises(InvalidValueError):
        Money(amount, EUR)  # type: ignore[arg-type]


def test_it_reads_like_money() -> None:
    assert str(Money(146_035, EUR)) == "1460.35 EUR"
    assert str(Money(-2_549, EUR)) == "-25.49 EUR"
    assert str(Money(5, EUR)) == "0.05 EUR"


def test_zero_still_carries_its_currency() -> None:
    assert Money.zero(EUR) == Money(0, EUR)
    assert Money.zero(EUR) != Money.zero(USD)
