from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from support.episodes import ACCOUNT, INTENT, Bank, a_stack

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
)
from finguard_orchestrator.application.graph.payment_workflow import (
    PaymentWorkflowGraph,
    _thread_id,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.engine.state import PaymentConfirmationPending
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore


def workflow() -> tuple[PaymentWorkflowGraph, Bank, MemoryDialogueStore]:
    bank = Bank()
    commands = CommandBus()
    commands.register(PrepareChosenPaymentCommand, PrepareChosenPaymentHandler(bank))
    commands.register(ConfirmPaymentCommand, ConfirmPaymentHandler(bank))
    store = MemoryDialogueStore()
    graph = PaymentWorkflowGraph(commands, store, InMemorySaver())
    return graph, bank, store


@pytest.mark.asyncio
async def test_payment_graph_pauses_on_the_exact_preview_then_resumes_for_yes() -> None:
    graph, bank, store = workflow()
    command = PrepareChosenPaymentCommand(
        account_id=ACCOUNT,
        recipient_name="Marta Solis Vega",
        amount_minor=2_000,
        currency="EUR",
        idempotency_key="attempt-yes",
    )

    preview = await graph.prepare(command, account_id=ACCOUNT)

    assert preview.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert preview.workflow_id == "attempt-yes"
    assert preview.preview is not None
    assert len(bank.prepared) == 1
    snapshot = await graph._graph.aget_state(  # noqa: SLF001
        {"configurable": {"thread_id": _thread_id(ACCOUNT, "attempt-yes")}}
    )
    assert snapshot.next
    assert snapshot.values["phase"] == "waiting"
    stored = await store.load(UUID(snapshot.values["conversation_id"]), account_id=ACCOUNT)
    assert stored is not None and isinstance(stored.payment, PaymentConfirmationPending)
    assert stored.payment.preview == preview.preview

    paid = await graph.confirm(
        workflow_id="attempt-yes", account_id=ACCOUNT, intent_id=INTENT, reply="sí"
    )

    assert paid.outcome == PaymentOutcome.PAID.value
    assert bank.confirmed == [INTENT]
    cleared = await store.load(stored.conversation_id, account_id=ACCOUNT)
    assert cleared is not None and cleared.payment is None

    replay = await graph.confirm(
        workflow_id="attempt-yes", account_id=ACCOUNT, intent_id=INTENT, reply="sí"
    )
    assert replay == paid
    assert bank.confirmed == [INTENT]


@pytest.mark.asyncio
async def test_unclear_payment_reply_reinterrupts_without_calling_the_bank() -> None:
    graph, bank, store = workflow()
    preview = await graph.prepare(
        PrepareChosenPaymentCommand(
            account_id=ACCOUNT,
            recipient_name="Marta Solis Vega",
            amount_minor=2_000,
            currency="EUR",
            idempotency_key="attempt-unclear",
        ),
        account_id=ACCOUNT,
    )

    unclear = await graph.confirm(
        workflow_id="attempt-unclear",
        account_id=ACCOUNT,
        intent_id=INTENT,
        reply="sí, pero cambia el importe",
    )

    assert unclear.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert unclear.preview is None  # the stored bank preview remains authoritative
    assert bank.confirmed == []
    pending = await store.load(
        UUID(
            (
                await graph._graph.aget_state(  # noqa: SLF001
                    {"configurable": {"thread_id": _thread_id(ACCOUNT, "attempt-unclear")}}
                )
            ).values["conversation_id"]
        ),
        account_id=ACCOUNT,
    )
    assert pending is not None and isinstance(pending.payment, PaymentConfirmationPending)
    assert pending.payment.preview == preview.preview


@pytest.mark.asyncio
async def test_payment_checkpoint_cannot_be_resumed_by_another_account_or_intent() -> None:
    graph, bank, _ = workflow()
    await graph.prepare(
        PrepareChosenPaymentCommand(
            account_id=ACCOUNT,
            recipient_name="Marta Solis Vega",
            amount_minor=2_000,
            currency="EUR",
            idempotency_key="attempt-scope",
        ),
        account_id=ACCOUNT,
    )

    with pytest.raises(StaleDialogueError):
        await graph.confirm(
            workflow_id="attempt-scope",
            account_id=UUID(int=999),
            intent_id=INTENT,
            reply="sí",
        )
    with pytest.raises(StaleDialogueError):
        await graph.confirm(
            workflow_id="attempt-scope",
            account_id=ACCOUNT,
            intent_id=UUID(int=999),
            reply="sí",
        )
    assert bank.confirmed == []


@pytest.mark.asyncio
async def test_payment_http_routes_prepare_pause_and_resume_in_graph_mode() -> None:
    from finguard_orchestrator.domain.enums import PaymentOutcome
    from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import (
        MemoryDialogueStore,
    )

    stack = a_stack()
    commands = CommandBus()
    commands.register(PrepareChosenPaymentCommand, PrepareChosenPaymentHandler(stack.bank))
    commands.register(ConfirmPaymentCommand, ConfirmPaymentHandler(stack.bank))
    app.state.payment_workflow = PaymentWorkflowGraph(
        commands, MemoryDialogueStore(), InMemorySaver()
    )
    try:
        prepared = stack.client.post(
            "/v1/payments/chosen",
            headers={"Idempotency-Key": "web-payment-attempt"},
            json={
                "recipient_name": "Marta Solis Vega",
                "amount_minor": 2_000,
                "currency": "EUR",
            },
        )
        assert prepared.status_code == 200
        preview = prepared.json()
        assert preview["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
        assert preview["workflow_id"] == "web-payment-attempt"
        assert preview["intent_id"] == str(INTENT)
        checkpoint = await app.state.payment_workflow._graph.aget_state(  # noqa: SLF001
            {"configurable": {"thread_id": _thread_id(ACCOUNT, "web-payment-attempt")}}
        )
        assert checkpoint.values["account_id"] == str(ACCOUNT)
        assert checkpoint.values["preview"]
        assert checkpoint.values["phase"] == "waiting"

        confirmed = stack.client.post(
            f"/v1/payments/{INTENT}/confirm",
            json={"reply": "sí", "workflow_id": preview["workflow_id"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["outcome"] == PaymentOutcome.PAID.value
        assert stack.bank.confirmed == [INTENT]
    finally:
        app.state.payment_workflow = None
        app.dependency_overrides.clear()
