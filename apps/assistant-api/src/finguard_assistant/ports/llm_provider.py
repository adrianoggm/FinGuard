from typing import Protocol, runtime_checkable


@runtime_checkable
class LlmProvider(Protocol):
    """Contrato único que cumple cualquier proveedor de LLM.

    El resto del servicio solo conoce esta interfaz: cambiar de proveedor no
    obliga a tocar los casos de uso ni el dominio.
    """

    name: str

    async def complete(self, prompt: str, *, model: str, temperature: float) -> str: ...
