from pydantic import BaseModel, ConfigDict, Field


class LlmPolicy(BaseModel):
    """Política de generación activa: qué proveedor se usa y con qué parámetros.

    Es inmutable: cambiar de política significa sustituirla, no mutarla, para que
    una petición en curso no vea la configuración cambiar a mitad.
    """

    # protected_namespaces=(): el campo se llama `model` a propósito, es el nombre
    # que usan todos los proveedores.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, gt=0)
