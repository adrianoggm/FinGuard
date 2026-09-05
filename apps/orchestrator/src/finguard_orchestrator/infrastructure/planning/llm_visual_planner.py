"""Implements VisualPlannerPort against the model already loaded, with a different prompt.

Mirrors `LlmQueryPlanner`: one call, one structure repair with the same `STRUCTURE_REPAIR`
suffix, and a `ProviderFailedError` when the provider will not honour the schema twice -- which is
a provider failure, not an abstention, and the policy records it as one. `budget_seconds` is
accepted and unused here: the policy enforces it with `wait_for`, because only the policy holds the
floor to fall back to.
"""

import json
import logging
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

logger = logging.getLogger(__name__)


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
            # Never echo malformed provider text into the repair prompt or the log: the
            # exact schema is sufficient, and the second result is still validated without
            # coercion. Mirrors `llm_query_planner.py`'s own repair log -- no user text, no
            # model text, ever.
            logger.warning("visual_planner.invalid_structure retrying=true")
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


# The three keys `visual_schema()` marks `required` on an encoding item, and the one key it marks
# `required` on a `sort` object. A missing one is a structure failure, never a fabricated `"None"`
# string built by `str(item.get(...))` -- the model wrote no channel, and the record should not
# claim it wrote `"None"` (finding 13).
_ENCODING_REQUIRED = ("channel", "field", "type")


def _encodings_are_well_formed(encodings: object) -> bool:
    if not isinstance(encodings, list):
        return False
    for item in encodings:
        if not isinstance(item, dict) or not all(key in item for key in _ENCODING_REQUIRED):
            return False
        sort = item.get("sort")
        if isinstance(sort, dict) and "mode" not in sort:
            return False
    return True


def _parse(answer: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(answer)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    if "mark" in parsed and parsed["mark"] is None:
        # Abstention, however tersely it was written. Checked before the structural gate below
        # so a minimal `{"mark": null}` reads as the abstention it is rather than failing
        # `"encodings" in parsed` and being recorded as a provider error (finding 9).
        return {"mark": None, "encodings": []}
    if "encodings" not in parsed or not _encodings_are_well_formed(parsed["encodings"]):
        return None
    limit = parsed.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        # A limit the adapter cannot read is a structure failure, not a silently dropped or
        # coerced value (finding 1): `isinstance(True, int)` is `True` in Python, so `bool` is
        # excluded explicitly or a JSON boolean would become `limit=1`.
        return None
    return parsed


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
        # The type is now guaranteed by `_parse`'s own gate -- passed through unconditionally,
        # never coerced (finding 1).
        limit=parsed.get("limit"),
        rationale=parsed.get("rationale") if isinstance(parsed.get("rationale"), str) else None,
    )
