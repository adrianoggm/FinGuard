from datetime import UTC, datetime, timedelta

from finguard_voice_gateway.tickets import TicketStore


def test_expired_unconsumed_ticket_and_credential_are_purged() -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = TicketStore(30, clock=lambda: current[0])
    store.issue("session", "bearer-secret")
    assert len(store) == 1
    current[0] += timedelta(seconds=31)
    assert store.purge_expired() == 1
    assert len(store) == 0


def test_random_token_is_rejected_and_does_not_consume_existing_ticket() -> None:
    store = TicketStore(30)
    issued = store.issue("session-a", "secret")
    assert store.consume("not-a-real-token", "session-a") is None
    assert len(store) == 1
    assert store.consume(issued.token, "session-a") == issued


def test_expired_ticket_is_rejected_and_consumed() -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = TicketStore(30, clock=lambda: current[0])
    issued = store.issue("session-a", "secret")
    current[0] += timedelta(seconds=31)
    assert store.consume(issued.token, "session-a") is None
    assert len(store) == 0


def test_ticket_isolated_by_session_id_and_wrong_attempt_consumes_token() -> None:
    store = TicketStore(30)
    issued = store.issue("session-a", "secret")
    assert store.consume(issued.token, "session-b") is None
    assert len(store) == 0
    assert store.consume(issued.token, "session-a") is None
