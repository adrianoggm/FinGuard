"""What a gym learns about the person: a pairwise pseudonym and nothing else (spec §5.4).

The pattern of OpenID Connect's pairwise subject identifiers: stable for one person at one agent,
different at every other agent, so two gyms comparing notes cannot join a person.
"""

import base64
import re

import pytest

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services.member_reference import member_reference

KEY = b"pairwise-test-key"


def test_it_is_stable_for_one_holder_at_one_agent() -> None:
    assert member_reference(KEY, "holder-1", "gym-norte") == member_reference(
        KEY, "holder-1", "gym-norte"
    )


def test_it_differs_across_agents_and_across_holders_and_keys() -> None:
    one = member_reference(KEY, "holder-1", "gym-norte")
    assert one != member_reference(KEY, "holder-1", "gym-sur")
    assert one != member_reference(KEY, "holder-2", "gym-norte")
    assert one != member_reference(b"another-key", "holder-1", "gym-norte")


def test_the_separator_keeps_holder_and_agent_apart() -> None:
    """Without it, holder "a" at agent "b-c" and holder "a-b" at agent "c" would be one string."""
    assert member_reference(KEY, "a", "b-c") != member_reference(KEY, "a-b", "c")


def test_it_is_base64url_without_padding_and_carries_no_holder_reference() -> None:
    ref = member_reference(KEY, "holder-1", "gym-norte")
    assert len(ref) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", ref)
    assert len(base64.urlsafe_b64decode(ref + "=")) == 32
    assert "holder-1" not in ref and "holder" not in ref


def test_an_empty_key_is_refused() -> None:
    """HMAC under an empty key is a hash anybody can compute: every gym could join everybody."""
    with pytest.raises(InvalidValueError):
        member_reference(b"", "holder-1", "gym-norte")
