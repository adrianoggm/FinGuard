"""The wire format of the LLM endpoints.

These are DTOs, not domain models: the domain knows nothing about camelCase, aliases
or HTTP. Keeping them apart is what stops a change in the contract from reaching the
core — before this, `LlmPolicy` was both the domain object and the request body.
"""

from pydantic import BaseModel, ConfigDict, Field

from finguard_assistant.domain.constants import MAX_PROMPT_LENGTH


class _Payload(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class ModelResponse(_Payload):
    alias: str
    provider: str
    model: str


class PolicyResponse(_Payload):
    model: ModelResponse
    temperature: float
    max_output_tokens: int


class CatalogueResponse(_Payload):
    active: PolicyResponse
    fallback: str
    available_models: list[ModelResponse]
    available_providers: list[str]


class PolicyRequest(_Payload):
    """Only the alias travels: the provider and the model name come from the catalogue,
    so a caller cannot invent a pairing this deployment does not have."""

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    model_alias: str = Field(min_length=1)
    temperature: float | None = None
    max_output_tokens: int | None = None


class MessageRequest(_Payload):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class MessageResponse(_Payload):
    text: str
    policy: PolicyResponse
    # Empty on the happy path. Non-empty means a fallback served this answer.
    attempted: list[str]
