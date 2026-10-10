from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request

from finguard_orchestrator.application import CommandBus, QueryBus
from finguard_orchestrator.domain.errors import FeatureDisabledError
from finguard_orchestrator.domain.ports.caller_identity import CallerIdentityPort
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

if TYPE_CHECKING:
    from finguard_orchestrator.application import AskFinancialQuestionHandler


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus


def get_ask_handler(request: Request) -> AskFinancialQuestionHandler | None:
    """The compatibility handler for the legacy streaming route, if that path is enabled.

    A bus is request and response: one message in, one answer out. A stream is a
    different shape. Graph mode does not construct the handler; the route must have already been
    answered by the graph or fail closed before reaching its compatibility path.
    """
    handler: AskFinancialQuestionHandler | None = request.app.state.ask_handler
    return handler


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


def payments_enabled(request: Request) -> None:
    """Refuses the payment endpoints unless a deployment opted in.

    Separate from `questions_enabled` because they are separate risks: a deployment that answers
    questions has not thereby agreed to move money. Off by default, like the other one, so a
    surface that can pay somebody is something an operator turned on.
    """
    if not request.app.state.payments_enabled:
        raise FeatureDisabledError("payments are disabled; set ORCHESTRATOR_PAYMENTS_ENABLED=true")
    if getattr(request.app.state, "conversation_graph_enabled", False):
        raise FeatureDisabledError(
            "direct payment routes are disabled in graph mode; use the unified conversation"
        )


def metrics_enabled(request: Request) -> None:
    if not request.app.state.metrics_enabled:
        raise FeatureDisabledError(
            "saved metrics are disabled; set ORCHESTRATOR_METRICS_ENABLED=true"
        )


def metrics_available(request: Request) -> bool:
    """Whether question responses may offer a save action without refusing the question."""
    enabled: bool = request.app.state.metrics_enabled
    return enabled


def known_categories(request: Request) -> tuple[str, ...]:
    """The categories a screen may offer.

    Read from the application state rather than imported from the config: a controller that imports
    the composition root closes a circle — `bootstrap.config` is reached through `infrastructure`,
    which is where the controllers live, and the service refused to start with a partially
    initialised module. The same reason every other setting arrives this way.
    """
    categories: tuple[str, ...] = request.app.state.categories
    return categories


def payments_available(request: Request) -> bool:
    """Whether payments are on, without refusing when they are not.

    The chat route needs to *answer* rather than fail: a sentence asking for a payment on a
    deployment that does not make them is answered with words, not with a 503.
    """
    enabled: bool = request.app.state.payments_enabled
    return enabled


def turn_interpreter(request: Request) -> TurnInterpreterPort:
    """Whatever reads a turn on this deployment.

    Which one is a startup decision, and the point of the port is that the routes cannot tell:
    deterministic today, the same rules with a model shadowing them when the shadow is switched on,
    a model deciding once the numbers say so. One place chooses; nothing downstream changes.
    """
    interpreter: TurnInterpreterPort = request.app.state.turn_interpreter
    return interpreter


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


def attempt_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> str:
    """The caller's name for this attempt, or a fresh one.

    Inventing it was the defect. A key minted per request differs between a request and its retry,
    which is the same as having no key: a client that resent a request whose answer it never saw
    got a second intent for one intention. No money moves — confirming is keyed on the intent — but
    the holder is left holding two previews of a payment they meant once, and only one of them can
    be the one they approve.

    Optional, unlike banking's, which requires one: this is the surface a browser talks to and a
    client written before the header existed has to keep working. What sending none costs is
    exactly the orphan above. What sending the same one for a *different* payment costs is a 409
    from banking, which is the guard that makes handing this over safe at all.

    A dependency rather than a helper each route calls, so a route that forgets it forgets it
    visibly, and so the header appears in the schema.
    """
    return idempotency_key or str(uuid.uuid4())


def required_attempt_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> str:
    """Require a caller-stable key for a durable graph turn and its HTTP retries.

    Compatibility routes retain ``attempt_key`` for clients predating the header. The graph cannot
    mint a replacement per request: that would make a retry a second checkpoint/reservation.
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
