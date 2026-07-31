"""Maps a provider name to the adapter that speaks to it.

This is the plug board. Registering a real provider is: write a module next to
`echo_llm_provider.py` that fulfils `LlmProviderPort`, add one line to `_BUILDERS`,
and name it in the catalogue file. Neither the domain nor the use cases change.

The registry only knows how to *build* providers. Which ones a deployment may use, and
in what order they are tried, is the catalogue's business — configuration, not code.
"""

from collections.abc import Callable

from finguard_assistant.domain.errors import UnknownProviderError
from finguard_assistant.domain.ports.llm_provider import LlmProviderPort
from finguard_assistant.infrastructure.llm.echo_llm_provider import EchoLlmProvider

_BUILDERS: dict[str, Callable[[], LlmProviderPort]] = {
    EchoLlmProvider.name: EchoLlmProvider,
}


class ProviderRegistry:
    """Builds each provider once, on first use."""

    def __init__(self, builders: dict[str, Callable[[], LlmProviderPort]] | None = None) -> None:
        self._builders = dict(builders if builders is not None else _BUILDERS)
        self._built: dict[str, LlmProviderPort] = {}

    def available(self) -> list[str]:
        return sorted(self._builders)

    def get(self, name: str) -> LlmProviderPort:
        if name not in self._builders:
            raise UnknownProviderError(name, self.available())
        if name not in self._built:
            self._built[name] = self._builders[name]()
        return self._built[name]
