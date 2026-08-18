from enum import StrEnum


class SessionState(StrEnum):
    CONNECTING = "connecting"
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


class SessionProtocolError(Exception):
    def __init__(self, code: str, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fatal = fatal


class SessionStateMachine:
    """Small deterministic transport state machine; it owns no financial meaning."""

    def __init__(self) -> None:
        self.state = SessionState.CONNECTING
        self.turn_id: str | None = None
        self.last_client_sequence = -1
        self.audio_bytes = 0

    def accept_sequence(self, sequence: int) -> None:
        if sequence <= self.last_client_sequence:
            raise SessionProtocolError(
                "DUPLICATE_SEQUENCE", "client sequence must increase", fatal=True
            )
        self.last_client_sequence = sequence

    def configure(self) -> None:
        self._require(SessionState.CONNECTING)
        self.state = SessionState.READY

    def start_input(self, turn_id: str) -> bool:
        barged_in = self.state in (SessionState.THINKING, SessionState.SPEAKING)
        if self.state not in (SessionState.READY, SessionState.THINKING, SessionState.SPEAKING):
            raise SessionProtocolError("INVALID_STATE", "input.start is not valid now")
        self.state = SessionState.LISTENING
        self.turn_id = turn_id
        self.audio_bytes = 0
        return barged_in

    def receive_audio(self, size: int, *, max_utterance_bytes: int) -> None:
        self._require(SessionState.LISTENING)
        self.audio_bytes += size
        if self.audio_bytes > max_utterance_bytes:
            raise SessionProtocolError("UTTERANCE_TOO_LONG", "utterance exceeds the time limit")

    def commit(self) -> None:
        self._require(SessionState.LISTENING)
        self.state = SessionState.TRANSCRIBING

    def thinking(self) -> None:
        self._require(SessionState.TRANSCRIBING)
        self.state = SessionState.THINKING

    def speaking(self) -> None:
        self._require(SessionState.THINKING)
        self.state = SessionState.SPEAKING

    def completed(self) -> None:
        self._require(SessionState.SPEAKING, SessionState.THINKING)
        self.state = SessionState.READY
        self.turn_id = None
        self.audio_bytes = 0

    def cancel(self) -> str | None:
        old_turn_id = self.turn_id
        was_active = self.state in (
            SessionState.TRANSCRIBING,
            SessionState.THINKING,
            SessionState.SPEAKING,
        )
        if was_active:
            self.state = SessionState.READY
            self.turn_id = None
            self.audio_bytes = 0
        return old_turn_id if was_active else None

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.turn_id = None

    def _require(self, *states: SessionState) -> None:
        if self.state not in states:
            names = ", ".join(state.value for state in states)
            raise SessionProtocolError("INVALID_STATE", f"expected {names}")
