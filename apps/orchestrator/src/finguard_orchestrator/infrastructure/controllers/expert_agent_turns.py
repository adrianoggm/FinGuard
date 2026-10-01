"""The expert-agent turns: one helper, called from both question routes (spec §14 A11).

The web types into `/v1/questions/stream` and everything else posts to `/v1/questions`, so a branch
wired into one route only is a branch half the conversations never reach -- the lesson every
payment branch in `llm_controller` repeats. Both routes call `expert_agent_reply` at the same point,
after the payment branches that own their turns and before the visual and financial paths, and
return its answer when it has one.

Three branches, in this order, and `None` for every turn none of them claims -- the caller then
continues exactly as it did before this existed:

    1. a booking waiting for a yes owns the turn, whatever its words, like a payment preview: a
       yes on screen sends it once, a no drops it, a yes by voice is sent to the screen (§14 A10),
       and anything else repeats the preview;
    2. a booking request (`is_a_booking_request`) is prepared against the sealed offers, and
       nothing is sent;
    3. a timetable question (`asks_an_expert_agent`, §14 A1) is searched.

The offers a booking reads are the ones `llm_controller._conversation_of` verified: an echo whose
seal does not check out arrives untrusted and is never booked from (§5.3). Every sentence is
`activity_writer`'s, every value a verified one, and the offers leave sealed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Final
from uuid import uuid4

from finguard_orchestrator.application.commands.book_activity import (
    BookActivityHandler,
    BookingOutcome,
    BookingOutcomeKind,
)
from finguard_orchestrator.application.queries.search_activities import (
    ActivitySearch,
    SearchActivitiesHandler,
    SearchActivitiesQuery,
)
from finguard_orchestrator.domain.constants import MAX_EXPERT_AGENTS
from finguard_orchestrator.domain.models.conversation import (
    ActivityOffers,
    BookingConfirmation,
    Conversation,
    PaymentInProgress,
)
from finguard_orchestrator.domain.models.expert_agents import ActivityQuery, ActivitySlot
from finguard_orchestrator.domain.services import activity_writer, confirmation
from finguard_orchestrator.domain.services.expert_agent_words import (
    asks_an_expert_agent,
    is_a_booking_request,
)
from finguard_orchestrator.domain.services.slot_reference import SlotAmbiguous, SlotNotOffered
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    ActivitySlotPayload,
    AgentNoticePayload,
    ConversationPayload,
    QuestionRequest,
    QuestionResponse,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

# A refusal about what the sentence asked, as opposed to who could answer it.
_UNCLEAR: Final = frozenset({"no_activity", "no_window"})

# What a sent booking came to, by the name the turn answers with.
_BOOKED: Final[Mapping[BookingOutcomeKind, str]] = {
    "booked": "BOOKED",
    "alternatives": "BOOKING_ALTERNATIVES",
    "link_required": "BOOKING_LINK_REQUIRED",
    "unsupported_request": "BOOKING_UNSUPPORTED",
    "unavailable": "BOOKING_UNAVAILABLE",
    "failed": "BOOKING_FAILED",
    "unknown": "BOOKING_UNKNOWN",
}


async def expert_agent_reply(
    request: QuestionRequest,
    who: Caller,
    was: Conversation,
    *,
    searches: SearchActivitiesHandler | None,
    bookings: BookActivityHandler | None,
    codec: MetricTokenCodec,
) -> QuestionResponse | None:
    """The answer to an expert-agent turn, or `None` when this is not one (module doc).

    `searches` and `bookings` are `None` when no gateway is configured: the capability does not
    exist and says so (spec §3.2).
    """
    if isinstance(was.active, BookingConfirmation):
        return await _answering(request, who, was, was.active, bookings, codec)
    if is_a_booking_request(request.question, has_offers=was.activity_offers is not None):
        return _preparing(request, who, was, bookings, codec)
    if not asks_an_expert_agent(request.question):
        return None
    if searches is None:
        return _said("EXPERT_AGENTS_OFF", activity_writer.off(), was)
    if not who.may(Scope.AGENTS_ASK):
        return _said("EXPERT_AGENTS_FORBIDDEN", activity_writer.forbidden(), was)
    search = await searches.handle(SearchActivitiesQuery(request.question, who))
    if search.refusal is not None or search.query is None:
        refusal = search.refusal or "no_activity"
        outcome = "ACTIVITY_UNCLEAR" if refusal in _UNCLEAR else "AGENTS_UNAVAILABLE"
        return _said(outcome, activity_writer.unclear(refusal), was)
    return _searched(search, search.query, who, was, codec)


# --- 2. a booking request: prepared, nothing sent ---------------------------------------------


def _preparing(
    request: QuestionRequest,
    who: Caller,
    was: Conversation,
    bookings: BookActivityHandler | None,
    codec: MetricTokenCodec,
) -> QuestionResponse:
    """A preview of the class the sentence points at, or why there is none. Nothing leaves.

    Only `agents:ask`: a preview is a reading of offers a search already brought back, and the
    booking itself waits for `agents:book` and a yes (§14 A4).
    """
    if bookings is None:
        return _said("EXPERT_AGENTS_OFF", activity_writer.off(), was)
    if not who.may(Scope.AGENTS_ASK):
        return _said("EXPERT_AGENTS_FORBIDDEN", activity_writer.forbidden(), was)
    offers = was.activity_offers
    if offers is None or not offers.trusted:
        return _said("BOOKING_SEARCH_AGAIN", activity_writer.search_again(), was)
    prepared = bookings.prepare(request.question, offers, now=bookings.now(who))
    if isinstance(prepared, SlotAmbiguous):
        names = dict(offers.agent_names)
        return QuestionResponse(
            outcome="BOOKING_AMBIGUOUS",
            text=activity_writer.which_booking(prepared.candidates, names),
            conversation=conversation_mapper.to_payload(was),
            suggestions=activity_writer.booking_suggestions(prepared.candidates, names),
        )
    if isinstance(prepared, SlotNotOffered):
        return _said("BOOKING_NOT_OFFERED", activity_writer.not_offered(), was)
    if not isinstance(prepared, BookingConfirmation):  # "started" (Review Focus 3)
        return _said("BOOKING_STARTED", activity_writer.started(), was)
    if isinstance(was.active, PaymentInProgress):
        # Ruling R18: one workflow is active at a time, and a preview would replace a half-built
        # payment without a word -- the search keeps one by suspending it, a preview cannot. So no
        # preview: the payment is named, set aside as a search would set it aside, and kept.
        return _said(
            "BOOKING_PAYMENT_PAUSED",
            activity_writer.booking_after_payment(was.active.draft),
            was.suspend(),
        )
    # A preview is a question like a payment's: it replaces whatever else was open (a payment under
    # way excepted, above), and ends the one-turn payment referent.
    after = was.without_recent_payment().with_active(prepared)
    return QuestionResponse(
        outcome="BOOKING_AWAITING_CONFIRMATION",
        text=activity_writer.booking_preview(prepared.slot, prepared.agent_name),
        conversation=_sealed(after, who, codec),
    )


# --- 1. a booking waiting for a yes ------------------------------------------------------------


async def _answering(
    request: QuestionRequest,
    who: Caller,
    was: Conversation,
    pending: BookingConfirmation,
    bookings: BookActivityHandler | None,
    codec: MetricTokenCodec,
) -> QuestionResponse:
    """The reply to a preview, read by the payments' own closed reader (`confirmation`).

    Only a yes typed on screen, by a credential with `agents:book`, for a class the verified
    offers still hold and that has not started, reaches `confirm` -- and `confirm` sends once.
    """
    reply = request.question
    if confirmation.is_refusal(reply):
        return _closed("BOOKING_CANCELLED", activity_writer.booking_cancelled(), was, who, codec)
    if not confirmation.is_confirmation(reply):
        text = activity_writer.booking_unclear(pending.slot, pending.agent_name)
        return _said("BOOKING_UNCLEAR", text, was)
    if request.channel == "VOICE":
        # §14 A10: voice may search and prepare, never confirm. The booking waits on screen.
        return _said("BOOKING_ON_SCREEN", activity_writer.on_screen(), was)
    if bookings is None:
        return _closed("EXPERT_AGENTS_OFF", activity_writer.off(), was, who, codec)
    if not who.may(Scope.AGENTS_BOOK):
        return _said("EXPERT_AGENTS_FORBIDDEN", activity_writer.booking_forbidden(), was)
    if not _vouched_for(pending, was.activity_offers):
        return _closed("BOOKING_SEARCH_AGAIN", activity_writer.search_again(), was, who, codec)
    if pending.slot.start <= bookings.now(who):
        return _closed("BOOKING_STARTED", activity_writer.started(), was, who, codec)
    outcome = await bookings.confirm(pending, who)
    return _sent(outcome, pending, was.with_active(None), who, codec)


def _vouched_for(pending: BookingConfirmation, offers: ActivityOffers | None) -> bool:
    """Whether the verified offers still hold this class, under this name and this context.

    The pending booking travels beside the offers unsealed, so it is believed only as far as the
    seal reaches: a slot the browser edited, or one whose offers no longer verify, is a class
    nobody was shown, and nothing is sent for it.
    """
    agent_id = pending.slot.agent_id
    return (
        offers is not None
        and offers.trusted
        and pending.slot in offers.slots
        and offers.name_of(agent_id) == pending.agent_name
        and dict(offers.contexts).get(agent_id) == pending.context_id
    )


def _sent(
    outcome: BookingOutcome,
    pending: BookingConfirmation,
    after: Conversation,
    who: Caller,
    codec: MetricTokenCodec,
) -> QuestionResponse:
    """What a booking sent once came to. The booking is closed whatever it was: a gym asking
    for linking, offering other classes or not answering is not asked again (§5.2, §7)."""
    name = pending.agent_name
    slots: tuple[ActivitySlot, ...] = ()
    notices: list[AgentNoticePayload] = []
    match outcome.kind:
        case "booked" if outcome.receipt is not None:
            text = activity_writer.booked(outcome.receipt, name)
            # Booked, it is not on offer any more (ruling R30, as R17 after `unknown`): asking for
            # it again takes a new search, never a second message for the same class.
            after = replace(after, activity_offers=_without(after.activity_offers, pending.slot))
        case "alternatives" if outcome.alternatives:
            slots = outcome.alternatives
            text = activity_writer.booking_alternatives(pending.slot, name, slots)
            # The alternatives are what this gym offers now: the next «apúntame» reads them -- under
            # every name the last offers carried (ruling R28), so «en el Norte» still names a gym.
            contexts = ((pending.slot.agent_id, pending.context_id),) if pending.context_id else ()
            names = _names(after.activity_offers, pending.slot.agent_id, name)
            offers = ActivityOffers(slots, names, contexts)
            after = replace(after, activity_offers=offers)
        case "link_required":
            text = activity_writer.booking_link_required(name)
            notices = [AgentNoticePayload(agent_name=name, kind="link_required", link=outcome.link)]
        case "unsupported_request":
            text = activity_writer.booking_unsupported(name)
        case "unavailable":
            text = activity_writer.booking_unavailable(name)
        case "unknown":
            text = activity_writer.booking_unknown(name)
            # Ruling R17: the class may be booked. It leaves the offers, so booking it again takes
            # a new search -- where the gym's own timetable says whether it is still free -- and
            # these offers can never mint a second message for it.
            after = replace(after, activity_offers=_without(after.activity_offers, pending.slot))
        case _:
            text = activity_writer.booking_failed(name)
    return QuestionResponse(
        outcome=_BOOKED[outcome.kind],
        text=text,
        conversation=_sealed(after, who, codec),
        activities=_slot_payloads(slots, {pending.slot.agent_id: name}),
        agent_notices=notices,
        suggestions=activity_writer.choose_suggestions(slots),
    )


def _without(offers: ActivityOffers | None, slot: ActivitySlot) -> ActivityOffers | None:
    """`offers` less `slot`, with the context of any gym left offering nothing dropped too -- but
    not its name (ruling R28): «en el Sur» still names the Sur. `None` once nothing is left."""
    if offers is None:
        return None
    kept = tuple(each for each in offers.slots if each != slot)
    if not kept:
        return None
    offering = {each.agent_id for each in kept}
    return ActivityOffers(
        kept,
        offers.agent_names,
        tuple(item for item in offers.contexts if item[0] in offering),
        trusted=offers.trusted,
    )


def _names(offers: ActivityOffers | None, agent_id: str, name: str) -> tuple[tuple[str, str], ...]:
    """The names `offers` carried, `agent_id`'s under `name` -- first when they did not carry it,
    so the cap can never leave the offering gym unnamed."""
    carried = offers.agent_names if offers is not None else ()
    if any(each == agent_id for each, _ in carried):
        return tuple((each, name if each == agent_id else shown) for each, shown in carried)
    return ((agent_id, name), *carried)[:MAX_EXPERT_AGENTS]


# --- 3. a search -------------------------------------------------------------------------------


def _searched(
    search: ActivitySearch,
    query: ActivityQuery,
    who: Caller,
    was: Conversation,
    codec: MetricTokenCodec,
) -> QuestionResponse:
    notices = [outcome for outcome in search.outcomes if outcome.kind != "answered"]
    names = {outcome.agent_id: outcome.agent_name for outcome in search.outcomes}
    if search.slots:
        outcome = "ACTIVITIES_FOUND"
        text = activity_writer.offers(search.slots, notices, names, activity=query.activity)
    elif any(part.kind == "answered" for part in search.outcomes):
        outcome = "ACTIVITIES_NONE"
        sentences = map(activity_writer.notice, notices)
        text = " ".join([activity_writer.none_found(query.activity, query), *sentences])
    else:
        outcome = "AGENTS_UNAVAILABLE"
        text = activity_writer.unavailable(notices)
    # A search is a turn about something else: a payment under way steps aside, a question left
    # open is closed, the one-turn payment referent ends, and these offers replace the last ones
    # -- none at all when this search found none.
    after = replace(
        was.suspend().without_recent_payment(),
        last_asked=None,
        activity_offers=search.offers,
    )
    return QuestionResponse(
        outcome=outcome,
        text=text,
        conversation=_sealed(after, who, codec),
        activities=_slot_payloads(search.slots, names),
        # Only the agents that were sent something (ruling R16): a named gym never asked is not
        # one that «no ha respondido de forma válida», and the text already says why it wasn't.
        agent_notices=[
            AgentNoticePayload.model_validate(
                {"agent_name": notice.agent_name, "kind": notice.kind, "link": notice.link}
            )
            for notice in notices
            if notice.asked
        ],
        suggestions=activity_writer.choose_suggestions(search.slots),
    )


# --- shared ------------------------------------------------------------------------------------


def _said(outcome: str, text: str, was: Conversation) -> QuestionResponse:
    """A turn that changed nothing: the conversation is left exactly as it was, offers included --
    still sealed when they verified, still unusable when they did not."""
    return QuestionResponse(
        outcome=outcome, text=text, conversation=conversation_mapper.to_payload(was)
    )


def _closed(
    outcome: str, text: str, was: Conversation, who: Caller, codec: MetricTokenCodec
) -> QuestionResponse:
    """A turn that closes the pending booking and changes nothing else."""
    return QuestionResponse(
        outcome=outcome, text=text, conversation=_sealed(was.with_active(None), who, codec)
    )


def _slot_payloads(
    slots: Sequence[ActivitySlot], names: Mapping[str, str]
) -> list[ActivitySlotPayload]:
    return [
        ActivitySlotPayload(
            agent_id=slot.agent_id,
            agent_name=names.get(slot.agent_id, slot.agent_id),
            activity=slot.activity,
            start=slot.start,
            end=slot.end,
            free_places=slot.free_places,
            slot_ref=slot.slot_ref,
        )
        for slot in slots
    ]


def _sealed(
    conversation: Conversation, who: Caller, codec: MetricTokenCodec
) -> ConversationPayload:
    """The conversation's wire shape with its offers sealed for this holder and conversation.

    Only offers this service trusts: ones a search or a booking just built, or ones whose echoed
    seal verified. Sealing an untrusted echo would launder offers nobody vouched for into a seal
    that verifies, so those go out as they came in -- tokenless, and unusable next turn too.
    """
    conversation_id = conversation.conversation_id or uuid4()
    payload = conversation_mapper.to_payload(replace(conversation, conversation_id=conversation_id))
    offers = conversation.activity_offers
    if offers is not None and not offers.trusted:
        return payload
    return conversation_mapper.seal_offers_payload(
        payload, conversation_id, who.holder_reference, codec
    )
