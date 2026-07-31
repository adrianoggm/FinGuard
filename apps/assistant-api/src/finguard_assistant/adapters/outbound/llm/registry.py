from finguard_assistant.adapters.outbound.llm.echo import EchoProvider
from finguard_assistant.domain.errors import UnknownProviderError
from finguard_assistant.ports.llm_provider import LlmProvider

# Adding a real provider (OpenAI, Gemini, ...) means writing a module that fulfils
# LlmProvider and registering it here. Neither the domain nor the use cases change.
_PROVIDERS: dict[str, LlmProvider] = {provider.name: provider for provider in (EchoProvider(),)}


def available() -> list[str]:
    return sorted(_PROVIDERS)


def get(name: str) -> LlmProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise UnknownProviderError(name, available())
    return provider
