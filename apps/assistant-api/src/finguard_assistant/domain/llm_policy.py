from pydantic import BaseModel, ConfigDict, Field


class LlmPolicy(BaseModel):
    """The active generation policy: which provider is used and with what settings.

    It is immutable: changing policy means replacing it, not mutating it, so that a
    request already in flight never sees the configuration change halfway through.
    """

    # protected_namespaces=(): the field is called `model` on purpose, that is the
    # name every provider uses.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, gt=0)
