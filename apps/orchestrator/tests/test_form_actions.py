"""A payment built in a form runs as a turn of the conversation, over HTTP, with no model call.

The Bizum and transfer screens used to post to their own routes, which the graph never saw. Now
they send a typed action to `/v1/conversation/actions` and the graph does what it does for a chat
payment: grounds the amount and the rail, asks banking for the preview through the guarded
commands, keeps it in canonical state, and answers it only through the confirmation allow-list.
What is asserted is the money: what banking was asked to prepare, confirm or cancel.
"""

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from support.episodes import (
    ACCOUNT,
    INTENT,
    PIGGY,
    Accounts,
    Contacts,
    Directory,
    Stack,
    a_stack,
)
from support.memory_dialogue_store import MemoryDialogueStore
from support.memory_turn_reservations import MemoryTurnReservations

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import PaymentOutcome, Rail
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.graph.graph_state import GraphPorts
from finguard_orchestrator.infrastructure.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.infrastructure.http.dependencies import authenticated_caller
from finguard_orchestrator.infrastructure.llm.counting_provider import calls_so_far

ROUTE = "/v1/conversation/actions"


class NoModel:
    """A form has nothing to interpret: any call to the interpreter is the defect."""

    async def interpret(self, *_: object, **__: object) -> tuple[()]:
        raise AssertionError("a typed action must not reach the interpreter")


class NoInvestigation:
    async def assess(self, _: object) -> object:
        raise AssertionError("a payment needs no investigation")


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(app.state, "engine", None, raising=False)
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def a_graph() -> Stack:
    stack = a_stack()
    app.state.engine = GraphTurnHandler(
        GraphPorts(
            NoModel(),  # type: ignore[arg-type]
            NoInvestigation(),  # type: ignore[arg-type]
            MemoryDialogueStore(),
            stack.ledger,  # type: ignore[arg-type]
            Accounts(),  # type: ignore[arg-type]
            Directory(),  # type: ignore[arg-type]
            Contacts(),  # type: ignore[arg-type]
            frozenset({"RESTAURANTS"}),
            count_calls=calls_so_far,
            commands=stack.commands,
        ),
        MemoryTurnReservations(),
        InMemorySaver(),
    )
    return stack


def act(stack: Stack, body: dict[str, Any], key: str | None = None) -> Any:
    return stack.client.post(ROUTE, headers={"Idempotency-Key": key or uuid4().hex}, json=body)


def bizum(conversation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "action": "pay_contact",
        "recipient_name": "Marta Solis Vega",
        "amount_minor": 2_000,
        "conversation": conversation,
    }


def decide(decision: str, conversation: dict[str, Any] | None) -> dict[str, Any]:
    return {"action": "answer_preview", "decision": decision, "conversation": conversation}


def test_a_form_bizum_is_a_bank_preview_and_nothing_moves() -> None:
    stack = a_graph()

    preview = act(stack, bizum())

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert body["conversation"]["intent_id"] == str(INTENT)
    assert body["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    [prepared] = stack.bank.prepared
    assert (prepared.recipient_name, prepared.amount_minor, prepared.rail) == (
        "Marta Solis Vega",
        2_000,
        Rail.BIZUM,
    )
    assert stack.bank.sources == [ACCOUNT]
    assert stack.bank.confirmed == stack.bank.cancelled == []


def test_confirming_the_preview_pays_it_once() -> None:
    stack = a_graph()
    preview = act(stack, bizum()).json()

    paid = act(stack, decide("confirm", preview["conversation"]))

    assert paid.status_code == 200, paid.text
    assert paid.json()["outcome"] == PaymentOutcome.PAID.value
    assert stack.bank.confirmed == [INTENT]
    assert stack.bank.cancelled == []


def test_cancelling_the_preview_never_confirms_it() -> None:
    stack = a_graph()
    preview = act(stack, bizum()).json()

    cancelled = act(stack, decide("cancel", preview["conversation"]))

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["outcome"] == PaymentOutcome.CANCELLED.value
    assert stack.bank.cancelled == [INTENT]
    assert stack.bank.confirmed == []


def test_a_second_confirmation_of_the_same_preview_pays_nothing_more() -> None:
    """A double tap: the first yes consumed the preview, so the second answers a stale one."""
    stack = a_graph()
    preview = act(stack, bizum()).json()
    act(stack, decide("confirm", preview["conversation"]))

    again = act(stack, decide("confirm", preview["conversation"]))

    assert again.status_code == 409
    assert stack.bank.confirmed == [INTENT]


def test_a_retried_confirmation_is_the_same_answer_not_a_second_payment() -> None:
    """A client resending after a timeout uses the same key: the answer is replayed."""
    stack = a_graph()
    preview = act(stack, bizum()).json()

    first = act(stack, decide("confirm", preview["conversation"]), key="tap-1")
    retry = act(stack, decide("confirm", preview["conversation"]), key="tap-1")

    assert first.status_code == retry.status_code == 200
    assert retry.json()["outcome"] == first.json()["outcome"] == PaymentOutcome.PAID.value
    assert stack.bank.confirmed == [INTENT]


def test_a_retried_preparation_is_one_preview() -> None:
    stack = a_graph()

    act(stack, bizum(), key="prepare-1")
    act(stack, bizum(), key="prepare-1")

    assert len(stack.bank.prepared) == 1


def test_there_is_nothing_to_confirm_without_a_preview() -> None:
    stack = a_graph()

    answered = act(stack, decide("confirm", None))

    assert answered.status_code == 409
    assert stack.bank.confirmed == []


def test_a_transfer_between_own_accounts_names_the_account_by_id() -> None:
    stack = a_graph()

    preview = act(
        stack,
        {"action": "move_between_accounts", "to_account_id": str(PIGGY), "amount_minor": 5_000},
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    [prepared] = stack.bank.prepared
    assert (prepared.rail, prepared.to_account_id, prepared.amount_minor) == (
        Rail.INTERNAL,
        PIGGY,
        5_000,
    )


def test_an_account_that_is_not_the_holders_is_refused() -> None:
    stack = a_graph()

    refused = act(
        stack,
        {"action": "move_between_accounts", "to_account_id": str(UUID(int=7)), "amount_minor": 50},
    )

    assert refused.status_code == 422
    assert stack.bank.prepared == []


def test_confirming_needs_the_confirm_permission() -> None:
    stack = a_graph()
    preview = act(stack, bizum()).json()
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE}),
    )

    refused = act(stack, decide("confirm", preview["conversation"]))

    assert refused.status_code == 403
    assert stack.bank.confirmed == []


def test_actions_are_closed_when_payments_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = a_graph()
    monkeypatch.setattr(app.state, "payments_enabled", False)

    refused = act(stack, bizum())

    assert refused.status_code == 503
    assert stack.bank.prepared == []
