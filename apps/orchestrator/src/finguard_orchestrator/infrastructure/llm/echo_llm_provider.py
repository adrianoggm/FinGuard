import json
from typing import Any

from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort


class EchoLlmProvider(LlmProviderPort):
    """A deterministic provider for development and tests: it calls no API.

    It is an outbound adapter — it implements a domain port and talks to the outside —
    which is why it lives under infrastructure and carries its technology in its name.

    It echoes the model back in the answer, which makes it useful for checking which
    model actually served a request after a fallback.
    """

    name = "echo"

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> str:
        if schema is not None:
            # Structurally valid and semantically empty, which is the honest thing for a
            # provider that does not think. A caller wiring the pipeline up gets JSON it
            # can parse; a caller measuring accuracy gets the floor any real model has to
            # beat, and a benchmark where echo scores well is a benchmark that measures
            # structure instead of understanding.
            return json.dumps(_smallest_instance(schema))
        return f"[{self.name}:{model}] {prompt}"


def _smallest_instance(schema: dict[str, Any]) -> Any:
    """The smallest value the schema admits — only the subset the planner uses."""
    match schema.get("type"):
        case "object":
            properties: dict[str, Any] = schema.get("properties", {})
            required: list[str] = schema.get("required", list(properties))
            return {name: _smallest_instance(properties[name]) for name in required}
        case "array":
            return []
        case "integer" | "number":
            return 0
        case "boolean":
            return False
        case _:
            enum: list[Any] = schema.get("enum", [])
            return enum[0] if enum else ""
