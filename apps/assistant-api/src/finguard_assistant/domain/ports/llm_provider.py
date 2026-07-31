from typing import Protocol, runtime_checkable


@runtime_checkable
class LlmProviderPort(Protocol):
    """The single contract every LLM provider fulfils.

    The rest of the service only knows this interface: switching provider does not
    force a change in the use cases or the domain. An implementation that cannot
    answer raises ProviderFailedError — that is what the fallback policy reacts to.
    """

    name: str

    async def complete(
        self, prompt: str, *, model: str, temperature: float, max_output_tokens: int
    ) -> str: ...
