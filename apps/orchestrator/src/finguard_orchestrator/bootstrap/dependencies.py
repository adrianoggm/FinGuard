from datetime import date
from typing import Annotated

from fastapi import Depends, Header, Request

from finguard_orchestrator.application import AskFinancialQuestionHandler, CommandBus, QueryBus
from finguard_orchestrator.domain.errors import FeatureDisabledError
from finguard_orchestrator.domain.ports.caller_identity import CallerIdentityPort
from finguard_orchestrator.domain.value_objects.caller import Caller


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus


def get_ask_handler(request: Request) -> AskFinancialQuestionHandler:
    """The one handler a controller reaches without the bus.

    A bus is request and response: one message in, one answer out. A stream is a
    different shape, and bending `ask` into returning an async iterator would make every
    query pay for the one that streams. So this route takes the handler directly and
    says so, rather than quietly widening the bus.
    """
    handler: AskFinancialQuestionHandler = request.app.state.ask_handler
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
