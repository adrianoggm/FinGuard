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

# ponytail: these endpoints change how the assistant behaves and today they ask for
# no credentials. Before the service is exposed beyond localhost they have to sit
# behind OAuth with an administration scope (§12 of the vision).
admin = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(policies: Policies) -> JSONResponse:
    """Ready to serve: the active policy points at a provider that exists."""
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
    # The provider is validated before touching the state: an invalid policy never
    # leaves the service half configured.
    registry.get(policy.provider)
    return PolicyView(
        active=await policies.replace(policy), available_providers=registry.available()
    )


@admin.post("/llm-policy/reset")
async def reset_policy(policies: Policies) -> PolicyView:
    return PolicyView(active=await policies.reset(), available_providers=registry.available())
