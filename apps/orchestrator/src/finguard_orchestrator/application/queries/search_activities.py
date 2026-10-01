"""SearchActivities: one question asked of every trusted agent at once (spec §5.1).

«¿Qué clases de spinning hay el jueves por la tarde?» becomes a verified query -- the activity from
the lexicon, the window from `activity_when` -- and that query, never the sentence, is what every
agent is sent: a template (`activity_writer.request_text`) and a data part with the activity, the
window and the pairwise `memberRef` (§5.4). The agents are asked in parallel, each inside its own
budget, and each reply is read by the model into a draft that the verifier accepts or rejects
whole. One agent failing never silences another: it becomes an `AgentOutcome` line, and the slots
of the ones that answered are merged, ordered by start and capped.

Nothing here raises for an agent. Every per-agent coroutine catches what an agent, the gateway or
the model can do wrong and turns it into an outcome with a closed rule, so `gather` has nothing to
propagate and the turn always has an answer to give.
"""

import asyncio
import logging
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from finguard_orchestrator.domain.constants import (
    MAX_AGENT_NAME,
    MAX_CONTEXT_ID,
    MAX_EXPERT_AGENTS,
    MAX_OFFERS,
)
from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.conversation import ActivityOffers
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentRejection,
    AgentReply,
    Capability,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
)
from finguard_orchestrator.domain.ports.agent_reading import (
    ReplyNormaliserPort,
    SkillClassifierPort,
)
from finguard_orchestrator.domain.ports.expert_agents import ExpertAgentsPort
from finguard_orchestrator.domain.services import activity_writer
from finguard_orchestrator.domain.services.activity_when import activity_window
from finguard_orchestrator.domain.services.agent_reply_verifier import link_in, verify_slots
from finguard_orchestrator.domain.services.expert_agent_words import (
    activity_in,
    claims_an_agent_name,
    named_agents,
)
from finguard_orchestrator.domain.services.member_reference import member_reference
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.observability.decisions import Step, Verdict, record

logger = logging.getLogger(__name__)

AgentOutcomeKind = Literal["answered", "unavailable", "link_required", "unsupported_request"]
SearchRefusal = Literal["no_activity", "no_window", "no_agents", "none_named", "unreachable"]

_FAILED_STATES: Final = frozenset({"FAILED", "REJECTED", "CANCELED"})


@dataclass(frozen=True)
class AgentOutcome:
    """How one agent took part in a search, as a line of the answer. `rule` is for the record:
    the verifier's rule, the gateway's code, the failed state, `budget`, or -- for a named agent
    the gateway lists as unusable -- its reason."""

    agent_id: str
    agent_name: str
    kind: AgentOutcomeKind
    link: str | None = None
    rule: str | None = None
    # Whether anything was sent to it. A named agent the gateway lists as unusable, or one whose
    # card the model could not read, never was -- so it never "answered badly", and the web's list
    # of agents that did must not say so (ruling R16); the text gives the reason instead.
    asked: bool = True


@dataclass(frozen=True)
class SearchActivitiesQuery:
    sentence: str
    caller: Caller


@dataclass(frozen=True)
class ActivitySearch:
    query: ActivityQuery | None
    slots: tuple[ActivitySlot, ...]
    outcomes: tuple[AgentOutcome, ...]
    offers: ActivityOffers | None
    refusal: SearchRefusal | None


@dataclass(frozen=True)
class _Asked:
    """One agent's part of the search, before the merge."""

    outcome: AgentOutcome
    slots: tuple[ActivitySlot, ...] = ()
    context_id: str | None = None
    rejected: bool = False  # the verifier refused the reply, which is a decision, not a failure


class SearchActivitiesHandler:
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

    async def handle(self, query: SearchActivitiesQuery) -> ActivitySearch:
        now = self._clock(ZoneInfo(query.caller.reporting_timezone))
        activity = activity_in(query.sentence)
        if activity is None:
            return _refused(None, "no_activity")
        window = activity_window(query.sentence, now=now)
        if window is None:
            return _refused(None, "no_window")
        asked = ActivityQuery(activity, window.start, window.end, band=window.band)
        try:
            listed = await self._agents.agents()
        except ExpertAgentsError as exc:
            logger.warning("expert_agents.unreachable", extra={"code": exc.code})
            return _refused(asked, "unreachable")

        named = named_agents(query.sentence, listed)
        if not named and claims_an_agent_name(query.sentence):
            return _refused(asked, "none_named")
        pool = [agent for agent in listed if not named or agent.id in named]
        # An unusable agent is never asked. Said only when the sentence named it: the holder asked
        # for that gym, and «no cumple el estándar» is the answer to the question they asked.
        lines = [
            _Asked(
                AgentOutcome(
                    agent.id,
                    _shown(agent),
                    "unavailable",
                    rule=agent.reason or "unusable",
                    asked=False,
                )
            )
            for agent in pool
            if not agent.usable and named
        ]
        usable = [agent for agent in pool if agent.usable]
        skills = await asyncio.gather(*(self._availability_skill(agent) for agent in usable))
        askable: list[tuple[ExpertAgent, str]] = []
        for agent, skill in zip(usable, skills, strict=True):
            if isinstance(skill, _Asked):
                lines.append(skill)
            elif skill is not None:
                askable.append((agent, skill))
        askable = askable[:MAX_EXPERT_AGENTS]
        if not askable and not lines:
            # Named or not, the agents are there and none offers a timetable (ruling R16):
            # `none_named` is only for a name no agent has, refused above.
            return _refused(asked, "no_agents")
        if named:
            asked = replace(asked, agent_ids=named)

        answers = await asyncio.gather(
            *(self._ask(agent, skill, asked, query.caller, now) for agent, skill in askable)
        )
        every = [*lines, *answers]
        for part in every:
            record(
                Step.EXPERT_AGENT,
                _verdict(part),
                changed=False,
                detail=f"availability:{part.outcome.agent_id}:{part.outcome.kind}",
            )
        return _merged(asked, every)

    async def _availability_skill(self, agent: ExpertAgent) -> str | _Asked | None:
        """The skill this agent serves availability with, `None` when it serves none, or its
        outcome line when the model could not say."""
        try:
            mapping: Mapping[Capability, str] = await asyncio.wait_for(
                self._classifier.classify(agent), self._budget
            )
        except (ProviderFailedError, TimeoutError) as exc:
            rule = activity_writer.BUDGET if isinstance(exc, TimeoutError) else exc.code
            return _Asked(
                AgentOutcome(agent.id, _shown(agent), "unavailable", rule=rule, asked=False)
            )
        return mapping.get(Capability.AVAILABILITY)

    async def _ask(
        self, agent: ExpertAgent, skill: str, query: ActivityQuery, caller: Caller, now: datetime
    ) -> _Asked:
        """Send, read and verify, inside the budget. Never raises."""
        name = _shown(agent)
        try:
            return await asyncio.wait_for(
                self._exchange(agent, name, skill, query, caller, now), self._budget
            )
        except TimeoutError:
            return _Asked(AgentOutcome(agent.id, name, "unavailable", rule=activity_writer.BUDGET))
        except ExpertAgentsError as exc:
            return _Asked(AgentOutcome(agent.id, name, "unavailable", rule=exc.code))
        except ProviderFailedError as exc:
            return _Asked(AgentOutcome(agent.id, name, "unavailable", rule=exc.code))

    async def _exchange(
        self,
        agent: ExpertAgent,
        name: str,
        skill: str,
        query: ActivityQuery,
        caller: Caller,
        now: datetime,
    ) -> _Asked:
        reply = await self._agents.send(
            agent.id,
            skill_id=skill,
            message_id=str(uuid4()),
            outbound=self._outbound(query, caller, agent.id),
            context_id=None,
        )
        if reply.state == "AUTH_REQUIRED":
            link = link_in(reply, agent.link_hosts)
            return _Asked(AgentOutcome(agent.id, name, "link_required", link=link))
        if reply.state in _FAILED_STATES:
            return _Asked(AgentOutcome(agent.id, name, "unavailable", rule=reply.state))
        return await self._read(agent, name, query, reply, now)

    async def _read(
        self, agent: ExpertAgent, name: str, query: ActivityQuery, reply: AgentReply, now: datetime
    ) -> _Asked:
        """A COMPLETED reply's slots, or an INPUT_REQUIRED reply's alternatives (§7)."""
        draft = await self._normaliser.availability(query, reply)
        if draft is None:
            return _Asked(AgentOutcome(agent.id, name, "unavailable", rule="structure"))
        # An agent asking for something else, or saying it cannot serve this request, has answered
        # nothing a timetable can hold -- and nothing it asked for is forwarded (§7).
        if draft.unsupported_request or (reply.state == "INPUT_REQUIRED" and not draft.slots):
            return _Asked(AgentOutcome(agent.id, name, "unsupported_request"))
        verified = verify_slots(draft, query=query, reply=reply, agent_id=agent.id, now=now)
        if isinstance(verified, AgentRejection):
            return _Asked(
                AgentOutcome(agent.id, name, "unavailable", rule=verified.rule), rejected=True
            )
        return _Asked(
            AgentOutcome(agent.id, name, "answered"), slots=verified, context_id=reply.context_id
        )

    def _outbound(self, query: ActivityQuery, caller: Caller, agent_id: str) -> Outbound:
        """The template and the data part, filled from the verified query alone (§5.4).

        The band travels too (ruling R15): over several days `from`-`to` is one interval, and an
        agent that read only that would answer Saturday night for «el fin de semana por la tarde»
        -- a session the verifier's band rule then rejects together with the whole reply.
        """
        data: dict[str, object] = {
            "request": "availability",
            "activity": query.activity,
            "from": query.start.isoformat(timespec="seconds"),
            "to": query.end.isoformat(timespec="seconds"),
        }
        if query.band is not None:
            data["band"] = {"from": f"{query.band[0]:%H:%M}", "to": f"{query.band[1]:%H:%M}"}
        data["memberRef"] = member_reference(self._key, caller.holder_reference, agent_id)
        return Outbound(text=activity_writer.request_text(query), data=data)


def _refused(query: ActivityQuery | None, refusal: SearchRefusal) -> ActivitySearch:
    return ActivitySearch(query, (), (), None, refusal)


def _merged(query: ActivityQuery, parts: list[_Asked]) -> ActivitySearch:
    """Every verified slot, earliest first, at most `MAX_OFFERS`, and the offers they make.

    The offers name every agent that was asked, offering or not (ruling R28): «la de las 20:00
    en el Sur» names a gym that answered nothing, and a booking must read it as that gym rather
    than as no gym at all -- which would let it fall through to another's 20:00.
    """
    ordered = sorted(
        (slot for part in parts for slot in part.slots),
        key=lambda slot: (slot.start, slot.agent_id),
    )
    kept = tuple(ordered[:MAX_OFFERS])
    offering = {slot.agent_id for slot in kept}
    # The offering ones first, so the cap can never leave a kept slot's agent unnamed.
    asked = sorted(
        ((part.outcome.agent_id, part.outcome.agent_name) for part in parts if part.outcome.asked),
        key=lambda named: named[0] not in offering,
    )
    names = tuple(asked[:MAX_EXPERT_AGENTS])
    # A context an agent named in a form the sealed offers cannot carry is not kept: a booking then
    # starts a new one, which an agent must accept anyway (a context is a hint, not a key).
    contexts = tuple(
        (part.outcome.agent_id, part.context_id)
        for part in parts
        if part.outcome.agent_id in offering
        and part.context_id is not None
        and _plain(part.context_id, MAX_CONTEXT_ID)
    )
    offers = ActivityOffers(kept, names, contexts) if kept else None
    return ActivitySearch(query, kept, tuple(part.outcome for part in parts), offers, None)


def _verdict(part: _Asked) -> Verdict:
    if part.outcome.kind == "answered":
        return Verdict.AGREED
    if part.rejected or part.outcome.kind == "unsupported_request":
        return Verdict.DECLINED
    return Verdict.FAILED


def _shown(agent: ExpertAgent) -> str:
    """The card name, when it is fit to show a person; otherwise the id, which the gateway derived
    from the registered URL and is always plain."""
    name = agent.name.strip()
    return name if _plain(name, MAX_AGENT_NAME) else agent.id


def _plain(text: str, ceiling: int) -> bool:
    """Bounded, and with no control, format or unassigned character: a bidi override in a name
    would reorder what the holder reads."""
    return 1 <= len(text) <= ceiling and not any(
        unicodedata.category(character).startswith("C") for character in text
    )
