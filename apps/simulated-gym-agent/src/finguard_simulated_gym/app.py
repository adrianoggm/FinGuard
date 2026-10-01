import hmac
import html
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from finguard_simulated_gym.cards import card_for, legacy_card
from finguard_simulated_gym.config import Settings
from finguard_simulated_gym.executor import GymExecutor
from finguard_simulated_gym.timetable import Timetable

_PAGE = (
    '<!doctype html><html lang="es"><meta charset="utf-8"><title>Gimnasio</title>'
    "<body>{body}</body></html>"
)
_FORM = _PAGE.format(
    body=(
        '<h1>Vincular cuenta de socio</h1><form method="post" action="/vincular">'
        '<input type="hidden" name="ref" value="{{ref}}">'
        '<label>Número de socio <input type="text" name="socio"></label> '
        '<button type="submit">Vincular</button></form>'
    )
)


def _page(body: str) -> str:
    return _PAGE.format(body=f"<p>{html.escape(body)}</p>")


def _authorised(request: Request, settings: Settings) -> bool:
    if settings.style == "norte":
        sent, expected = request.headers.get("X-Gym-Key", ""), settings.api_key or ""
    else:
        sent, expected = request.headers.get("Authorization", ""), f"Bearer {settings.bearer_token}"
    return hmac.compare_digest(sent.encode(), expected.encode())


def build_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    if settings.style == "legacy":
        app = FastAPI(title="Gimnasio Antiguo")
        raw = legacy_card(settings)

        @app.get("/.well-known/agent-card.json")
        async def legacy_agent_card() -> dict[str, Any]:
            return raw

    else:
        card = card_for(settings)
        executor = GymExecutor(Timetable(settings.seed, settings.current_day()), settings)
        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        app = FastAPI(title=card.name)
        add_a2a_routes_to_fastapi(
            app,
            agent_card_routes=create_agent_card_routes(card),
            jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a/jsonrpc"),
            rest_routes=create_rest_routes(handler, path_prefix="/a2a/rest"),
        )
        app.state.card = card
        app.state.executor = executor

        @app.middleware("http")
        async def require_credential(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            if request.url.path.startswith("/a2a/") and not _authorised(request, settings):
                return JSONResponse({"error": "unauthorised"}, status_code=401)
            return await call_next(request)

        @app.get("/vincular", response_class=HTMLResponse)
        async def link_form(ref: str = "") -> str:
            return _FORM.format(ref=html.escape(ref, quote=True))

        @app.post("/vincular")
        async def link_member(request: Request) -> HTMLResponse:
            form = parse_qs((await request.body()).decode())
            ref = form.get("ref", [""])[0]
            socio = form.get("socio", [""])[0]
            if not ref or not socio:
                return HTMLResponse(_page("Faltan datos."), status_code=400)
            executor.linked[ref] = socio
            return HTMLResponse(_page("Cuenta vinculada. Ya puedes volver a FinGuard y reservar."))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
