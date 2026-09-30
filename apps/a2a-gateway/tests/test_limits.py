import httpx
from a2a.utils.errors import InternalError
from fakes import AUTH, HostRouter, Recorder, data_agent, gateway_for, raising, reply_with
from fastapi import FastAPI

ENV = {"A2A_GATEWAY_CREDENTIAL_GYM_NORTE": "k1"}
BODY = {"skillId": "timetable", "messageId": "m-1", "text": "Free on Monday?"}


async def _post(app: FastAPI) -> httpx.Response:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    return await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)


async def test_a_reply_over_the_limits_is_reply_too_large() -> None:
    text = reply_with(artifact=[{"text": "x" * 50}])
    app = gateway_for({"gym-norte": data_agent(Recorder(), script=text)}, ENV, max_text_bytes=40)
    response = await _post(app)
    assert (response.status_code, response.json()["code"]) == (502, "reply_too_large")

    data = reply_with(artifact=[{"data": {"k": "y" * 200}}])
    app = gateway_for({"gym-norte": data_agent(Recorder(), script=data)}, ENV, max_data_bytes=100)
    response = await _post(app)
    assert (response.status_code, response.json()["code"]) == (502, "reply_too_large")


async def test_a_timeout_is_504() -> None:
    norte = data_agent(Recorder())
    cards = HostRouter({"gym-norte": norte})

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise httpx.ReadTimeout("slow", request=request)
            return await cards.handle_async_request(request)

    response = await _post(gateway_for({"gym-norte": norte}, ENV, transport=Slow()))
    assert (response.status_code, response.json()["code"]) == (504, "timeout")


async def test_an_agent_error_is_502() -> None:
    script = raising(InternalError("boom"))
    app = gateway_for({"gym-norte": data_agent(Recorder(), script=script)}, ENV)
    response = await _post(app)
    assert (response.status_code, response.json()["code"]) == (502, "agent_error")


async def test_an_invalid_reply_is_502_reply_invalid() -> None:
    norte = data_agent(Recorder())
    cards = HostRouter({"gym-norte": norte})

    class NotJsonRpc(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"hello": "world"})
            return await cards.handle_async_request(request)

    response = await _post(gateway_for({"gym-norte": norte}, ENV, transport=NotJsonRpc()))
    assert (response.status_code, response.json()["code"]) == (502, "reply_invalid")


async def test_the_gateway_never_retries() -> None:
    norte = Recorder()
    script = raising(InternalError("boom"))
    app = gateway_for({"gym-norte": data_agent(norte, script=script)}, ENV)
    response = await _post(app)
    assert response.status_code == 502
    assert len(norte.headers) == 1
