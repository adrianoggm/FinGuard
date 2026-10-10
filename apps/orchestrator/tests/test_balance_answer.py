"""The balance, asked in the chat.

    "Buenos días, ¿podrías decirme cuánto dinero tengo en mis cuentas?"
    → "No he encontrado movimientos del 2026-08-04 al 2026-08-04."

Three things were wrong and only one of them was the model's doing. The router had no intent for
it, so it went to the financial planner; `FinancialPlan` cannot express a balance, so the model
built the nearest thing it could reach — today's movements; and the figure it was asked for had
been available all along, with no model in the path, on the same port the home screen reads.

So this is not a new capability. It is a route to one that already existed.
"""

from datetime import datetime

from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.services import answer_writer

SUMMARY = AccountSummary(
    currency="EUR",
    booked_minor=1_042_042,
    available_minor=1_037_042,
    as_of=datetime(2026, 8, 4, 8, 12),
)


# --- the sentence -------------------------------------------------------------------


def test_the_two_balances_are_both_named_when_they_differ() -> None:
    """Booked is what the ledger has settled, available is what can be spent. Showing one and
    calling it the other tells somebody they have money they do not, so when they differ the
    answer says both and labels which is which."""
    said = answer_writer.balance(SUMMARY)

    assert said == (
        "Tienes 10.370,42 € disponibles en tu cuenta. El saldo contabilizado es de 10.420,42 €."
    )


def test_one_figure_when_there_is_nothing_pending() -> None:
    """The common case. Repeating the same number twice under two labels reads like a discrepancy
    the user has to work out, so it is said once."""
    settled = AccountSummary(
        currency="EUR",
        booked_minor=1_037_042,
        available_minor=1_037_042,
        as_of=datetime(2026, 8, 4, 8, 12),
    )

    assert answer_writer.balance(settled) == "Tienes 10.370,42 € en tu cuenta."
