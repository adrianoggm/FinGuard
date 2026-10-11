"""Implements SkillClassifierPort: a model sorts a card's skills into the closed catalogue (§6.1).

Built like `LlmVisualPlanner`: one call under a closed schema, one repair with the same
`STRUCTURE_REPAIR` suffix, a strict parse. What the model says is then checked against the card
itself, and each check narrows rather than guesses:

    an id the card does not have            dropped
    one id classified twice                 NONE, for every entry of it
    two skills for one capability           NONE for both -- asking the wrong one of two is worse
                                            than not offering the capability at all

An invalid structure twice is an empty mapping: the agent is left without capabilities for this
search and asked again next time, never given a guessed one. A `ProviderFailedError` propagates --
the caller treats it as the agent not answering. A classification that succeeded is cached by the
card's digest for the life of the process: a card that changes has a new digest.
"""

import logging
from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType

from finguard_orchestrator.domain.models.expert_agents import Capability, ExpertAgent
from finguard_orchestrator.domain.ports.agent_reading import SkillClassifierPort
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.expert_agents.agent_answer import (
    Classified,
    parse_classification,
)
from finguard_orchestrator.infrastructure.expert_agents.agent_prompt import (
    classifier_prompt,
    classifier_schema,
)
from finguard_orchestrator.infrastructure.llm.structure_repair import STRUCTURE_REPAIR

logger = logging.getLogger(__name__)


class LlmSkillClassifier(SkillClassifierPort):
    def __init__(
        self, provider: LlmProviderPort, *, model: str, temperature: float, max_output_tokens: int
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._schema = classifier_schema()
        self._cache: dict[str, Mapping[Capability, str]] = {}

    async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
        if not agent.skills:
            return MappingProxyType({})
        cached = self._cache.get(agent.card_digest)
        if cached is not None:
            return cached
        prompt = classifier_prompt(agent)
        answer = parse_classification(await self._complete(prompt))
        if answer is None:
            # Never echo the provider's text into the repair prompt or the log (llm_query_planner).
            logger.warning("skill_classifier.invalid_structure", extra={"agent": agent.id})
            answer = parse_classification(await self._complete(prompt + STRUCTURE_REPAIR))
            if answer is None:
                logger.warning("skill_classifier.unclassified", extra={"agent": agent.id})
                return MappingProxyType({})
        found = MappingProxyType(_capabilities(agent, answer))
        self._cache[agent.card_digest] = found
        return found

    async def _complete(self, prompt: str) -> str:
        return await self._provider.complete(
            prompt,
            model=self._model,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            schema=self._schema,
        )


def _capabilities(
    agent: ExpertAgent, answer: tuple[tuple[str, Classified], ...]
) -> dict[Capability, str]:
    known = {skill.id for skill in agent.skills}
    entries = [(skill_id, capability) for skill_id, capability in answer if skill_id in known]
    if len(entries) < len(answer):
        logger.warning("skill_classifier.unknown_skill", extra={"agent": agent.id})
    repeated = {skill_id for skill_id, n in Counter(s for s, _ in entries).items() if n > 1}
    if repeated:
        logger.warning("skill_classifier.repeated_skill", extra={"agent": agent.id})
    serving: dict[Capability, list[str]] = {}
    for skill_id, capability in entries:
        if capability != "NONE" and skill_id not in repeated:
            serving.setdefault(Capability(capability), []).append(skill_id)
    found: dict[Capability, str] = {}
    for offered, skills in serving.items():
        if len(skills) == 1:
            found[offered] = skills[0]
        else:
            logger.warning(
                "skill_classifier.tie", extra={"agent": agent.id, "capability": offered.value}
            )
    return found
