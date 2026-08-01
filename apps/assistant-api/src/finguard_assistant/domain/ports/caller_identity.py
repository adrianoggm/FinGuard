from typing import Protocol, runtime_checkable

from finguard_assistant.domain.value_objects.caller import Caller


@runtime_checkable
class CallerIdentityPort(Protocol):
    """Turns a credential into an authenticated caller, or refuses.

    A port because the credential is a technology choice and the identity is not. Today
    the adapter is a static development token; §12.2 asks for OAuth 2.0 with short-lived
    tokens and per-operation scopes, and that is one adapter away rather than a change
    that reaches the application layer.

    Raises NotAuthenticatedError. It never answers with an anonymous caller, because a
    caller that is "nobody" would have to be given permissions somewhere, and deny by
    default is easier to keep true when there is no such object to reason about.
    """

    async def resolve(self, credential: str | None) -> Caller: ...
