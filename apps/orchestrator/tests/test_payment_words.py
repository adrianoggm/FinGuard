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
        ("veinte euros", 2_000),
        ("venti euros", 2_000),
        ("treinta y cinco euros", 3_500),
        ("mil doscientos euros", 120_000),
        ("un bizum de veinte euros", 2_000),
        ("veinte euros y cincuenta céntimos", 2_050),
        ("cincuenta céntimos", 50),
        ("pues veinte", 2_000),
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
        "un bizum a Marta",
        # Typo tolerance is an explicit allow-list, not arbitrary fuzzy arithmetic.
        "vienti euros",
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


def test_a_written_amount_beyond_the_payment_ceiling_is_refused() -> None:
    assert payment_words.amount_in("once mil euros") is None


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


# --- the most specific match wins ----------------------------------------------------
#
# From the report, three turns in a row:
#
#     "Marta Vega"                              → "Tienes varios contactos con ese nombre…"
#     "a Marta Vega quiero mandarle el bizum"   → the same question again
#
# A dead end: the user names one person and the assistant keeps asking which of two. Matching on
# any single part of a name made "Marta Vega" match Marta Ruiz Gomez on "marta" and Marta Solis
# Vega on "marta" and "vega" — two matches, so ambiguous forever.
#
# The rule is how much of a name the sentence gets right. Two parts beats one, so "Marta Vega"
# resolves, and "Marta" alone still names both because they tie on one.


def test_two_parts_of_a_name_beat_one() -> None:
    found = payment_words.contacts_in("Marta Vega", EVERYBODY)

    assert [c.display_name for c in found] == ["Marta Solis Vega"]


def test_the_sentence_around_the_name_does_not_change_it() -> None:
    found = payment_words.contacts_in("a Marta Vega quiero mandarle el bizum", EVERYBODY)

    assert [c.display_name for c in found] == ["Marta Solis Vega"]


def test_a_first_name_shared_by_two_is_still_ambiguous() -> None:
    """The tie is real, and resolving it by picking one would be the assistant choosing who gets
    the money."""
    found = payment_words.contacts_in("un bizum a Marta", EVERYBODY)

    assert {c.display_name for c in found} == {"Marta Ruiz Gomez", "Marta Solis Vega"}


def test_a_surname_alone_still_resolves() -> None:
    assert [c.display_name for c in payment_words.contacts_in("a Ruiz", EVERYBODY)] == [
        "Marta Ruiz Gomez"
    ]


def test_the_order_of_the_parts_does_not_matter() -> None:
    """ "Vega Marta" is not how anybody writes it and it names the same person. The rule counts the
    parts the sentence gets right, not the order it puts them in."""
    assert [c.display_name for c in payment_words.contacts_in("Vega Marta", EVERYBODY)] == [
        "Marta Solis Vega"
    ]


# --- the concept ---------------------------------------------------------------------
#
# "pues 15 euros con concepto de comida a Marta" reached the continuation path, which read the
# amount and the name and nothing else — so the note the user wrote was lost on exactly the turn
# they wrote it.
#
# Only the explicit form is read. "Para Marta" is a recipient and "por fin" is filler, so guessing a
# concept out of any preposition would put arbitrary words on a payment.


@pytest.mark.parametrize(
    ("sentence", "concept"),
    [
        ("15 euros con concepto de comida", "comida"),
        ("con el concepto tapas", "tapas"),
        ("15 euros concepto: cena del viernes", "cena del viernes"),
        ("mándale 20 en concepto de alquiler", "alquiler"),
        ("con concepto de la cena de ayer", "la cena de ayer"),
        ("con el concepto viaje a Málaga", "viaje a Málaga"),
        ("con el concepto cena por bizum ahora mismo", "cena"),
        ("con el conecpto cena por favor", "cena"),
        ("el concepto del pago es viaje a Málaga", "viaje a Málaga"),
    ],
)
def test_the_concept_is_read_when_it_is_named(sentence: str, concept: str) -> None:
    assert payment_words.concept_in(sentence) == concept


@pytest.mark.parametrize(
    "sentence",
    [
        "15 euros",
        "a Marta Solis Vega",
        "para Marta",
        "por fin",
        "",
        # The word without anything after it is not a concept.
        "con concepto",
    ],
)
def test_nothing_else_is_a_concept(sentence: str) -> None:
    assert payment_words.concept_in(sentence) is None


def test_the_recipient_is_not_swallowed_into_the_concept() -> None:
    """ "con concepto de comida a Marta" ends at "a Marta": the name is a different slot, and a
    concept that ate it would put the recipient in the ledger's description."""
    assert payment_words.concept_in("con concepto de comida a Marta", EVERYBODY) == "comida"


def test_a_place_is_not_mistaken_for_a_recipient() -> None:
    assert payment_words.concept_in("con concepto de viaje a Málaga", EVERYBODY) == "viaje a Málaga"


def test_a_place_before_a_real_recipient_stays_in_the_concept() -> None:
    assert (
        payment_words.concept_in("con concepto viaje a Málaga a Marta Solis Vega", EVERYBODY)
        == "viaje a Málaga"
    )


def test_a_concept_longer_than_the_field_is_refused() -> None:
    """None rather than a truncation. A note cut in half is a note the user did not write."""
    assert payment_words.concept_in("con concepto de " + "x" * 300) is None


# --- a name is not the word after it ---------------------------------------------------------


@pytest.mark.parametrize(
    ("read", "name"),
    [
        ("aitor anda", "aitor"),
        ("aitor anda por favor", "aitor"),
        ("Aitor, venga", "Aitor"),
        ("Javi ya", "Javi"),
        ("Marta Solis Vega", "Marta Solis Vega"),
        ("Carmen Nieto Lara", "Carmen Nieto Lara"),
        # Every word is filler, so no name was read. Returning it unchanged lets the layer above
        # refuse it as the non-name it is rather than crash on an empty string.
        ("anda", "anda"),
        ("Vale", "Vale"),
    ],
)
def test_a_conversational_tail_is_not_part_of_the_name(read: str, name: str) -> None:
    """Reported. "Hazle un bixum a aitor anda" was read as a payment to *"aitor anda"*, the question
    came back "¿de cuánto quieres hacerle el bixum a aitor anda?", and the contact lookup then found
    nobody — two turns lost to a word that means "go on".

    Stripped rather than tolerated in the lookup, because the name is echoed back at the holder. A
    question naming somebody who does not exist reads as the assistant having misheard.
    """
    assert payment_words.without_trailing_filler(read) == name


# --- which turn belongs to the open question --------------------------------------------------


@pytest.mark.parametrize("sentence", ["pues bizum", "bizum", "un bizum", "vale, bizum", "y bisum"])
def test_a_turn_that_is_only_a_rail_word_belongs_to_the_open_question(sentence: str) -> None:
    """ "Pues bizum", offered as the answer to "¿de qué comercio o categoría?", is a word given to a
    question and not an order — reading it as one asked who to send money to, about a payment nobody
    had ordered."""
    assert payment_words.is_only_a_rail_word(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum a Carmen",
        "que le hagas el bizum",
        "hagamos un bisum",
        "bizum de 20 euros",
        "bizum a Marta",
    ],
)
def test_anything_more_than_the_word_is_an_order(sentence: str) -> None:
    """The inversion, and why it is the right way round. The set of Spanish ways to say "do it" is
    unbounded and the previous rule enumerated it — so every form nobody thought of fell on the side
    of being swallowed by a question. Enumerating the replies instead is a closed, tiny set, and a
    form nobody thought of now falls on the side where the holder gets a preview to decline."""
    assert payment_words.is_only_a_rail_word(sentence) is False
