import asyncio

from finguard_assistant.domain.llm_policy import LlmPolicy


class PolicyStore:
    """Guarda la política activa y permite cambiarla sin reiniciar el servicio.

    ponytail: el estado vive en la memoria del proceso. Con más de una réplica
    cada una tendría su propia política; mover a Redis cuando haya réplicas.
    """

    def __init__(self, default: LlmPolicy) -> None:
        self._default = default
        self._current = default
        self._lock = asyncio.Lock()

    @property
    def default(self) -> LlmPolicy:
        return self._default

    def current(self) -> LlmPolicy:
        return self._current

    async def replace(self, policy: LlmPolicy) -> LlmPolicy:
        # El lock serializa dos cambios simultáneos; la sustitución es atómica,
        # así que ninguna petición ve una política a medio aplicar.
        async with self._lock:
            self._current = policy
            return self._current

    async def reset(self) -> LlmPolicy:
        return await self.replace(self._default)
