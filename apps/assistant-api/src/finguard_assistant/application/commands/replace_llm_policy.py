"""ReplaceLlmPolicyCommand · ResetLlmPolicyCommand · their handlers.

Swapping the active policy changes state, so it is a command. Both answer with the
resulting catalogue: the endpoint has to reply with it, and a second read to fetch what
was just written would be a race waiting to happen.

This is the seam phase 5 needs. When a money movement has to be idempotent and audited,
it hooks here — once — rather than inside each handler.
"""

from dataclasses import dataclass

from finguard_assistant.application.bus.command_bus import Command
from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.queries.get_llm_catalogue import CatalogueResult, build_result
from finguard_assistant.domain.ports.model_catalogue import ModelCataloguePort
from finguard_assistant.domain.value_objects.llm_policy import LlmPolicy


@dataclass(frozen=True)
class ReplaceLlmPolicyCommand(Command[CatalogueResult]):
    """Only the alias: the provider and the model name come from the catalogue, so a
    caller cannot invent a pairing this deployment does not have. What it leaves out
    keeps its current value."""

    model_alias: str
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ResetLlmPolicyCommand(Command[CatalogueResult]):
    pass


class ReplaceLlmPolicyHandler:
    def __init__(
        self, policies: PolicyStore, catalogue: ModelCataloguePort, providers: list[str]
    ) -> None:
        self._policies = policies
        self._catalogue = catalogue
        self._providers = providers

    async def handle(self, command: ReplaceLlmPolicyCommand) -> CatalogueResult:
        current = self._policies.current()
        # The alias is resolved against the catalogue before anything is rebound: an
        # unknown model never becomes active.
        policy = LlmPolicy(
            model=self._catalogue.get(command.model_alias),
            temperature=current.temperature if command.temperature is None else command.temperature,
            max_output_tokens=(
                current.max_output_tokens
                if command.max_output_tokens is None
                else command.max_output_tokens
            ),
        )
        return build_result(await self._policies.replace(policy), self._catalogue, self._providers)


class ResetLlmPolicyHandler:
    def __init__(
        self, policies: PolicyStore, catalogue: ModelCataloguePort, providers: list[str]
    ) -> None:
        self._policies = policies
        self._catalogue = catalogue
        self._providers = providers

    async def handle(self, command: ResetLlmPolicyCommand) -> CatalogueResult:
        return build_result(await self._policies.reset(), self._catalogue, self._providers)
