from typing import Protocol, runtime_checkable


@runtime_checkable
class LlmProvider(Protocol):
    """The single contract every LLM provider fulfils.

    The rest of the service only knows this interface: switching provider does not
    force a change in the use cases or the domain.
    """

    name: str

    async def complete(self, prompt: str, *, model: str, temperature: float) -> str: ...
