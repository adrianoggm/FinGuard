"""«La de las 19:00 en el Norte»: a class named against the sealed offers, never guessed (§5.2).

Resolved the way `visual_reference` resolves a bar -- by time, by the gym's name, by an ordinal --
and when two offers still fit, the holder is asked which (Review Focus 2). The offers here are four,
ordered by start as the search leaves them: Sur 18:30 spinning, Norte 19:00 spinning, Sur 19:00
yoga, Norte 20:00 spinning.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import ActivityOffers
from finguard_orchestrator.domain.models.expert_agents import ActivitySlot
from finguard_orchestrator.domain.services.slot_reference import (
    SlotAmbiguous,
    SlotChosen,
    SlotNotOffered,
    resolve_slot,
)

ZONE = ZoneInfo("Europe/Madrid")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


SUR_1830 = ActivitySlot("gym-sur", "Spinning", at(1, 18, 30), None, 6, None)
NORTE_1900 = ActivitySlot("gym-norte", "Spinning", at(1, 19), at(1, 19, 45), 4, "N-1001-1900")
SUR_YOGA_1900 = ActivitySlot("gym-sur", "Yoga", at(1, 19), None, 8, None)
NORTE_2000 = ActivitySlot("gym-norte", "Spinning", at(1, 20), at(1, 20, 45), 0, "N-1001-2000")
NAMES = (("gym-sur", "Gimnasio Sur"), ("gym-norte", "Gimnasio Norte"))
OFFERS = ActivityOffers((SUR_1830, NORTE_1900, SUR_YOGA_1900, NORTE_2000), NAMES, ())


def test_a_time_chooses_its_slot() -> None:
    assert resolve_slot("apúntame a la de las 18:30", OFFERS) == SlotChosen(SUR_1830)


def test_one_time_at_two_gyms_asks_which() -> None:  # Review Focus 2
    assert resolve_slot("apúntame a la de las 19:00", OFFERS) == SlotAmbiguous(
        (NORTE_1900, SUR_YOGA_1900)
    )
    assert resolve_slot("apúntame a la de las 19:00 en el Norte", OFFERS) == SlotChosen(NORTE_1900)
    assert resolve_slot("la de las 19:00 de yoga", OFFERS) == SlotChosen(SUR_YOGA_1900)


def test_an_ordinal_and_a_gym() -> None:
    assert resolve_slot("resérvame la primera", OFFERS) == SlotChosen(SUR_1830)
    assert resolve_slot("la última del Norte", OFFERS) == SlotChosen(NORTE_2000)


def test_a_time_not_offered() -> None:
    assert resolve_slot("apúntame a la de las 21:00", OFFERS) == SlotNotOffered()


def test_what_the_offers_do_not_hold_is_not_offered() -> None:
    """A gym the offers do not name, an activity none of them is, an ordinal past the end: each is
    a class the holder was never shown, so it is refused rather than matched to the nearest one."""
    assert (
        resolve_slot("apúntame a la de las 19:00 en el Gimnasio Oeste", OFFERS) == SlotNotOffered()
    )
    assert resolve_slot("apúntame a pilates", OFFERS) == SlotNotOffered()
    assert resolve_slot("resérvame la tercera del Norte", OFFERS) == SlotNotOffered()


def test_a_sentence_that_narrows_nothing_asks_among_every_offer() -> None:
    assert resolve_slot("apúntame", OFFERS) == SlotAmbiguous(OFFERS.slots)
    lonely = ActivityOffers((SUR_1830,), NAMES[:1], ())
    assert resolve_slot("apúntame", lonely) == SlotChosen(SUR_1830)


def test_the_day_tells_apart_one_time_on_two_days() -> None:
    """Offers over a weekend hold the same time twice; the weekday named is the one meant."""
    saturday = ActivitySlot("gym-norte", "Natación", at(3, 18, 30), None, 2, None)
    sunday = ActivitySlot("gym-norte", "Natación", at(4, 18, 30), None, 5, None)
    weekend = ActivityOffers((saturday, sunday), NAMES[1:], ())

    assert resolve_slot("apúntame a la de las 18:30", weekend) == SlotAmbiguous((saturday, sunday))
    assert resolve_slot("apúntame a la de las 18:30 del domingo", weekend) == SlotChosen(sunday)


def test_an_ordinal_needs_its_article() -> None:
    """«a primera hora» is a time of day, not the first class offered."""
    assert resolve_slot("apúntame a primera hora", OFFERS) == SlotAmbiguous(OFFERS.slots)


def test_an_ambiguity_holds_at_least_two_candidates() -> None:
    with pytest.raises(InvalidValueError):
        SlotAmbiguous((SUR_1830,))
