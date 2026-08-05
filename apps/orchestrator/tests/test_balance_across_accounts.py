"""«¿Cuánto tengo?» once that has more than one answer.

The route already existed — `Intent.ACCOUNT_BALANCE`, no model, straight off the port the home
screen reads. What changes is that the port now knows about the holder's other accounts, so the
sentence has to decide what "tengo" covers.

Two decisions carry the whole file, and both are about not answering a question nobody asked:

    the default is a breakdown, not a question   naming nothing is normal, not ambiguous
    an investment is not added into the total    a cost basis is not what something is worth
"""

import uuid
from datetime import UTC, datetime

from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.services import account_words, answer_writer

MOMENT = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)


def holding(kind: str, name: str | None, minor: int, currency: str = "EUR") -> Holding:
    return Holding(uuid.uuid4(), kind, name, currency, minor)


def summary(*holdings: Holding, available: int = 227_042) -> AccountSummary:
    return AccountSummary(
        currency="EUR",
        booked_minor=available,
        available_minor=available,
        as_of=MOMENT,
        holdings=holdings,
    )


CURRENT = holding("CURRENT", "Cuenta corriente", 227_042)
PIGGY = holding("SAVINGS", "Hucha", 548_550)
FUND = holding("ASSET", "Fondo indexado", 270_000)
EVERYTHING = summary(CURRENT, PIGGY, FUND)


def said(sentence: str, of: AccountSummary = EVERYTHING) -> str:
    """The whole path a sentence takes: vocabulary, then prose."""
    return answer_writer.balance(of, account_words.named_in(sentence))


# --- naming nothing -----------------------------------------------------------------------


def test_the_total_comes_with_the_accounts_it_came_from() -> None:
    """A breakdown rather than a clarification. "¿Cuánto tengo?" is a perfectly clear question and
    asking which account back would be treating clarity as ambiguity."""
    answer = said("¿Cuánto tengo?")

    assert "7.755,92 €" in answer
    assert "2.270,42 € en Cuenta corriente" in answer
    assert "5.485,50 € en Hucha" in answer


def test_the_investment_is_not_inside_the_total() -> None:
    """The decision the sentence turns on. 2.700,00 € is what was paid for the fund, not what it is
    worth — there is no price source — so adding it into "tienes X" would report a purchase price
    as somebody's money. 7.755,92 and not 10.455,92."""
    answer = said("¿Cuánto tengo?")

    assert "10.455,92" not in answer
    assert "7.755,92 €" in answer


def test_the_investment_is_still_mentioned_and_says_what_it_is() -> None:
    """Left out of the total, not left out of the answer. Omitting it would be its own wrong
    answer — somebody with 2.700 € invested has not been told about it."""
    answer = said("¿Cuánto tengo?")

    assert "2.700,00 € en Fondo indexado" in answer
    assert "no lo que vale hoy" in answer


# --- naming one ---------------------------------------------------------------------------


def test_naming_the_piggy_bank_answers_about_the_piggy_bank() -> None:
    answer = said("¿Cuánto tengo en la hucha?")

    assert answer == "Tienes 5.485,50 € en Hucha."


def test_naming_the_fund_keeps_the_caveat() -> None:
    """Asked about directly, it still has to say the figure is a cost and not a valuation. The
    caveat belongs to the number, not to the sentence that happened to introduce it."""
    answer = said("¿y en el fondo?")

    assert "2.700,00 €" in answer
    assert "no lo que vale hoy" in answer


def test_naming_two_accounts_totals_those_two() -> None:
    """ "En la hucha y en la corriente" — and "la corriente" without the noun, because that is how
    the second half of a sentence says it. Reading only the first half would answer about one
    account and look like a complete answer."""
    answer = said("¿cuánto tengo en la hucha y en la corriente?")

    assert "7.755,92 € entre esas cuentas" in answer
    assert "Cuenta corriente" in answer
    assert "Hucha" in answer


def test_a_kind_the_holder_does_not_have_is_said_plainly() -> None:
    """Not answered with the total. Somebody who asked about savings they do not have would read a
    current-account figure as their savings, and nothing in the sentence would give it away."""
    answer = said("¿cuánto tengo ahorrado en la hucha?", summary(CURRENT))

    assert "No tienes ninguna cuenta de ese tipo" in answer
    assert "Cuenta corriente" in answer


# --- what must not change -------------------------------------------------------------------


def test_a_bare_cuenta_gets_the_breakdown_rather_than_a_guess() -> None:
    """ "En cuenta" is the current account to one person and everything to another. The vocabulary
    deliberately does not know the word, so this falls through to the honest answer."""
    assert said("¿Cuánto tengo en cuenta?") == said("¿Cuánto tengo?")


def test_one_account_still_gets_the_sentence_it_always_got() -> None:
    """A holder with one account, or a banking that has not been deployed with the new endpoint.
    Neither should see a degraded version of the multi-account sentence."""
    assert answer_writer.balance(summary()) == "Tienes 2.270,42 € en tu cuenta."


def test_pending_money_still_separates_the_two_figures() -> None:
    """Untouched by any of this. Booked and available differ when something is pending, and showing
    one under the other's name tells somebody they have money they do not."""
    pending = AccountSummary(
        currency="EUR", booked_minor=250_000, available_minor=227_042, as_of=MOMENT
    )

    assert answer_writer.balance(pending) == (
        "Tienes 2.270,42 € disponibles en tu cuenta. El saldo contabilizado es de 2.500,00 €."
    )


def test_no_total_across_two_currencies() -> None:
    """Adding minor units across currencies gives a valid integer that means nothing. The list on
    its own is the honest answer, and no figure is invented to lead it."""
    answer = answer_writer.balance(summary(CURRENT, holding("SAVINGS", "Dollars", 50_000, "USD")))

    assert "entre tus cuentas" not in answer
    assert "2.270,42 €" in answer
    assert "500,00 USD" in answer


# --- the prose and the data are one decision ------------------------------------------------


def figure(sentence: str, of: AccountSummary = EVERYTHING) -> int | None:
    return answer_writer.leading_figure(of, account_words.named_in(sentence))


def test_the_figure_beside_the_sentence_is_the_one_in_it() -> None:
    """`total_minor` travels with the text so a client does not parse prose to find the number.
    They come from the same function for the same reason: two of them would eventually be two
    different numbers on one screen, which is the failure this product is named after."""
    assert figure("¿Cuánto tengo?") == 775_592
    assert "7.755,92 €" in said("¿Cuánto tengo?")


def test_the_figure_narrows_when_the_sentence_does() -> None:
    """Otherwise a client shows 2.270,42 € beside a sentence about the piggy bank."""
    assert figure("¿Cuánto tengo en la hucha?") == 548_550
    assert figure("¿cuánto tengo en la hucha y en la corriente?") == 775_592


def test_no_figure_when_there_is_no_single_figure() -> None:
    """Two currencies have no total, and a kind the holder does not have has no figure. `None`
    rather than picking one, which would be picking which question to answer."""
    mixed = summary(CURRENT, holding("SAVINGS", "Dollars", 50_000, "USD"))

    assert answer_writer.leading_figure(mixed) is None
    assert figure("¿cuánto tengo en la hucha?", summary(CURRENT)) is None


def test_one_account_keeps_the_figure_it_always_gave() -> None:
    """The available balance, unchanged. Not the booked one: the sentence leads with available."""
    pending = AccountSummary(
        currency="EUR", booked_minor=250_000, available_minor=227_042, as_of=MOMENT
    )

    assert answer_writer.leading_figure(pending) == 227_042


def test_an_account_nobody_named_is_called_by_its_kind() -> None:
    """Never by its reference. "ES-0001-2" is not something anybody recognises as theirs."""
    answer = answer_writer.balance(summary(CURRENT, holding("SAVINGS", None, 100_00)))

    assert "tus ahorros" in answer
    assert "ES-" not in answer
