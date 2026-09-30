"""The expert-agent turns: one helper, called from both question routes (spec §14 A11).

The web types into `/v1/questions/stream` and everything else posts to `/v1/questions`, so a branch
wired into one route only is a branch half the conversations never reach -- the lesson every
payment branch in `llm_controller` repeats. Both routes call `expert_agent_reply` at the same point,
after the payment branches that own their turns and before the visual and financial paths, and
return its answer when it has one.

It answers only a turn `asks_an_expert_agent` claims (spec §14 A1), and for every other turn
returns `None`: the caller then continues exactly as it did before this existed. Task 5 adds the
booking branches above the search -- a pending booking's reply first, then a booking request
against the sealed offers -- which is what `bookings` is here for.

Every sentence is `activity_writer`'s, every value a verified one, and the offers leave sealed.
"""

from dataclasses import replace
from typing import Final
from uuid import uuid4

from finguard_orchestrator.application.queries.search_activities import (
    ActivitySearch,
    SearchActivitiesHandler,
    SearchActivitiesQuery,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.expert_agents import ActivityQuery
from finguard_orchestrator.domain.services import activity_writer
from finguard_orchestrator.domain.services.expert_agent_words import asks_an_expert_agent
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


async def expert_agent_reply(
    request: QuestionRequest,
    who: Caller,
    was: Conversation,
    *,
    searches: SearchActivitiesHandler | None,
    bookings: object | None,
    codec: MetricTokenCodec,
) -> QuestionResponse | None:
    """The answer to an expert-agent turn, or `None` when this is not one.

    `searches` is `None` when no gateway is configured: the capability does not exist and says so
    (spec §3.2). `bookings` is Task 5's `BookActivityHandler`, unused until its branches land.
    """
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


def _said(outcome: str, text: str, was: Conversation) -> QuestionResponse:
    """A turn that asked no agent: the conversation is left exactly as it was, offers included."""
    return QuestionResponse(
        outcome=outcome, text=text, conversation=conversation_mapper.to_payload(was)
    )


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
        activities=[
            ActivitySlotPayload(
                agent_id=slot.agent_id,
                agent_name=names.get(slot.agent_id, slot.agent_id),
                activity=slot.activity,
                start=slot.start,
                end=slot.end,
                free_places=slot.free_places,
                slot_ref=slot.slot_ref,
            )
            for slot in search.slots
        ],
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


def _sealed(
    conversation: Conversation, who: Caller, codec: MetricTokenCodec
) -> ConversationPayload:
    """The conversation's wire shape with its offers sealed for this holder and conversation."""
    conversation_id = conversation.conversation_id or uuid4()
    payload = conversation_mapper.to_payload(replace(conversation, conversation_id=conversation_id))
    return conversation_mapper.seal_offers_payload(
        payload, conversation_id, who.holder_reference, codec
    )
