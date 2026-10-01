import re

import pytest

from finguard_a2a.adapters.outbound.env_registry import EnvRegistry


def test_an_agent_is_one_url_and_its_id_is_the_host() -> None:
    registry = EnvRegistry(
        "http://gym-norte:8000, http://gym-sur:8000/ ,https://agents.example.com/gym"
    )
    assert [(a.id, a.base_url) for a in registry.agents()] == [
        ("gym-norte", "http://gym-norte:8000"),
        ("gym-sur", "http://gym-sur:8000"),
        ("agents-example-com", "https://agents.example.com/gym"),
    ]


def test_a_repeated_or_malformed_url_stops_the_service() -> None:
    for bad in ("http://a:1,http://a:1", "gym-norte", "ftp://x", ""):
        with pytest.raises(ValueError):
            EnvRegistry(bad)


@pytest.mark.parametrize(
    ("url", "agent_id"),
    [
        ("http://gym_norte:8000", "gym_norte"),
        ("http://[::1]:8000", "::1"),
        (f"http://{'g' * 65}:8000", "g" * 65),
    ],
)
def test_an_id_the_orchestrator_cannot_accept_stops_the_service_and_is_named(
    url: str, agent_id: str
) -> None:
    """Ruling R30: the orchestrator holds an agent id as `[a-z0-9-]{1,64}` and puts it in a path;
    a gateway listing any other id would offer an agent nobody can ask."""
    with pytest.raises(ValueError, match=re.escape(repr(agent_id))):
        EnvRegistry(f"http://gym-sur:8000,{url}")
