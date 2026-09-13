import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import cast

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from finguard_voice_gateway.adapters.outbound.fake_assistant import FakeAssistantClient
from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech
from finguard_voice_gateway.app import (
    VoiceSession,
    _process_turn,
    app,
    chart_identity,
    handle_control,
    process_turn,
    receive_audio,
    services,
    sessions,
    spoken_text,
)
from finguard_voice_gateway.domain.state import SessionProtocolError, SessionState
from finguard_voice_gateway.ports.assistant_client import AssistantClientError, AssistantReply
from finguard_voice_gateway.ports.speech_to_text import SpeechToTextTimeoutError
from finguard_voice_gateway.protocol import parse_event

# The four events `chart_identity`'s result is spread onto (D-4.4-1) -- one place naming them,
# not three that could drift apart.
IDENTITY_EVENT_TYPES = (
    "assistant.text.final",
    "audio.started",
    "audio.completed",
    "turn.completed",
)


def _control(event_type: str, session_id: str, sequence: int, turn_id: str | None = None) -> str:
    return json.dumps(
        {
            "type": event_type,
            "sessionId": session_id,
            "turnId": turn_id,
            "sequence": sequence,
            "at": datetime.now().isoformat(),
        }
    )


def test_browser_preflight_is_answered_for_a_listed_origin_only() -> None:
    # The web client cannot reach this service without this. A browser sends OPTIONS before a
    # cross-origin POST carrying an Authorization header, and until this existed that preflight got
    # 405 with no allow-origin: the page said "Voz no disponible / Failed to fetch" while `curl`
    # answered every route perfectly. The Python benchmark never noticed: it is not a browser.
    with TestClient(app) as client:
        allowed = client.options(
            "/v1/voice/sessions",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
        assert "authorization" in allowed.headers["access-control-allow-headers"].lower()

        # An origin nobody listed gets no such statement: the credential is in a header, so a
        # wildcard would let any page a holder visits spend their session.
        refused = client.options(
            "/v1/voice/sessions",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert "access-control-allow-origin" not in refused.headers


def test_health_version_and_ticket_is_one_use() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/version").json()["service"] == "voice-gateway"
        response = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"})
        body = response.json()
        assert response.status_code == 200
        assert body["sessionId"] not in sessions
        with client.websocket_connect(
            body["websocketUrl"],
            subprotocols=["finguard.voice.v1", body["ticket"]],
        ) as socket:
            socket.send_text(_control("session.configure", body["sessionId"], 0))
            assert socket.receive_json()["type"] == "session.ready"
        try:
            with client.websocket_connect(
                body["websocketUrl"],
                subprotocols=["finguard.voice.v1", body["ticket"]],
            ):
                raise AssertionError("a consumed ticket must not connect")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401
    sessions.clear()


def test_ticket_and_protocol_are_required() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with pytest.raises(WebSocketDisconnect) as missing_protocol:
            with client.websocket_connect(body["websocketUrl"], subprotocols=[body["ticket"]]):
                pass
        assert missing_protocol.value.code == 4406
        with pytest.raises(WebSocketDisconnect) as missing_ticket:
            with client.websocket_connect(body["websocketUrl"], subprotocols=["finguard.voice.v1"]):
                pass
        assert missing_ticket.value.code == 4401
    sessions.clear()


def test_silent_socket_expires_without_waiting_for_a_long_idle_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_idle = services.settings.session_idle_seconds
    monkeypatch.setattr(services.settings, "session_idle_seconds", 0)
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
            ).json()
            with client.websocket_connect(
                body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
            ) as socket:
                error = socket.receive_json()
                assert error["type"] == "error"
                assert error["code"] == "SESSION_EXPIRED"
                assert error["fatal"] is True
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                assert closed.value.code == 4400
            assert body["sessionId"] not in sessions
    finally:
        monkeypatch.setattr(services.settings, "session_idle_seconds", original_idle)
        sessions.clear()


def test_fake_text_turn_and_spoken_payment_confirmation_refusal() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            # I-2: a charted warm-up turn first, so the *next* turn's conversation carries a
            # stale `visual_contexts` entry -- otherwise the assertion below would pass whether
            # or not the guard exists, because there would be nothing to leak in the first place.
            socket.send_text(_control("input.start", session_id, 1, "warm-up"))
            socket.send_bytes("quiero ver el gráfico".encode())
            socket.send_text(_control("input.commit", session_id, 2, "warm-up"))
            while True:
                item = socket.receive()
                if item.get("text") and json.loads(item["text"])["type"] == "turn.completed":
                    break
            socket.send_text(_control("input.start", session_id, 3, "turn-1"))
            socket.send_bytes(b"si")
            socket.send_text(_control("input.commit", session_id, 4, "turn-1"))
            received: list[dict[str, object]] = []
            while True:
                item = socket.receive()
                if item.get("text"):
                    payload = json.loads(item["text"])
                    received.append(payload)
                    if payload["type"] == "turn.completed":
                        break
            response = next(item for item in received if item["type"] == "assistant.text.final")
            response_text = response["text"]
            assert isinstance(response_text, str)
            assert "He entendido" in response_text
            response_answer = response["answer"]
            assert isinstance(response_answer, dict)
            assert response_answer["text"] == response_text
            assert response_answer["groups"] == []
            assert response["paymentConfirmationRequired"] is False
            # An uncharted turn's four identity-bearing events look exactly as they did
            # before Wave 4.4: no chartId, no visualTurnId -- even though the warm-up turn
            # above left a stale chart context sitting in this turn's own conversation (I-2).
            for event_type in IDENTITY_EVENT_TYPES:
                identity_event = next(item for item in received if item["type"] == event_type)
                assert "chartId" not in identity_event
                assert "visualTurnId" not in identity_event
    sessions.clear()


def test_charted_turn_carries_chart_identity_on_its_four_events() -> None:
    # M-3: `services.tts` is a module-level singleton shared across this file's tests, and
    # `spoken` only ever accumulates -- reading `[-1]` below is only this turn's own entry
    # because the recorder starts clean here, not by luck of test ordering.
    assert isinstance(services.tts, FakeTextToSpeech)
    services.tts.spoken.clear()
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes("quiero ver el gráfico".encode())
            socket.send_text(_control("input.commit", session_id, 2, "turn-1"))
            received: list[dict[str, object]] = []
            indices: dict[str, int] = {}
            while True:
                item = socket.receive()
                if item.get("text"):
                    payload = json.loads(item["text"])
                    received.append(payload)
                    indices.setdefault(payload["type"], len(received) - 1)
                    if payload["type"] == "turn.completed":
                        break
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == "chart-fake-1"
                assert event_payload["visualTurnId"] == "11111111-1111-1111-1111-111111111111"
            # The chart before audio: the text (and its chart) is on screen before the
            # summary starts playing, not an accident of ordering but an asserted invariant.
            assert indices["assistant.text.final"] < indices["audio.started"]
            text_final_answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(text_final_answer, dict)
            visualization = text_final_answer["visualization"]
            assert isinstance(visualization, dict)
            assert visualization["chart_id"] == received[indices["assistant.text.final"]]["chartId"]
            # G-3: the real `ChartVisualizationResponse` always emits `unit` and `limit`; a fake
            # that omitted them proved nothing about a payload the real web client would accept.
            assert visualization["unit"] == "money"
            assert visualization["limit"] is None
            # Task 3, Wave 4.4: the screen shows the full written answer (`text`, asserted above
            # via `answer`), but the audio speaks the fake's `spoken` summary instead of `text`.
            assert isinstance(services.tts, FakeTextToSpeech)
            assert services.tts.spoken[-1] == (
                "Restaurantes fue la categoría con mayor gasto. En pantalla tienes el "
                "desglose, y puedes preguntarme por cualquiera de las categorías."
            )
    sessions.clear()


def test_chart_identity_omits_a_stale_visual_turn_id_on_an_uncharted_answer() -> None:
    """I-2: `visual_contexts` persist across turns (`Conversation.with_visual_context`), so an
    uncharted answer's conversation can still carry a previous turn's context. `chart_identity`
    must not stamp that stale `turn_id` onto a turn that drew no chart of its own."""
    stale_conversation = {"visual_contexts": [{"chart_id": "chart-old", "turn_id": "turn-old-id"}]}
    assert chart_identity({"visualization": None}, stale_conversation) == {}
    # Even a charted answer must not borrow a *different* chart's context: the newest context's
    # own `chart_id` has to equal the chart this turn just drew.
    mismatched_answer = {"visualization": {"kind": "chart", "chart_id": "chart-new"}}
    assert chart_identity(mismatched_answer, stale_conversation) == {"chartId": "chart-new"}


def test_chart_identity_ignores_a_chart_id_on_an_unknown_visualization_kind() -> None:
    """G-11: a future `kind` this gateway has never seen -- anything but `"chart"` -- must not be
    read as if it named a chart merely because it carries a same-named field. `apps/web/src/
    api.ts` throws on any `kind` it does not recognise; stamping a `chartId` the client will
    never render is worse than stamping none."""
    unknown_kind_answer = {"visualization": {"kind": "table", "chart_id": "c9"}}
    assert chart_identity(unknown_kind_answer, None) == {}
    # A KPI is the one real kind with no `chart_id` at all -- also omitted, and already the
    # docstring's own claim before this test existed to check it (G-8).
    kpi_answer = {"visualization": {"kind": "kpi", "value_field": "value_minor", "unit": "money"}}
    assert chart_identity(kpi_answer, None) == {}


def test_chart_identity_reads_about_chart_id_when_nothing_else_names_a_chart() -> None:
    """D-H-1b (fix wave): `VISUAL_EXPLAINED` and the refusal-shaped `VISUAL_*` outcomes carry no
    `visualization`/`visualization_derivation` -- they draw no new chart -- but the orchestrator's
    `about_chart_id` (D-H-1, orchestrator side) names the chart the answer is *about*. `chartId`
    is stamped from it; `visualTurnId` never is, because this turn drew nothing new to date."""
    answer = {"about_chart_id": "chart-old"}
    assert chart_identity(answer, None) == {"chartId": "chart-old"}
    # Even with a conversation carrying a visual context, no `visualTurnId` is stamped: the turn
    # named a chart, it did not draw one, so there is no fresh decision identity to attach.
    conversation = {"visual_contexts": [{"chart_id": "chart-old", "turn_id": "turn-old-id"}]}
    assert chart_identity(answer, conversation) == {"chartId": "chart-old"}


def test_chart_identity_prefers_a_drawn_chart_over_about_chart_id() -> None:
    """`about_chart_id` is the last resort -- a turn that actually drew or derived a chart names
    it through `visualization`/`visualization_derivation`, which win first, exactly as before."""
    answer = {
        "visualization": {"kind": "chart", "chart_id": "chart-new"},
        "about_chart_id": "chart-old",
    }
    assert chart_identity(answer, None) == {"chartId": "chart-new"}


def test_chart_identity_ignores_a_non_string_about_chart_id() -> None:
    assert chart_identity({"about_chart_id": None}, None) == {}
    assert chart_identity({"about_chart_id": 7}, None) == {}


def test_chart_identity_picks_the_newest_of_three_contexts() -> None:
    """G-9: `app.py:131-143`'s own assumption -- `conversation["visual_contexts"][-1]` is this
    turn's chart -- was never exercised against a list longer than one before this test, because
    the fake always *replaced* the list with a single element where the real `Conversation.
    with_visual_context` removes-and-appends up to `MAX_VISUAL_CONTEXTS`. Three contexts here,
    not two: two would still pass a bug that just returns "the last of exactly two"."""
    three_contexts = {
        "visual_contexts": [
            {"chart_id": "chart-1", "turn_id": "turn-1"},
            {"chart_id": "chart-2", "turn_id": "turn-2"},
            {"chart_id": "chart-3", "turn_id": "turn-3"},
        ]
    }
    newest_answer = {"visualization": {"kind": "chart", "chart_id": "chart-3"}}
    assert chart_identity(newest_answer, three_contexts) == {
        "chartId": "chart-3",
        "visualTurnId": "turn-3",
    }
    # An older context among the three is not mistaken for the newest: naming it still stamps a
    # `chartId` (the answer really did draw it), but no `visualTurnId` -- it does not match
    # `visual_contexts[-1]`.
    oldest_answer = {"visualization": {"kind": "chart", "chart_id": "chart-1"}}
    assert chart_identity(oldest_answer, three_contexts) == {"chartId": "chart-1"}


def test_a_third_charted_turn_keeps_all_three_contexts_newest_last() -> None:
    """G-9: end to end, through the fake's own `_with_visual_context` (mirroring `Conversation.
    with_visual_context`) -- three different charted turns on one session leave the conversation
    carrying all three contexts, newest last, and each turn's own events are identified by its
    own chart, never the session's first or its accumulated middle one."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            first, first_indices = _run_one_turn(
                socket, session_id, 1, "turn-1", "quiero ver el gráfico".encode()
            )
            for event_type in IDENTITY_EVENT_TYPES:
                assert first[first_indices[event_type]]["chartId"] == "chart-fake-1"
            second, second_indices = _run_one_turn(socket, session_id, 3, "turn-2", b"guardalo")
            for event_type in IDENTITY_EVENT_TYPES:
                assert second[second_indices[event_type]]["chartId"] == "chart-fake-4"
            third, third_indices = _run_one_turn(socket, session_id, 5, "turn-3", b"reestilo")
            for event_type in IDENTITY_EVENT_TYPES:
                assert third[third_indices[event_type]]["chartId"] == "chart-fake-2"
            final_conversation = third[third_indices["turn.completed"]]["conversation"]
            assert isinstance(final_conversation, dict)
            visual_contexts = final_conversation["visual_contexts"]
            assert visual_contexts == [
                {"chart_id": "chart-fake-1", "turn_id": "11111111-1111-1111-1111-111111111111"},
                {"chart_id": "chart-fake-4", "turn_id": "44444444-4444-4444-4444-444444444444"},
                {"chart_id": "chart-fake-2", "turn_id": "22222222-2222-2222-2222-222222222222"},
            ]
    sessions.clear()


def test_restyle_turn_carries_chart_identity_from_the_derivation() -> None:
    """I-1: a restyle answers through `_visual_only_response`, whose `QuestionResponse` carries
    the new chart only in `visualization_derivation.chart_id` (`visualization` stays `None`).
    The four identity-bearing events must still carry that `chartId`, or the web fence
    (`voice.ts`'s `chartIdOf`) is comparing against nothing on exactly this turn."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes(b"reestilo")
            socket.send_text(_control("input.commit", session_id, 2, "turn-1"))
            received: list[dict[str, object]] = []
            indices: dict[str, int] = {}
            while True:
                item = socket.receive()
                if item.get("text"):
                    payload = json.loads(item["text"])
                    received.append(payload)
                    indices.setdefault(payload["type"], len(received) - 1)
                    if payload["type"] == "turn.completed":
                        break
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == "chart-fake-2"
            text_final_answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(text_final_answer, dict)
            assert text_final_answer["visualization"] is None
            # G-2: the full shape `VisualizationDerivation` requires -- `source_chart_id`,
            # `chart_id`, `spec` and `limit` are all present, not the two-field stub the real
            # `apps/web/src/api.ts` client rejects.
            assert text_final_answer["visualization_derivation"] == {
                "source_chart_id": "chart-fake-1",
                "chart_id": "chart-fake-2",
                "spec": {
                    "mark": {"type": "line"},
                    "encoding": {
                        "x": {"field": "dimension", "type": "nominal"},
                        "y": {"field": "value_minor", "type": "quantitative"},
                    },
                },
                "limit": None,
            }
            # The real controller's own outcome for this path is `VISUAL_UPDATED`
            # (`_visual_only_response`), never `ANSWERED`.
            assert text_final_answer["outcome"] == "VISUAL_UPDATED"
    sessions.clear()


def _run_one_turn(
    socket: object, session_id: str, sequence: int, turn_id: str, audio: bytes
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Send one committed turn and collect its events, indexed by first occurrence of `type` --
    the same loop every test in this file already repeats inline."""
    socket.send_text(_control("input.start", session_id, sequence, turn_id))  # type: ignore[attr-defined]
    socket.send_bytes(audio)  # type: ignore[attr-defined]
    socket.send_text(_control("input.commit", session_id, sequence + 1, turn_id))  # type: ignore[attr-defined]
    received: list[dict[str, object]] = []
    indices: dict[str, int] = {}
    while True:
        item = socket.receive()  # type: ignore[attr-defined]
        if item.get("text"):
            payload = json.loads(item["text"])
            received.append(payload)
            indices.setdefault(payload["type"], len(received) - 1)
            if payload["type"] == "turn.completed":
                break
    return received, indices


def test_kpi_visualization_carries_no_chart_identity() -> None:
    """G-8: zero coverage before this test for a KPI answer -- `chart_identity`'s own docstring
    already claimed a KPI has no `chart_id` to stamp; nothing exercised that claim."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(socket, session_id, 1, "turn-1", b"dame el kpi ")
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            visualization = answer["visualization"]
            assert isinstance(visualization, dict)
            assert visualization["kind"] == "kpi"
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert "chartId" not in event_payload
                assert "visualTurnId" not in event_payload
    sessions.clear()


def test_explanation_answer_carries_the_chart_identity_it_is_about() -> None:
    """D-H-1b (fix wave): `VISUAL_EXPLAINED` mirrors `_explanation_response` -- the answer draws
    no new chart (no `visualization`, no `visualization_derivation`), but the orchestrator now
    names which chart it explains through `about_chart_id` (D-H-1). `chart_identity` stamps
    `chartId` from it and never `visualTurnId`: naming a chart is not drawing one, so there is no
    fresh decision identity to attach."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(
                socket, session_id, 1, "turn-1", "explícame el gráfico".encode()
            )
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            assert answer["outcome"] == "VISUAL_EXPLAINED"
            explanation = answer["explanation"]
            assert isinstance(explanation, dict)
            assert explanation["chart_reason"]
            assert answer.get("visualization") is None
            assert answer.get("visualization_derivation") is None
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == "chart-fake-1"
                assert "visualTurnId" not in event_payload
    sessions.clear()


@pytest.mark.parametrize(
    "phrase,outcome",
    [
        ("un grafico viejo", "VISUAL_UNTRUSTED"),
        ("ya esta asi gracias ", "VISUAL_UNCHANGED"),
        ("esa regla rota no ", "VISUAL_INCOMPATIBLE"),
        ("eso es ambiguo", "VISUAL_AMBIGUOUS"),
    ],
)
def test_every_refusal_shaped_visual_outcome_carries_the_chart_identity_it_is_about(
    phrase: str, outcome: str
) -> None:
    """D-H-1b (fix wave): the refusal-shaped `VISUAL_*` outcomes (`_visual_only_response`,
    `_compound_read_reply`'s `AMBIGUOUS` branch) draw no new chart, but each now names which
    chart it is about through `about_chart_id` (D-H-1) -- `chart_identity` stamps `chartId` from
    it, never `visualTurnId` (no fresh decision was made this turn)."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(socket, session_id, 1, "turn-1", phrase.encode())
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            assert answer["outcome"] == outcome
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == "chart-fake-1"
                assert "visualTurnId" not in event_payload
    sessions.clear()


def test_visual_needs_target_carries_no_chart_identity_when_none_could_be_resolved() -> None:
    """D-H-1b's own exception: `VISUAL_NEEDS_TARGET` means no target could be resolved at all
    (D-H-1's own comment on the orchestrator side, `about_chart_id=None`) -- there is genuinely
    no chart to name, so `chart_identity` stamps nothing, honestly, same as before this fix."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(
                socket, session_id, 1, "turn-1", b"sin grafico por favor "
            )
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            assert answer["outcome"] == "VISUAL_NEEDS_TARGET"
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert "chartId" not in event_payload
                assert "visualTurnId" not in event_payload
    sessions.clear()


def test_save_candidate_names_the_same_chart_the_answer_drew() -> None:
    """G-8: zero coverage before this test for `save_candidate` -- `SaveCandidate.chart_id`
    names the same chart this turn's own `visualization` just drew."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(socket, session_id, 1, "turn-1", b"guardalo")
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            save_candidate = answer["save_candidate"]
            assert isinstance(save_candidate, dict)
            visualization = answer["visualization"]
            assert isinstance(visualization, dict)
            assert save_candidate["chart_id"] == visualization["chart_id"]
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == visualization["chart_id"]
    sessions.clear()


def test_compound_answer_identity_is_the_conversations_newest_context() -> None:
    """G-5: a compound (`parts`) answer's own top-level `visualization`/`visualization_
    derivation` are always `None` (`_compound_read_reply`); the chart its second part draws
    rides only inside `answer["parts"][1]["visualization"]`. `chart_identity` reads the
    conversation's own newest visual context instead -- the compound's last charted part -- so
    the four turn events still carry `chartId`/`visualTurnId` rather than nothing."""
    assert isinstance(services.tts, FakeTextToSpeech)
    services.tts.spoken.clear()
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            received, indices = _run_one_turn(
                socket, session_id, 1, "turn-1", b"pregunta compuesta"
            )
            answer = received[indices["assistant.text.final"]]["answer"]
            assert isinstance(answer, dict)
            assert answer["outcome"] == "COMPOUND"
            assert answer["text"] == (
                "1. En agosto gastaste 100,00 €.\n2. En septiembre gastaste 200,00 €."
            )
            parts = answer["parts"]
            assert isinstance(parts, list) and len(parts) == 2
            second_part_visualization = parts[1]["visualization"]
            assert isinstance(second_part_visualization, dict)
            assert second_part_visualization["chart_id"] == "chart-fake-3"
            assert answer.get("visualization") is None
            assert answer.get("visualization_derivation") is None
            for event_type in IDENTITY_EVENT_TYPES:
                event_payload = received[indices[event_type]]
                assert event_payload["chartId"] == "chart-fake-3"
                assert event_payload["visualTurnId"] == "33333333-3333-3333-3333-333333333333"
            # G-5: no `spoken` on this answer (the real orchestrator never sets one for a
            # compound part either), so TTS reads `text` with the numbering stripped -- never
            # "1. En agosto ... \n2. En septiembre ...", read aloud verbatim.
            assert isinstance(services.tts, FakeTextToSpeech)
            assert services.tts.spoken[-1] == (
                "En agosto gastaste 100,00 €. En septiembre gastaste 200,00 €."
            )
    sessions.clear()


def test_spoken_text_strips_only_a_leading_numbered_line() -> None:
    """G-5: a direct check of the stripping rule, independent of the fake -- `spoken_text`
    prefers `reply_spoken` outright, and otherwise removes a leading "N. " from every line of
    `reply_text`, joining what remains with ". " rather than the newline the numbering rode on.
    A plain, non-numbered answer is returned unchanged."""
    assert spoken_text("dicho en voz alta", "1. uno\n2. dos") == "dicho en voz alta"
    assert spoken_text(None, "1. En agosto.\n2. En septiembre.") == "En agosto. En septiembre."
    assert spoken_text(None, "He entendido: hola") == "He entendido: hola"


def test_an_uncharted_turn_speaks_the_written_text_itself() -> None:
    """No `spoken` on the fake's uncharted case (`fake_assistant.py`'s own reply has none) --
    the audio speaks `reply.text`, same as before Task 3."""
    # M-3: see the same line in the charted test above -- the recorder starts clean so `[-1]`
    # below is not order-dependent on whatever an earlier test in this file already spoke.
    assert isinstance(services.tts, FakeTextToSpeech)
    services.tts.spoken.clear()
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes(b"si")
            socket.send_text(_control("input.commit", session_id, 2, "turn-1"))
            received: list[dict[str, object]] = []
            while True:
                item = socket.receive()
                if item.get("text"):
                    payload = json.loads(item["text"])
                    received.append(payload)
                    if payload["type"] == "turn.completed":
                        break
            response_text = next(
                item for item in received if item["type"] == "assistant.text.final"
            )["text"]
            assert isinstance(response_text, str)
            assert isinstance(services.tts, FakeTextToSpeech)
            assert services.tts.spoken[-1] == response_text
    sessions.clear()


def test_payment_policy_blocks_next_spoken_turn_without_calling_assistant() -> None:
    assert isinstance(services.assistant, FakeAssistantClient)
    with TestClient(app) as client:
        before = services.assistant.call_count
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            # I-2: a charted warm-up turn first, so the payment-refusal turn's own conversation
            # (carried through `session.conversation` since the refusal branch never calls the
            # assistant) carries a stale `visual_contexts` entry to guard against.
            for sequence, turn_id, audio in (
                (1, "warm-up", "quiero ver el gráfico".encode()),
                (3, "prepare", b"quiero preparar un pago "),
                (5, "confirm", b"si"),
            ):
                socket.send_text(_control("input.start", session_id, sequence, turn_id))
                socket.send_bytes(audio)
                socket.send_text(_control("input.commit", session_id, sequence + 1, turn_id))
                events: list[dict[str, object]] = []
                while True:
                    item = socket.receive()
                    if item.get("text"):
                        payload = json.loads(item["text"])
                        events.append(payload)
                        if payload["type"] == "turn.completed":
                            break
            assert services.assistant.call_count == before + 2
            assert services.assistant.received_credential is True
            final = [item for item in events if item["type"] == "assistant.text.final"][0]
            final_text = final["text"]
            assert isinstance(final_text, str)
            assert "No puedo confirmar" in final_text
            assert final["paymentConfirmationRequired"] is True
            # The payment-refusal branch builds a reply with no chart, and the warm-up turn's
            # stale context must not leak in either: no identity to lift.
            assert "chartId" not in final
            assert "visualTurnId" not in final
    sessions.clear()


def test_the_refusal_answer_carries_only_outcome_text_and_conversation() -> None:
    """G-7: the gateway must not fabricate an orchestrator answer -- ADR-010 says it carries no
    financial domain types (§1), and the assistant is never asked on this turn, so it has no
    real value for `intent_id`, `plan`, `currency`, `total_minor`, `movement_count`, `groups`,
    `movements` or `suggestions`. Before the fix this dict carried all of those, every one
    invented."""
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            for sequence, turn_id, audio in (
                (1, "prepare", b"quiero preparar un pago "),
                (3, "confirm", b"si"),
            ):
                socket.send_text(_control("input.start", session_id, sequence, turn_id))
                socket.send_bytes(audio)
                socket.send_text(_control("input.commit", session_id, sequence + 1, turn_id))
                while True:
                    item = socket.receive()
                    if item.get("text"):
                        payload = json.loads(item["text"])
                        if payload["type"] == "assistant.text.final":
                            final = payload
                        if payload["type"] == "turn.completed":
                            break
            answer = final["answer"]
            assert isinstance(answer, dict)
            assert set(answer.keys()) == {"outcome", "text", "conversation"}
            assert answer["outcome"] == "AWAITING_CONFIRMATION"
    sessions.clear()


class _RejectsOnceThenAnswers:
    """G-10: a fake assistant client that answers the orchestrator's own 422 shape once -- an
    `AssistantClientError` coded `ORCHESTRATOR_REJECTED`, exactly what `orchestrator_assistant.
    py`'s `_status_message` returns for any 4xx that is not 401/403 -- and answers normally on
    every call after."""

    def __init__(self) -> None:
        self.calls = 0

    async def ask(
        self,
        text: str,
        conversation: object,
        *,
        credential: str,
        idempotency_key: str,
        on_stage: object = None,
    ) -> AssistantReply:
        self.calls += 1
        if self.calls == 1:
            raise AssistantClientError("orchestrator rejected the request", "ORCHESTRATOR_REJECTED")
        return AssistantReply(
            text=f"He entendido: {text}",
            conversation={"last": text},
            answer={"outcome": "ANSWERED", "text": f"He entendido: {text}", "conversation": None},
            payment_confirmation_required=False,
        )


def test_an_orchestrator_4xx_is_a_recoverable_turn_error_not_a_socket_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G-10: before the fix, every `AssistantClientError` -- including a 4xx, which means only
    that *this turn's own request* was rejected (e.g. its echoed `conversation` failed the
    orchestrator's DTO validation) -- closed the socket with a fatal error, destroying the whole
    session over one bad turn. `ORCHESTRATOR_REJECTED` must instead surface as a non-fatal
    `error` and leave the socket, and the session, usable for the very next turn."""
    rejecting_assistant = _RejectsOnceThenAnswers()
    monkeypatch.setattr(services, "assistant", rejecting_assistant)
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
            ).json()
            with client.websocket_connect(
                body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
            ) as socket:
                session_id = body["sessionId"]
                socket.send_text(_control("session.configure", session_id, 0))
                socket.receive_json()
                socket.send_text(_control("input.start", session_id, 1, "turn-1"))
                socket.send_bytes(b"ok")
                socket.send_text(_control("input.commit", session_id, 2, "turn-1"))
                error = None
                while error is None:
                    item = socket.receive_json()
                    if item["type"] == "error":
                        error = item
                assert error["type"] == "error"
                assert error["code"] == "ORCHESTRATOR_REJECTED"
                assert error["fatal"] is False

                # The socket is still open and the session is ready for a normal next turn.
                socket.send_text(_control("input.start", session_id, 3, "turn-2"))
                socket.send_bytes(b"ok")
                socket.send_text(_control("input.commit", session_id, 4, "turn-2"))
                received: list[dict[str, object]] = []
                while True:
                    item = socket.receive()
                    if item.get("text"):
                        payload = json.loads(item["text"])
                        received.append(payload)
                        if payload["type"] == "turn.completed":
                            break
                final = next(e for e in received if e["type"] == "assistant.text.final")
                final_text = final["text"]
                assert isinstance(final_text, str)
                assert "He entendido" in final_text
                assert rejecting_assistant.calls == 2
    finally:
        sessions.clear()


def test_a_refused_voice_payment_does_not_silence_the_session() -> None:
    """G-1: the refusal fires once and then clears, so the very next turn -- whatever it says,
    a cancel included -- reaches the assistant again. Before the fix, `payment_confirmation_
    required` never cleared and every later turn on the session answered the same refusal
    forever, `assistant.call_count` never rising again."""
    assert isinstance(services.assistant, FakeAssistantClient)
    with TestClient(app) as client:
        before = services.assistant.call_count
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()

            def run_turn(sequence: int, turn_id: str, audio: bytes) -> list[dict[str, object]]:
                socket.send_text(_control("input.start", session_id, sequence, turn_id))
                socket.send_bytes(audio)
                socket.send_text(_control("input.commit", session_id, sequence + 1, turn_id))
                collected: list[dict[str, object]] = []
                while True:
                    item = socket.receive()
                    if item.get("text"):
                        payload = json.loads(item["text"])
                        collected.append(payload)
                        if payload["type"] == "turn.completed":
                            break
                return collected

            run_turn(1, "prepare", b"quiero preparar un pago ")
            refusal_events = run_turn(3, "confirm", b"si")
            refusal = next(e for e in refusal_events if e["type"] == "assistant.text.final")
            refusal_text = refusal["text"]
            assert isinstance(refusal_text, str)
            assert "No puedo confirmar" in refusal_text
            assert services.assistant.call_count == before + 1

            # The next turn is answered normally: the assistant is called again, not refused a
            # second time.
            next_events = run_turn(5, "after-refusal", b"hola")
            answered = next(e for e in next_events if e["type"] == "assistant.text.final")
            answered_text = answered["text"]
            assert isinstance(answered_text, str)
            assert "No puedo confirmar" not in answered_text
            assert answered["paymentConfirmationRequired"] is False
            assert services.assistant.call_count == before + 2

            # G-1 explicitly covers a cancel too: sent right after another refusal, it still
            # reaches the assistant on the turn after.
            run_turn(7, "prepare-again", b"quiero preparar un pago ")
            run_turn(9, "confirm-again", b"si")
            assert services.assistant.call_count == before + 3
            # A cancel with no active turn -- no `response.cancelled` to wait for, but it must
            # not resurrect the latch either: the very next turn still reaches the assistant.
            socket.send_text(_control("response.cancel", session_id, 11))
            after_cancel_events = run_turn(12, "after-cancel", b"hola")
            after_cancel = next(
                e for e in after_cancel_events if e["type"] == "assistant.text.final"
            )
            after_cancel_text = after_cancel["text"]
            assert isinstance(after_cancel_text, str)
            assert "No puedo confirmar" not in after_cancel_text
            assert services.assistant.call_count == before + 4
    sessions.clear()


class _Socket:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def send_json(self, value: dict[str, object]) -> None:
        self.events.append(value)

    async def close(self, code: int = 1000) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("active_state", [SessionState.THINKING, SessionState.SPEAKING])
async def test_barge_in_reports_old_turn_before_new_turn(active_state: SessionState) -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    if active_state == SessionState.SPEAKING:
        session.machine.speaking()
    socket = _Socket()
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
    )
    assert socket.events[0]["type"] == "response.cancelled"
    assert socket.events[0]["turnId"] == "old-turn"
    assert session.machine.state.value == SessionState.LISTENING.value
    assert session.machine.turn_id == "new-turn"


@pytest.mark.asyncio
async def test_barge_in_ack_is_one_shot_and_has_no_waiting_window() -> None:
    ticks = iter((0.0, 0.010, 0.020, 0.030, 0.040, 0.050))
    session = VoiceSession("session", "credential", services, monotonic=lambda: next(ticks))
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    socket = _Socket()
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
    )
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("response.cancel", "session", 1))),
    )
    assert session.last_cancel_ack_ms == pytest.approx(10.0)
    assert session.last_cancel_ack_ms is not None
    assert session.last_cancel_ack_ms < 250
    assert [event["type"] for event in socket.events].count("response.cancelled") == 1


@pytest.mark.asyncio
async def test_barge_in_does_not_wait_for_a_provider_that_suppresses_cancellation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_provider() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    session.task = asyncio.create_task(stubborn_provider())
    await entered.wait()
    socket = _Socket()

    await asyncio.wait_for(
        handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
        ),
        timeout=0.25,
    )

    assert session.machine.state == SessionState.LISTENING
    assert session.machine.turn_id == "new-turn"
    assert socket.events[0]["type"] == "response.cancelled"
    assert len(session.retired_tasks) == 1
    release.set()
    await asyncio.gather(*session.retired_tasks)
    await asyncio.sleep(0)
    assert session.retired_tasks == set()


@pytest.mark.asyncio
async def test_cancel_returns_none_when_state_machine_does_not_cancel() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("listening-turn")
    assert await session.cancel() is None
    assert session.machine.state.value == SessionState.LISTENING.value


@pytest.mark.asyncio
async def test_session_and_turn_mismatch_are_rejected_without_mutating_state() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    socket = _Socket()
    with pytest.raises(SessionProtocolError) as session_error:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.start", "other", 1, "turn"))),
        )
    assert session_error.value.code == "SESSION_MISMATCH"
    assert session.machine.state == SessionState.READY
    session.machine.start_input("active")
    with pytest.raises(SessionProtocolError) as turn_error:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.commit", "session", 2, "other"))),
        )
    assert turn_error.value.code == "TURN_MISMATCH"
    assert session.machine.state.value == SessionState.LISTENING.value


@pytest.mark.asyncio
async def test_audio_frame_rules_are_checked_without_transport_races() -> None:
    session = VoiceSession("session", "credential", services)
    socket = _Socket()
    session.machine.configure()
    with pytest.raises(SessionProtocolError) as outside_listening:
        await receive_audio(cast(WebSocket, socket), session, b"ok")
    assert outside_listening.value.code == "INVALID_STATE"
    assert session.audio == bytearray()

    session.machine.start_input("turn")
    with pytest.raises(SessionProtocolError) as odd_frame:
        await receive_audio(cast(WebSocket, socket), session, b"x")
    assert odd_frame.value.code == "INVALID_AUDIO"
    assert session.audio == bytearray()
    await receive_audio(cast(WebSocket, socket), session, b"ok")
    session.machine.commit()
    with pytest.raises(SessionProtocolError) as after_commit:
        await receive_audio(cast(WebSocket, socket), session, b"ok")
    assert after_commit.value.code == "INVALID_STATE"
    assert session.audio == bytearray(b"ok")


def test_ping_and_repeated_configure_are_deterministic() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("session.configure", sid, 1))
            assert socket.receive_json()["code"] == "INVALID_STATE"
            socket.send_text(_control("ping", sid, 2))
            assert socket.receive_json()["type"] == "pong"
    sessions.clear()


@pytest.mark.asyncio
async def test_repeated_commit_is_invalid_state_before_processing_can_run() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("turn")
    socket = _Socket()
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.commit", "session", 0, "turn")))
    )
    with pytest.raises(SessionProtocolError) as repeated:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.commit", "session", 1, "turn"))),
        )
    assert repeated.value.code == "INVALID_STATE"
    await session.cancel()


def test_close_removes_session_and_cancel_without_active_response_is_silent() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            socket.receive_json()
            socket.send_text(_control("response.cancel", sid, 1))
            socket.send_text(_control("ping", sid, 2))
            assert socket.receive_json()["type"] == "pong"
            socket.send_text(_control("session.close", sid, 3))
        assert sid not in sessions


def test_disconnect_removes_session_without_explicit_close() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            socket.receive_json()
            assert sid in sessions
        assert sid not in sessions


class _BlockingSTT:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        self.entered.set()
        await self.release.wait()
        return "texto antiguo"


class _RecordingTransport(_Socket):
    def __init__(self) -> None:
        super().__init__()
        self.audio: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)


@pytest.mark.asyncio
async def test_fencing_discards_late_turn_events_and_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocking_stt = _BlockingSTT()
    monkeypatch.setattr(services, "stt", blocking_stt)
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old")
    session.audio.extend(b"ok")
    session.machine.commit()
    transport = _RecordingTransport()
    task = asyncio.create_task(_process_turn(transport, session, "old"))
    await blocking_stt.entered.wait()
    events_at_cancel = len(transport.events)
    session.cancel_generation += 1
    assert session.machine.cancel() == "old"
    blocking_stt.release.set()
    await task
    assert len(transport.events) == events_at_cancel
    assert transport.audio == []


class _FailingSTT:
    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        raise SpeechToTextTimeoutError("secret audio/transcript must not leak")


class _FailingTTS:
    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def stream() -> AsyncIterator[bytes]:
            raise RuntimeError("tts down")
            yield b"unreachable"

        return stream()


class _CancellationResistantTTS:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def stream() -> AsyncIterator[bytes]:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.release.wait()
            yield b"stale audio"

        return stream()


@pytest.mark.asyncio
async def test_barge_in_during_tts_still_keeps_the_chart_the_turn_drew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G-4: a barge-in is cancellation during TTS -- the normal voice gesture, not a reason to
    discard the chart the turn already drew. Before the fix, the conversation committed only
    after `audio.completed` survived, so interrupting playback of a charted answer left the
    *next* turn's `session.conversation` pointing at the previous, uncharted turn -- a follow-up
    "ponlo en horizontal" would hit `VISUAL_NEEDS_TARGET` or restyle a stale chart. The fix moves
    the commit to right after the reply is known, before synthesis starts, so it survives a
    cancellation that only ever fences *sends*, never session state, from this point on."""
    original_tts = services.tts
    resistant_tts = _CancellationResistantTTS()
    monkeypatch.setattr(services, "tts", resistant_tts)
    session = VoiceSession("session", "credential", services)
    # A stale visual context from an earlier, already-committed turn -- I-2's guard is
    # meaningless here unless the *next* turn's conversation actually carries one.
    original_conversation = {
        "last": "previous",
        "visual_contexts": [
            {"chart_id": "chart-fake-1", "turn_id": "11111111-1111-1111-1111-111111111111"}
        ],
    }
    session.conversation = original_conversation
    session.machine.configure()
    session.machine.start_input("old-turn")
    # A charted old turn: the probe below is meaningless if there is no chart identity to fence.
    session.audio.extend(b"old grafico text")
    session.machine.commit()
    transport = _RecordingTransport()
    session.task = asyncio.create_task(process_turn(transport, session, "old-turn"))
    await resistant_tts.entered.wait()

    try:
        # By the time the TTS generator has entered, assistant.text.final and audio.started for
        # the old, charted turn are already on the wire -- and charted, or this probe would be
        # vacuous.
        events_before_cancel = list(transport.events)
        charted_before_cancel = [
            item for item in events_before_cancel if item.get("chartId") == "chart-fake-1"
        ]
        assert charted_before_cancel

        await handle_control(
            transport,
            session,
            parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
        )
    finally:
        # Always released, assertions above notwithstanding: otherwise a failure here leaves
        # the resistant TTS's generator parked on an Event nothing will ever set, and teardown
        # hangs rather than reporting the failure.
        resistant_tts.release.set()
    await asyncio.gather(*session.retired_tasks)

    # G-4: the barge-in fenced the *audio*, never the conversation the answer already produced --
    # `session.conversation` is the cancelled turn's own charted reply, not the stale value from
    # before it ran. `is not original_conversation` first: a bug that reverted to committing
    # nothing would leave the identity comparison below vacuously true only by accident.
    assert session.conversation is not original_conversation
    assert session.conversation == {
        "previous": original_conversation,
        "last": "old grafico text",
        "visual_contexts": [
            {"chart_id": "chart-fake-1", "turn_id": "11111111-1111-1111-1111-111111111111"}
        ],
    }
    assert session.machine.turn_id == "new-turn"
    assert transport.audio == []
    # No event carrying the old turn's chart identity reaches the transport after the cancel
    # point: fencing discards the old turn's late work regardless of whether it was charted.
    events_after_cancel = transport.events[len(events_before_cancel) :]
    assert not any(item.get("chartId") == "chart-fake-1" for item in events_after_cancel)

    # The new turn is uncharted; its events carry no identity at all -- none of the old
    # turn's leaks onto it.
    # M-4: through `monkeypatch`, the same fixture that installed `resistant_tts` above, rather
    # than a direct attribute set -- correct today only because the fixture would undo it anyway.
    monkeypatch.setattr(services, "tts", original_tts)
    session.audio.extend(b"hola")
    session.machine.commit()
    charted_conversation_after_barge_in = session.conversation
    events_before_new_turn = len(transport.events)
    await process_turn(transport, session, "new-turn")
    new_turn_events = transport.events[events_before_new_turn:]
    assert new_turn_events
    for item in new_turn_events:
        assert "chartId" not in item
        assert "visualTurnId" not in item
    # G-4's own claim: the *next* turn sees the charted conversation the barge-in interrupted --
    # the fake assistant was asked with it as `conversation` and echoes it back under `previous`.
    next_turn_conversation = session.conversation
    assert isinstance(next_turn_conversation, dict)
    assert next_turn_conversation["previous"] is charted_conversation_after_barge_in


@pytest.mark.parametrize("provider", ["stt", "tts"])
def test_provider_errors_are_typed_and_close_socket(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = getattr(services, provider)
    monkeypatch.setattr(services, provider, _FailingSTT() if provider == "stt" else _FailingTTS())
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
            ).json()
            with client.websocket_connect(
                body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
            ) as socket:
                session_id = body["sessionId"]
                socket.send_text(_control("session.configure", session_id, 0))
                socket.receive_json()
                socket.send_text(_control("input.start", session_id, 1, "turn"))
                socket.send_bytes(b"ok")
                socket.send_text(_control("input.commit", session_id, 2, "turn"))
                error = None
                while error is None:
                    item = socket.receive_json()
                    if item["type"] == "error":
                        error = item
                assert error["code"] == ("STT_TIMEOUT" if provider == "stt" else "PROVIDER_ERROR")
                assert error["fatal"] is True
                if provider == "stt":
                    assert error["message"] == "speech provider failed"
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                assert closed.value.code == 1011
    finally:
        monkeypatch.setattr(services, provider, original)
        sessions.clear()


def test_duplicate_sequence_is_fatal_and_closes_after_error() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            socket.send_text(_control("ping", session_id, 0))
            assert socket.receive_json()["fatal"] is True
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 4400
    sessions.clear()


def test_oversized_frame_is_rejected() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes(b"x" * (64 * 1024 + 1))
            assert socket.receive_json()["code"] == "FRAME_TOO_LARGE"
    sessions.clear()
