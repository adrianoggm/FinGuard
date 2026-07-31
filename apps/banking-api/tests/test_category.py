"""The category validates itself: the plan should not have to."""

import pytest

from finguard_banking.domain.constants import MAX_CATEGORY_LENGTH
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.category import Category


def test_a_category_is_letters_digits_and_underscores() -> None:
    assert Category("TRANSPORT_FUEL").value == "TRANSPORT_FUEL"
    assert str(Category("GROCERIES")) == "GROCERIES"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "TRANSPORT_FUEL'; DROP TABLE ledger_entries; --",
        "with space",
        "acentuación",
        "semi;colon",
        "*",
    ],
)
def test_anything_else_is_rejected(value: str) -> None:
    # These arrive from the LLM. The compiler passes them as parameters, never
    # concatenated, so this is the second lock rather than the only one.
    with pytest.raises(InvalidValueError):
        Category(value)


def test_a_category_has_a_maximum_length() -> None:
    assert Category("A" * MAX_CATEGORY_LENGTH)

    with pytest.raises(InvalidValueError):
        Category("A" * (MAX_CATEGORY_LENGTH + 1))


def test_two_categories_with_the_same_value_are_the_same_thing() -> None:
    assert Category("GROCERIES") == Category("GROCERIES")
    assert Category("GROCERIES") != Category("RESTAURANTS")
