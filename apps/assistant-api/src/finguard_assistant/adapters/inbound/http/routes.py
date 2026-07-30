from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from finguard_assistant.adapters.outbound.llm import registry
from finguard_assistant.application.answer_message import AnswerMessage
from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.bootstrap.dependencies import get_answer_message, get_policies
from finguard_assistant.domain.llm_policy import LlmPolicy

Policies = Annotated[PolicyStore, Depends(get_policies)]
Answering = Annotated[AnswerMessage, Depends(get_answer_message)]


class PolicyView(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    active: LlmPolicy
    available_providers: list[str]


class MessageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    text: str
    policy: LlmPolicy


router = APIRouter()

# ponytail: estos endpoints cambian el comportamiento del asistente y hoy no piden
# credenciales. Antes de exponer el servicio fuera de localhost tienen que quedar
# detrás de OAuth con un scope de administración (§12 de la visión).
admin = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
async def health() -> dict[str, str]:
    """El proceso responde. No comprueba dependencias a propósito."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(policies: Policies) -> JSONResponse:
    """Listo para servir: la política activa apunta a un proveedor que existe."""
    provider = policies.current().provider
    if provider not in registry.available():
        return JSONResponse({"status": "unavailable", "provider": provider}, status_code=503)
    return JSONResponse({"status": "ok", "provider": provider})


@router.post("/v1/messages")
async def send_message(body: MessageRequest, answering: Answering) -> MessageResponse:
    answer = await answering(body.prompt)
    return MessageResponse(text=answer.text, policy=answer.policy)


@admin.get("/llm-policy")
async def read_policy(policies: Policies) -> PolicyView:
    return PolicyView(active=policies.current(), available_providers=registry.available())


@admin.put("/llm-policy")
async def replace_policy(policy: LlmPolicy, policies: Policies) -> PolicyView:
    # Se valida el proveedor antes de tocar el estado: una política inválida no
    # deja el servicio a medio configurar.
    registry.get(policy.provider)
    return PolicyView(
        active=await policies.replace(policy), available_providers=registry.available()
    )


@admin.post("/llm-policy/reset")
async def reset_policy(policies: Policies) -> PolicyView:
    return PolicyView(active=await policies.reset(), available_providers=registry.available())
