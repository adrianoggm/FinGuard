"""The model's answers about an agent, read back whole against the schemas `agent_prompt` sends.

Same contract as `planning/visual_answer.py`: strict mode and `extra="forbid"`, so a wrong JSON
type, an unknown enum value, a missing key or an extra key is a *structure* failure -- the adapter
repairs once and then gives up. Nothing here coerces, defaults or drops what the model wrote. An
extra key is not tolerated even when harmless-looking: `{"intent": "PAYMENT"}` beside the slots is
exactly the field the schema exists to refuse.

Not checked here, on purpose: whether any value is *true*. That is the verifier's job
(`domain/services/agent_reply_verifier.py`), which rejects with a named rule instead of a parse
failure, so a record can tell a malformed answer from an invented one.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finguard_orchestrator.domain.constants import MAX_AGENT_SLOTS
from finguard_orchestrator.domain.models.expert_agents import (
    AvailabilityDraft,
    BookingDraft,
    SlotDraft,
)

# What a skill can be classified as: the domain's `Capability` catalogue, plus NONE. A test holds
# the two together, so the catalogue cannot grow without this growing with it.
Classified = Literal["AVAILABILITY", "BOOKING", "NONE"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SkillAnswer(_Strict):
    skill_id: str = Field(alias="skillId")
    capability: Classified


class ClassificationAnswer(_Strict):
    skills: list[SkillAnswer]


class SlotAnswer(_Strict):
    activity: str
    start: str
    end: str | None
    free_places: int | None = Field(alias="freePlaces")
    slot_ref: str | None = Field(alias="slotRef")


class AvailabilityAnswer(_Strict):
    slots: list[SlotAnswer] = Field(max_length=MAX_AGENT_SLOTS)
    unsupported_request: bool = Field(alias="unsupportedRequest")


class BookingAnswer(_Strict):
    booked: bool
    booking_code: str | None = Field(alias="bookingCode")
    start: str | None
    activity: str | None
    slot_ref: str | None = Field(alias="slotRef")


def parse_classification(text: str) -> tuple[tuple[str, Classified], ...] | None:
    """(skill id, capability) as the model wrote them, in order, or `None` for a structure
    failure. Unknown ids and repeats are the classifier's to judge, not the parser's."""
    try:
        answer = ClassificationAnswer.model_validate_json(text)
    except ValidationError:
        return None
    return tuple((skill.skill_id, skill.capability) for skill in answer.skills)


def parse_availability(text: str) -> AvailabilityDraft | None:
    try:
        answer = AvailabilityAnswer.model_validate_json(text)
    except ValidationError:
        return None
    return AvailabilityDraft(
        tuple(
            SlotDraft(slot.activity, slot.start, slot.end, slot.free_places, slot.slot_ref)
            for slot in answer.slots
        ),
        unsupported_request=answer.unsupported_request,
    )


def parse_booking(text: str) -> BookingDraft | None:
    try:
        answer = BookingAnswer.model_validate_json(text)
    except ValidationError:
        return None
    return BookingDraft(
        booked=answer.booked,
        booking_code=answer.booking_code,
        start=answer.start,
        activity=answer.activity,
        slot_ref=answer.slot_ref,
    )
