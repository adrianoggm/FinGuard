"""Gemini over Google's OpenAI-compatible chat completions endpoint.

Google accepts small JSON Schemas through this endpoint, but FinGuard's complete operations
schema exceeds Gemini's schema nesting limit. For that schema, send a reduced wire schema which
keeps the single-key operation and field unions, the plan fields, and the three window variants.
The unchanged ``TurnOperations`` reader then validates the complete operation, all nested fields,
enums, ranges and categories before anything reaches the deterministic transition or banking
executor.

This adapter does not relax application validation. It only moves some constraints from
generation-time decoding to the existing typed reader. Non-operation schemas, such as the flat
query-planning schema, pass through unchanged.
"""

from typing import Any, Final

from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)

GEMINI: Final = "gemini"
_DEFAULT_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiProvider(OpenAiCompatibleProvider):
    """The shared OpenAI-compatible adapter with a shallow operations schema for Gemini."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            name=GEMINI,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            reasoning_headroom=0,
        )

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
            schema = _shallow_operations_schema(schema)
        return await super().complete(
            prompt,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            schema=schema,
        )


def _shallow_operations_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Project FinGuard's operation union onto a schema shallow enough for Gemini.

    Keep the closed one-key unions and enough nested shape to distinguish plan fields and windows.
    The interpreter's existing Pydantic reader still enforces the full original schema before the
    resulting operations are accepted. Other schemas are returned as-is.
    """
    definitions = schema.get("$defs")
    operations = schema.get("properties", {}).get("operations")
    variants = operations.get("items", {}).get("anyOf") if isinstance(operations, dict) else None
    if not isinstance(definitions, dict) or not isinstance(variants, list):
        return schema

    def resolve(node: Any) -> Any:
        seen: set[str] = set()
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            name = node["$ref"].rsplit("/", 1)[-1]
            if name in seen or name not in definitions:
                return node
            seen.add(name)
            node = definitions[name]
        return node

    def simple_value(node: Any) -> dict[str, Any]:
        field = resolve(node)
        kind = field.get("type") if isinstance(field, dict) else None
        nullable_branches = field.get("anyOf", []) if isinstance(field, dict) else []
        if isinstance(nullable_branches, list) and nullable_branches:
            primitive: list[dict[str, Any]] = []
            for choice in nullable_branches:
                resolved = resolve(choice)
                choice_type = resolved.get("type") if isinstance(resolved, dict) else None
                if choice_type not in {"string", "integer", "number", "boolean", "null"}:
                    primitive = []
                    break
                item: dict[str, Any] = {"type": choice_type}
                if isinstance(resolved.get("enum"), list):
                    item["enum"] = resolved["enum"]
                primitive.append(item)
            if primitive:
                return {"anyOf": primitive}
        if not isinstance(kind, str):
            enum_choices: list[Any] = []
            variants = field.get("anyOf", []) if isinstance(field, dict) else []
            if isinstance(variants, list):
                for choice in variants:
                    if not isinstance(choice, dict):
                        continue
                    if "const" in choice:
                        enum_choices.append(choice["const"])
                    elif isinstance(choice.get("enum"), list) and choice["enum"]:
                        enum_choices.append(choice["enum"][0])
            if enum_choices:
                return {"type": "string", "enum": enum_choices}
            return {"type": "object"}
        value: dict[str, Any] = {"type": kind}
        if isinstance(field.get("enum"), list):
            value["enum"] = field["enum"]
        elif isinstance(field.get("anyOf"), list):
            choices = [
                choice["const"]
                for choice in field["anyOf"]
                if isinstance(choice, dict) and "const" in choice
            ]
            if choices:
                value["enum"] = choices
        if kind == "array":
            item = resolve(field.get("items", {}))
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type in {"string", "integer", "number", "boolean"}:
                value["items"] = {"type": item_type}
                if isinstance(item.get("enum"), list):
                    value["items"]["enum"] = item["enum"]
            else:
                value["items"] = {"type": "object", "additionalProperties": True}
        return value

    def field_items() -> dict[str, Any]:
        ask = resolve(definitions.get("AskOp", {}))
        ask_fields = ask.get("properties", {}).get("ask", {}) if isinstance(ask, dict) else {}
        field_variants = resolve(ask_fields).get("items", {}).get("anyOf", [])
        properties: dict[str, dict[str, Any]] = {}
        for item_variant in field_variants:
            item = resolve(item_variant)
            item_properties = item.get("properties", {}) if isinstance(item, dict) else {}
            if not isinstance(item_properties, dict) or len(item_properties) != 1:
                continue
            name, value = next(iter(item_properties.items()))
            properties[name] = window_value(value) if name == "window" else simple_value(value)
        variants = [
            {
                "type": "object",
                "properties": {name: value},
                "required": [name],
                "additionalProperties": False,
            }
            for name, value in properties.items()
        ]
        return {
            "type": "array",
            "items": {"anyOf": variants},
        }

    def window_value(node: Any) -> dict[str, Any]:
        window = resolve(node)
        branches = window.get("anyOf", []) if isinstance(window, dict) else []
        properties: dict[str, dict[str, Any]] = {}
        for branch in branches:
            variant = resolve(branch)
            variant_properties = variant.get("properties", {}) if isinstance(variant, dict) else {}
            if not isinstance(variant_properties, dict) or len(variant_properties) != 1:
                continue
            name, value = next(iter(variant_properties.items()))
            resolved = resolve(value)
            if name == "counted" and isinstance(resolved, dict):
                properties[name] = {
                    "type": "object",
                    "properties": {
                        key: simple_value(child)
                        for key, child in resolved.get("properties", {}).items()
                    },
                    "additionalProperties": False,
                }
            elif name == "range" and isinstance(resolved, dict):
                date_range = resolve(value)
                range_properties = date_range.get("properties", {})
                properties[name] = {
                    "type": "object",
                    "properties": {
                        key: simple_value(child) for key, child in range_properties.items()
                    },
                    "additionalProperties": False,
                }
            else:
                properties[name] = simple_value(value)
        return {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }

    plan_schema = field_items()

    operation_properties: dict[str, dict[str, Any]] = {}
    for variant in variants:
        definition = resolve(variant)
        properties = definition.get("properties") if isinstance(definition, dict) else None
        if not isinstance(properties, dict) or len(properties) != 1:
            return schema
        name, original = next(iter(properties.items()))
        field = resolve(original)
        kind = field.get("type") if isinstance(field, dict) else None
        value: dict[str, Any]
        if name == "prepare_own_transfer" and isinstance(field, dict):
            value = {"anyOf": [simple_value(branch) for branch in field.get("anyOf", [])]}
            operation_properties[name] = value
            continue
        if not isinstance(kind, str):
            return schema

        if name == "ask":
            value = plan_schema
        elif name == "patch":
            body = field
            value = {
                "type": "object",
                "properties": {
                    "target": simple_value(body.get("properties", {}).get("target", {})),
                    "changes": plan_schema,
                },
                "required": ["target", "changes"],
                "additionalProperties": False,
            }
        elif name == "compare":
            value = {
                "type": "object",
                "properties": {key: plan_schema for key in ("shared", "earlier", "latest")},
                "required": ["shared", "earlier", "latest"],
                "additionalProperties": False,
            }
        elif name == "prepare_bizum":
            properties = field.get("properties", {})
            value = {
                "type": "object",
                "properties": {key: simple_value(child) for key, child in properties.items()},
                "required": list(properties),
                "additionalProperties": False,
            }
        elif kind == "object" and not field.get("properties"):
            # `reset` is the one empty object operation and remains closed at generation time.
            value = {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        else:
            value = simple_value(field)
        operation_properties[name] = value

    operation_variants = [
        {
            "type": "object",
            "properties": {name: value},
            "required": [name],
            "additionalProperties": False,
        }
        for name, value in operation_properties.items()
    ]
    return {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {"anyOf": operation_variants},
            }
        },
        "required": ["operations"],
        "additionalProperties": False,
    }
