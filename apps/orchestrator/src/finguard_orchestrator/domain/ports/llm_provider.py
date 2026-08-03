from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LlmProviderPort(Protocol):
    """The single contract every LLM provider fulfils.

    The rest of the service only knows this interface: switching provider does not
    force a change in the use cases or the domain. An implementation that cannot
    answer raises ProviderFailedError — that is what the fallback policy reacts to.
    """

    name: str

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> str:
        """Answers the prompt. With `schema`, the answer must be JSON matching it.

        A schema is a request for *constrained decoding*, not a hint: a runtime that
        supports it makes an invalid token unreachable, so structural validity stops
        being something the caller hopes for. A provider that cannot constrain output
        still has to honour the contract — the caller is going to parse the answer
        either way — which is why this is a parameter and not a prompt convention.

        It is deliberately not enough on its own. A schema decides the shape; whether
        the plan answers the question asked is a separate matter, and the only thing
        that measures it is the evaluation dataset.
        """
        ...
