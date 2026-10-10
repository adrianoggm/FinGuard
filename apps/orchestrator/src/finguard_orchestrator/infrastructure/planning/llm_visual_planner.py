"""Implements VisualPlannerPort against the model already loaded, with a different prompt.

One call, one structure repair with the shared `STRUCTURE_REPAIR`
suffix, and a `ProviderFailedError` when the provider will not honour the schema twice -- which is
a provider failure, not an abstention, and the policy records it as one. `budget_seconds` is
accepted and unused here: the policy enforces it with `wait_for`, because only the policy holds the
floor to fall back to.

The answer is validated whole by `visual_answer.VisualAnswer` (the schema's own closed sets,
strict); a violation is the structure failure the repair path handles.
"""

import logging

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    VisualPlanDraft,
)
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.llm.structure_repair import STRUCTURE_REPAIR
from finguard_orchestrator.infrastructure.planning.visual_answer import (
    VisualAnswer,
    parse_visual_answer,
)
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

    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        prompt = build_visual_prompt(question, profile, presentation)
        answer = await self._complete(prompt)
        validated = parse_visual_answer(answer)
        if validated is None:
            # Never echo malformed provider text into the repair prompt or the log: the
            # exact schema is sufficient, and the second result is still validated without
            # coercion. Mirrors `llm_query_planner.py`'s own repair log -- no user text, no
            # model text, ever.
            logger.warning("visual_planner.invalid_structure retrying=true")
            answer = await self._complete(prompt + STRUCTURE_REPAIR)
            validated = parse_visual_answer(answer)
            if validated is None:
                raise ProviderFailedError(self._model, "visual planner: invalid structure twice")
        return _draft(validated)

    async def _complete(self, prompt: str) -> str:
        return await self._provider.complete(
            prompt,
            model=self._model,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            schema=self._schema,
        )


def _draft(answer: VisualAnswer) -> VisualPlanDraft | None:
    if answer.mark is None:
        # U-7 (Wave 3 hardening): `{"mark": null}` and nothing else is the abstention the port
        # contract promises ("None means abstention and nothing else"). `{"mark": null, "limit":
        # 3}` is a different thing -- a model that both declined to propose and proposed a
        # row cut in the same breath -- and used to be read as the clean abstention anyway,
        # silently dropping the contradictory fields with no record of them. Built into a draft
        # instead (`mark=None`, `# type: ignore[arg-type]`: `VisualPlanDraft.mark` is `str`, the
        # refusal is the point) and handed to `verify()` like any other proposal: `ChartSpec`'s
        # own `mark not in get_args(Mark)` check refuses it as `Rejection("structure", ...)`, so
        # the policy records `invalid_proposal:structure` -- the same rule/reason pair a
        # malformed proposal already gets -- rather than inventing a new one.
        if not (
            answer.encodings
            or answer.stack is not None
            or answer.sort is not None
            or answer.sort_field is not None
            or answer.limit is not None
        ):
            return None  # abstention, the one meaning None has
    return VisualPlanDraft(
        mark=answer.mark,  # type: ignore[arg-type]  # a contradictory abstention, refused at verify()
        encodings=tuple(
            ProposedEncoding(
                item.channel,
                item.field,
                item.type,
                ProposedSort(item.sort.mode, item.sort.field) if item.sort is not None else None,
            )
            for item in answer.encodings
        ),
        sort=answer.sort,
        sort_field=answer.sort_field,
        stack=answer.stack,
        limit=answer.limit,
        rationale=answer.rationale,
    )
