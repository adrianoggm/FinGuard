"""One question, asked of every trusted agent at once, and only what each reply proves is kept.

The search of spec §5.1 with fakes for its three ports: the gateway (`ExpertAgentsPort`), the skill
classifier and the reply normaliser. The verifier is the real one, so a slot survives here exactly
when it would survive in production. `NOW` is fixed through the handler's `clock`: Wednesday
30 September 2026, 10:00 in Madrid, so «el jueves por la tarde» is 1 October, 15:00-21:00.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from finguard_orchestrator.application.queries.search_activities import (
    AgentOutcome,
    SearchActivitiesHandler,
    SearchActivitiesQuery,
)
from finguard_orchestrator.domain.constants import MAX_CONTEXT_ID, MAX_OFFERS
from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentReply,
    AgentSkill,
    AvailabilityDraft,
    Capability,
    DataReplyPart,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    SlotDraft,
    TextReplyPart,
)
from finguard_orchestrator.domain.services import activity_writer
from finguard_orchestrator.domain.services.member_reference import member_reference
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.observability.decisions import (
    Step,
    Verdict,
    recording_turn,
)

ZONE = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=ZONE)
KEY = b"test-pairwise-key-thirty-two-bytes!!"
HOLDER = "ES-0001"
CALLER = Caller(holder_reference=HOLDER, account_id=UUID(int=7))
SENTENCE = "¿Qué clases de spinning hay el jueves por la tarde?"

TIMETABLE = AgentSkill("timetable", "Horario", "Clases por actividad y fecha.")
NORTE = ExpertAgent(
    "gym-norte", "Gimnasio Norte", True, None, "sha256:n", ("127.0.0.1:9101",), (TIMETABLE,)
)
SUR = ExpertAgent("gym-sur", "Gimnasio Sur", True, None, "sha256:s", (), (TIMETABLE,))
LEGACY = ExpertAgent(
    "gym-antiguo", "Gimnasio Antiguo", False, "version_unsupported", "sha256:a", (), ()
)


def norte_reply(**changes: Any) -> AgentReply:
    sessions = [
        {
            "clase": "Spinning",
            "fecha": "2026-10-01",
            "hora_inicio": "19:00",
            "duracion_min": 45,
            "huecos": 4,
            "ref": "N-1001-1900",
        }
    ]
    fields: dict[str, Any] = {
        "state": "COMPLETED",
        "task_id": "gym-norte",
        "context_id": "ctx-norte",
        "parts": (DataReplyPart({"sesiones": sessions}),),
        "latency_ms": 5,
    }
    return AgentReply(**{**fields, **changes})


def sur_reply(**changes: Any) -> AgentReply:
    text = "Clases de spinning el jueves 1 de octubre: a las 18:30 (6 plazas)."
    fields: dict[str, Any] = {
        "state": "COMPLETED",
        "task_id": "gym-sur",
        "context_id": "ctx-sur",
        "parts": (TextReplyPart(text),),
        "latency_ms": 5,
    }
    return AgentReply(**{**fields, **changes})


NORTE_DRAFT = AvailabilityDraft(
    (SlotDraft("Spinning", "2026-10-01T19:00", "2026-10-01T19:45", 4, "N-1001-1900"),)
)
SUR_DRAFT = AvailabilityDraft((SlotDraft("spinning", "18:30", None, 6, None),))


class FakeAgents:
    """The gateway: a listing, and one canned answer (or failure, or coroutine) per agent."""

    def __init__(
        self,
        listing: tuple[ExpertAgent, ...] | BaseException = (NORTE, SUR),
        replies: Mapping[str, AgentReply | BaseException | Callable[[], Awaitable[AgentReply]]]
        | None = None,
    ) -> None:
        self.listing = listing
        self.replies = dict(replies or {"gym-norte": norte_reply(), "gym-sur": sur_reply()})
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
        reply = self.replies[agent_id]
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return await reply()
        return reply


class FakeClassifier:
    def __init__(self, mappings: Mapping[str, Mapping[Capability, str]] | None = None) -> None:
        self.mappings = dict(mappings or {})
        self.classified: list[str] = []

    async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
        self.classified.append(agent.id)
        return self.mappings.get(agent.id, {Capability.AVAILABILITY: "timetable"})


class FakeNormaliser:
    """Drafts keyed by the reply's task id, which the fixtures set to the agent's id."""

    def __init__(
        self, drafts: Mapping[str, AvailabilityDraft | None | BaseException] | None = None
    ) -> None:
        self.drafts = dict(drafts or {"gym-norte": NORTE_DRAFT, "gym-sur": SUR_DRAFT})
        self.queries: list[ActivityQuery] = []

    async def availability(
        self, query: ActivityQuery, reply: AgentReply
    ) -> AvailabilityDraft | None:
        self.queries.append(query)
        draft = self.drafts[reply.task_id or ""]
        if isinstance(draft, BaseException):
            raise draft
        return draft

    async def booking(self, slot: ActivitySlot, reply: AgentReply) -> None:
        raise AssertionError("a search never reads a booking")


def handler(
    agents: FakeAgents | None = None,
    classifier: FakeClassifier | None = None,
    normaliser: FakeNormaliser | None = None,
    *,
    budget: float = 5.0,
) -> SearchActivitiesHandler:
    return SearchActivitiesHandler(
        agents or FakeAgents(),
        classifier or FakeClassifier(),
        normaliser or FakeNormaliser(),
        pairwise_key=KEY,
        budget_seconds=budget,
        clock=lambda zone: NOW.astimezone(zone),
    )


def search(sentence: str = SENTENCE, **kwargs: Any) -> Any:
    return handler(**kwargs).handle(SearchActivitiesQuery(sentence, CALLER))


def outcome_of(result: Any, agent_id: str) -> AgentOutcome:
    [found] = [outcome for outcome in result.outcomes if outcome.agent_id == agent_id]
    return found


async def test_both_agents_are_asked_in_parallel_with_the_template_and_the_pairwise_reference() -> (
    None
):
    together = asyncio.Barrier(2)

    def meets(reply: AgentReply) -> Callable[[], Awaitable[AgentReply]]:
        async def answer() -> AgentReply:
            # Neither send can finish until both have started: a sequential fan-out never gets here.
            await asyncio.wait_for(together.wait(), 1)
            return reply

        return answer

    agents = FakeAgents(replies={"gym-norte": meets(norte_reply()), "gym-sur": meets(sur_reply())})
    result = await search(agents=agents)

    assert {sent["agent_id"] for sent in agents.sent} == {"gym-norte", "gym-sur"}
    assert [o.kind for o in result.outcomes] == ["answered", "answered"]
    refs = []
    for sent in agents.sent:
        outbound: Outbound = sent["outbound"]
        assert sent["skill_id"] == "timetable" and sent["context_id"] is None
        assert outbound.text == (
            "Busco clases de spinning el jueves 1 de octubre de 2026 entre las 15:00 y las 21:00."
        )
        assert dict(outbound.data) == {
            "request": "availability",
            "activity": "spinning",
            "from": "2026-10-01T15:00:00+02:00",
            "to": "2026-10-01T21:00:00+02:00",
            # Ruling R15: the daily hours travel in the request, not only in the text.
            "band": {"from": "15:00", "to": "21:00"},
            "memberRef": member_reference(KEY, HOLDER, sent["agent_id"]),
        }
        refs.append(outbound.data["memberRef"])
        for leaked in (HOLDER, SENTENCE, "¿Qué"):
            assert leaked not in outbound.text and leaked not in str(dict(outbound.data))
    assert refs[0] != refs[1]
    assert len({sent["message_id"] for sent in agents.sent}) == 2


async def test_a_weekend_afternoon_is_asked_for_with_its_band_on_every_day() -> None:
    """Ruling R15: over several days the daily hours are part of the request -- in the data part
    and in the text -- so an agent does not answer Saturday night or Sunday morning and have its
    whole reply rejected by the band rule."""
    agents = FakeAgents(listing=(NORTE,), replies={"gym-norte": norte_reply()})
    await search("¿hay natación este fin de semana por la tarde?", agents=agents)

    [sent] = agents.sent
    outbound: Outbound = sent["outbound"]
    assert outbound.text == (
        "Busco clases de natación desde el sábado 3 de octubre de 2026 a las 15:00 hasta el "
        "domingo 4 de octubre de 2026 a las 21:00, de 15:00 a 21:00."
    )
    assert dict(outbound.data) == {
        "request": "availability",
        "activity": "natacion",
        "from": "2026-10-03T15:00:00+02:00",
        "to": "2026-10-04T21:00:00+02:00",
        "band": {"from": "15:00", "to": "21:00"},
        "memberRef": member_reference(KEY, HOLDER, "gym-norte"),
    }


async def test_several_days_with_no_band_are_asked_for_without_one() -> None:
    agents = FakeAgents(listing=(NORTE,), replies={"gym-norte": norte_reply()})
    await search("¿hay yoga esta semana?", agents=agents)

    [sent] = agents.sent
    outbound: Outbound = sent["outbound"]
    assert outbound.text == (
        "Busco clases de yoga desde el miércoles 30 de septiembre de 2026 a las 10:00 hasta el "
        "domingo 4 de octubre de 2026 a las 23:00."
    )
    assert "band" not in outbound.data
    assert (outbound.data["from"], outbound.data["to"]) == (
        "2026-09-30T10:00:00+02:00",
        "2026-10-04T23:00:00+02:00",
    )


async def test_this_week_asked_on_a_thursday_morning_is_asked_from_that_hour() -> None:
    """Ruling R23: the text states the interval the data part carries, to the minute -- an agent
    reading only the text would otherwise answer Thursday's 07:30, which has already happened,
    and have the whole reply rejected by the window rule."""
    thursday = datetime(2026, 10, 1, 10, 0, tzinfo=ZONE)
    agents = FakeAgents(listing=(NORTE,), replies={"gym-norte": norte_reply()})
    searching = SearchActivitiesHandler(
        agents,
        FakeClassifier(),
        FakeNormaliser(),
        pairwise_key=KEY,
        budget_seconds=5.0,
        clock=lambda zone: thursday.astimezone(zone),
    )

    await searching.handle(SearchActivitiesQuery("¿hay yoga esta semana?", CALLER))

    [sent] = agents.sent
    outbound: Outbound = sent["outbound"]
    assert outbound.text == (
        "Busco clases de yoga desde el jueves 1 de octubre de 2026 a las 10:00 hasta el "
        "domingo 4 de octubre de 2026 a las 23:00."
    )
    assert (outbound.data["from"], outbound.data["to"]) == (
        "2026-10-01T10:00:00+02:00",
        "2026-10-04T23:00:00+02:00",
    )


async def test_slots_are_merged_and_ordered_by_start() -> None:
    result = await search()

    assert [(slot.agent_id, f"{slot.start:%H:%M}") for slot in result.slots] == [
        ("gym-sur", "18:30"),
        ("gym-norte", "19:00"),
    ]
    assert result.offers is not None and result.offers.slots == result.slots
    assert set(result.offers.contexts) == {("gym-norte", "ctx-norte"), ("gym-sur", "ctx-sur")}
    assert dict(result.offers.agent_names) == {
        "gym-norte": "Gimnasio Norte",
        "gym-sur": "Gimnasio Sur",
    }
    assert result.refusal is None
    assert result.query == ActivityQuery(
        "spinning",
        datetime(2026, 10, 1, 15, 0, tzinfo=ZONE),
        datetime(2026, 10, 1, 21, 0, tzinfo=ZONE),
        band=(time(15, 0), time(21, 0)),
    )


async def test_the_offers_name_every_agent_that_was_asked() -> None:
    """Acceptance finding A3 (ruling R28): a gym asked and offering nothing is still a gym the
    holder can name -- «la de las 20:00 en el Sur» must not fall through to another gym's 20:00
    because the Sur had dropped out of the names."""
    nothing = FakeNormaliser({"gym-norte": NORTE_DRAFT, "gym-sur": AvailabilityDraft(())})

    result = await search(normaliser=nothing)

    assert result.offers is not None
    assert {slot.agent_id for slot in result.offers.slots} == {"gym-norte"}
    assert dict(result.offers.agent_names) == {
        "gym-norte": "Gimnasio Norte",
        "gym-sur": "Gimnasio Sur",
    }
    assert result.offers.contexts == (("gym-norte", "ctx-norte"),)


async def test_a_named_agent_is_the_only_one_asked() -> None:
    agents = FakeAgents()
    result = await search("¿hay spinning el jueves por la tarde en el Norte?", agents=agents)

    assert [sent["agent_id"] for sent in agents.sent] == ["gym-norte"]
    assert [slot.agent_id for slot in result.slots] == ["gym-norte"]
    assert result.query is not None and result.query.agent_ids == ("gym-norte",)


async def test_only_agents_with_a_classified_availability_skill_are_asked() -> None:
    agents = FakeAgents(listing=(NORTE, SUR, LEGACY))
    classifier = FakeClassifier({"gym-sur": {}})
    result = await search(agents=agents, classifier=classifier)

    assert [sent["agent_id"] for sent in agents.sent] == ["gym-norte"]
    assert [outcome.agent_id for outcome in result.outcomes] == ["gym-norte"]
    assert "gym-antiguo" not in classifier.classified, "an unusable agent is never even read"


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"agents": {"gym-norte": ExpertAgentsError("timeout")}}, "timeout"),
        ({"drafts": {"gym-norte": ProviderFailedError("ollama", "down")}}, "provider_failed"),
        ({"drafts": {"gym-norte": None}}, "structure"),
        (
            {  # 17:00 is a time the reply never states
                "drafts": {
                    "gym-norte": AvailabilityDraft(
                        (SlotDraft("Spinning", "2026-10-01T17:00", None, None, None),)
                    )
                }
            },
            "grounding",
        ),
        ({"agents": {"gym-norte": norte_reply(state="FAILED")}}, "FAILED"),
    ],
)
async def test_one_agent_failing_does_not_silence_the_other(
    change: dict[str, dict[str, Any]], rule: str
) -> None:
    agents = FakeAgents(replies={"gym-norte": norte_reply(), "gym-sur": sur_reply()})
    agents.replies.update(change.get("agents", {}))
    normaliser = FakeNormaliser()
    normaliser.drafts.update(change.get("drafts", {}))
    result = await search(agents=agents, normaliser=normaliser)

    failed = outcome_of(result, "gym-norte")
    assert (failed.kind, failed.rule, failed.agent_name) == ("unavailable", rule, "Gimnasio Norte")
    assert [slot.agent_id for slot in result.slots] == ["gym-sur"]
    assert outcome_of(result, "gym-sur").kind == "answered"


async def test_the_budget_bounds_each_agent() -> None:
    async def slow() -> AgentReply:
        await asyncio.sleep(2)
        return norte_reply()

    agents = FakeAgents(replies={"gym-norte": slow, "gym-sur": sur_reply()})
    result = await search(agents=agents, budget=0.2)

    assert (outcome_of(result, "gym-norte").kind, outcome_of(result, "gym-norte").rule) == (
        "unavailable",
        "budget",
    )
    assert [slot.agent_id for slot in result.slots] == ["gym-sur"]


@pytest.mark.parametrize(
    ("sentence", "setup", "refusal"),
    [
        ("¿qué clases hay el jueves?", {}, "no_activity"),
        ("¿qué clases de yoga hubo ayer?", {}, "no_window"),
        (SENTENCE, {"classifier": {"gym-norte": {}, "gym-sur": {}}}, "no_agents"),
        ("¿hay spinning el jueves en el Gimnasio Oeste?", {}, "none_named"),
        # Ruling R16: named, listed and usable, but it offers no timetable. The gym exists, so
        # «ninguno tiene ese nombre» would be false: nobody asked offers timetables.
        ("¿hay spinning el jueves en el Norte?", {"classifier": {"gym-norte": {}}}, "no_agents"),
        (SENTENCE, {"listing": ExpertAgentsError("unreachable", "ConnectError")}, "unreachable"),
    ],
)
async def test_refusals(sentence: str, setup: dict[str, Any], refusal: str) -> None:
    agents = FakeAgents(listing=setup.get("listing", (NORTE, SUR)))
    result = await search(
        sentence, agents=agents, classifier=FakeClassifier(setup.get("classifier"))
    )

    assert result.refusal == refusal
    assert result.slots == () and result.offers is None
    assert agents.sent == [], "a refused search sends nothing"
    if refusal in ("no_activity", "no_window"):
        assert result.query is None


@pytest.mark.parametrize(
    ("reason", "sentence"),
    [
        ("auth_unsupported", "Gimnasio Antiguo pide una autenticación que FinGuard no admite."),
        ("credential_missing", "Gimnasio Antiguo pide una autenticación que FinGuard no admite."),
        ("version_unsupported", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
        ("card_invalid", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
        ("binding_unsupported", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
        ("no_skills", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
        ("card_unreachable", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
        ("a_reason_added_later", "Gimnasio Antiguo no cumple el estándar A2A que FinGuard exige."),
    ],
)
async def test_a_named_agent_that_is_not_usable_says_why(reason: str, sentence: str) -> None:
    antiguo = ExpertAgent("gym-antiguo", "Gimnasio Antiguo", False, reason, "sha256:a", (), ())
    agents = FakeAgents(listing=(NORTE, SUR, antiguo))
    result = await search("¿hay spinning el jueves en el Antiguo?", agents=agents)

    assert agents.sent == []
    assert result.refusal is None
    [outcome] = result.outcomes
    assert (outcome.agent_id, outcome.kind, outcome.rule) == ("gym-antiguo", "unavailable", reason)
    assert activity_writer.notice(outcome) == sentence
    assert not outcome.asked, "never sent anything, so it cannot have answered badly"

    unnamed = await search(agents=FakeAgents(listing=(NORTE, SUR, antiguo)))
    assert "gym-antiguo" not in {outcome.agent_id for outcome in unnamed.outcomes}


async def test_an_agent_whose_card_could_not_be_read_was_never_asked() -> None:
    class FailingClassifier(FakeClassifier):
        async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
            if agent.id == "gym-norte":
                raise ProviderFailedError("ollama", "down")
            return await super().classify(agent)

    agents = FakeAgents()
    result = await search(agents=agents, classifier=FailingClassifier())

    norte = outcome_of(result, "gym-norte")
    assert (norte.kind, norte.rule, norte.asked) == ("unavailable", "provider_failed", False)
    assert outcome_of(result, "gym-sur").asked
    assert [sent["agent_id"] for sent in agents.sent] == ["gym-sur"]


async def test_every_agent_that_was_sent_something_says_so() -> None:
    normaliser = FakeNormaliser()
    normaliser.drafts["gym-norte"] = None
    result = await search(normaliser=normaliser)

    assert all(outcome.asked for outcome in result.outcomes)


async def test_input_required_with_alternatives_on_search_is_read_as_slots() -> None:
    replies = {"gym-norte": norte_reply(state="INPUT_REQUIRED")}
    result = await search(agents=FakeAgents(listing=(NORTE,), replies=replies))

    assert outcome_of(result, "gym-norte").kind == "answered"
    assert [f"{slot.start:%H:%M}" for slot in result.slots] == ["19:00"]


async def test_input_required_for_anything_else_is_unsupported_request() -> None:
    replies = {"gym-norte": norte_reply(state="INPUT_REQUIRED")}
    normaliser = FakeNormaliser({"gym-norte": AvailabilityDraft((), unsupported_request=True)})
    with recording_turn() as turn:
        result = await search(
            agents=FakeAgents(listing=(NORTE,), replies=replies), normaliser=normaliser
        )

    outcome = outcome_of(result, "gym-norte")
    assert (outcome.kind, result.slots) == ("unsupported_request", ())
    assert activity_writer.notice(outcome) == (
        "Gimnasio Norte pide un dato que FinGuard no proporciona."
    )
    [decision] = turn.sealed().where(Step.EXPERT_AGENT)
    assert decision.verdict is Verdict.DECLINED


async def test_an_agent_asking_for_linking_offers_only_a_link_on_its_own_hosts() -> None:
    link = TextReplyPart("Vincula tu cuenta en http://127.0.0.1:9101/vincular?ref=abc y vuelve.")
    elsewhere = TextReplyPart("Vincula tu cuenta en http://evil.example/vincular.")
    for part, expected in ((link, "http://127.0.0.1:9101/vincular?ref=abc"), (elsewhere, None)):
        replies = {"gym-norte": norte_reply(state="AUTH_REQUIRED", parts=(part,))}
        result = await search(agents=FakeAgents(listing=(NORTE,), replies=replies))
        outcome = outcome_of(result, "gym-norte")
        assert (outcome.kind, outcome.link) == ("link_required", expected)


async def test_each_agent_asked_leaves_one_decision_in_its_own_words() -> None:
    normaliser = FakeNormaliser()
    normaliser.drafts["gym-norte"] = None
    with recording_turn() as turn:
        await search(normaliser=normaliser)

    decisions = sorted(turn.sealed().where(Step.EXPERT_AGENT), key=lambda d: d.detail or "")
    assert [(d.verdict, d.changed, d.detail) for d in decisions] == [
        (Verdict.FAILED, False, "availability:gym-norte:unavailable"),
        (Verdict.AGREED, False, "availability:gym-sur:answered"),
    ]


async def test_a_verifier_rejection_is_declined_not_failed() -> None:
    normaliser = FakeNormaliser()
    normaliser.drafts["gym-norte"] = AvailabilityDraft(
        (SlotDraft("Spinning", "2026-10-01T17:00", None, None, None),)
    )
    with recording_turn() as turn:
        await search(normaliser=normaliser)

    verdicts = {d.detail: d.verdict for d in turn.sealed().where(Step.EXPERT_AGENT)}
    assert verdicts["availability:gym-norte:unavailable"] is Verdict.DECLINED


async def test_the_verifiers_rule_is_recorded_and_logged_for_a_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review item 9: the booking path logged the rule that refused a reply and the search did
    not -- the one fact that says why a gym's whole timetable was dropped."""
    normaliser = FakeNormaliser()
    normaliser.drafts["gym-norte"] = AvailabilityDraft(
        (SlotDraft("Spinning", "2026-10-01T17:00", None, None, None),)
    )
    with caplog.at_level(logging.INFO), recording_turn() as turn:
        await search(normaliser=normaliser)

    by_agent = {d.detail: d for d in turn.sealed().where(Step.EXPERT_AGENT)}
    rejected = by_agent["availability:gym-norte:unavailable"]
    assert (rejected.field, rejected.now) == ("rule", "grounding")
    answered = by_agent["availability:gym-sur:answered"]
    assert (answered.field, answered.now) == (None, None)
    [logged] = [
        r for r in caplog.records if r.getMessage() == "expert_agents.availability_rejected"
    ]
    assert logged.levelno == logging.INFO
    assert (logged.agent, logged.rule) == ("gym-norte", "grounding")  # type: ignore[attr-defined]


async def test_the_offers_keep_the_earliest_slots_up_to_the_ceiling() -> None:
    """Two agents with fifteen sessions each: thirty verified slots, twenty kept, earliest first."""

    def timetable(prefix: str, minute: int) -> tuple[AgentReply, AvailabilityDraft]:
        starts = [f"{15 + i // 3}:{minute + (i % 3) * 20:02d}" for i in range(15)]
        sessions = [
            {"clase": "Spinning", "fecha": "2026-10-01", "hora_inicio": start} for start in starts
        ]
        reply = norte_reply(
            task_id=f"gym-{prefix}",
            context_id=f"ctx-{prefix}",
            parts=(DataReplyPart({"sesiones": sessions}),),
        )
        draft = AvailabilityDraft(
            tuple(
                SlotDraft("Spinning", f"2026-10-01T{s['hora_inicio']}", None, None, None)
                for s in sessions
            )
        )
        return reply, draft

    norte, norte_draft = timetable("norte", 0)
    sur, sur_draft = timetable("sur", 5)
    result = await search(
        agents=FakeAgents(replies={"gym-norte": norte, "gym-sur": sur}),
        normaliser=FakeNormaliser({"gym-norte": norte_draft, "gym-sur": sur_draft}),
    )

    assert result.offers is not None and len(result.offers.slots) == MAX_OFFERS
    starts = [slot.start for slot in result.offers.slots]
    assert starts == sorted(starts)
    assert len(result.slots) == MAX_OFFERS


async def test_a_card_name_unfit_to_show_is_replaced_by_the_agent_id() -> None:
    """A card name is a third party's text shown to the holder: one carrying a bidi override or
    longer than a name is shown as the agent's id instead, never as written."""
    spoofed = ExpertAgent(
        "gym-norte", "Gimnasio \u202eetroN", True, None, "sha256:n", (), (TIMETABLE,)
    )
    result = await search(
        agents=FakeAgents(listing=(spoofed,), replies={"gym-norte": norte_reply()})
    )

    assert dict(result.offers.agent_names) == {"gym-norte": "gym-norte"}
    assert outcome_of(result, "gym-norte").agent_name == "gym-norte"


async def test_a_context_the_sealed_offers_cannot_carry_is_not_kept() -> None:
    """An agent's `contextId` travels in the sealed offers so a booking can continue it; one too
    long, or with a control character, is dropped and the slots are kept."""
    agents = FakeAgents(
        replies={
            "gym-norte": norte_reply(context_id="c" * (MAX_CONTEXT_ID + 1)),
            "gym-sur": sur_reply(context_id="ctx\nsur"),
        }
    )
    result = await search(agents=agents)

    assert result.offers is not None and result.offers.contexts == ()
    assert len(result.offers.slots) == 2
