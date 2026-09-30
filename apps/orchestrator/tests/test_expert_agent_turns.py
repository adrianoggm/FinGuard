"""The expert-agent turn: one helper, called from both question routes (spec §14 A11).

`expert_agent_reply` answers a turn the predicate claims and returns `None` for every other one, so
the caller continues exactly as before. These drive it with a fake search handler -- the handler's
own behaviour is `test_search_activities.py` -- and then through both HTTP routes, because the web
types into `/v1/questions/stream` and a branch wired into one route only is a branch the
conversation never gets.
"""

import json
import logging
from dataclasses import replace
from datetime import datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

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
    Conversation,
    PaymentInProgress,
)
from finguard_orchestrator.domain.models.expert_agents import ActivityQuery, ActivitySlot
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


async def reply(
    sentence: str = SENTENCE,
    *,
    who: Caller = ASKER,
    was: Conversation | None = None,
    searches: FakeSearches | None = None,
    configured: bool = True,
) -> QuestionResponse | None:
    was = was or conversation_mapper.to_domain(None)
    return await expert_agent_reply(
        QuestionRequest(question=sentence),
        who,
        was,
        searches=(searches or FakeSearches()) if configured else None,
        bookings=None,
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
        "gym-antiguo", "Gimnasio Antiguo", "unavailable", rule="auth_unsupported"
    )
    answer = await reply(
        searches=FakeSearches(ActivitySearch(THURSDAY, (), (antiguo,), None, None))
    )

    assert answer is not None and answer.outcome == "AGENTS_UNAVAILABLE"
    assert answer.text == "Gimnasio Antiguo pide una autenticación que FinGuard no admite."


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


# --- both routes (A11) ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Any:
    before = getattr(app.state, "expert_searches", None)
    enabled = app.state.questions_enabled
    app.state.expert_searches = FakeSearches()
    app.state.questions_enabled = True
    app.dependency_overrides[authenticated_caller] = lambda: ASKER
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.expert_searches = before
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


def test_a_gateway_on_loopback_runs_on_the_development_keys_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="finguard_orchestrator.bootstrap.main"):
        built = _create_app_with(monkeypatch, ORCHESTRATOR_A2A_GATEWAY_URL="http://127.0.0.1:8003")

    assert isinstance(built.state.expert_searches, SearchActivitiesHandler)
    said = " ".join(record.message for record in caplog.records)
    assert "ORCHESTRATOR_A2A_PAIRWISE_KEY" in said and "ORCHESTRATOR_A2A_GATEWAY_TOKEN" in said
