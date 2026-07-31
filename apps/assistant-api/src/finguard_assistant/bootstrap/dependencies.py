from fastapi import Request

from finguard_assistant.adapters.outbound.llm import registry
from finguard_assistant.application.answer_message import AnswerMessage
from finguard_assistant.application.policy_store import PolicyStore


def get_policies(request: Request) -> PolicyStore:
    policies: PolicyStore = request.app.state.policies
    return policies


def get_answer_message(request: Request) -> AnswerMessage:
    # The use case receives the registry as a function: it does not know which
    # providers exist.
    return AnswerMessage(get_policies(request), registry.get)
