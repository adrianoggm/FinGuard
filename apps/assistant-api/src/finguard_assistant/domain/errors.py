class AssistantError(Exception):
    """A domain error of the assistant."""


class UnknownProviderError(AssistantError):
    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"unknown LLM provider: {name!r}; available: {available}")
