import asyncio

from finguard_orchestrator.domain.ports.model_catalogue import ModelCataloguePort
from finguard_orchestrator.domain.value_objects.llm_policy import LlmPolicy


class PolicyStore:
    """Holds the active policy and swaps it without restarting the service.

    The swap is atomic in the sense that matters: the policy is a frozen object and
    `_current` is rebound in a single assignment, so a reader either sees the whole old
    policy or the whole new one — never a half-applied mix of model and temperature.
    The lock serialises two concurrent writers; readers need none.

    ponytail: the state lives in this process. With more than one replica each would
    have its own policy; move it to Redis once there are replicas.
    """

    def __init__(self, catalogue: ModelCataloguePort, default: LlmPolicy) -> None:
        self._catalogue = catalogue
        self._default = default
        self._current = default
        self._lock = asyncio.Lock()

    @property
    def default(self) -> LlmPolicy:
        return self._default

    def current(self) -> LlmPolicy:
        return self._current

    async def replace(self, policy: LlmPolicy) -> LlmPolicy:
        # The model is checked against the catalogue before anything is rebound, so a
        # policy naming a model this deployment does not have never becomes active.
        self._catalogue.get(policy.model.alias)
        async with self._lock:
            self._current = policy
            return self._current

    async def reset(self) -> LlmPolicy:
        return await self.replace(self._default)
