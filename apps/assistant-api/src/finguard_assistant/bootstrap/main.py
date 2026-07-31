from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from finguard_assistant.adapters.inbound.http.routes import admin, router
from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.bootstrap.config import get_settings
from finguard_assistant.domain.errors import UnknownProviderError

app = FastAPI(title="FinGuard Assistant API", version="0.1.0")

# The active policy is created at startup and lives in the application state: the
# service is deliberately not stateless, its configuration changes at runtime.
app.state.policies = PolicyStore(get_settings().default_policy())

app.include_router(router)
app.include_router(admin)


@app.exception_handler(UnknownProviderError)
async def unknown_provider(request: Request, exc: Exception) -> JSONResponse:
    available = exc.available if isinstance(exc, UnknownProviderError) else []
    return JSONResponse({"detail": str(exc), "available": available}, status_code=422)
