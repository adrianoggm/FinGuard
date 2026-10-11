"""A turn is arbitrated once, before any reader can claim it.

Measured on 2026-10-07: «olvidalo» after an answer was read as the name of a shop. These are the
rules that survive the arbitration itself: the shape of an `Arbitration`, and the stop-list that
keeps a reset word from ever being read as a shop.
"""

import pytest

from finguard_orchestrator.domain.services import merchant_words
from finguard_orchestrator.domain.services.turn_arbiter import Arbitration, TurnType


@pytest.mark.parametrize("sentence", ["olvidalo", "olvídalo", "no importa"])
def test_the_reset_words_are_never_a_shop_either(sentence: str) -> None:
    """The arbitration answers a whole-turn reset first; the stop-list makes sure the bare-shop
    guess is never the reader that sees one — «olvidalo» ran as `merchants: ['olvidalo']`."""
    assert merchant_words.never_a_merchant(sentence) is True


def test_an_arbitration_fills_no_slot_unless_it_says_so() -> None:
    """`fills` belongs to a reply that answers the open question; the kinds decided here fill
    nothing."""
    assert Arbitration(TurnType.RESET).fills == frozenset()
