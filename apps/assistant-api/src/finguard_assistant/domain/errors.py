class AssistantError(Exception):
    """Error de dominio del asistente."""


class UnknownProviderError(AssistantError):
    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"proveedor de LLM desconocido: {name!r}; disponibles: {available}")
