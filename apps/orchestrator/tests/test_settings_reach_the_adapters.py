"""Operational numbers belong to the deployment, not to a constant in an adapter.

Every value here was already a constructor parameter, which made the adapters testable and
did nothing for the person running the service: changing a timeout meant editing Python.
The distance between "it is a parameter" and "it is configurable" is exactly this file.

Each one is a knob somebody will need at an awkward moment. A timeout because a machine is
slower than the one it was tuned on. `num_ctx` because a bigger model needs a smaller
window to stay resident. The seed because a demo wants variety and a measurement wants
none. Waiting for that moment to make them configurable means changing code under
pressure.
"""

from finguard_orchestrator.bootstrap.config import Settings
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider


def test_the_ledger_client_takes_its_timeout_and_evidence_size_from_settings() -> None:
    settings = Settings(banking_timeout_seconds=3.5, supporting_movements=4)

    client = HttpFinancialQuery(
        base_url=settings.banking_base_url,
        timeout_seconds=settings.banking_timeout_seconds,
        supporting_movements=settings.supporting_movements,
    )

    assert client.timeout_seconds == 3.5
    assert client.supporting_movements == 4


def test_the_provider_takes_every_operational_number_from_settings() -> None:
    settings = Settings(
        ollama_keep_alive="5m",
        ollama_timeout_seconds=30.0,
        ollama_context_tokens=8192,
        ollama_seed=7,
        ollama_think="low",
    )

    provider = OllamaLlmProvider(
        base_url=settings.ollama_base_url,
        keep_alive=settings.ollama_keep_alive,
        timeout_seconds=settings.ollama_timeout_seconds,
        context_tokens=settings.ollama_context_tokens,
        seed=settings.ollama_seed,
        think=settings.ollama_think,
    )

    assert provider.keep_alive == "5m"
    assert provider.timeout_seconds == 30.0
    assert provider.context_tokens == 8192
    assert provider.seed == 7
    assert provider.think == "low"


def test_the_planner_sampling_is_configuration() -> None:
    """Temperature and the output ceiling were literals at the composition root, which is
    the same defect one layer up: a number nobody can change without a commit."""
    settings = Settings(planner_temperature=0.3, planner_max_output_tokens=512)

    assert settings.planner_temperature == 0.3
    assert settings.planner_max_output_tokens == 512


def test_the_defaults_are_the_measured_ones() -> None:
    """The numbers in the adapters were not arbitrary — `num_ctx: 4096` and a pinned seed
    each came from an experiment written up in evaluation/planning/RESULTS.md. Making them
    configurable must not quietly change them."""
    settings = Settings(ollama_think=False)

    assert settings.ollama_context_tokens == 4096
    assert settings.ollama_seed == 0
    assert settings.ollama_think is False
    assert settings.ollama_keep_alive == "30m"
    assert settings.planner_temperature == 0.0
    assert settings.supporting_movements == 10
