class EchoProvider:
    """Proveedor determinista para desarrollo y tests: no llama a ninguna API.

    Devuelve el modelo en la respuesta, así que sirve para comprobar qué política
    estaba activa cuando se atendió la petición.
    """

    name = "echo"

    async def complete(self, prompt: str, *, model: str, temperature: float) -> str:
        return f"[{self.name}:{model}] {prompt}"
