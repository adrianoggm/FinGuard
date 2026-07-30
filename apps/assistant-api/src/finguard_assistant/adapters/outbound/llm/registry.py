from finguard_assistant.adapters.outbound.llm.echo import EchoProvider
from finguard_assistant.domain.errors import UnknownProviderError
from finguard_assistant.ports.llm_provider import LlmProvider

# Añadir un proveedor real (OpenAI, Gemini, ...) es escribir un módulo que cumpla
# LlmProvider y registrarlo aquí. Ni el dominio ni los casos de uso cambian.
_PROVIDERS: dict[str, LlmProvider] = {provider.name: provider for provider in (EchoProvider(),)}


def available() -> list[str]:
    return sorted(_PROVIDERS)


def get(name: str) -> LlmProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise UnknownProviderError(name, available())
    return provider
