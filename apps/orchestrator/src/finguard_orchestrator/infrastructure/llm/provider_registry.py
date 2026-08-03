"""Maps a provider name to the adapter that speaks to it.

This is the plug board. Registering a real provider is: write a module next to
`echo_llm_provider.py` that fulfils `LlmProviderPort`, hand its builder to the registry at
the composition root, and name it in the catalogue file. Neither the domain nor the use
cases change.

`_BUILDERS` holds only what can be built with no configuration, which after the
operational numbers moved to .env.defaults means only `echo`. Ollama used to be here too
and it was a quiet second source of truth: a zero-argument builder had to invent a base
URL, a timeout and a context window, so a caller who never configured anything still got a
provider — configured by whatever the constants happened to say. It is now supplied by
bootstrap/main.py with the settings, and a provider nobody configured cannot be built at
all.

The registry only knows how to *build* providers. Which ones a deployment may use, and
in what order they are tried, is the catalogue's business — configuration, not code.
"""

from collections.abc import Callable

from finguard_orchestrator.domain.errors import UnknownProviderError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider

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
