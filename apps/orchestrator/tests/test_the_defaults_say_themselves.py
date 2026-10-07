"""What a question means when it does not say, and the answer saying it (spec §3.5).

Measured live on 2026-10-07: «¿Cuánto llevo gastado en Spotify?» was asked which account and then
which period, two questions about things the person had a perfectly ordinary meaning for. The
defaults are the product's now — every account unless the person chose one, the year so far for
«llevo gastado», the last 30 days for «últimamente» — and each answer names the window it used, so a
default is never a silent guess. The old account question is one setting away
(`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask`).

Driven over HTTP on the episodes stack: three accounts, nothing settled.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from support import episodes
from support.pending import ask

from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import app, create_app


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# --- the account -------------------------------------------------------------------------------


def test_an_unqualified_spending_question_does_not_ask_for_the_account() -> None:
    stack = episodes.a_stack()  # directory with three accounts, nothing settled
    answered = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert answered["outcome"] == "PLANNED"
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas" not in answered["text"]


def test_the_old_question_is_one_setting_away() -> None:
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"


def test_a_balance_still_lists_every_account() -> None:
    answered = ask(episodes.a_stack(), "¿cuánto tengo?")
    assert "Hucha" in answered["text"] and "Cuenta corriente" in answered["text"]


def test_a_payment_still_names_its_source_account() -> None:
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    previewed = ask(stack, "15 euros con concepto de comida", started["conversation"])
    assert previewed["outcome"] == "AWAITING_CONFIRMATION"
    assert stack.bank.prepared[-1].amount_minor == 1500  # unchanged by the analytics default


def test_a_payment_leaves_from_the_credentials_account_under_either_setting() -> None:
    """The analytics default is about which figures answer a question. A payment's source is the
    account the credential names, and neither value of the setting reaches it."""
    for setting in ("all", "ask"):
        stack = episodes.a_stack(account_scope_default=setting)
        started = ask(stack, "hagamos un bizum a Marta Solis Vega")
        ask(stack, "15 euros con concepto de comida", started["conversation"])
        assert stack.bank.sources == [episodes.ACCOUNT], setting


def test_an_account_named_in_the_sentence_still_wins() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto gasté en la hucha en julio?")
    ran = stack.ledger.plans[-1]
    assert not ran.across_accounts and ran.only_account_id == episodes.PIGGY
    assert "Consultado en Hucha." in answered["text"]


def test_an_account_chosen_earlier_in_the_conversation_still_wins() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "¿Cuánto gasté en la hucha en julio?")
    ask(stack, "¿Cuánto gasté en restaurantes en junio?", first["conversation"])
    ran = stack.ledger.plans[-1]
    assert not ran.across_accounts and ran.only_account_id == episodes.PIGGY


def test_every_account_is_said_when_the_sentence_asks_for_every_account() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto gasté en julio en todas mis cuentas?")
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas." in answered["text"]


def test_every_account_chosen_at_the_question_is_said() -> None:
    """Under `ask`, the chip that answers the question is the scope chosen this turn, and the answer
    confirms it — the line a later turn that never mentions accounts goes on saying there, because
    every account was the person's choice rather than the default."""
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    answered = ask(stack, "Todas mis cuentas", asked["conversation"])
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas." in answered["text"]


# --- the setting -------------------------------------------------------------------------------


@pytest.mark.parametrize("chosen", ["all", "ask"])
def test_the_setting_reaches_the_handler(monkeypatch: pytest.MonkeyPatch, chosen: str) -> None:
    """Through `create_app`, because what could break is the wiring: a handler built without the
    setting would run on its own default whatever the deployment said."""
    monkeypatch.setenv("ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT", chosen)
    get_settings.cache_clear()
    try:
        built = create_app()
    finally:
        get_settings.cache_clear()
    assert built.state.ask_handler._account_scope_default == chosen  # noqa: SLF001


def test_every_account_is_what_ships() -> None:
    """Read from the class and from `.env.defaults`, not from an instance, so a developer whose own
    `.env` asks the question does not fail a test about what the code ships with."""
    assert Settings.model_fields["account_scope_default"].default == "all"
    shipped = (Path(__file__).resolve().parents[1] / ".env.defaults").read_text(encoding="utf-8")
    assert "\nORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=all\n" in shipped
