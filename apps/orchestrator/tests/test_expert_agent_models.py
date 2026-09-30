"""The orchestrator's own view of expert agents: every invariant refuses at construction."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from finguard_orchestrator.domain.constants import MAX_AGENT_SLOTS, MAX_BOOKING_CODE
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentRejection,
    AvailabilityDraft,
    BookingReceipt,
    Capability,
    ExpertAgent,
    ExpertAgentsError,
    SlotDraft,
)

ZONE = ZoneInfo("Europe/Madrid")
AT = datetime(2026, 10, 1, 19, 0, tzinfo=ZONE)


def _slot(**overrides: object) -> ActivitySlot:
    fields: dict[str, object] = {
        "agent_id": "gym-norte",
        "activity": "Spinning",
        "start": AT,
        "end": AT + timedelta(minutes=45),
        "free_places": 4,
        "slot_ref": "N-1001-1900",
    }
    return ActivitySlot(**{**fields, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "Gym-Norte", "gym_norte", "gym norte", "x" * 65, "gym/../x"])
def test_an_agent_id_is_lower_case_letters_digits_and_dashes(bad: str) -> None:
    with pytest.raises(InvalidValueError):
        ExpertAgent(bad, "Gimnasio", True, None, "sha256:0", (), ())
    with pytest.raises(InvalidValueError):
        _slot(agent_id=bad)
    with pytest.raises(InvalidValueError):
        ActivityQuery("spinning", AT, AT + timedelta(hours=1), agent_ids=(bad,))


def test_an_end_at_or_before_the_start_is_refused() -> None:
    with pytest.raises(InvalidValueError):
        _slot(end=AT)
    with pytest.raises(InvalidValueError):
        _slot(end=AT - timedelta(minutes=1))
    with pytest.raises(InvalidValueError):
        ActivityQuery("spinning", AT, AT)


def test_negative_places_are_refused() -> None:
    with pytest.raises(InvalidValueError):
        _slot(free_places=-1)


def test_a_naive_moment_is_refused() -> None:
    naive = datetime(2026, 10, 1, 19, 0)
    with pytest.raises(InvalidValueError):
        _slot(start=naive, end=None)
    with pytest.raises(InvalidValueError):
        ActivityQuery("spinning", naive, naive + timedelta(hours=1))


def test_more_slots_than_the_ceiling_are_refused() -> None:
    one = SlotDraft("Spinning", "19:00", None, None, None)
    AvailabilityDraft((one,) * MAX_AGENT_SLOTS)
    with pytest.raises(InvalidValueError):
        AvailabilityDraft((one,) * (MAX_AGENT_SLOTS + 1))


def test_a_valid_slot_round_trips_its_fields() -> None:
    slot = _slot()
    held = (slot.agent_id, slot.activity, slot.start, slot.end, slot.free_places, slot.slot_ref)
    assert held == ("gym-norte", "Spinning", AT, AT + timedelta(minutes=45), 4, "N-1001-1900")
    unknown = _slot(end=None, free_places=None, slot_ref=None)
    assert (unknown.end, unknown.free_places, unknown.slot_ref) == (None, None, None)


def test_a_receipt_names_the_agent_that_was_asked_and_a_bounded_code() -> None:
    slot = _slot()
    assert BookingReceipt("gym-norte", slot, "NT-4821").booking_code == "NT-4821"
    with pytest.raises(InvalidValueError):
        BookingReceipt("gym-sur", slot, "NT-4821")
    with pytest.raises(InvalidValueError):
        BookingReceipt("gym-norte", slot, "")
    with pytest.raises(InvalidValueError):
        BookingReceipt("gym-norte", slot, "X" * (MAX_BOOKING_CODE + 1))


def test_a_rejection_names_one_closed_rule() -> None:
    assert AgentRejection("grounding") == AgentRejection("grounding")
    with pytest.raises(InvalidValueError):
        AgentRejection("because")  # type: ignore[arg-type]


def test_the_capability_catalogue_is_closed_and_an_error_carries_its_code() -> None:
    assert [c.value for c in Capability] == ["AVAILABILITY", "BOOKING"]
    error = ExpertAgentsError("agent_unusable", "credential_missing")
    assert (error.code, error.reason) == ("agent_unusable", "credential_missing")
    assert ExpertAgentsError("timeout").reason is None


@pytest.mark.parametrize("bad", ["N 1001", "-N1001", "N-1001\n", "Ñ-1001", "a" * 65])
def test_a_reference_and_a_code_are_one_ascii_token(bad: str) -> None:
    with pytest.raises(InvalidValueError):
        _slot(slot_ref=bad)
    with pytest.raises(InvalidValueError):
        BookingReceipt("gym-norte", _slot(), bad)
    assert _slot(slot_ref="N-1001.19:00/a").slot_ref == "N-1001.19:00/a"


def test_a_band_opens_before_it_closes() -> None:
    from datetime import time

    query = ActivityQuery("spinning", AT, AT + timedelta(days=2), band=(time(15), time(21)))
    assert query.band == (time(15), time(21))
    with pytest.raises(InvalidValueError):
        ActivityQuery("spinning", AT, AT + timedelta(days=2), band=(time(21), time(15)))


@pytest.mark.parametrize("host", ["", " ", "gym norte:8000"])
def test_a_link_host_is_a_host(host: str) -> None:
    """Review finding 10a: an empty host would accept a link that opens anywhere."""
    with pytest.raises(InvalidValueError):
        ExpertAgent("gym-norte", "Gimnasio", True, None, "sha256:0", ("gym-norte:8000", host), ())
