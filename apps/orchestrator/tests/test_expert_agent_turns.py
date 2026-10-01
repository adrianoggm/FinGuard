"""The expert-agent turn: one helper, called from both question routes (spec §14 A11).

`expert_agent_reply` answers a turn the predicate claims and returns `None` for every other one, so
the caller continues exactly as before. These drive it with a fake search handler -- the handler's
own behaviour is `test_search_activities.py` -- and then through both HTTP routes, because the web
types into `/v1/questions/stream` and a branch wired into one route only is a branch the
conversation never gets.
"""

import ast
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.commands.book_activity import (
    BookActivityHandler,
    BookingOutcome,
)
from finguard_orchestrator.application.queries.search_activities import (
    ActivitySearch,
    AgentOutcome,
    SearchActivitiesHandler,
    SearchActivitiesQuery,
)
from finguard_orchestrator.bootstrap import main as bootstrap_main
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller
from finguard_orchestrator.bootstrap.main import app, create_app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import (
    ActivityOffers,
    BookingConfirmation,
    Conversation,
    PaymentInProgress,
)
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    BookingReceipt,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers.expert_agent_turns import expert_agent_reply
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest, QuestionResponse
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

ZONE = ZoneInfo("Europe/Madrid")
ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
HOLDER = "ES-0001"
SENTENCE = "¿Qué clases de spinning hay el jueves por la tarde?"
CODEC = MetricTokenCodec({"k1": b"k" * 32}, "k1")
ASKER = Caller(HOLDER, ACCOUNT, scopes=frozenset({Scope.QUESTIONS_READ, Scope.AGENTS_ASK}))
READER = Caller(HOLDER, ACCOUNT, scopes=frozenset({Scope.QUESTIONS_READ}))

THURSDAY = ActivityQuery(
    "spinning",
    datetime(2026, 10, 1, 15, 0, tzinfo=ZONE),
    datetime(2026, 10, 1, 21, 0, tzinfo=ZONE),
    band=(time(15, 0), time(21, 0)),
)
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
ANSWERED_NORTE = AgentOutcome("gym-norte", "Gimnasio Norte", "answered")
ANSWERED_SUR = AgentOutcome("gym-sur", "Gimnasio Sur", "answered")
SILENT_ESTE = AgentOutcome("gym-este", "Gimnasio Este", "unavailable", rule="budget")
LINKING_OESTE = AgentOutcome(
    "gym-oeste", "Gimnasio Oeste", "link_required", link="http://127.0.0.1:9104/vincular"
)
OFFERS = ActivityOffers(
    (SUR_1830, NORTE_1900),
    (("gym-sur", "Gimnasio Sur"), ("gym-norte", "Gimnasio Norte")),
    (("gym-sur", "ctx-sur"), ("gym-norte", "ctx-norte")),
)
FOUND = ActivitySearch(
    THURSDAY,
    (SUR_1830, NORTE_1900),
    (ANSWERED_NORTE, ANSWERED_SUR, SILENT_ESTE, LINKING_OESTE),
    OFFERS,
    None,
)
LAST_MONTH = FinancialPlan(
    direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
)


class FakeSearches:
    def __init__(self, result: ActivitySearch = FOUND) -> None:
        self.result = result
        self.queries: list[SearchActivitiesQuery] = []

    async def handle(self, query: SearchActivitiesQuery) -> ActivitySearch:
        self.queries.append(query)
        return self.result


# Wednesday 30 September 2026, 10:00 in Madrid: the eve of the Thursday the offers are for.
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=ZONE)
BOOKER = Caller(
    HOLDER,
    ACCOUNT,
    scopes=frozenset({Scope.QUESTIONS_READ, Scope.AGENTS_ASK, Scope.AGENTS_BOOK}),
)


class Unreachable:
    """A gateway and two readers nothing may reach: preparing a booking is local (§5.2), so any
    call to one of these is a send that should not have happened."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def agents(self) -> tuple[()]:
        self.sent.append("agents")
        raise AssertionError("nothing is listed while a booking is prepared")

    async def send(self, *args: object, **kwargs: object) -> None:
        self.sent.append((args, kwargs))
        raise AssertionError("nothing is sent before a yes")


def booked(pending: BookingConfirmation) -> BookingOutcome:
    return BookingOutcome(
        "booked", receipt=BookingReceipt(pending.slot.agent_id, pending.slot, "SUR-7731")
    )


class FakeBookings(BookActivityHandler):
    """The real `prepare` and clock over a gateway nothing may reach, and a scripted `confirm`:
    what each outcome of a send becomes is `test_book_activity.py`'s; this is what a turn says."""

    def __init__(
        self,
        outcome: Callable[[BookingConfirmation], BookingOutcome] = booked,
        *,
        now: datetime = NOW,
    ) -> None:
        self.gateway = Unreachable()
        super().__init__(
            self.gateway,
            self.gateway,  # type: ignore[arg-type]
            self.gateway,  # type: ignore[arg-type]
            pairwise_key=b"k" * 32,
            budget_seconds=5.0,
            clock=lambda zone: now.astimezone(zone),
        )
        self.outcome = outcome
        self.confirmed: list[BookingConfirmation] = []

    async def confirm(self, pending: BookingConfirmation, caller: Caller) -> BookingOutcome:
        self.confirmed.append(pending)
        return self.outcome(pending)

    @property
    def sent_nothing(self) -> bool:
        return self.confirmed == [] and self.gateway.sent == []


async def reply(
    sentence: str = SENTENCE,
    *,
    who: Caller = ASKER,
    was: Conversation | None = None,
    searches: FakeSearches | None = None,
    bookings: FakeBookings | None = None,
    configured: bool = True,
    channel: Any = "TEXT",
) -> QuestionResponse | None:
    was = was or conversation_mapper.to_domain(None)
    return await expert_agent_reply(
        QuestionRequest(question=sentence, channel=channel),
        who,
        was,
        searches=(searches or FakeSearches()) if configured else None,
        bookings=bookings if configured else None,
        codec=CODEC,
    )


async def test_a_sentence_the_predicate_does_not_claim_is_left_to_the_caller() -> None:
    searches = FakeSearches()
    for sentence in ("¿cuánto gasto en el gimnasio?", "hazle un bizum de 20 a Marta", "hola"):
        assert await reply(sentence, searches=searches) is None
    assert searches.queries == []


async def test_without_a_gateway_the_capability_does_not_exist() -> None:
    answer = await reply(configured=False)

    assert answer is not None
    assert answer.outcome == "EXPERT_AGENTS_OFF"
    assert answer.text == "Esta instalación no tiene agentes externos configurados."
    assert answer.activities == [] and answer.agent_notices == []


async def test_a_caller_without_agents_ask_is_told_so_and_nothing_is_searched() -> None:
    searches = FakeSearches()
    answer = await reply(who=READER, searches=searches)

    assert answer is not None
    assert answer.outcome == "EXPERT_AGENTS_FORBIDDEN"
    assert answer.text == "Tu acceso no permite consultar agentes externos."
    assert searches.queries == []


async def test_a_found_search_lists_the_slots_seals_the_offers_and_offers_chips() -> None:
    searches = FakeSearches()
    was = conversation_mapper.to_domain(None)
    answer = await reply(was=was, searches=searches)

    assert answer is not None and answer.outcome == "ACTIVITIES_FOUND"
    assert searches.queries == [SearchActivitiesQuery(SENTENCE, ASKER)]
    assert answer.text == (
        "He encontrado 2 clases de spinning el jueves 1 de octubre: 18:30 en Gimnasio Sur "
        "(6 plazas) y 19:00 en Gimnasio Norte (4 plazas). Gimnasio Este no ha respondido de "
        "forma válida. Gimnasio Oeste necesita que vincules tu cuenta de socio."
    )
    assert [(slot.agent_name, f"{slot.start:%H:%M}") for slot in answer.activities] == [
        ("Gimnasio Sur", "18:30"),
        ("Gimnasio Norte", "19:00"),
    ]
    assert answer.activities[1].model_dump(mode="json") == {
        "agent_id": "gym-norte",
        "agent_name": "Gimnasio Norte",
        "activity": "Spinning",
        "start": "2026-10-01T19:00:00+02:00",
        "end": "2026-10-01T19:45:00+02:00",
        "free_places": 4,
        "slot_ref": "N-1001-1900",
    }
    assert [notice.model_dump() for notice in answer.agent_notices] == [
        {"agent_name": "Gimnasio Este", "kind": "unavailable", "link": None},
        {
            "agent_name": "Gimnasio Oeste",
            "kind": "link_required",
            "link": "http://127.0.0.1:9104/vincular",
        },
    ]
    assert answer.suggestions == ["Apúntame a la de las 18:30", "Apúntame a la de las 19:00"]

    conversation = answer.conversation
    assert conversation is not None and conversation.activity_offers is not None
    token = conversation.activity_offers.token
    assert token is not None and token.startswith("o1.")
    assert conversation.conversation_id == was.conversation_id
    assert (
        CODEC.verify_offers(
            token,
            HOLDER,
            was.conversation_id,
            conversation_mapper.offers_semantics(conversation.activity_offers),
        )
        == "ok"
    )


async def test_a_search_replaces_the_offers_and_keeps_the_rest_of_the_conversation() -> None:
    stale = ActivityOffers((SUR_1830,), (("gym-sur", "Gimnasio Sur"),), ())
    paying = PaymentInProgress(draft=PaymentDraft(recipient_name="Marta"))
    was = replace(
        conversation_mapper.to_domain(None).answered(LAST_MONTH).with_active(paying),
        activity_offers=stale,
    )

    answer = await reply(was=was)

    assert answer is not None and answer.conversation is not None
    after = conversation_mapper.to_domain(answer.conversation)
    assert after.activity_offers is not None
    assert after.activity_offers.slots == OFFERS.slots
    assert after.last_plan == LAST_MONTH, "a search is a turn, not a reset"
    assert isinstance(after.active, PaymentInProgress) and after.active.suspended, (
        "a payment under way steps aside for the search, as for any other subject"
    )


async def test_no_slots_is_activities_none_and_the_old_offers_go() -> None:
    nothing = ActivitySearch(THURSDAY, (), (ANSWERED_NORTE, SILENT_ESTE), None, None)
    was = replace(conversation_mapper.to_domain(None), activity_offers=OFFERS)

    answer = await reply(was=was, searches=FakeSearches(nothing))

    assert answer is not None and answer.outcome == "ACTIVITIES_NONE"
    assert answer.text == (
        "No he encontrado clases de spinning el jueves 1 de octubre entre las 15:00 y las 21:00. "
        "Gimnasio Este no ha respondido de forma válida."
    )
    assert answer.activities == [] and answer.suggestions == []
    assert [notice.agent_name for notice in answer.agent_notices] == ["Gimnasio Este"]
    assert answer.conversation is not None and answer.conversation.activity_offers is None


async def test_every_agent_failed_is_agents_unavailable() -> None:
    rejected_sur = AgentOutcome("gym-sur", "Gimnasio Sur", "unavailable", rule="grounding")
    failed = ActivitySearch(THURSDAY, (), (SILENT_ESTE, rejected_sur), None, None)

    answer = await reply(searches=FakeSearches(failed))

    assert answer is not None and answer.outcome == "AGENTS_UNAVAILABLE"
    assert answer.text == "Ningún gimnasio ha respondido de forma válida."
    assert len(answer.agent_notices) == 2


async def test_a_named_gym_that_does_not_meet_the_standard_is_said_so() -> None:
    antiguo = AgentOutcome(
        "gym-antiguo", "Gimnasio Antiguo", "unavailable", rule="auth_unsupported", asked=False
    )
    answer = await reply(
        searches=FakeSearches(ActivitySearch(THURSDAY, (), (antiguo,), None, None))
    )

    assert answer is not None and answer.outcome == "AGENTS_UNAVAILABLE"
    assert answer.text == "Gimnasio Antiguo pide una autenticación que FinGuard no admite."
    # Ruling R16: a gym that was never asked is not listed as one that answered badly -- the text
    # already says why it was not asked.
    assert answer.agent_notices == []


async def test_a_never_asked_gym_beside_a_found_search_is_in_the_text_only() -> None:
    antiguo = AgentOutcome(
        "gym-antiguo", "Gimnasio Antiguo", "unavailable", rule="version_unsupported", asked=False
    )
    found = replace(FOUND, outcomes=(ANSWERED_NORTE, ANSWERED_SUR, SILENT_ESTE, antiguo))

    answer = await reply(searches=FakeSearches(found))

    assert answer is not None and answer.outcome == "ACTIVITIES_FOUND"
    assert "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige." in answer.text
    assert [notice.agent_name for notice in answer.agent_notices] == ["Gimnasio Este"]


async def test_a_named_gym_with_no_timetable_is_not_said_to_be_unknown() -> None:
    refused = ActivitySearch(THURSDAY, (), (), None, "no_agents")

    answer = await reply("¿hay spinning el jueves en el Norte?", searches=FakeSearches(refused))

    assert answer is not None and answer.outcome == "AGENTS_UNAVAILABLE"
    assert answer.text == "Ningún agente configurado ofrece horarios de clases."


@pytest.mark.parametrize(
    ("refusal", "outcome", "text"),
    [
        (
            "no_activity",
            "ACTIVITY_UNCLEAR",
            "¿Qué actividad buscas? Puedo consultar clases de spinning, yoga, pilates, pádel, "
            "natación, crossfit, zumba, boxeo, body pump y aquagym.",
        ),
        (
            "no_window",
            "ACTIVITY_UNCLEAR",
            "Solo puedo buscar clases de hoy en adelante: dime qué día, por ejemplo "
            "«el jueves por la tarde».",
        ),
        ("no_agents", "AGENTS_UNAVAILABLE", "Ningún agente configurado ofrece horarios de clases."),
        (
            "none_named",
            "AGENTS_UNAVAILABLE",
            "Ninguno de los gimnasios configurados tiene ese nombre.",
        ),
        (
            "unreachable",
            "AGENTS_UNAVAILABLE",
            "No puedo contactar con los agentes externos ahora mismo.",
        ),
    ],
)
async def test_each_refusal_says_its_sentence(refusal: Any, outcome: str, text: str) -> None:
    refused = ActivitySearch(
        None if refusal in ("no_activity", "no_window") else THURSDAY, (), (), None, refusal
    )
    was = replace(conversation_mapper.to_domain(None), activity_offers=OFFERS)

    answer = await reply(was=was, searches=FakeSearches(refused))

    assert answer is not None
    assert (answer.outcome, answer.text) == (outcome, text)
    assert answer.conversation is not None
    assert answer.conversation.activity_offers is not None, "a refusal leaves the offers alone"


# --- the booking (spec §5.2), in the helper's order of precedence --------------------------------

SUR_1900 = ActivitySlot(
    "gym-sur", "Spinning", datetime(2026, 10, 1, 19, 0, tzinfo=ZONE), None, 3, None
)
AT_SEVEN = ActivityOffers((SUR_1830, NORTE_1900, SUR_1900), OFFERS.agent_names, OFFERS.contexts)
PREVIEW = "Vas a reservar Spinning, jueves 1 de octubre, 18:30, en Gimnasio Sur. ¿Confirmas?"
PENDING = BookingConfirmation(UUID(int=21), SUR_1830, "Gimnasio Sur", "ctx-sur")


def offering(offers: ActivityOffers = OFFERS) -> Conversation:
    return replace(conversation_mapper.to_domain(None), activity_offers=offers)


def pending(
    booking: BookingConfirmation = PENDING, offers: ActivityOffers = OFFERS
) -> Conversation:
    return offering(offers).with_active(booking)


async def test_a_booking_is_prepared_from_the_offers_and_nothing_is_sent() -> None:
    bookings = FakeBookings()
    searches = FakeSearches()

    answer = await reply(
        "apúntame a la de las 18:30", was=offering(), bookings=bookings, searches=searches
    )

    assert answer is not None
    assert (answer.outcome, answer.text) == ("BOOKING_AWAITING_CONFIRMATION", PREVIEW)
    assert answer.conversation is not None
    assert answer.conversation.workflow == "BOOKING_CONFIRMATION"
    assert answer.conversation.awaiting == "CONFIRMATION"
    assert answer.conversation.booking_slot is not None
    assert answer.conversation.booking_slot.start == SUR_1830.start
    assert answer.conversation.booking_agent_name == "Gimnasio Sur"
    assert answer.conversation.booking_context_id == "ctx-sur"
    assert answer.conversation.activity_offers is not None, "the offers stay for the next turn"
    assert bookings.sent_nothing and searches.queries == []


async def test_a_class_with_an_end_is_previewed_with_its_hours() -> None:
    answer = await reply(
        "apúntame a la de las 19:00 en el Norte", was=offering(), bookings=FakeBookings()
    )

    assert answer is not None and answer.text == (
        "Vas a reservar Spinning, jueves 1 de octubre, 19:00–19:45, en Gimnasio Norte. ¿Confirmas?"
    )


async def test_one_time_at_two_gyms_is_asked_about_with_a_chip_for_each() -> None:
    bookings = FakeBookings()

    answer = await reply("apúntame a la de las 19:00", was=offering(AT_SEVEN), bookings=bookings)

    assert answer is not None
    assert (answer.outcome, answer.text) == (
        "BOOKING_AMBIGUOUS",
        "¿Cuál de estas: 19:00 en Gimnasio Norte o 19:00 en Gimnasio Sur?",
    )
    assert answer.suggestions == [
        "Apúntame a la de las 19:00 en Gimnasio Norte",
        "Apúntame a la de las 19:00 en Gimnasio Sur",
    ]
    assert answer.conversation is not None and answer.conversation.workflow is None
    assert bookings.sent_nothing


async def test_a_chip_names_the_one_class_it_offers() -> None:
    """Each suggestion of an ambiguity, sent back, prepares exactly its class."""
    bookings = FakeBookings()
    asked = await reply("apúntame a la de las 19:00", was=offering(AT_SEVEN), bookings=bookings)
    assert asked is not None

    for chip, chosen in zip(asked.suggestions, (NORTE_1900, SUR_1900), strict=True):
        answer = await reply(chip, was=offering(AT_SEVEN), bookings=bookings)
        assert answer is not None and answer.outcome == "BOOKING_AWAITING_CONFIRMATION"
        assert answer.conversation is not None and answer.conversation.booking_slot is not None
        assert answer.conversation.booking_slot.agent_id == chosen.agent_id


@pytest.mark.parametrize(
    ("sentence", "was", "now", "outcome", "text"),
    [
        (
            "apúntame a la de las 21:00",
            offering(),
            NOW,
            "BOOKING_NOT_OFFERED",
            "Esa clase no está entre las que te he ofrecido.",
        ),
        (
            "apúntame a la de las 18:30",
            offering(),
            datetime(2026, 10, 1, 18, 45, tzinfo=ZONE),
            "BOOKING_STARTED",
            "Esa clase ya ha empezado.",
        ),
        (
            "apúntame a la de las 18:30",
            offering(replace(OFFERS, trusted=False)),
            NOW,
            "BOOKING_SEARCH_AGAIN",
            "No puedo usar esa búsqueda; vuelve a pedirla.",
        ),
        (
            "la primera",
            offering(replace(OFFERS, trusted=False)),
            NOW,
            "BOOKING_SEARCH_AGAIN",
            "No puedo usar esa búsqueda; vuelve a pedirla.",
        ),
        (
            "apúntame a spinning",
            conversation_mapper.to_domain(None),
            NOW,
            "BOOKING_SEARCH_AGAIN",
            "No puedo usar esa búsqueda; vuelve a pedirla.",
        ),
    ],
    ids=["not-offered", "started", "untrusted", "untrusted-pointer", "no-offers"],
)
async def test_a_booking_that_cannot_be_prepared_says_why_and_leaves_everything_as_it_was(
    sentence: str, was: Conversation, now: datetime, outcome: str, text: str
) -> None:
    bookings = FakeBookings(now=now)
    searches = FakeSearches()

    answer = await reply(sentence, was=was, bookings=bookings, searches=searches)

    assert answer is not None and (answer.outcome, answer.text) == (outcome, text)
    assert answer.conversation == conversation_mapper.to_payload(was)
    assert bookings.sent_nothing and searches.queries == []


@pytest.mark.parametrize(
    ("sentence", "was"),
    [
        ("pasa 100 a la reserva", offering()),
        ("la primera", conversation_mapper.to_domain(None)),
        ("¿cuánto tengo en la reserva?", offering()),
    ],
    ids=["payment", "pointer-without-offers", "balance"],
)
async def test_money_and_a_pointer_at_nothing_are_left_to_the_bank(
    sentence: str, was: Conversation
) -> None:
    bookings = FakeBookings()

    assert await reply(sentence, was=was, bookings=bookings) is None
    assert bookings.sent_nothing


async def test_a_booking_without_the_capability_or_the_scope_says_so() -> None:
    off = await reply("apúntame a la de las 18:30", was=offering(), configured=False)
    assert off is not None and off.outcome == "EXPERT_AGENTS_OFF"

    bookings = FakeBookings()
    forbidden = await reply(
        "apúntame a la de las 18:30", who=READER, was=offering(), bookings=bookings
    )
    assert forbidden is not None and forbidden.outcome == "EXPERT_AGENTS_FORBIDDEN"
    assert forbidden.conversation is not None and forbidden.conversation.workflow is None
    assert bookings.sent_nothing


@pytest.mark.parametrize("suspended", [True, False], ids=["paused", "under-way"])
async def test_a_preview_never_drops_a_payment_under_way(suspended: bool) -> None:
    """Ruling R18: one workflow is active at a time, and a preview would replace a half-built
    Bizum without a word. So the preview is not made: the turn names the payment and how to
    finish or drop it, and leaves it set aside -- as a search would -- with the offers intact."""
    bookings = FakeBookings()
    paying = PaymentInProgress(draft=PaymentDraft(recipient_name="Marta"), suspended=suspended)
    was = offering().with_active(paying)

    answer = await reply("apúntame a la de las 18:30", who=BOOKER, was=was, bookings=bookings)

    assert answer is not None
    assert (answer.outcome, answer.text) == (
        "BOOKING_PAYMENT_PAUSED",
        "Antes de reservar, termina o descarta el pago que tienes a medias. El Bizum a Marta "
        "está pausado. Para retomarlo, escribe «continuar con el Bizum»; para descartarlo, "
        "responde «no».",
    )
    after = conversation_mapper.to_domain(answer.conversation)
    assert isinstance(after.active, PaymentInProgress) and after.active.suspended
    assert after.active.draft == paying.draft
    assert after.activity_offers is not None and after.activity_offers.slots == OFFERS.slots
    assert bookings.sent_nothing


async def test_a_payment_under_way_does_not_stop_a_question_the_offers_answer() -> None:
    """Only a preview would replace the payment; a question back leaves everything as it was."""
    paying = PaymentInProgress(draft=PaymentDraft(recipient_name="Marta"), suspended=True)
    was = offering(AT_SEVEN).with_active(paying)

    answer = await reply("apúntame a la de las 19:00", was=was, bookings=FakeBookings())

    assert answer is not None and answer.outcome == "BOOKING_AMBIGUOUS"
    assert answer.conversation == conversation_mapper.to_payload(was)


# --- the yes, on screen only ---------------------------------------------------------------------


def alternatives(pending: BookingConfirmation) -> BookingOutcome:
    later = [
        ActivitySlot(
            pending.slot.agent_id,
            "Spinning",
            datetime(2026, 10, 1, hour, 0, tzinfo=ZONE),
            None,
            places,
            None,
        )
        for hour, places in ((20, 2), (21, 5))
    ]
    return BookingOutcome("alternatives", alternatives=tuple(later))


def alternatives_over_two_days(pending: BookingConfirmation) -> BookingOutcome:
    later = [
        ActivitySlot(pending.slot.agent_id, "Spinning", at, None, 3, None)
        for at in (
            datetime(2026, 10, 1, 21, 0, tzinfo=ZONE),
            datetime(2026, 10, 2, 18, 30, tzinfo=ZONE),
        )
    ]
    return BookingOutcome("alternatives", alternatives=tuple(later))


LINK = "http://127.0.0.1:9102/vincular?ref=abc"


@pytest.mark.parametrize(
    ("outcome", "expected", "text"),
    [
        (
            booked,
            "BOOKED",
            "Reserva hecha en Gimnasio Sur: Spinning, jueves 1 de octubre, 18:30. Código SUR-7731.",
        ),
        (
            alternatives,
            "BOOKING_ALTERNATIVES",
            "La de las 18:30 no está disponible; Gimnasio Sur ofrece las 20:00 y las 21:00.",
        ),
        (
            alternatives_over_two_days,
            "BOOKING_ALTERNATIVES",
            "La de las 18:30 no está disponible; Gimnasio Sur ofrece las 21:00 y el viernes 2 "
            "a las 18:30.",
        ),
        (
            lambda _: BookingOutcome("link_required", link=LINK),
            "BOOKING_LINK_REQUIRED",
            "Gimnasio Sur necesita que vincules tu cuenta de socio; cuando lo hayas hecho, "
            "vuelve a pedir la reserva.",
        ),
        (
            lambda _: BookingOutcome("unsupported_request"),
            "BOOKING_UNSUPPORTED",
            "Gimnasio Sur pide un dato que FinGuard no proporciona; la reserva no se ha hecho.",
        ),
        (
            lambda _: BookingOutcome("unavailable"),
            "BOOKING_UNAVAILABLE",
            "Esa clase no está disponible en Gimnasio Sur; vuelve a buscar.",
        ),
        (
            lambda _: BookingOutcome("failed"),
            "BOOKING_FAILED",
            "Gimnasio Sur no ha confirmado la reserva.",
        ),
        (
            lambda _: BookingOutcome("failed", sent=False),
            "BOOKING_FAILED",
            "No he podido contactar con Gimnasio Sur; no se ha enviado nada.",
        ),
        (
            lambda _: BookingOutcome("unknown"),
            "BOOKING_UNKNOWN",
            "No sé si la reserva se ha completado; consulta en Gimnasio Sur.",
        ),
    ],
    ids=[
        "booked",
        "alternatives",
        "alternatives-two-days",
        "link",
        "unsupported",
        "unavailable",
        "failed",
        "not-sent",
        "unknown",
    ],
)
async def test_a_yes_on_screen_confirms_once_and_closes_the_booking(
    outcome: Callable[[BookingConfirmation], BookingOutcome], expected: str, text: str
) -> None:
    bookings = FakeBookings(outcome)

    answer = await reply("sí", who=BOOKER, was=pending(), bookings=bookings)

    assert answer is not None and (answer.outcome, answer.text) == (expected, text)
    assert bookings.confirmed == [PENDING]
    assert answer.conversation is not None and answer.conversation.workflow is None


async def test_alternatives_become_the_new_offers_sealed() -> None:
    was = pending()
    answer = await reply("sí", who=BOOKER, was=was, bookings=FakeBookings(alternatives))

    assert answer is not None and answer.conversation is not None
    assert [f"{slot.start:%H:%M}" for slot in answer.activities] == ["20:00", "21:00"]
    assert answer.suggestions == ["Apúntame a la de las 20:00", "Apúntame a la de las 21:00"]
    offers = answer.conversation.activity_offers
    assert offers is not None and offers.token is not None
    assert [f"{slot.start:%H:%M}" for slot in offers.slots] == ["20:00", "21:00"]
    # Ruling R28: the names the holder was shown stay with the alternatives.
    assert [(name.agent_id, name.name) for name in offers.agent_names] == [
        ("gym-sur", "Gimnasio Sur"),
        ("gym-norte", "Gimnasio Norte"),
    ]
    assert [(ctx.agent_id, ctx.context_id) for ctx in offers.contexts] == [("gym-sur", "ctx-sur")]
    assert (
        CODEC.verify_offers(
            offers.token,
            HOLDER,
            was.conversation_id,
            conversation_mapper.offers_semantics(offers),
        )
        == "ok"
    )


async def test_a_gym_asking_for_linking_leaves_a_notice_with_its_link() -> None:
    answer = await reply(
        "sí",
        who=BOOKER,
        was=pending(),
        bookings=FakeBookings(lambda _: BookingOutcome("link_required", link=LINK)),
    )

    assert answer is not None
    assert [notice.model_dump() for notice in answer.agent_notices] == [
        {"agent_name": "Gimnasio Sur", "kind": "link_required", "link": LINK}
    ]


def verified_echo(answer: QuestionResponse) -> Conversation:
    """The conversation an answer left, as the next turn's `_conversation_of` reads it back."""
    assert answer.conversation is not None
    echoed = answer.conversation.model_validate(answer.conversation.model_dump(mode="json"))
    return conversation_mapper.to_domain(
        echoed,
        verify_offers=lambda token, conversation_id, semantics: CODEC.verify_offers(
            token, HOLDER, conversation_id, semantics
        ),
    )


async def test_a_booking_whose_outcome_is_unknown_is_no_longer_offered() -> None:
    """Ruling R17: after «no sé si la reserva se ha completado», that class leaves the sealed
    offers, so booking it again takes a new search -- where the gym's own timetable says whether
    it is still free -- and these offers can never mint a second message for it."""
    bookings = FakeBookings(lambda _: BookingOutcome("unknown"))
    unknown = await reply("sí", who=BOOKER, was=pending(), bookings=bookings)
    assert unknown is not None and unknown.outcome == "BOOKING_UNKNOWN"

    after = verified_echo(unknown)

    assert after.activity_offers is not None and after.activity_offers.trusted
    assert after.activity_offers.slots == (NORTE_1900,)
    # Ruling R28: the Sur still has its name -- «en el Sur» is a gym, not a word to ignore.
    assert after.activity_offers.agent_names == OFFERS.agent_names
    assert after.activity_offers.contexts == (("gym-norte", "ctx-norte"),)
    again = await reply("apúntame a la de las 18:30", who=BOOKER, was=after, bookings=bookings)
    assert again is not None and again.outcome == "BOOKING_NOT_OFFERED"
    assert bookings.confirmed == [PENDING] and bookings.gateway.sent == []


async def test_the_last_offer_gone_unknown_leaves_a_search_to_ask_for() -> None:
    alone = ActivityOffers((SUR_1830,), (("gym-sur", "Gimnasio Sur"),), (("gym-sur", "ctx-sur"),))
    bookings = FakeBookings(lambda _: BookingOutcome("unknown"))
    unknown = await reply("sí", who=BOOKER, was=pending(offers=alone), bookings=bookings)
    assert unknown is not None and unknown.conversation is not None
    assert unknown.conversation.activity_offers is None

    again = await reply(
        "apúntame a la de las 18:30", who=BOOKER, was=verified_echo(unknown), bookings=bookings
    )

    assert again is not None and again.outcome == "BOOKING_SEARCH_AGAIN"
    assert bookings.confirmed == [PENDING]


async def test_a_gym_that_said_no_leaves_its_class_offered() -> None:
    """`failed` is only the gym's own refusal (or a failure before anything was sent): the class
    is still there to be asked for, as the search found it."""
    bookings = FakeBookings(lambda _: BookingOutcome("failed"))
    failed = await reply("sí", who=BOOKER, was=pending(), bookings=bookings)
    assert failed is not None and failed.outcome == "BOOKING_FAILED"

    after = verified_echo(failed)
    again = await reply("apúntame a la de las 18:30", who=BOOKER, was=after, bookings=bookings)

    assert after.activity_offers is not None and after.activity_offers.slots == OFFERS.slots
    assert again is not None and again.outcome == "BOOKING_AWAITING_CONFIRMATION"


async def test_a_yes_by_voice_is_sent_to_the_screen() -> None:  # §14 A10
    bookings = FakeBookings()
    was = pending()

    answer = await reply("sí", who=BOOKER, was=was, bookings=bookings, channel="VOICE")

    assert answer is not None
    assert (answer.outcome, answer.text) == (
        "BOOKING_ON_SCREEN",
        "Confirma la reserva en la pantalla.",
    )
    assert answer.conversation == conversation_mapper.to_payload(was), "the booking waits on"
    assert bookings.sent_nothing


async def test_a_yes_without_agents_book_books_nothing() -> None:
    bookings = FakeBookings()
    was = pending()

    answer = await reply("sí", who=ASKER, was=was, bookings=bookings)

    assert answer is not None and answer.outcome == "EXPERT_AGENTS_FORBIDDEN"
    assert answer.text == "Tu acceso no permite reservar con agentes externos."
    assert answer.conversation == conversation_mapper.to_payload(was)
    assert bookings.sent_nothing


async def test_a_no_cancels_the_booking_and_sends_nothing() -> None:
    bookings = FakeBookings()

    answer = await reply("no", who=BOOKER, was=pending(), bookings=bookings)

    assert answer is not None
    assert (answer.outcome, answer.text) == ("BOOKING_CANCELLED", "De acuerdo, no reservo nada.")
    assert answer.conversation is not None and answer.conversation.workflow is None
    assert answer.conversation.activity_offers is not None, "the offers stay"
    assert bookings.sent_nothing


@pytest.mark.parametrize(
    "sentence", ["sí pero a las 20:00", "¿cuánto tengo?", "apúntame a la de las 19:00", "gracias"]
)
async def test_anything_else_repeats_the_preview_and_waits(sentence: str) -> None:
    bookings = FakeBookings()
    searches = FakeSearches()
    was = pending()

    answer = await reply(sentence, who=BOOKER, was=was, bookings=bookings, searches=searches)

    assert answer is not None
    assert (answer.outcome, answer.text) == ("BOOKING_UNCLEAR", f"{PREVIEW} Responde sí o no.")
    assert answer.conversation == conversation_mapper.to_payload(was)
    assert bookings.sent_nothing and searches.queries == []


@pytest.mark.parametrize(
    "was",
    [
        pending(offers=replace(OFFERS, trusted=False)),
        replace(pending(), activity_offers=None),
        pending(replace(PENDING, slot=replace(SUR_1830, free_places=60))),
        pending(replace(PENDING, agent_name="Gimnasio Falso")),
        pending(replace(PENDING, context_id="ctx-other")),
    ],
    ids=["untrusted-offers", "no-offers", "retimed-slot", "renamed", "recontexted"],
)
async def test_a_booking_the_offers_do_not_vouch_for_is_never_sent(was: Conversation) -> None:
    """The pending booking travels unsealed beside the sealed offers, so a yes sends only a class
    those offers still hold, under the name and context they hold it with."""
    bookings = FakeBookings()

    answer = await reply("sí", who=BOOKER, was=was, bookings=bookings)

    assert answer is not None
    assert (answer.outcome, answer.text) == (
        "BOOKING_SEARCH_AGAIN",
        "No puedo usar esa búsqueda; vuelve a pedirla.",
    )
    assert answer.conversation is not None and answer.conversation.workflow is None
    assert bookings.sent_nothing


async def test_a_class_that_started_after_its_preview_is_not_sent() -> None:
    bookings = FakeBookings(now=datetime(2026, 10, 1, 18, 31, tzinfo=ZONE))

    answer = await reply("sí", who=BOOKER, was=pending(), bookings=bookings)

    assert answer is not None
    assert (answer.outcome, answer.text) == ("BOOKING_STARTED", "Esa clase ya ha empezado.")
    assert answer.conversation is not None and answer.conversation.workflow is None
    assert bookings.sent_nothing


async def test_a_yes_with_no_gateway_closes_a_booking_nothing_can_send() -> None:
    answer = await reply("sí", who=BOOKER, was=pending(), configured=False)

    assert answer is not None and answer.outcome == "EXPERT_AGENTS_OFF"
    assert answer.conversation is not None and answer.conversation.workflow is None


# --- both routes (A11) ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Any:
    before = getattr(app.state, "expert_searches", None)
    booking_before = getattr(app.state, "expert_bookings", None)
    enabled = app.state.questions_enabled
    app.state.expert_searches = FakeSearches()
    app.state.expert_bookings = FakeBookings()
    app.state.questions_enabled = True
    app.dependency_overrides[authenticated_caller] = lambda: BOOKER
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.expert_searches = before
    app.state.expert_bookings = booking_before
    app.state.questions_enabled = enabled


def events_of(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_the_question_route_answers_an_agent_request_with_the_helper(client: TestClient) -> None:
    response = client.post("/v1/questions", json={"question": SENTENCE})

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "ACTIVITIES_FOUND"
    assert [slot["start"] for slot in body["activities"]] == [
        "2026-10-01T18:30:00+02:00",
        "2026-10-01T19:00:00+02:00",
    ]
    assert body["conversation"]["activity_offers"]["token"].startswith("o1.")


def test_the_streaming_route_answers_an_agent_request_with_the_helper(client: TestClient) -> None:
    response = client.post("/v1/questions/stream", json={"question": SENTENCE})

    assert response.status_code == 200
    [answer] = events_of(response)
    assert answer["type"] == "answer" and answer["outcome"] == "ACTIVITIES_FOUND"
    assert answer["conversation"]["activity_offers"]["token"].startswith("o1.")
    # The frame is the JSON the other route sends, not `str()` of a datetime: the web reads the
    # slot's time off the ISO string.
    assert answer["activities"][0]["start"] == "2026-10-01T18:30:00+02:00"
    assert answer["activities"][1]["end"] == "2026-10-01T19:45:00+02:00"
    offered = answer["conversation"]["activity_offers"]["slots"]
    assert offered[0]["start"] == "2026-10-01T18:30:00+02:00"

    # The conversation it left, sealed offers and all, is one the route accepts on the next turn.
    echoed = client.post(
        "/v1/questions/stream",
        json={"question": "¿hay yoga mañana?", "conversation": answer["conversation"]},
    )
    assert echoed.status_code == 200
    assert events_of(echoed)[0]["outcome"] == "ACTIVITIES_FOUND"


def test_a_route_without_a_gateway_says_the_capability_does_not_exist(client: TestClient) -> None:
    app.state.expert_searches = None
    for route in ("/v1/questions", "/v1/questions/stream"):
        response = client.post(route, json={"question": SENTENCE})
        answer = events_of(response)[0] if route.endswith("stream") else response.json()
        assert answer["outcome"] == "EXPERT_AGENTS_OFF"


def _answer_of(client: TestClient, route: str, question: str, conversation: Any = None) -> Any:
    body: dict[str, Any] = {"question": question}
    if conversation is not None:
        body["conversation"] = conversation
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    return events_of(response)[0] if route.endswith("stream") else response.json()


@pytest.mark.parametrize("route", ["/v1/questions", "/v1/questions/stream"])
def test_a_search_a_booking_and_a_yes_go_through_either_route(
    client: TestClient, route: str
) -> None:
    """The whole booking over HTTP: the sealed offers the search left are verified on the way back
    in (`_conversation_of`), the preview is prepared from them, and the yes is sent once."""
    searched = _answer_of(client, route, SENTENCE)
    prepared = _answer_of(client, route, "apúntame a la de las 18:30", searched["conversation"])

    assert prepared["outcome"] == "BOOKING_AWAITING_CONFIRMATION"
    assert prepared["conversation"]["workflow"] == "BOOKING_CONFIRMATION"
    assert prepared["conversation"]["booking_slot"]["start"] == "2026-10-01T18:30:00+02:00"

    done = _answer_of(client, route, "sí", prepared["conversation"])

    assert done["outcome"] == "BOOKED"
    assert done["text"].endswith("Código SUR-7731.")
    assert done["conversation"]["workflow"] is None
    assert len(app.state.expert_bookings.confirmed) == 1


def _new_free_places(offers: dict[str, Any]) -> None:
    offers["slots"][0]["free_places"] = 60


def _forged_token(offers: dict[str, Any]) -> None:
    offers["token"] = "o1.AAAA.BBBB"


@pytest.mark.parametrize("route", ["/v1/questions", "/v1/questions/stream"])
@pytest.mark.parametrize("tamper", [_new_free_places, _forged_token], ids=["places", "token"])
def test_offers_tampered_in_the_browser_cannot_be_booked(
    client: TestClient, route: str, tamper: Callable[[dict[str, Any]], None]
) -> None:
    """Task 4's review: `to_domain` trusts offers when no verifier is passed. This route passes
    one, so an echo edited in the browser is offers nobody sealed, and a booking asks for the
    search again instead of reading them."""
    searched = _answer_of(client, route, SENTENCE)
    echoed = json.loads(json.dumps(searched["conversation"]))
    tamper(echoed["activity_offers"])

    answer = _answer_of(client, route, "apúntame a la de las 18:30", echoed)

    assert answer["outcome"] == "BOOKING_SEARCH_AGAIN"
    assert app.state.expert_bookings.confirmed == []


# --- the guard: every booking reads offers `_conversation_of` verified --------------------------

_INFRASTRUCTURE = Path(__file__).resolve().parents[1] / "src" / "finguard_orchestrator"


def _name_of(call: ast.Call) -> str | None:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _own_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Every node of `function`'s body, and none of a function nested inside it."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        )
    return found


def _functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path in sorted((_INFRASTRUCTURE / "infrastructure").rglob("*.py")):
        relative = path.relative_to(_INFRASTRUCTURE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        found.extend(
            (relative, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )
    return found


def _passes(call: ast.Call, keyword: str) -> bool:
    """`keyword=` given a value -- a literal `None` does not count as given."""
    return any(
        item.arg == keyword
        and not (isinstance(item.value, ast.Constant) and item.value.value is None)
        for item in call.keywords
    )


def test_every_conversation_a_booking_reads_had_its_offers_verified() -> None:
    """`to_domain(verify_offers=None)` trusts whatever offers were echoed (Task 4's review), so a
    booking resolved against `ActivityOffers.trusted` is only as safe as the call that built the
    conversation it reads. Two facts, walked over `infrastructure/`:

    1. `to_domain` binds `verify_offers=` in `_conversation_of`, the one HTTP boundary, and nowhere
       else is it called without one -- except `_compound_read_reply`, whose result is read for
       its `visual_contexts` and nothing else, so no offers it builds reach anything.
    2. Every call of `expert_agent_reply`, the booking path's one entry, hands it a conversation
       that is `_conversation_of`'s, or derived from it by its own methods.

    A new call site of either fails here until someone decides which shape it is.
    """
    verified: set[str] = set()
    unverified: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]] = []
    entries: list[tuple[str, str]] = []
    for relative, function in _functions():
        nodes = _own_nodes(function)
        for call in (node for node in nodes if isinstance(node, ast.Call)):
            if _name_of(call) == "to_domain":
                if _passes(call, "verify_offers"):
                    verified.add(function.name)
                else:
                    unverified.append((relative, function, call))
            if _name_of(call) == "expert_agent_reply":
                entries.append((function.name, _conversation_argument(call, function, nodes)))

    assert verified == {"_conversation_of"}
    assert [(relative, function.name) for relative, function, _ in unverified] == [
        ("infrastructure/controllers/llm_controller.py", "_compound_read_reply")
    ]
    [(_, compound, call)] = unverified
    assert _read_only_for_visual_contexts(compound, call)
    assert sorted(entries) == [
        ("ask", "_conversation_of"),
        ("ask_streaming", "_conversation_of"),
    ]


def _conversation_argument(
    call: ast.Call, function: ast.FunctionDef | ast.AsyncFunctionDef, nodes: list[ast.AST]
) -> str:
    """Where the conversation handed to `expert_agent_reply` comes from: the one function every
    assignment to it calls, when each is that function or one of the conversation's own methods."""
    argument = call.args[2] if len(call.args) > 2 else None
    argument = argument or next((item.value for item in call.keywords if item.arg == "was"), None)
    assert isinstance(argument, ast.Name), ast.dump(call)
    sources: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == argument.id for t in node.targets):
            continue
        value = node.value
        assert isinstance(value, ast.Call), f"{function.name}: {ast.dump(value)}"
        on_itself = (
            isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == argument.id
        )
        if not on_itself:
            sources.add(_name_of(value) or "?")
    assert len(sources) == 1, (function.name, sources)
    return sources.pop()


def _read_only_for_visual_contexts(
    function: ast.FunctionDef | ast.AsyncFunctionDef, call: ast.Call
) -> bool:
    parents = {
        child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
    }
    assignment = parents[call]
    assert isinstance(assignment, ast.Assign) and len(assignment.targets) == 1
    [target] = assignment.targets
    assert isinstance(target, ast.Name)
    reads = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == target.id and isinstance(node.ctx, ast.Load)
    ]
    uses = [parents[read] for read in reads]
    return bool(uses) and all(
        isinstance(use, ast.Attribute) and use.attr == "visual_contexts" for use in uses
    )


# --- the composition root ------------------------------------------------------------------------

# `base64.urlsafe_b64encode(b"x" * 32)`, as `ORCHESTRATOR_METRICS_SIGNING_KEYS` expects it.
_SIGNING_KEYS = '["default:eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="]'
_PUBLIC = '["https://app.example.com"]'


def _create_app_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    """`create_app` under `env`, with its logging left alone so `caplog` sees the warnings."""
    monkeypatch.setattr(bootstrap_main, "configure_logging", lambda **kwargs: None)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


def test_without_a_gateway_url_no_search_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _create_app_with(monkeypatch, ORCHESTRATOR_A2A_GATEWAY_URL="")

    assert built.state.expert_searches is None
    assert built.state.expert_bookings is None


def test_a_gateway_on_a_public_origin_needs_a_pairwise_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signing keys' refusal, for the key every `memberRef` is derived under: on the
    development key anybody who reads this repository can join a person across gyms."""
    with pytest.raises(ValueError, match="ORCHESTRATOR_A2A_PAIRWISE_KEY.*non-loopback"):
        _create_app_with(
            monkeypatch,
            ORCHESTRATOR_A2A_GATEWAY_URL="http://a2a-gateway:8000",
            ORCHESTRATOR_METRICS_SIGNING_KEYS=_SIGNING_KEYS,
            ORCHESTRATOR_WEB_ORIGINS=_PUBLIC,
        )


def test_a_gateway_with_its_keys_builds_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _create_app_with(
        monkeypatch,
        ORCHESTRATOR_A2A_GATEWAY_URL="http://a2a-gateway:8000",
        ORCHESTRATOR_A2A_GATEWAY_TOKEN="a-real-gateway-token",
        ORCHESTRATOR_A2A_PAIRWISE_KEY="a-real-pairwise-key-of-thirty-two-bytes",
        ORCHESTRATOR_METRICS_SIGNING_KEYS=_SIGNING_KEYS,
        ORCHESTRATOR_WEB_ORIGINS=_PUBLIC,
    )

    assert isinstance(built.state.expert_searches, SearchActivitiesHandler)
    # Beside the search, on the same gateway, classifier and normaliser.
    assert isinstance(built.state.expert_bookings, BookActivityHandler)


def test_a_gateway_on_loopback_runs_on_the_development_keys_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="finguard_orchestrator.bootstrap.main"):
        built = _create_app_with(monkeypatch, ORCHESTRATOR_A2A_GATEWAY_URL="http://127.0.0.1:8003")

    assert isinstance(built.state.expert_searches, SearchActivitiesHandler)
    said = " ".join(record.message for record in caplog.records)
    assert "ORCHESTRATOR_A2A_PAIRWISE_KEY" in said and "ORCHESTRATOR_A2A_GATEWAY_TOKEN" in said


NORTE_2000 = ActivitySlot(
    "gym-norte", "Spinning", datetime(2026, 10, 1, 20, 0, tzinfo=ZONE), None, 0, None
)


async def test_a_class_at_a_gym_that_offered_nothing_is_not_another_gyms() -> None:
    """Acceptance finding A3 (ruling R28): the Sur was asked and offered nothing; «la de las
    20:00 en el Sur» is not the Norte's 20:00."""
    only_norte = ActivityOffers(
        (NORTE_1900, NORTE_2000), OFFERS.agent_names, (("gym-norte", "ctx-norte"),)
    )
    bookings = FakeBookings()

    answer = await reply(
        "apúntame a la de las 20:00 en el Sur", was=offering(only_norte), bookings=bookings
    )

    assert answer is not None and answer.outcome == "BOOKING_NOT_OFFERED"
    assert bookings.sent_nothing


async def test_alternatives_after_a_booking_keep_every_name_the_offers_had() -> None:
    """Ruling R28: after the Sur's alternatives, «en el Norte» still names a gym."""
    answer = await reply("sí", who=BOOKER, was=pending(), bookings=FakeBookings(alternatives))
    after = verified_echo(answer)  # type: ignore[arg-type]

    again = await reply(
        "apúntame a la de las 20:00 en el Norte", who=BOOKER, was=after, bookings=FakeBookings()
    )

    assert again is not None and again.outcome == "BOOKING_NOT_OFFERED"


async def test_a_booked_class_is_no_longer_offered() -> None:
    """Ruling R30: like a booking whose outcome is unknown (R17), a booked class leaves the sealed
    offers -- «apúntame a la de las 18:30» again must not mint a second booking for it."""
    bookings = FakeBookings()
    done = await reply("sí", who=BOOKER, was=pending(), bookings=bookings)
    assert done is not None and done.outcome == "BOOKED"

    after = verified_echo(done)
    assert after.activity_offers is not None and SUR_1830 not in after.activity_offers.slots
    again = await reply("apúntame a la de las 18:30", who=BOOKER, was=after, bookings=bookings)

    assert again is not None and again.outcome == "BOOKING_NOT_OFFERED"
    assert bookings.confirmed == [PENDING] and bookings.gateway.sent == []
