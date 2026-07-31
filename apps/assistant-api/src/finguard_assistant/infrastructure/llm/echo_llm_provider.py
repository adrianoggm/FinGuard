class EchoLlmProvider:
    """A deterministic provider for development and tests: it calls no API.

    It is an outbound adapter — it implements a domain port and talks to the outside —
    which is why it lives under infrastructure and carries its technology in its name.

    It echoes the model back in the answer, which makes it useful for checking which
    model actually served a request after a fallback.
    """

    name = "echo"

    async def complete(
        self, prompt: str, *, model: str, temperature: float, max_output_tokens: int
    ) -> str:
        return f"[{self.name}:{model}] {prompt}"
