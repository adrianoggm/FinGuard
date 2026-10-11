"""The typed actions a screen sends to the conversation, instead of a sentence.

A form already knows the contact, the account and the amount, so it says them as fields and the
graph runs them as a turn of the conversation it names (`conversation`, echoed from the previous
answer, or none for a new one). The answer is a `QuestionResponse`, the same as a question's.

Answering a preview is a decision, `confirm` or `cancel`, never free text: the screen has two
buttons, and the confirmation allow-list reads only what those two buttons mean.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from finguard_orchestrator.domain.constants import MAX_RECIPIENT_NAME
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload


class _Action(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    conversation: ConversationPayload | None = None


class PayContactAction(_Action):
    """A contact picked from the list. By name: banking verifies it against this holder's own
    contacts, which is what makes a forged identifier useless."""

    action: Literal["pay_contact"]
    recipient_name: str = Field(min_length=1, max_length=MAX_RECIPIENT_NAME)
    amount_minor: int = Field(gt=0)
    rail: Literal["BIZUM", "TRANSFER"] = "BIZUM"


class MoveBetweenAccountsAction(_Action):
    """Money into another of the holder's own accounts, by the id the account list showed."""

    action: Literal["move_between_accounts"]
    to_account_id: UUID
    amount_minor: int = Field(gt=0)


class AnswerPreviewAction(_Action):
    """The holder's decision on the preview this conversation holds."""

    action: Literal["answer_preview"]
    decision: Literal["confirm", "cancel"]


ActionRequest = Annotated[
    PayContactAction | MoveBetweenAccountsAction | AnswerPreviewAction,
    Field(discriminator="action"),
]
