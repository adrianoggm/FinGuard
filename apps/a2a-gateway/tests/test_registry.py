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
