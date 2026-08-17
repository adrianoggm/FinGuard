import pytest

from finguard_voice_gateway.domain.state import (
    SessionProtocolError,
    SessionState,
    SessionStateMachine,
)


def test_state_machine_follows_voice_lifecycle() -> None:
    machine = SessionStateMachine()
    machine.configure()
    machine.start_input("turn-1")
    machine.receive_audio(20, max_utterance_bytes=100)
    machine.commit()
    machine.thinking()
    machine.speaking()
    machine.completed()
    assert machine.state == SessionState.READY


def test_duplicate_sequence_and_wrong_order_are_deterministic() -> None:
    machine = SessionStateMachine()
    machine.accept_sequence(1)
    with pytest.raises(SessionProtocolError, match="sequence"):
        machine.accept_sequence(1)
    with pytest.raises(SessionProtocolError, match="input.start"):
        machine.start_input("turn-1")


def test_cancel_is_idempotent() -> None:
    machine = SessionStateMachine()
    machine.configure()
    machine.start_input("turn-1")
    machine.commit()
    assert machine.cancel() == "turn-1"
    assert machine.cancel() is None


def test_thinking_accepts_barge_in_as_a_new_turn() -> None:
    machine = SessionStateMachine()
    machine.configure()
    machine.start_input("old-turn")
    machine.commit()
    machine.thinking()
    assert machine.start_input("new-turn") is True
    assert machine.state == SessionState.LISTENING
    assert machine.turn_id == "new-turn"


@pytest.mark.parametrize(
    "operation",
    ["configure", "start_input", "receive_audio", "commit", "thinking", "speaking"],
)
def test_lifecycle_operations_reject_invalid_state(operation: str) -> None:
    machine = SessionStateMachine()
    if operation == "configure":
        machine.configure()
    elif operation == "start_input":
        machine.configure()
        machine.start_input("existing")
    elif operation == "receive_audio":
        machine.configure()
        machine.start_input("existing")
        machine.commit()
    else:
        machine.configure()
    expected = "input.start" if operation == "start_input" else "expected"
    with pytest.raises(SessionProtocolError, match=expected):
        if operation == "configure":
            machine.configure()
        elif operation == "start_input":
            machine.start_input("turn")
        elif operation == "receive_audio":
            machine.receive_audio(2, max_utterance_bytes=2)
        elif operation == "commit":
            machine.commit()
        elif operation == "thinking":
            machine.thinking()
        else:
            machine.speaking()


def test_sequence_must_be_strictly_monotonic() -> None:
    machine = SessionStateMachine()
    machine.accept_sequence(2)
    machine.accept_sequence(9)
    with pytest.raises(SessionProtocolError, match="increase"):
        machine.accept_sequence(8)


def test_utterance_limit_is_exact_and_then_rejected() -> None:
    machine = SessionStateMachine()
    machine.configure()
    machine.start_input("turn")
    machine.receive_audio(4, max_utterance_bytes=4)
    with pytest.raises(SessionProtocolError, match="time limit"):
        machine.receive_audio(2, max_utterance_bytes=4)
