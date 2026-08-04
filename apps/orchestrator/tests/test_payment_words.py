"""Reading an amount and a recipient out of a reply, without a model.

The two slots a half-built payment can be missing, and both are decidable here.

An **amount** is a closed grammar: digits, an optional decimal, and a currency word. "15
euros" is not ambiguous in any dialect, and it is the sentence that broke the reported
conversation — it went to the financial planner, having money in it and no payment verb.

A **recipient** is a closed vocabulary *for this account*: the contact list. "Marta" names two
people and "Marta Solis Vega" names one, and both facts come from the directory rather than from a
guess. That is what makes "¿a quién?" answerable deterministically, and it is the same trick the
category and window vocabularies use.
"""

import uuid

import pytest

from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import payment_words

MARTA_RUIZ = Contact(uuid.UUID(int=1), "Marta Ruiz Gomez", "+34600111222")
MARTA_SOLIS = Contact(uuid.UUID(int=2), "Marta Solis Vega", "+34600333444")
AITOR = Contact(uuid.UUID(int=3), "Aitor Beltran Diaz", "+34600555666")
CARMEN = Contact(uuid.UUID(int=4), "Carmen Nieto Lara", "+34600777888")
EVERYBODY = (AITOR, CARMEN, MARTA_RUIZ, MARTA_SOLIS)


# --- the amount ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "minor"),
    [
        ("15 euros", 1_500),
        ("15€", 1_500),
        ("15 €", 1_500),
        ("15,50 euros", 1_550),
        ("15.50 euros", 1_550),
        ("pues 20", 2_000),
        ("mejor 12,05", 1_205),
        ("1.000 euros", 100_000),
        ("0,99 €", 99),
        ("EUR 30", 3_000),
    ],
)
def test_an_amount_is_read_from_the_reply(sentence: str, minor: int) -> None:
    assert payment_words.amount_in(sentence) == minor


@pytest.mark.parametrize(
    "sentence",
    [
        "",
        "no sé",
        "a Marta",
        "lo que tú veas",
        # A phone number is not an amount, and neither is a date. Both would otherwise arrive as
        # enormous payments in front of a confirmation prompt.
        "+34600111222",
        "el 15 de julio",
        # Beyond the ceiling the assistant will offer. None sends it to the model, which refuses it
        # with a sentence rather than this returning a number nobody may act on.
        "20000000 euros",
    ],
)
def test_anything_else_is_not_an_amount(sentence: str) -> None:
    assert payment_words.amount_in(sentence) is None


def test_the_first_amount_wins() -> None:
    """A payment of 15, not of 100. Reading the largest figure, or the last, would both be
    defensible — and neither is what the sentence says first."""
    assert payment_words.amount_in("15 euros de los 100 que le debo") == 1_500


# --- the recipient -------------------------------------------------------------------


def test_a_full_name_names_one_contact() -> None:
    found = payment_words.contacts_in("Marta Solis Vega", EVERYBODY)

    assert [c.display_name for c in found] == ["Marta Solis Vega"]


def test_a_first_name_names_everybody_who_has_it() -> None:
    """Which is the honest answer: the ambiguity is real, and banking is the one that refuses to
    choose. Returning both is what lets the assistant offer them."""
    found = payment_words.contacts_in("le hacemos un bizum a Marta", EVERYBODY)

    assert {c.display_name for c in found} == {"Marta Ruiz Gomez", "Marta Solis Vega"}


def test_a_surname_alone_names_one() -> None:
    assert [c.display_name for c in payment_words.contacts_in("Solis", EVERYBODY)] == [
        "Marta Solis Vega"
    ]


def test_accents_and_case_do_not_matter() -> None:
    """Nobody types "Solís" with the accent, and the directory spells it either way."""
    accented = (Contact(uuid.UUID(int=5), "Martá Solís Vega", "+34600999000"),)

    assert payment_words.contacts_in("marta solis vega", accented) != ()


@pytest.mark.parametrize(
    "sentence",
    [
        "15 euros",
        "no sé",
        "",
        "a mi hermana",
        # A word that is not a name, and a name nobody in the directory has. Both must find
        # nothing rather than the nearest match: paying the wrong person is the failure this
        # whole path is built around.
        "supermercado",
        "a Lucía",
    ],
)
def test_a_sentence_that_names_nobody_finds_nobody(sentence: str) -> None:
    assert payment_words.contacts_in(sentence, EVERYBODY) == ()


def test_a_short_name_is_not_found_inside_a_longer_word() -> None:
    """ "Ana" must not be found in "manana". Word boundaries, like every other vocabulary here."""
    ana = (Contact(uuid.UUID(int=6), "Ana Lopez", "+34600123123"),)

    assert payment_words.contacts_in("se lo mando manana", ana) == ()
    assert payment_words.contacts_in("mándale algo a Ana", ana) != ()
