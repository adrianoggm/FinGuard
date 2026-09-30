"""What a gym learns about the person: `memberRef`, and nothing else (spec §5.4).

A pairwise pseudonymous identifier, the pattern of OpenID Connect's pairwise subject identifiers:
HMAC-SHA256 under this deployment's key over the holder and the agent, so it is stable for one
person at one agent and different at every other -- two gyms comparing their members cannot join a
person, and neither can reverse it to the holder without the key. Never a name, an account, an IBAN
or the holder reference itself.

The NUL between the two halves keeps them apart: without it holder `a` at agent `b-c` and holder
`a-b` at agent `c` would be the same message.
"""

import base64
import hmac
from hashlib import sha256

from finguard_orchestrator.domain.errors import InvalidValueError


def member_reference(key: bytes, holder_reference: str, agent_id: str) -> str:
    """43 characters of base64url, unpadded."""
    if not key:
        # An empty key is a hash anybody can compute, which is the join this exists to prevent.
        raise InvalidValueError("a pairwise member reference needs a key")
    digest = hmac.new(key, f"{holder_reference}\x00{agent_id}".encode(), sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
