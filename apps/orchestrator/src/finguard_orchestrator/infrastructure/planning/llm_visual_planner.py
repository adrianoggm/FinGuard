"""Implements VisualPlannerPort against the model already loaded, with a different prompt.

Mirrors `LlmQueryPlanner`: one call, one structure repair with the same `STRUCTURE_REPAIR`
suffix, and a `ProviderFailedError` when the provider will not honour the schema twice -- which is
a provider failure, not an abstention, and the policy records it as one. `budget_seconds` is
accepted and unused here: the policy enforces it with `wait_for`, because only the policy holds the
floor to fall back to.
"""

import json
from typing import Any

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    VisualPlanDraft,
)
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.planning.llm_query_planner import STRUCTURE_REPAIR
from finguard_orchestrator.infrastructure.planning.visual_prompt import (
    build_visual_prompt,
    visual_schema,
)


class LlmVisualPlanner:
    def __init__(
        self, provider: LlmProviderPort, *, model: str, temperature: float, max_output_tokens: int
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._schema = visual_schema()
        self.last_prompt: str | None = None

    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        prompt = build_visual_prompt(question, profile, presentation)
        self.last_prompt = prompt
        answer = await self._complete(prompt)
        parsed = _parse(answer)
        if parsed is None:
            answer = await self._complete(prompt + STRUCTURE_REPAIR)
            parsed = _parse(answer)
            if parsed is None:
                raise ProviderFailedError(self._model, "visual planner: invalid structure twice")
        return _draft(parsed)

    async def _complete(self, prompt: str) -> str:
        return await self._provider.complete(
            prompt,
            model=self._model,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            schema=self._schema,
        )


def _parse(answer: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(answer)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and "encodings" in parsed else None


def _draft(parsed: dict[str, Any]) -> VisualPlanDraft | None:
    if parsed.get("mark") is None:
        return None  # abstention, the one meaning None has
    encodings = tuple(
        ProposedEncoding(
            str(item.get("channel")),
            str(item.get("field")),
            str(item.get("type")),
            ProposedSort(str(item["sort"].get("mode")), item["sort"].get("field"))
            if isinstance(item.get("sort"), dict)
            else None,
        )
        for item in parsed.get("encodings", [])
        if isinstance(item, dict)
    )
    return VisualPlanDraft(
        mark=str(parsed["mark"]),
        encodings=encodings,
        sort=parsed.get("sort"),
        sort_field=parsed.get("sort_field"),
        stack=parsed.get("stack"),
        limit=parsed.get("limit") if isinstance(parsed.get("limit"), int) else None,
        rationale=parsed.get("rationale") if isinstance(parsed.get("rationale"), str) else None,
    )
