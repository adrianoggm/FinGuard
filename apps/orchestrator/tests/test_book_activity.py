"""A booking is prepared locally and sent once, only after the person confirms (spec §5.2).

`prepare` resolves the sentence against the sealed offers and mints a booking id: nothing leaves.
`confirm` sends the template -- the slot, its reference and the pairwise `memberRef`, never the
holder's sentence -- to the skill the card's classification says books, under a message id derived
from the booking id, and reads the reply through the real verifier. Whatever goes wrong once the
send has been attempted is an unknown outcome and is never sent again: the handler has no retry.

The fakes are the gateway, the classifier and the normaliser; `NOW` is Wednesday 30 September 2026,
10:00 in Madrid, the eve of the Thursday the offers are for.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

import pytest

from finguard_orchestrator.application.commands.book_activity import (
    BookActivityHandler,
    BookingOutcome,
)
from finguard_orchestrator.domain.errors import InvalidValueError, ProviderFailedError
from finguard_orchestrator.domain.models.conversation import ActivityOffers, BookingConfirmation
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentReply,
    AgentSkill,
    AvailabilityDraft,
    BookingDraft,
    BookingReceipt,
    Capability,
    DataReplyPart,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    SlotDraft,
    TextReplyPart,
)
from finguard_orchestrator.domain.services.member_reference import member_reference
from finguard_orchestrator.domain.services.slot_reference import SlotAmbiguous, SlotNotOffered
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.observability.decisions import Step, Verdict, recording_turn

ZONE = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=ZONE)
KEY = b"test-pairwise-key-thirty-two-bytes!!"
HOLDER = "ES-0001"
CALLER = Caller(holder_reference=HOLDER, account_id=UUID(int=7))
BOOKING_ID = UUID(int=5)

SUR_1830 = ActivitySlot(
    "gym-sur", "Spinning", datetime(2026, 10, 1, 18, 30, tzinfo=ZONE), None, 6, None
)
NORTE_1900 = ActivitySlot(
    "gym-norte",
    "Spinning",
    datetime(2026, 10, 1, 19, 0, tzinfo=ZONE),
    datetime(2026, 10, 1, 19, 45, tzinfo=ZONE),
    4,
    "N-1001-1900",
)
OFFERS = ActivityOffers(
    (SUR_1830, NORTE_1900),
    (("gym-sur", "Gimnasio Sur"), ("gym-norte", "Gimnasio Norte")),
    (("gym-sur", "ctx-sur"), ("gym-norte", "ctx-norte")),
)
PENDING = BookingConfirmation(BOOKING_ID, NORTE_1900, "Gimnasio Norte", "ctx-norte")

TIMETABLE = AgentSkill("timetable", "Horario", "Clases por actividad y fecha.")
RESERVE = AgentSkill("reserve", "Reservar", "Reserva una plaza en una clase.")
NORTE = ExpertAgent(
    "gym-norte",
    "Gimnasio Norte",
    True,
    None,
    "sha256:n",
    ("127.0.0.1:9101",),
    (TIMETABLE, RESERVE),
)
SUR = ExpertAgent("gym-sur", "Gimnasio Sur", True, None, "sha256:s", (), (TIMETABLE, RESERVE))


def reply(state: Any = "COMPLETED", *parts: Any) -> AgentReply:
    return AgentReply(state, "task-1", "ctx-norte", tuple(parts), 5)


BOOKED_REPLY = reply(
    "COMPLETED",
    DataReplyPart(
        {
            "reserva": {
                "codigo": "NT-4821",
                "ref": "N-1001-1900",
                "clase": "Spinning",
                "fecha": "2026-10-01",
                "hora_inicio": "19:00",
            }
        }
    ),
)
BOOKED_DRAFT = BookingDraft(True, "NT-4821", "2026-10-01T19:00", "Spinning", "N-1001-1900")
FULL_REPLY = reply(
    "INPUT_REQUIRED",
    DataReplyPart(
        {
            "alternativas": [
                {"clase": "Spinning", "fecha": "2026-10-01", "hora_inicio": "20:00", "huecos": 3},
                {"clase": "Spinning", "fecha": "2026-10-01", "hora_inicio": "21:00", "huecos": 5},
            ]
        }
    ),
)
ALTERNATIVES_DRAFT = AvailabilityDraft(
    (
        SlotDraft("Spinning", "2026-10-01T20:00", None, 3, None),
        SlotDraft("Spinning", "2026-10-01T21:00", None, 5, None),
    )
)
LINK = "http://127.0.0.1:9101/vincular?ref=abc"
LINK_REPLY = reply(
    "AUTH_REQUIRED", TextReplyPart(f"Para reservar necesito vincular tu cuenta de socio: {LINK}")
)


class FakeAgents:
    """The gateway: a listing and one canned answer (or failure, or coroutine) for the booking."""

    def __init__(
        self,
        answer: AgentReply | BaseException | Callable[[], Awaitable[AgentReply]] = BOOKED_REPLY,
        listing: tuple[ExpertAgent, ...] | BaseException = (NORTE, SUR),
    ) -> None:
        self.answer = answer
        self.listing = listing
        self.sent: list[dict[str, Any]] = []

    async def agents(self) -> tuple[ExpertAgent, ...]:
        if isinstance(self.listing, BaseException):
            raise self.listing
        return self.listing

    async def send(
        self,
        agent_id: str,
        *,
        skill_id: str,
        message_id: str,
        outbound: Outbound,
        context_id: str | None,
    ) -> AgentReply:
        self.sent.append(
            {
                "agent_id": agent_id,
                "skill_id": skill_id,
                "message_id": message_id,
                "outbound": outbound,
                "context_id": context_id,
            }
        )
        if isinstance(self.answer, BaseException):
            raise self.answer
        if callable(self.answer):
            return await self.answer()
        return self.answer


class FakeClassifier:
    def __init__(self, mapping: Mapping[Capability, str] | None = None) -> None:
        self.mapping = (
            dict(mapping)
            if mapping is not None
            else {Capability.AVAILABILITY: "timetable", Capability.BOOKING: "reserve"}
        )

    async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
        return self.mapping


class FakeNormaliser:
    def __init__(
        self,
        booking: BookingDraft | None | BaseException = BOOKED_DRAFT,
        availability: AvailabilityDraft | None = ALTERNATIVES_DRAFT,
    ) -> None:
        self.booked = booking
        self.available = availability
        self.queries: list[ActivityQuery] = []
        self.bookings: list[ActivitySlot] = []

    async def availability(
        self, query: ActivityQuery, reply: AgentReply
    ) -> AvailabilityDraft | None:
        self.queries.append(query)
        return self.available

    async def booking(self, slot: ActivitySlot, reply: AgentReply) -> BookingDraft | None:
        self.bookings.append(slot)
        if isinstance(self.booked, BaseException):
            raise self.booked
        return self.booked


def handler(
    agents: FakeAgents | None = None,
    classifier: FakeClassifier | None = None,
    normaliser: FakeNormaliser | None = None,
    *,
    budget: float = 5.0,
) -> BookActivityHandler:
    return BookActivityHandler(
        agents or FakeAgents(),
        classifier or FakeClassifier(),
        normaliser or FakeNormaliser(),
        pairwise_key=KEY,
        budget_seconds=budget,
        clock=lambda zone: NOW.astimezone(zone),
    )


# --- prepare: nothing leaves -----------------------------------------------------------------


def test_prepare_sends_nothing() -> None:
    agents = FakeAgents()
    booking = handler(agents)

    prepared = booking.prepare("apúntame a la de las 19:00 en el Norte", OFFERS, now=NOW)
    again = booking.prepare("apúntame a la de las 19:00 en el Norte", OFFERS, now=NOW)

    assert agents.sent == []
    assert isinstance(prepared, BookingConfirmation) and isinstance(again, BookingConfirmation)
    assert prepared.slot == NORTE_1900
    assert prepared.agent_name == "Gimnasio Norte"
    assert prepared.context_id == "ctx-norte"
    assert prepared.booking_id != again.booking_id, "every preview is a booking of its own"


def test_a_slot_that_has_started_is_not_booked() -> None:  # Review Focus 3
    agents = FakeAgents()
    booking = handler(agents)

    for now in (NORTE_1900.start, datetime(2026, 10, 1, 19, 5, tzinfo=ZONE)):
        assert booking.prepare("apúntame a la de las 19:00 en el Norte", OFFERS, now=now) == (
            "started"
        )
    assert agents.sent == []


def test_prepare_passes_on_what_the_reference_could_not_decide() -> None:
    both = ActivityOffers(
        (
            SUR_1830,
            NORTE_1900,
            ActivitySlot("gym-sur", "Spinning", NORTE_1900.start, None, 2, None),
        ),
        OFFERS.agent_names,
        OFFERS.contexts,
    )
    assert isinstance(handler().prepare("apúntame a la de las 19:00", both, now=NOW), SlotAmbiguous)
    assert handler().prepare("apúntame a la de las 21:00", OFFERS, now=NOW) == SlotNotOffered()


def test_an_agent_without_a_context_prepares_one_without() -> None:
    offers = ActivityOffers(OFFERS.slots, OFFERS.agent_names, (("gym-norte", "ctx-norte"),))

    prepared = handler().prepare("apúntame a la de las 18:30", offers, now=NOW)

    assert isinstance(prepared, BookingConfirmation) and prepared.context_id is None


# --- confirm: one send, the template, a stable message id ------------------------------------


async def test_confirm_sends_the_template_with_the_booking_skill_and_a_stable_message_id() -> None:
    agents = FakeAgents()

    await handler(agents).confirm(PENDING, CALLER)

    member = member_reference(KEY, HOLDER, "gym-norte")
    assert agents.sent == [
        {
            "agent_id": "gym-norte",
            "skill_id": "reserve",
            "message_id": str(uuid5(NAMESPACE_URL, f"finguard:booking:{BOOKING_ID}")),
            "outbound": Outbound(
                text=(
                    "Quiero reservar la clase de Spinning del jueves 1 de octubre de 2026 a las "
                    f"19:00. Referencia: N-1001-1900. Socio: {member}."
                ),
                data={
                    "request": "booking",
                    "ref": "N-1001-1900",
                    "activity": "Spinning",
                    "at": "2026-10-01T19:00:00+02:00",
                    "memberRef": member,
                },
            ),
            "context_id": "ctx-norte",
        }
    ]


async def test_a_slot_with_no_reference_is_asked_for_by_its_activity_and_time() -> None:
    agents = FakeAgents()
    pending = BookingConfirmation(BOOKING_ID, SUR_1830, "Gimnasio Sur", None)

    await handler(agents).confirm(pending, CALLER)

    [sent] = agents.sent
    member = member_reference(KEY, HOLDER, "gym-sur")
    assert sent["outbound"] == Outbound(
        text=(
            "Quiero reservar la clase de Spinning del jueves 1 de octubre de 2026 a las 18:30. "
            f"Socio: {member}."
        ),
        data={
            "request": "booking",
            "activity": "Spinning",
            "at": "2026-10-01T18:30:00+02:00",
            "memberRef": member,
        },
    )
    assert sent["context_id"] is None


async def test_the_same_booking_is_always_the_same_message() -> None:
    """The message id is the booking id's, so an agent that deduplicates by it (§14 A9) sees one
    booking however the holder's answer reached us -- and never the holder reference."""
    first, second = FakeAgents(), FakeAgents()

    await handler(first).confirm(PENDING, CALLER)
    await handler(second).confirm(PENDING, CALLER)

    assert first.sent[0]["message_id"] == second.sent[0]["message_id"]
    assert HOLDER not in first.sent[0]["message_id"]


# --- confirm: every way an agent can answer --------------------------------------------------


async def test_confirm_outcomes() -> None:
    booked = await handler().confirm(PENDING, CALLER)
    assert booked == BookingOutcome(
        "booked", receipt=BookingReceipt("gym-norte", NORTE_1900, "NT-4821")
    )

    forged = FakeNormaliser(booking=BookingDraft(True, "NT-9999", "2026-10-01T19:00", "Spinning"))
    assert (await handler(normaliser=forged).confirm(PENDING, CALLER)).kind == "failed"
    declined = FakeNormaliser(booking=BookingDraft(False))
    assert (await handler(normaliser=declined).confirm(PENDING, CALLER)).kind == "failed"
    unread = FakeNormaliser(booking=None)
    assert (await handler(normaliser=unread).confirm(PENDING, CALLER)).kind == "failed"

    for state in ("FAILED", "REJECTED", "CANCELED"):
        failed = await handler(FakeAgents(reply(state, TextReplyPart("No.")))).confirm(
            PENDING, CALLER
        )
        assert failed == BookingOutcome("failed")

    linking = await handler(FakeAgents(LINK_REPLY)).confirm(PENDING, CALLER)
    assert linking == BookingOutcome("link_required", link=LINK)
    elsewhere = reply(
        "AUTH_REQUIRED", TextReplyPart("Vincula tu cuenta en http://evil.example/vincular")
    )
    assert await handler(FakeAgents(elsewhere)).confirm(PENDING, CALLER) == BookingOutcome(
        "link_required", link=None
    )


async def test_a_full_class_offers_its_alternatives_read_as_a_timetable_of_its_day() -> None:
    """An INPUT_REQUIRED reply confirms nothing, so it is never read as a booking: its alternatives
    are read with the availability reader over the slot's own day, and verified like any search."""
    normaliser = FakeNormaliser()

    outcome = await handler(FakeAgents(FULL_REPLY), normaliser=normaliser).confirm(PENDING, CALLER)

    assert outcome == BookingOutcome(
        "alternatives",
        alternatives=(
            ActivitySlot(
                "gym-norte", "Spinning", datetime(2026, 10, 1, 20, 0, tzinfo=ZONE), None, 3, None
            ),
            ActivitySlot(
                "gym-norte", "Spinning", datetime(2026, 10, 1, 21, 0, tzinfo=ZONE), None, 5, None
            ),
        ),
    )
    assert normaliser.bookings == []
    [query] = normaliser.queries
    assert query.start.date() == query.end.date() == date(2026, 10, 1)
    assert query.band is None and query.activity == "Spinning"


async def test_anything_else_a_paused_agent_asks_for_is_not_forwarded() -> None:
    for draft in (
        None,
        AvailabilityDraft((), unsupported_request=True),
        AvailabilityDraft(()),
        # Another day is not an alternative to this class, and it is not in the reply either.
        AvailabilityDraft((SlotDraft("Spinning", "2026-10-02T20:00", None, 3, None),)),
    ):
        asking = FakeNormaliser(availability=draft)
        outcome = await handler(FakeAgents(FULL_REPLY), normaliser=asking).confirm(PENDING, CALLER)
        assert outcome == BookingOutcome("unsupported_request"), draft


async def test_the_class_that_was_refused_is_not_its_own_alternative() -> None:
    full = reply(
        "INPUT_REQUIRED",
        TextReplyPart(
            "La clase de Spinning de las 19:00 está completa. Te puedo ofrecer el jueves 1 de "
            "octubre a las 20:00."
        ),
    )
    both = AvailabilityDraft(
        (
            SlotDraft("Spinning", "19:00", None, None, None),
            SlotDraft("Spinning", "20:00", None, None, None),
        )
    )
    only_itself = AvailabilityDraft((SlotDraft("Spinning", "19:00", None, None, None),))

    offered = await handler(FakeAgents(full), normaliser=FakeNormaliser(availability=both)).confirm(
        PENDING, CALLER
    )
    itself = await handler(
        FakeAgents(full), normaliser=FakeNormaliser(availability=only_itself)
    ).confirm(PENDING, CALLER)

    assert [f"{slot.start:%H:%M}" for slot in offered.alternatives] == ["20:00"]
    assert itself == BookingOutcome("unsupported_request")


async def test_an_agent_that_cannot_book_is_never_sent_anything() -> None:
    for agents, classifier in (
        (FakeAgents(), FakeClassifier({Capability.AVAILABILITY: "timetable"})),
        (FakeAgents(listing=ExpertAgentsError("unreachable")), None),
        (FakeAgents(listing=(SUR,)), None),  # the agent is gone from the registry
        (
            FakeAgents(
                listing=(
                    ExpertAgent(
                        "gym-norte", "Gimnasio Norte", False, "version_unsupported", "x", (), ()
                    ),
                )
            ),
            None,
        ),
    ):
        outcome = await handler(agents, classifier).confirm(PENDING, CALLER)
        assert outcome == BookingOutcome("failed")
        assert agents.sent == []


async def test_a_classifier_that_cannot_answer_is_a_failure_before_any_send() -> None:
    class Broken(FakeClassifier):
        async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
            raise ProviderFailedError("echo", "down")

    agents = FakeAgents()
    assert await handler(agents, Broken()).confirm(PENDING, CALLER) == BookingOutcome("failed")
    assert agents.sent == []


@pytest.mark.parametrize(
    "lost",
    [
        ExpertAgentsError("timeout"),
        ExpertAgentsError("agent_error"),
        ExpertAgentsError("unreachable"),
        ExpertAgentsError("reply_invalid"),
    ],
    ids=["timeout", "agent_error", "unreachable", "reply_invalid"],
)
async def test_a_lost_response_is_unknown_and_never_resent(lost: ExpertAgentsError) -> None:
    agents = FakeAgents(lost)

    outcome = await handler(agents).confirm(PENDING, CALLER)

    assert outcome == BookingOutcome("unknown")
    assert len(agents.sent) == 1


async def test_a_budget_that_runs_out_after_the_send_is_unknown() -> None:
    async def slow() -> AgentReply:
        await asyncio.sleep(5)
        return BOOKED_REPLY

    agents = FakeAgents(slow)

    outcome = await handler(agents, budget=0.05).confirm(PENDING, CALLER)

    assert outcome == BookingOutcome("unknown")
    assert len(agents.sent) == 1


async def test_a_reply_that_cannot_be_read_after_the_send_is_unknown() -> None:
    """The model failing on a COMPLETED reply is not the gym failing: it may have booked."""
    agents = FakeAgents()
    broken = FakeNormaliser(booking=ProviderFailedError("echo", "down"))

    assert await handler(agents, normaliser=broken).confirm(PENDING, CALLER) == BookingOutcome(
        "unknown"
    )
    assert len(agents.sent) == 1


def test_an_outcome_carries_exactly_what_its_kind_says() -> None:
    """A turn reads the kind and the fields together, so the two can never disagree."""
    receipt = BookingReceipt("gym-norte", NORTE_1900, "NT-4821")
    for impossible in (
        {"kind": "booked"},
        {"kind": "failed", "receipt": receipt},
        {"kind": "alternatives"},
        {"kind": "unknown", "alternatives": (NORTE_1900,)},
        {"kind": "failed", "link": LINK},
    ):
        with pytest.raises(InvalidValueError):
            BookingOutcome(**impossible)  # type: ignore[arg-type]


# --- one decision per booking ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("agents", "verdict", "kind"),
    [
        (FakeAgents(), Verdict.AGREED, "booked"),
        (FakeAgents(FULL_REPLY), Verdict.DECLINED, "alternatives"),
        (FakeAgents(LINK_REPLY), Verdict.DECLINED, "link_required"),
        (FakeAgents(reply("FAILED")), Verdict.FAILED, "failed"),
        (FakeAgents(ExpertAgentsError("timeout")), Verdict.FAILED, "unknown"),
    ],
    ids=["booked", "alternatives", "link_required", "failed", "unknown"],
)
async def test_each_booking_leaves_one_decision(
    agents: FakeAgents, verdict: Verdict, kind: str
) -> None:
    with recording_turn() as turn:
        await handler(agents).confirm(PENDING, CALLER)

    [decision] = turn.sealed().where(Step.EXPERT_AGENT)
    assert (decision.verdict, decision.changed, decision.detail) == (
        verdict,
        False,
        f"booking:gym-norte:{kind}",
    )
