import asyncio

from finguard_assistant.domain.llm_policy import LlmPolicy


class PolicyStore:
    """Holds the active policy and allows swapping it without restarting the service.

    ponytail: the state lives in the process memory. With more than one replica each
    would have its own policy; move it to Redis once there are replicas.
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
        # The lock serialises two simultaneous changes; the swap is atomic, so no
        # request ever sees a half-applied policy.
        async with self._lock:
            self._current = policy
            return self._current

    async def reset(self) -> LlmPolicy:
        return await self.replace(self._default)
