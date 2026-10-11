"""A payment of nothing is refused by the value object itself, before any guard sees it."""

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest


@pytest.mark.parametrize("amount_minor", [0, -2_000])
def test_a_payment_of_nothing_is_not_a_payment(amount_minor: int) -> None:
    """Refused when the value object is built, not by a guard: a negative payment is a withdrawal
    wearing a payment's clothes, and no policy should be able to switch that off."""
    with pytest.raises(InvalidValueError):
        PaymentRequest(recipient_name="Marta", amount_minor=amount_minor, currency="EUR")


def test_a_recipient_of_nothing_is_refused_the_same_way() -> None:
    with pytest.raises(InvalidValueError):
        PaymentRequest(recipient_name="   ", amount_minor=2_000, currency="EUR")
