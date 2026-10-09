"""The graph's assessment as one Groq-compatible structured call, never combined with tool calling.

The schema is closed per call: an inspection may only name a merchant the graph put in
`may_inspect`, and when there is none the only branch is to stop. Externally tagged like the
operations schema, with every key required and no extra keys, which is the shape Groq's strict
`json_schema` takes. No repair call: the assessment is optional, so an answer that cannot be read
is a stop with nothing to narrate, and the graph answers with what banking already said.

The prompt carries the value-minimised summary and nothing else: no sentence, no conversation, no
amount but the inspected purchase as the grounded answer already says it.
"""

import json
import logging
from dataclasses import asdict
from typing import Any, Final

from finguard_orchestrator.domain.errors import AnswerRefusedError
from finguard_orchestrator.domain.ports.investigation_assessor import (
    AssessmentFailedError,
    Inspect,
    InvestigationSummary,
    Stop,
)
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort

logger = logging.getLogger(__name__)

_INSTRUCTIONS: Final = """\
You review what a bank answered about which merchants a person now spends more at, and why.
Each risen merchant comes with the cause already computed from the bank's totals and counts:
MORE_PURCHASES (more movements), BIGGER_AMOUNTS (higher mean per movement), BOTH, or NEW.
"inspected" lists the merchants whose largest purchase was already looked at: ONE_PURCHASE means
that without that purchase the person would not have spent more than before, SPREAD that even
without it they would have.

Answer with exactly one of:
- {"inspect": {"merchant": M}} to look at the largest purchase of M, only for M in "may_inspect",
  when knowing whether one purchase explains the rise would change the answer;
- {"stop": {"why": TEXT}} when nothing else is worth reading. TEXT is at most two short Spanish
  sentences saying what the data shows, or "" for nothing to add. Quote no number that is not
  written in the summary exactly as it is written there, compute nothing, and never give a reason
  the bank cannot know (prices, habits, moves): say that the data does not show it.

Summary:
"""


class LlmInvestigationAssessor:
    """Implements InvestigationAssessorPort against one model."""

    def __init__(
        self,
        provider: LlmProviderPort,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    async def assess(self, summary: InvestigationSummary) -> Inspect | Stop:
        prompt = _INSTRUCTIONS + json.dumps(asdict(summary), ensure_ascii=False)
        try:
            answer = await self._provider.complete(
                prompt,
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=schema_for(summary.may_inspect),
            )
        except AnswerRefusedError:
            logger.warning("graph.assessment_refused_by_the_provider")
            return Stop()
        except Exception as failed:
            # Named by type only, and not chained, as the interpreter does.
            raise AssessmentFailedError(
                f"the assessor's model failed: {type(failed).__name__}"
            ) from None
        return read(answer, summary.may_inspect)


def schema_for(may_inspect: tuple[str, ...]) -> dict[str, Any]:
    """`{"next": {"inspect": {"merchant": M}} | {"stop": {"why": TEXT}}}`, with M closed to
    `may_inspect` and the inspect branch absent when it is empty."""
    branches = [_object({"stop": _object({"why": {"type": "string"}})})]
    if may_inspect:
        merchant = {"type": "string", "enum": list(may_inspect)}
        branches.insert(0, _object({"inspect": _object({"merchant": merchant})}))
    return _object({"next": {"anyOf": branches}})


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """A closed object requiring every key it has: the only shape strict decoders take."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def read(answer: str, may_inspect: tuple[str, ...]) -> Inspect | Stop:
    """The answer as a step, or a stop with nothing to say when it is not one of the two shapes.
    A merchant outside `may_inspect` is read as asked: the graph's own allow-list refuses it, and
    that refusal is what the tests pin, not this reader's."""
    try:
        chosen = json.loads(answer)["next"]
        if set(chosen) == {"inspect"} and isinstance(chosen["inspect"]["merchant"], str):
            return Inspect(chosen["inspect"]["merchant"])
        if set(chosen) == {"stop"} and isinstance(chosen["stop"]["why"], str):
            return Stop(chosen["stop"]["why"])
    except (ValueError, KeyError, TypeError):
        pass
    logger.warning("graph.assessment_unreadable")
    return Stop()
