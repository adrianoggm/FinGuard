"""LangGraph pause/resume for the existing guarded bank-payment commands.

The command handlers still own grounding, recipient resolution, explicit yes/no parsing, and the
bank's prepare/confirm calls. This graph owns the durable wait between the bank preview and the
holder's reply. The interrupt carries the server-authored preview; confirmation resumes the same
checkpoint and uses its preview, never fields supplied by the client.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Literal, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer, Command, interrupt

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    PaymentAnswer,
    PrepareChosenPaymentCommand,
    PrepareOwnTransferCommand,
    PreparePaymentCommand,
)
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.engine.state import (
    DialogueState,
    PaymentConfirmationPending,
)
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.ports.dialogue_store import (
    DialogueStorePort,
    StaleDialogueError,
)

PrepareCommand = PreparePaymentCommand | PrepareChosenPaymentCommand | PrepareOwnTransferCommand


class PaymentWorkflowState(TypedDict, total=False):
    account_id: str
    workflow_id: str
    conversation_id: str
    request_fingerprint: str
    store_version: int
    preview: str
    answer: str
    reply: str
    phase: Literal["prepared", "waiting", "finished"]


@dataclass(frozen=True)
class PaymentWorkflowContext:
    commands: CommandBus
    store: DialogueStorePort
    account_id: UUID
    workflow_id: str
    prepare_command: PrepareCommand | None = None
    confirm_command: ConfirmPaymentCommand | None = None


type Ctx = Runtime[PaymentWorkflowContext]


def _conversation_id(account_id: UUID, workflow_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"finguard-payment:{account_id}:{workflow_id}")


def _thread_id(account_id: UUID, workflow_id: str) -> str:
    digest = hashlib.sha256(f"{account_id}:{workflow_id}".encode()).hexdigest()
    return f"payment:{digest}"


async def _prepare(state: PaymentWorkflowState, runtime: Ctx) -> PaymentWorkflowState:
    command = runtime.context.prepare_command
    if command is None:
        raise RuntimeError("payment preparation command is missing")
    answer = await runtime.context.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment command returned an unexpected answer")
    update: PaymentWorkflowState = {"phase": "prepared"}
    if answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value:
        if answer.preview is None or answer.intent_id != answer.preview.intent_id:
            raise RuntimeError("bank preview is incomplete or inconsistent")
        update["preview"] = codec.dumps_typed(answer.preview)
    update["answer"] = codec.dumps_typed(answer)
    return update


async def _persist_preview(state: PaymentWorkflowState, runtime: Ctx) -> PaymentWorkflowState:
    ctx = runtime.context
    conversation_id = UUID(state["conversation_id"])
    held = await ctx.store.load(conversation_id, account_id=ctx.account_id)
    preview = codec.loads_typed(state["preview"])
    if not isinstance(preview, PaymentPreview):
        raise TypeError("checkpoint preview is not a PaymentPreview")
    if held is None:
        saved = await ctx.store.save(
            DialogueState(
                conversation_id,
                payment=PaymentConfirmationPending(preview, ctx.workflow_id),
            ),
            account_id=ctx.account_id,
            expected_version=0,
        )
    else:
        pending = held.payment
        if (
            pending is None
            or not isinstance(pending, PaymentConfirmationPending)
            or pending.preview.intent_id != preview.intent_id
        ):
            raise StaleDialogueError(held)
        saved = held
    return {"store_version": saved.version, "phase": "waiting"}


async def _wait_for_reply(state: PaymentWorkflowState) -> PaymentWorkflowState:
    preview = codec.loads_typed(state["preview"])
    if not isinstance(preview, PaymentPreview):
        raise TypeError("checkpoint preview is not a PaymentPreview")
    reply = interrupt(
        {
            "intent_id": str(preview.intent_id),
            "recipient": preview.recipient,
            "amount_minor": preview.amount_minor,
            "currency": preview.currency,
            "balance_after_minor": preview.balance_after_minor,
            "expires_at": preview.expires_at.isoformat(),
        }
    )
    if not isinstance(reply, str):
        raise ValueError("payment confirmation reply must be text")
    return {"reply": reply}


async def _confirm(state: PaymentWorkflowState, runtime: Ctx) -> PaymentWorkflowState:
    ctx = runtime.context
    command = ctx.confirm_command
    if command is None:
        raise RuntimeError("payment confirmation command is missing")
    preview = codec.loads_typed(state["preview"])
    if not isinstance(preview, PaymentPreview):
        raise TypeError("checkpoint preview is not a PaymentPreview")
    if command.intent_id != preview.intent_id:
        raise StaleDialogueError(
            await ctx.store.load(UUID(state["conversation_id"]), account_id=ctx.account_id)
            or DialogueState(UUID(state["conversation_id"]))
        )
    answer = await ctx.commands.dispatch(replace(command, reply=state["reply"], preview=preview))
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment confirmation returned an unexpected answer")
    phase: Literal["waiting", "finished"] = (
        "waiting"
        if answer.outcome
        in {
            PaymentOutcome.AWAITING_CONFIRMATION.value,
            PaymentOutcome.BANKING_UNAVAILABLE.value,
        }
        else "finished"
    )
    return {"answer": codec.dumps_typed(answer), "phase": phase}


async def _clear_completed_payment(
    state: PaymentWorkflowState, runtime: Ctx
) -> PaymentWorkflowState:
    ctx = runtime.context
    conversation_id = UUID(state["conversation_id"])
    held = await ctx.store.load(conversation_id, account_id=ctx.account_id)
    if held is None:
        raise RuntimeError("canonical payment state disappeared")
    preview = codec.loads_typed(state["preview"])
    if not isinstance(preview, PaymentPreview):
        raise TypeError("checkpoint preview is not a PaymentPreview")
    pending = held.payment
    if (
        pending is None
        or not isinstance(pending, PaymentConfirmationPending)
        or pending.preview.intent_id != preview.intent_id
    ):
        raise StaleDialogueError(held)
    saved = await ctx.store.save(
        replace(held, payment=None),
        account_id=ctx.account_id,
        expected_version=state["store_version"],
    )
    return {"store_version": saved.version}


def _after_prepare(state: PaymentWorkflowState) -> str:
    return "persist_preview" if "preview" in state else END


def _after_confirm(state: PaymentWorkflowState) -> str:
    return "wait" if state["phase"] == "waiting" else "clear"


class PaymentWorkflowGraph:
    """The durable payment confirmation interruption used by payment HTTP endpoints."""

    def __init__(
        self,
        commands: CommandBus,
        store: DialogueStorePort,
        checkpointer: Checkpointer,
    ) -> None:
        self._commands = commands
        self._store = store
        builder: Any = StateGraph(PaymentWorkflowState, context_schema=PaymentWorkflowContext)
        builder.add_node("prepare", _prepare)
        builder.add_node("persist_preview", _persist_preview)
        builder.add_node("wait", _wait_for_reply)
        builder.add_node("confirm", _confirm)
        builder.add_node("clear", _clear_completed_payment)
        builder.add_edge(START, "prepare")
        builder.add_conditional_edges("prepare", _after_prepare)
        builder.add_edge("persist_preview", "wait")
        builder.add_edge("wait", "confirm")
        builder.add_conditional_edges("confirm", _after_confirm)
        builder.add_edge("clear", END)
        self._graph: Any = builder.compile(checkpointer=checkpointer)

    async def prepare(self, command: PrepareCommand, *, account_id: UUID) -> PaymentAnswer:
        workflow_id = command.idempotency_key
        conversation_id = _conversation_id(account_id, workflow_id)
        fingerprint = hashlib.sha256(repr(command).encode()).hexdigest()
        config: Any = {"configurable": {"thread_id": _thread_id(account_id, workflow_id)}}
        prior = await self._graph.aget_state(config)
        if prior.values:
            if prior.values.get("request_fingerprint") != fingerprint:
                current = await self._store.load(conversation_id, account_id=account_id)
                raise StaleDialogueError(current or DialogueState(conversation_id))
            answer = prior.values.get("answer")
            if isinstance(answer, str):
                decoded = codec.loads_typed(answer)
            else:
                decoded = None
            if isinstance(decoded, PaymentAnswer):
                return replace(decoded, workflow_id=workflow_id)
        state = await self._graph.ainvoke(
            {
                "account_id": str(account_id),
                "workflow_id": workflow_id,
                "conversation_id": str(conversation_id),
                "request_fingerprint": fingerprint,
            },
            config,
            context=PaymentWorkflowContext(
                self._commands,
                self._store,
                account_id,
                workflow_id,
                prepare_command=command,
            ),
        )
        encoded_answer = state.get("answer")
        answer = codec.loads_typed(encoded_answer) if isinstance(encoded_answer, str) else None
        if not isinstance(answer, PaymentAnswer):
            raise RuntimeError("payment graph ended without a command answer")
        return replace(answer, workflow_id=workflow_id)

    async def confirm(
        self,
        *,
        workflow_id: str,
        account_id: UUID,
        intent_id: UUID,
        reply: str,
    ) -> PaymentAnswer:
        conversation_id = _conversation_id(account_id, workflow_id)
        config: Any = {"configurable": {"thread_id": _thread_id(account_id, workflow_id)}}
        checkpoint = await self._graph.aget_state(config)
        values = checkpoint.values
        if (
            values.get("account_id") != str(account_id)
            or values.get("conversation_id") != str(conversation_id)
            or not isinstance(values.get("preview"), str)
            or not isinstance(codec.loads_typed(values["preview"]), PaymentPreview)
            or codec.loads_typed(values["preview"]).intent_id != intent_id
        ):
            raise StaleDialogueError(
                await self._store.load(conversation_id, account_id=account_id)
                or DialogueState(conversation_id)
            )
        stored_answer = values.get("answer")
        decoded_answer = (
            codec.loads_typed(stored_answer) if isinstance(stored_answer, str) else None
        )
        if values.get("phase") == "finished" and isinstance(decoded_answer, PaymentAnswer):
            return replace(decoded_answer, workflow_id=workflow_id)
        held = await self._store.load(conversation_id, account_id=account_id)
        pending = held.payment if held is not None else None
        if (
            pending is None
            or not isinstance(pending, PaymentConfirmationPending)
            or pending.preview.intent_id != intent_id
        ):
            raise StaleDialogueError(held or DialogueState(conversation_id))
        state = await self._graph.ainvoke(
            Command(resume=reply),
            config,
            context=PaymentWorkflowContext(
                self._commands,
                self._store,
                account_id,
                workflow_id,
                confirm_command=ConfirmPaymentCommand(
                    reply=reply,
                    account_id=account_id,
                    intent_id=intent_id,
                    preview=pending.preview,
                ),
            ),
        )
        encoded_answer = state.get("answer")
        answer = codec.loads_typed(encoded_answer) if isinstance(encoded_answer, str) else None
        if not isinstance(answer, PaymentAnswer):
            raise RuntimeError("payment graph ended without a confirmation answer")
        return replace(answer, workflow_id=workflow_id)
