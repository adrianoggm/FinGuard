"""Which account a sentence names, and the ones it deliberately does not.

The vocabulary is small and the interesting half of it is the absences. A holder with a current
account, a piggy bank and a fund asks "¿cuánto tengo?" and means all three; the same holder asks
"¿cuánto tengo en la hucha?" and means one. Reading the first as the second is the failure this
product exists to prevent — a real figure answering a different question — and it is silent,
because a balance for the wrong account looks exactly like a balance.
"""

import pytest

from finguard_orchestrator.domain.services import account_words


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("¿Cuánto tengo en la hucha?", ("SAVINGS",)),
        ("Cuanto tiengo en mi huch", ("SAVINGS",)),
        ("¿y en el fondo?", ("ASSET",)),
        ("¿Cuánto me queda en la cuenta corriente?", ("CURRENT",)),
        ("¿cuánto me ha entrado de la nómina?", ("CURRENT",)),
        ("¿qué tengo en la cuenta de ahorros?", ("SAVINGS",)),
    ],
)
def test_a_sentence_that_names_an_account_names_its_kind(
    sentence: str, expected: tuple[str, ...]
) -> None:
    assert account_words.named_in(sentence) == expected


def test_naming_none_is_the_ordinary_case() -> None:
    """Not a failure, and a caller that treats it as one asks a clarifying question to somebody who
    asked a perfectly clear one. "¿Cuánto tengo?" is answered with the total and a breakdown."""
    assert account_words.named_in("¿Cuánto tengo?") == ()


def test_a_bare_cuenta_names_nothing() -> None:
    """The absence the table is built around, and the most tempting word to add back.

    "En cuenta" is the current account to one person and everything to another. Reading it as
    CURRENT would answer a different question with a real figure, and the breakdown is a better
    answer than a guess. It is also a verb — "cuenta cuánto gasté" is an instruction and names no
    account at all, which no word boundary would have caught.
    """
    assert account_words.named_in("¿Cuánto tengo en cuenta?") == ()
    assert account_words.named_in("cuenta cuánto gasté este mes") == ()


def test_two_accounts_come_back_in_the_order_they_were_said() -> None:
    """So an answer can follow the question. Naming two is ordinary: somebody comparing their
    current account with their savings said both on purpose."""
    assert account_words.named_in("la hucha y la cuenta corriente") == ("SAVINGS", "CURRENT")
    assert account_words.named_in("la cuenta corriente y la hucha") == ("CURRENT", "SAVINGS")


def test_accents_are_not_required() -> None:
    """Somebody typing "nomina" means "nómina". A match that needed the exact spelling would answer
    "I do not know that word" to a word it knows — the property `spanish.folded` exists for."""
    assert account_words.named_in("lo de la nomina") == ("CURRENT",)


@pytest.mark.parametrize(
    "sentence", ["La principal", "mi cuenta principal", "la princpal", "la nomnia"]
)
def test_principal_is_a_natural_name_for_the_current_account(sentence: str) -> None:
    assert account_words.named_in(sentence) == ("CURRENT",)


def test_a_custom_name_resolves_without_turning_a_generic_label_into_a_choice() -> None:
    names = ("Hucha", "Hucha viajes", "Vacaciones")

    assert account_words.custom_chosen_among("en Hucha viajes", names) == ("Hucha viajes",)
    assert account_words.custom_chosen_among("en la hucha", names) == ()
    assert account_words.custom_chosen_among("en mi cuenta Vacaciones", names) == ("Vacaciones",)
    assert account_words.custom_destination_among(
        "mueve de la cuenta Vacaciones a Hucha viajes", names
    ) == ("Hucha viajes",)


@pytest.mark.parametrize(
    ("sentence", "kind"),
    [
        ("y en la hcuha", "SAVINGS"),
        ("mueve 100 a la hcuha", "SAVINGS"),
        # The entry that was written by hand and is now derived: "nomnia" is "nomina" with two
        # letters swapped, and it was a line in the table standing for an open set.
        ("en la nomnia", "CURRENT"),
        ("en mi cuneta corriente", "CURRENT"),
    ],
)
def test_two_swapped_letters_still_name_the_account(sentence: str, kind: str) -> None:
    """A slip in an account name used to lose the account entirely.

    "Y en la hcuha" after a balance is a question about the savings balance, and it named nothing —
    so a follow-up this service can answer without a model was planned as a spending query.
    """
    assert account_words.named_in(sentence) == (kind,)


@pytest.mark.parametrize("sentence", ["he gastado mucha pasta", "la ducha nueva", "media hora"])
def test_a_substituted_letter_does_not_invent_an_account(sentence: str) -> None:
    """Why swaps and not any one edit. One substitution from "hucha" is "mucha", which is in every
    other Spanish sentence — and this vocabulary decides which accounts a figure is about."""
    assert account_words.named_in(sentence) == ()
