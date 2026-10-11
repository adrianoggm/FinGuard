from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import Depends, Header, Request

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.domain.errors import FeatureDisabledError
from finguard_orchestrator.domain.ports.caller_identity import CallerIdentityPort
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus


async def authenticated_caller(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Caller:
    """Identity, the first layer of §12.1 and the one that was missing.

    A dependency rather than middleware, so a route that needs a caller says so in its
    signature and a route that does not cannot accidentally receive one. The account it
    carries is where the account now comes from — the request body no longer has a say.
    """
    identity: CallerIdentityPort = request.app.state.identity
    return await identity.resolve(authorization)


def questions_enabled(request: Request) -> None:
    """Refuses the financial endpoints unless a deployment opted in.

    A dependency rather than a check inside each route, so a route added later is
    disabled by forgetting rather than enabled by it.
    """
    if not request.app.state.questions_enabled:
        raise FeatureDisabledError(
            "financial questions are disabled; set ORCHESTRATOR_QUESTIONS_ENABLED=true"
        )


def metrics_enabled(request: Request) -> None:
    if not request.app.state.metrics_enabled:
        raise FeatureDisabledError(
            "saved metrics are disabled; set ORCHESTRATOR_METRICS_ENABLED=true"
        )


def known_categories(request: Request) -> tuple[str, ...]:
    """The categories a screen may offer.

    Read from the application state rather than imported from the config: a controller that imports
    the composition root closes a circle — `bootstrap.config` is reached through `infrastructure`,
    which is where the controllers live, and the service refused to start with a partially
    initialised module. The same reason every other setting arrives this way.
    """
    categories: tuple[str, ...] = request.app.state.categories
    return categories


def reporting_today(who: Annotated[Caller, Depends(authenticated_caller)]) -> date:
    """What day it is *for this holder*.

    The clock lives here and nowhere deeper, exactly as in banking: the planner takes today
    as an argument, so a question asked on the 15th resolves the same way when the
    evaluation replays it in March.

    It used to read a module constant, which gave every holder the same day and left the
    zone able to differ silently from the one banking bins timestamps with. It now comes
    from the credential, so a holder in the Canaries is answered about their own day and the
    zone travels to banking beside the date.
    """
    return who.today_in()


def required_attempt_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> str:
    """Require a caller-stable key for a durable graph turn and its HTTP retries.

    The service cannot mint a replacement per request: that would make a retry a second
    checkpoint/reservation, and for a payment a second preview.
    """
    return idempotency_key


def requires(scope: Scope) -> Callable[[Caller], Caller]:
    """A dependency that authenticates and then checks one permission.

    Declared on the route rather than checked in the handler, so the permission a route
    needs is visible where somebody reads what the route is — and so a new route without one
    is a missing dependency rather than a missing `if` nobody notices.
    """

    def guard(who: Annotated[Caller, Depends(authenticated_caller)]) -> Caller:
        who.assert_may(scope)
        return who

    return guard
