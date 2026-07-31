class EchoProvider:
    """A deterministic provider for development and tests: it calls no API.

    It echoes the model back in the answer, which makes it useful for checking which
    policy was active when a request was served.
    """

    name = "echo"

    async def complete(self, prompt: str, *, model: str, temperature: float) -> str:
        return f"[{self.name}:{model}] {prompt}"
