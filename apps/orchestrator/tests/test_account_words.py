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


def test_an_explicit_custom_account_reference_is_recognised_without_knowing_its_name() -> None:
    assert account_words.explicitly_names_a_custom_account("en mi cuenta Vacaciones")
    assert account_words.names_a_custom_destination("mueve 20 a la cuenta Vacaciones")
    assert not account_words.names_a_custom_destination("mueve mi cuenta Vacaciones")
    assert not account_words.explicitly_names_a_custom_account("cuenta cuánto gasté")


def test_a_custom_name_resolves_without_turning_a_generic_label_into_a_choice() -> None:
    names = ("Hucha", "Hucha viajes", "Vacaciones")

    assert account_words.custom_chosen_among("en Hucha viajes", names) == ("Hucha viajes",)
    assert account_words.custom_chosen_among("en la hucha", names) == ()
    assert account_words.custom_chosen_among("en mi cuenta Vacaciones", names) == ("Vacaciones",)
    assert account_words.custom_destination_among(
        "mueve de la cuenta Vacaciones a Hucha viajes", names
    ) == ("Hucha viajes",)


def test_a_short_custom_name_can_answer_an_open_account_slot() -> None:
    assert account_words.looks_like_account_choice("Vacaciones")
    assert not account_words.looks_like_account_choice("¿cuánto gasté este mes?")
