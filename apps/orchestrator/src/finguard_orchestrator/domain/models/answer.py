from dataclasses import dataclass, field

from finguard_orchestrator.domain.value_objects.llm_policy import LlmPolicy


@dataclass(frozen=True)
class Answer:
    """What the assistant produced, and who produced it.

    `attempts` lists the model aliases tried before this one answered. It is empty on
    the happy path and it is the only way to notice, after the fact, that a fallback
    kicked in — otherwise a degraded answer is indistinguishable from a good one.
    """

    text: str
    policy: LlmPolicy
    attempts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def provider(self) -> str:
        return self.policy.model.provider

    @property
    def fell_back(self) -> bool:
        return bool(self.attempts)
