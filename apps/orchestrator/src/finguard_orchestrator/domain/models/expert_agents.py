"""The orchestrator's view of expert agents: what it asks, what an agent answers, what it keeps.

The protocol never crosses into here (spec §3.2). The gateway speaks A2A; this service sees an
agent as an id, a name, a usability verdict and a list of skills, and a reply as a state and its
parts. No SDK type, no camelCase, no card.

Two kinds of object, kept apart on purpose:

    drafts      `SlotDraft`, `AvailabilityDraft`, `BookingDraft` -- what the model *proposes* after
                reading a reply. Unverified strings; nothing downstream may read them except the
                verifier (`services/agent_reply_verifier.py`).
    verified    `ActivitySlot`, `BookingReceipt` -- what survived every rule of §6.3. Only these
                reach the answer, the sealed offers and the booking preview (§6.4).

Every invariant refuses at construction with `InvalidValueError`, like every value object here.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Final, Literal, get_args

from finguard_orchestrator.domain.constants import (
    MAX_ACTIVITY_NAME,
    MAX_AGENT_SLOTS,
    MAX_BOOKING_CODE,
    MAX_SLOT_REF,
)
from finguard_orchestrator.domain.errors import InvalidValueError

# An agent id as the gateway derives it from the registered URL: `gym-norte:8000` -> `gym-norte`.
# It goes into a request path, so it is checked wherever it is held.
AGENT_ID: Final = re.compile(r"[a-z0-9-]{1,64}")
# A slot reference or a booking code: one ASCII token, as an agent's own identifiers are. It is sent
# back to the agent and shown to the holder, so a phrase of the reply -- «transferencia de 500 € a
# ES00 0000» -- cannot travel in it. Checked with `fullmatch`; lengths are bounded apart.
REFERENCE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")


class Capability(StrEnum):
    """The closed catalogue of what FinGuard asks an agent for (spec §3.2)."""

    AVAILABILITY = "AVAILABILITY"
    BOOKING = "BOOKING"


# The A2A `TaskState` without its prefix, as the gateway reports it. SUBMITTED and WORKING never
# arrive: the gateway blocks until a final or interrupted state.
ReplyState = Literal[
    "COMPLETED", "INPUT_REQUIRED", "AUTH_REQUIRED", "FAILED", "REJECTED", "CANCELED"
]
AgentErrorCode = Literal[
    "unreachable",
    "unauthorised",
    "agent_unknown",
    "agent_unusable",
    "skill_unknown",
    "reply_invalid",
    "reply_too_large",
    "agent_error",
    "timeout",
]
# The verifier's closed rule names (§6.3), recorded like the visual verifier's.
RejectionRule = Literal["structure", "grounding", "window", "coherence", "binding", "not_booked"]


def _agent_id(value: str) -> None:
    if not AGENT_ID.fullmatch(value):
        raise InvalidValueError(f"not an agent id: {value!r}")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidValueError(f"{name} must carry its zone")


def _bounded(value: str, name: str, ceiling: int) -> None:
    if not 1 <= len(value) <= ceiling:
        raise InvalidValueError(f"{name} must be 1 to {ceiling} characters")


def is_reference(value: str, ceiling: int) -> bool:
    """One `REFERENCE` token of at most `ceiling` characters."""
    return len(value) <= ceiling and REFERENCE.fullmatch(value) is not None


def _reference(value: str, name: str, ceiling: int) -> None:
    if not is_reference(value, ceiling):
        raise InvalidValueError(f"{name} must be one token of at most {ceiling} characters")


@dataclass(frozen=True)
class AgentSkill:
    """One skill of an agent's card, as the gateway lists it. Third-party text throughout: it is
    read by the classifier, fenced as data, and never shown."""

    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpertAgent:
    """An agent in the gateway's registry. An unusable one is listed with its closed `reason` and
    no skills; `link_hosts` are the only hosts a linking address may point at (§14 A3)."""

    id: str
    name: str
    usable: bool
    reason: str | None
    card_digest: str
    link_hosts: tuple[str, ...]
    skills: tuple[AgentSkill, ...]

    def __post_init__(self) -> None:
        _agent_id(self.id)


@dataclass(frozen=True)
class Outbound:
    """What is sent: a template filled from the verified query, never the holder's sentence."""

    text: str
    data: Mapping[str, object]


@dataclass(frozen=True)
class TextReplyPart:
    text: str


@dataclass(frozen=True)
class DataReplyPart:
    data: Mapping[str, object]


@dataclass(frozen=True)
class AgentReply:
    state: ReplyState
    task_id: str | None
    context_id: str | None
    parts: tuple[TextReplyPart | DataReplyPart, ...]
    latency_ms: int


class ExpertAgentsError(Exception):
    """The gateway, or an agent behind it, did not answer. `code` is closed, so the sentence the
    holder reads is chosen by code, never by matching a message."""

    def __init__(self, code: AgentErrorCode, reason: str | None = None) -> None:
        self.code: AgentErrorCode = code
        self.reason = reason
        super().__init__(f"expert agents: {code}" + (f" ({reason})" if reason else ""))


@dataclass(frozen=True)
class ActivityQuery:
    activity: str  # folded lexicon form, e.g. "spinning"
    start: datetime  # aware, holder's zone
    end: datetime  # aware, > start
    agent_ids: tuple[str, ...] | None = None  # None = every usable agent
    # The daily hours asked for, applied to every day of the window (spec §6.3 rule 2): «el fin de
    # semana por la tarde» is 15:00-21:00 on both days, not Saturday 15:00 to Sunday 21:00.
    band: tuple[time, time] | None = None  # None = no band stated

    def __post_init__(self) -> None:
        _bounded(self.activity, "activity", MAX_ACTIVITY_NAME)
        _aware(self.start, "start")
        _aware(self.end, "end")
        if self.end <= self.start:
            raise InvalidValueError("a query window must end after it starts")
        for agent_id in self.agent_ids or ():
            _agent_id(agent_id)
        if self.band is not None and not self.band[0] < self.band[1]:
            raise InvalidValueError("a band must open before it closes")


@dataclass(frozen=True)
class ActivitySlot:
    """A session that survived the verifier: every value in it was in the agent's reply."""

    agent_id: str
    activity: str  # as the agent wrote it, e.g. "Spinning"
    start: datetime  # aware
    end: datetime | None
    free_places: int | None
    slot_ref: str | None

    def __post_init__(self) -> None:
        _agent_id(self.agent_id)
        _bounded(self.activity, "activity", MAX_ACTIVITY_NAME)
        _aware(self.start, "start")
        if self.end is not None:
            _aware(self.end, "end")
            if self.end <= self.start:
                raise InvalidValueError("a slot must end after it starts")
        if self.free_places is not None and self.free_places < 0:
            raise InvalidValueError("free places cannot be negative")
        if self.slot_ref is not None:
            _reference(self.slot_ref, "slot reference", MAX_SLOT_REF)


@dataclass(frozen=True)
class BookingReceipt:
    """A booking the agent confirmed, bound to the slot that was asked for and the agent asked."""

    agent_id: str
    slot: ActivitySlot
    booking_code: str

    def __post_init__(self) -> None:
        _agent_id(self.agent_id)
        if self.agent_id != self.slot.agent_id:
            raise InvalidValueError("a receipt comes from the agent whose slot was booked")
        _reference(self.booking_code, "booking code", MAX_BOOKING_CODE)


@dataclass(frozen=True)
class SlotDraft:  # the model's proposal, unverified strings
    activity: str
    start: str  # "YYYY-MM-DDTHH:MM" or "HH:MM"
    end: str | None
    free_places: int | None
    slot_ref: str | None


@dataclass(frozen=True)
class AvailabilityDraft:
    slots: tuple[SlotDraft, ...]
    unsupported_request: bool = False

    def __post_init__(self) -> None:
        if len(self.slots) > MAX_AGENT_SLOTS:
            raise InvalidValueError(f"at most {MAX_AGENT_SLOTS} slots per reply")


@dataclass(frozen=True)
class BookingDraft:
    booked: bool
    booking_code: str | None = None
    start: str | None = None
    activity: str | None = None
    slot_ref: str | None = None


@dataclass(frozen=True)
class AgentRejection:
    rule: RejectionRule

    def __post_init__(self) -> None:
        if self.rule not in get_args(RejectionRule):
            raise InvalidValueError(f"not a verifier rule: {self.rule!r}")
