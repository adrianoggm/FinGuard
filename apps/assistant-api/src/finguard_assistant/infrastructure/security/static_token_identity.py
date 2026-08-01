"""Development identity: a fixed table of tokens.

This is not the design. §12.2 asks for OAuth 2.0 / OpenID Connect with short-lived
tokens and per-operation scopes, and this adapter exists so the *seam* is real before
the provider is — the account already comes from the identity rather than from the
request body, which is the property that actually matters, and swapping this for an
OIDC adapter changes one module.

What it deliberately does not pretend to be:

- Tokens do not expire. There is no refresh, no revocation, no rotation.
- There are no scopes, so every authenticated caller can do everything.
- Comparison is constant-time, which is the one real property it keeps: these are
  shared secrets, and leaking their length or prefix through timing is free to avoid.

It refuses to run with no tokens configured. An empty table would authenticate nobody,
which sounds safe and is worse: the service would start, every request would 401, and
the first person to debug it would be tempted to loosen something.
"""

import hmac
from typing import Final

from finguard_assistant.domain.errors import NotAuthenticatedError
from finguard_assistant.domain.value_objects.caller import Caller

# A token this short is only defensible because it never leaves 127.0.0.1 (rule 13).
MIN_TOKEN_LENGTH: Final = 16


class StaticTokenIdentity:
    """Implements CallerIdentityPort against a table given at startup."""

    def __init__(self, callers: dict[str, Caller]) -> None:
        if not callers:
            raise ValueError("no development tokens configured; the service would deny everyone")
        for token in callers:
            if len(token) < MIN_TOKEN_LENGTH:
                raise ValueError(f"a development token needs {MIN_TOKEN_LENGTH}+ characters")
        self._callers = dict(callers)

    async def resolve(self, credential: str | None) -> Caller:
        if not credential:
            raise NotAuthenticatedError()

        token = credential.removeprefix("Bearer ").strip()

        # Compared against every entry rather than looked up, so the work does not
        # depend on which token was sent. A dict lookup would leak membership through
        # timing; with a handful of development tokens the cost of not doing that is
        # nothing.
        found: Caller | None = None
        for known, caller in self._callers.items():
            if hmac.compare_digest(token, known):
                found = caller

        if found is None:
            raise NotAuthenticatedError()
        return found
