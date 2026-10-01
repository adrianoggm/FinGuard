"""BookActivity: a class from the sealed offers, prepared here and sent once, after a yes (§5.2).

Two steps in two turns, the payment's discipline:

    prepare   resolves the sentence against the offers (`slot_reference`) and mints a booking id.
              Local: nothing is sent to any gym, and a class that has already started is refused
              here rather than asked for (Review Focus 3).
    confirm   runs only after the holder's yes on screen. It sends the template -- the slot, its
              reference and the pairwise `memberRef`, never the sentence (§5.4) -- to the skill the
              card's classification says books, under a message id derived from the booking id,
              and reads what comes back through the verifier.

**A booking is never sent twice.** There is no retry path here at all. Once the send has been
attempted, anything that goes wrong -- the gateway's timeout, the agent's error, a connection that
dropped with the request already out, the budget running out, the model failing to read a reply --
is an unknown outcome: the gym may well have booked, and saying otherwise would invite the second
booking this rule exists to prevent. Only what fails before the send (the listing, the card, no
booking skill), the gateway refusing before it contacted the agent (`agent_unknown`,
`agent_unusable`, `skill_unknown`, `unauthorised`: nothing left it, ruling R30) and the gym's own
refusal (`FAILED`, `REJECTED`, `CANCELED`) are plain failures -- the first two `sent=False`.

What an agent answers, and what it becomes:

    COMPLETED        the model's reading of the confirmation, `verify_booking`ed: `booked` with the
                     receipt, and otherwise `unknown` (ruling R17) -- an unreadable reply, a reading
                     the verifier refuses and one that says «not booked» alike, because the gym
                     completed something and nothing proves what.
    INPUT_REQUIRED   never a confirmation. Its alternatives are read with the availability reader
                     over the week from the booked class's day, and verified like any search's --
                     against that day first and, only when a session falls outside it, against the
                     week (ruling R22): `alternatives`; `unavailable` when none verifies;
                     `unsupported_request` when it asked for something else -- nothing it asked for
                     is forwarded (§7) -- or when reading it failed, the budget included: the state
                     already says nothing was booked (ruling R18).
    AUTH_REQUIRED    `link_required`, with the linking address `link_in` finds on the agent's own
                     link hosts, or none.
    FAILED, REJECTED, CANCELED
                     `failed`: the gym's own explicit no, which the protocol states.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from finguard_orchestrator.domain.errors import InvalidValueError, ProviderFailedError
from finguard_orchestrator.domain.models.conversation import ActivityOffers, BookingConfirmation
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentRejection,
    AgentReply,
    BookingReceipt,
    Capability,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    ReplyState,
)
from finguard_orchestrator.domain.ports.agent_reading import (
    ReplyNormaliserPort,
    SkillClassifierPort,
)
from finguard_orchestrator.domain.ports.expert_agents import ExpertAgentsPort
from finguard_orchestrator.domain.services import activity_writer
from finguard_orchestrator.domain.services.agent_reply_verifier import (
    link_in,
    verify_booking,
    verify_slots,
)
from finguard_orchestrator.domain.services.member_reference import member_reference
from finguard_orchestrator.domain.services.slot_reference import (
    SlotAmbiguous,
    SlotChosen,
    SlotNotOffered,
    resolve_slot,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.observability.decisions import Step, Verdict, record

logger = logging.getLogger(__name__)

BookingOutcomeKind = Literal[
    "booked",
    "alternatives",
    "link_required",
    "unsupported_request",
    "unavailable",
    "failed",
    "unknown",
]

_VERDICTS: Final[Mapping[BookingOutcomeKind, Verdict]] = {
    "booked": Verdict.AGREED,
    # The agent answered, and what it answered was not a booking: a decision, not a failure.
    "alternatives": Verdict.DECLINED,
    "link_required": Verdict.DECLINED,
    "unsupported_request": Verdict.DECLINED,
    "unavailable": Verdict.DECLINED,
    "failed": Verdict.FAILED,
    "unknown": Verdict.FAILED,
}
# The last minute of the booked class's day: a window that ends on the day it starts keeps the
# verifier's single-day reading, in which «a las 20:00» with no date is that day.
_DAY_CLOSES: Final = time(23, 59)
# How far a paused booking's alternatives may reach (ruling R22): the booked day and the six after
# it, to 23:00 on the last -- the gym's next session of a full class can be tomorrow's.
_WEEK_DAYS: Final = 6
_WEEK_CLOSES: Final = time(23, 0)
# The gateway's codes for a refusal it gave before contacting the agent (ruling R30): an agent it
# does not list or trust, a skill it does not know, a token it does not accept. Nothing left it.
_REFUSED_BEFORE_SENDING: Final = frozenset(
    {"agent_unknown", "agent_unusable", "skill_unknown", "unauthorised"}
)


@dataclass(frozen=True)
class BookingOutcome:
    """What one sent booking came to. A receipt exactly when it was booked and alternatives
    exactly when some were offered, so a turn can never say «Reserva hecha» with no code.
    `sent` is false only for a failure before anything reached the gym (ruling R30)."""

    kind: BookingOutcomeKind
    receipt: BookingReceipt | None = None
    alternatives: tuple[ActivitySlot, ...] = ()
    link: str | None = None
    sent: bool = True

    def __post_init__(self) -> None:
        if (self.kind == "booked") != (self.receipt is not None):
            raise InvalidValueError("a booking is booked exactly when it has its receipt")
        if (self.kind == "alternatives") != bool(self.alternatives):
            raise InvalidValueError("alternatives are offered exactly when there are some")
        if self.link is not None and self.kind != "link_required":
            raise InvalidValueError("only a gym asking for linking gives a link")
        if not self.sent and self.kind != "failed":
            raise InvalidValueError("only a failure can have sent nothing")


_FAILED: Final = BookingOutcome("failed")
_UNSENT: Final = BookingOutcome("failed", sent=False)


@dataclass
class _Answered:
    """The state a sent booking's reply came back in, once it has: what decides whether a failure
    after the send can mean the gym booked (ruling R18)."""

    state: ReplyState | None = None


def message_id_of(booking_id: UUID) -> str:
    """The one message a booking is ever sent as: the same booking id, the same message id."""
    return str(uuid5(NAMESPACE_URL, f"finguard:booking:{booking_id}"))


class BookActivityHandler:
    def __init__(
        self,
        agents: ExpertAgentsPort,
        classifier: SkillClassifierPort,
        normaliser: ReplyNormaliserPort,
        *,
        pairwise_key: bytes,
        budget_seconds: float,
        clock: Callable[[ZoneInfo], datetime] = lambda zone: datetime.now(zone),
    ) -> None:
        self._agents = agents
        self._classifier = classifier
        self._normaliser = normaliser
        self._key = pairwise_key
        self._budget = budget_seconds
        self._clock = clock

    def now(self, caller: Caller) -> datetime:
        """The moment in the caller's zone, by this handler's clock -- what `prepare` is asked
        against, so a class's having started is judged by one clock throughout."""
        return self._clock(ZoneInfo(caller.reporting_timezone))

    def prepare(
        self, sentence: str, offers: ActivityOffers, *, now: datetime
    ) -> BookingConfirmation | SlotAmbiguous | SlotNotOffered | Literal["started"]:
        """The booking the sentence asks for, or why there is none yet. Sends nothing.

        The caller hands over offers it has verified (`ActivityOffers.trusted`); this does not
        read the seal, it reads what the seal vouched for.
        """
        resolved = resolve_slot(sentence, offers)
        if not isinstance(resolved, SlotChosen):
            return resolved
        slot = resolved.slot
        if slot.start <= now:
            return "started"
        return BookingConfirmation(
            booking_id=uuid4(),
            slot=slot,
            agent_name=offers.name_of(slot.agent_id),
            context_id=dict(offers.contexts).get(slot.agent_id),
        )

    async def confirm(self, pending: BookingConfirmation, caller: Caller) -> BookingOutcome:
        """Send the booking once and say what came of it. Never raises for an agent."""
        outcome = await self._booked(pending, caller)
        record(
            Step.EXPERT_AGENT,
            _VERDICTS[outcome.kind],
            changed=False,
            detail=f"booking:{pending.slot.agent_id}:{outcome.kind}",
        )
        return outcome

    async def _booked(self, pending: BookingConfirmation, caller: Caller) -> BookingOutcome:
        agent_id = pending.slot.agent_id
        try:
            listed = await self._agents.agents()
        except ExpertAgentsError as exc:
            logger.warning("expert_agents.booking_unlisted", extra={"code": exc.code})
            return _UNSENT
        agent = next((each for each in listed if each.id == agent_id and each.usable), None)
        if agent is None:
            return _UNSENT
        try:
            skills = await asyncio.wait_for(self._classifier.classify(agent), self._budget)
        except (ProviderFailedError, TimeoutError):
            return _UNSENT
        skill = skills.get(Capability.BOOKING)
        if skill is None:
            return _UNSENT
        outbound = self._outbound(pending.slot, caller)
        zone = ZoneInfo(caller.reporting_timezone)
        # From here on the booking may have happened, whatever is raised: unknown, never re-sent --
        # unless the reply had already said it was paused for input (ruling R18): then nothing was
        # booked, and failing to read what it offers is a request FinGuard cannot answer.
        answered = _Answered()
        try:
            return await asyncio.wait_for(
                self._exchange(agent, skill, pending, outbound, zone, answered), self._budget
            )
        except Exception as exc:  # every failure after the send is one fact, read by the state
            if (
                answered.state is None
                and isinstance(exc, ExpertAgentsError)
                and exc.code in _REFUSED_BEFORE_SENDING
            ):
                logger.warning(
                    "expert_agents.booking_refused", extra={"agent": agent_id, "code": exc.code}
                )
                return _UNSENT
            paused = answered.state == "INPUT_REQUIRED"
            logger.warning(
                "expert_agents.booking_unread" if paused else "expert_agents.booking_unknown",
                extra={"agent": agent_id, "error": type(exc).__name__},
            )
            return BookingOutcome("unsupported_request" if paused else "unknown")

    async def _exchange(
        self,
        agent: ExpertAgent,
        skill: str,
        pending: BookingConfirmation,
        outbound: Outbound,
        zone: ZoneInfo,
        answered: _Answered,
    ) -> BookingOutcome:
        reply = await self._agents.send(
            agent.id,
            skill_id=skill,
            message_id=message_id_of(pending.booking_id),
            outbound=outbound,
            context_id=pending.context_id,
        )
        answered.state = reply.state
        if reply.state == "AUTH_REQUIRED":
            return BookingOutcome("link_required", link=link_in(reply, agent.link_hosts))
        if reply.state == "INPUT_REQUIRED":
            return await self._alternatives(pending.slot, reply, zone)
        if reply.state != "COMPLETED":
            return _FAILED
        # Ruling R17: the task says the gym completed something, and only a verified receipt says
        # what. A reading the model could not make, one the verifier refuses, or one that says «not
        # booked» proves nothing either way -- so the outcome is unknown, never a failure.
        draft = await self._normaliser.booking(pending.slot, reply)
        if draft is None:
            return BookingOutcome("unknown")
        receipt = verify_booking(draft, slot=pending.slot, reply=reply)
        if isinstance(receipt, AgentRejection):
            logger.warning(
                "expert_agents.booking_unverified",
                extra={"agent": agent.id, "rule": receipt.rule},
            )
            return BookingOutcome("unknown")
        return BookingOutcome("booked", receipt=receipt)

    async def _alternatives(
        self, slot: ActivitySlot, reply: AgentReply, zone: ZoneInfo
    ) -> BookingOutcome:
        """What a paused agent offers instead, as a timetable of the week from the booked day.

        One reading, verified twice at most and repaired never (ruling R22): against the booked
        day first -- the only window in which «te ofrezco la de las 21:00», naming no day, has
        one -- and, only when that fails on `window` because a session is on a later day,
        against the week.
        """
        day = slot.start.astimezone(zone).date()
        opens = datetime.combine(day, time(0, 0), zone)
        on_the_day = ActivityQuery(slot.activity, opens, datetime.combine(day, _DAY_CLOSES, zone))
        last = day + timedelta(days=_WEEK_DAYS)
        week = ActivityQuery(slot.activity, opens, datetime.combine(last, _WEEK_CLOSES, zone))
        draft = await self._normaliser.availability(week, reply)
        if draft is None or draft.unsupported_request:
            return BookingOutcome("unsupported_request")
        now = self._clock(zone)
        verified = verify_slots(
            draft, query=on_the_day, reply=reply, agent_id=slot.agent_id, now=now
        )
        if verified == AgentRejection("window"):
            verified = verify_slots(draft, query=week, reply=reply, agent_id=slot.agent_id, now=now)
        if isinstance(verified, AgentRejection):
            logger.info(
                "expert_agents.alternatives_rejected",
                extra={"agent": slot.agent_id, "rule": verified.rule},
            )
            return BookingOutcome("unavailable")
        # The class that was just refused is not its own alternative.
        offered = sorted(
            (each for each in verified if each.start != slot.start),
            key=lambda each: each.start,
        )
        if not offered:
            return BookingOutcome("unavailable")
        return BookingOutcome("alternatives", alternatives=tuple(offered))

    def _outbound(self, slot: ActivitySlot, caller: Caller) -> Outbound:
        """The template and the data part, filled from the verified slot alone (§5.4)."""
        member = member_reference(self._key, caller.holder_reference, slot.agent_id)
        data: dict[str, object] = {"request": "booking"}
        if slot.slot_ref is not None:
            data["ref"] = slot.slot_ref
        data["activity"] = slot.activity
        data["at"] = slot.start.isoformat(timespec="seconds")
        data["memberRef"] = member
        return Outbound(text=activity_writer.booking_request(slot, member), data=data)
