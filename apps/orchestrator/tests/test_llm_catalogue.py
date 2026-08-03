"""The catalogue comes from configuration: adding a model must not need a release."""

from pathlib import Path

import pytest

from finguard_orchestrator.domain.enums import FallbackPolicy
from finguard_orchestrator.domain.errors import InvalidValueError, UnknownModelError
from finguard_orchestrator.infrastructure.config.toml_model_catalogue import TomlModelCatalogue

CATALOGUE = """
fallback = "NEXT"
default = "fast"
temperature = 0.7
max_output_tokens = 512

[[models]]
alias = "fast"
provider = "echo"
model = "echo-1"

[[models]]
alias = "thorough"
provider = "echo"
model = "echo-2"
"""


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "llm.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_it_loads_models_providers_and_the_fallback_policy(tmp_path: Path) -> None:
    catalogue = TomlModelCatalogue.from_file(write(tmp_path, CATALOGUE))

    assert catalogue.fallback is FallbackPolicy.NEXT
    assert [model.alias for model in catalogue.models()] == ["fast", "thorough"]
    assert catalogue.default().alias == "fast"
    # Two models of the same provider are two separate choices.
    assert {model.model for model in catalogue.models()} == {"echo-1", "echo-2"}
    assert catalogue.get("thorough").provider == "echo"


def test_the_default_policy_comes_from_the_file(tmp_path: Path) -> None:
    policy = TomlModelCatalogue.from_file(write(tmp_path, CATALOGUE)).default_policy()

    assert policy.model.alias == "fast"
    assert policy.temperature == 0.7
    assert policy.max_output_tokens == 512


def test_the_fallback_order_is_the_file_order(tmp_path: Path) -> None:
    reversed_file = CATALOGUE.replace('default = "fast"', 'default = "thorough"')

    catalogue = TomlModelCatalogue.from_file(write(tmp_path, reversed_file))

    assert catalogue.default().alias == "thorough"
    assert [model.alias for model in catalogue.models()] == ["fast", "thorough"]


def test_an_unknown_alias_is_rejected_and_says_what_exists(tmp_path: Path) -> None:
    catalogue = TomlModelCatalogue.from_file(write(tmp_path, CATALOGUE))

    with pytest.raises(UnknownModelError) as raised:
        catalogue.get("ghost")

    assert raised.value.available == ["fast", "thorough"]


def test_a_default_outside_the_catalogue_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnknownModelError):
        TomlModelCatalogue.from_file(write(tmp_path, CATALOGUE.replace('"fast"', '"ghost"', 1)))


def test_duplicate_aliases_are_rejected(tmp_path: Path) -> None:
    duplicated = CATALOGUE.replace('alias = "thorough"', 'alias = "fast"')

    with pytest.raises(InvalidValueError, match="duplicate aliases"):
        TomlModelCatalogue.from_file(write(tmp_path, duplicated))


def test_without_a_file_the_service_still_has_a_catalogue() -> None:
    catalogue = TomlModelCatalogue.built_in()

    assert catalogue.default().provider == "echo"
    # FAIL by default: a deployment that has not been configured should not paper over
    # a broken provider.
    assert catalogue.fallback is FallbackPolicy.FAIL
