"""Reading an amount and a recipient out of a reply, without a model.

The two slots a half-built payment can be missing, and both are decidable here — which is what makes
"¿de cuánto?" and "¿a quién?" answerable in one turn instead of restarting the conversation.

An **amount** is a closed grammar: digits, an optional decimal, a currency word or symbol. "15
euros" is unambiguous in every dialect, and it is the exact sentence that broke the
reported conversation — a sentence with money and no payment verb is not a payment, so it
reached the financial planner and came back asking which month.

A **recipient** is a closed vocabulary *for this account*: the contact list. "Marta" names two
people and "Marta Solis Vega" names one, and both facts come from the directory rather than from a
guess. Same trick as the category and window vocabularies, with the same rule — nothing is matched
approximately, because paying the wrong person is the failure this whole path is built around.
"""

import re
from typing import Final

from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.payment_request import MAX_PAYMENT_MINOR

# A figure with an optional thousands separator and an optional two-decimal part. The currency is
# optional because "pues 20" is an answer to "¿de cuánto?" and carries no unit — but see the
# exclusions below for what that costs.
_AMOUNT: Final = re.compile(
    r"(?<![\d.,+])(\d{1,3}(?:\.\d{3})+|\d+)(?:[.,](\d{1,2}))?(?![\d])",
)

# What looks like an amount and is not. A phone number is the dangerous one: read as an amount it
# becomes an enormous payment in front of a confirmation prompt, where a distracted "sí" is the only
# thing between it and the ledger. A date is the common one — "el 15 de julio" is not 15 euros.
_NOT_AN_AMOUNT: Final = re.compile(
    r"\+?\d[\d\s]{7,}"  # a phone number, with or without spaces
    r"|\bel\s+\d{1,2}\s+de\s+\w+"  # "el 15 de julio"
    r"|\b\d{1,2}/\d{1,2}"  # a date written with slashes
    r"|\b\d{1,2}:\d{2}",  # a time
    re.IGNORECASE,
)


# Words that appear inside Spanish names and inside every Spanish sentence. Matching a contact on
# one of these would make "no sé qué hacer" name María del Carmen.
_PARTICLES: Final = frozenset({"del", "las", "los", "san", "santa", "von", "van"})


def amount_in(sentence: str) -> int | None:
    """The amount this sentence names, in minor units, or None.

    None is as useful as a figure: it means "this reply did not answer the amount question", and the
    turn goes to the model instead of a payment being built from a guess.

    The first figure wins. "15 euros de los 100 que le debo" is a payment of 15 — reading the
    largest or the last would both be defensible, and neither is what the sentence says first.
    """
    if _NOT_AN_AMOUNT.search(sentence):
        return None

    match = _AMOUNT.search(sentence)
    if match is None:
        return None

    units = int(match.group(1).replace(".", ""))
    cents = int((match.group(2) or "0").ljust(2, "0"))
    minor = units * 100 + cents
    if minor <= 0 or minor > MAX_PAYMENT_MINOR:
        # Beyond the ceiling the assistant will offer, so not an amount this can act on. None sends
        # it to the model, which refuses it in a sentence — better than returning a number that
        # every layer after this one has to reject.
        return None
    return minor


def contacts_in(sentence: str, directory: tuple[Contact, ...]) -> tuple[Contact, ...]:
    """Everybody in the directory whose name this sentence names.

    Zero, one or several, and several is not a failure: "Marta" really does name two people, and
    returning both is what lets the assistant offer the choice instead of guessing. Banking refuses
    to choose for exactly the same reason.

    Matches on the whole name or on any single part of it, with word boundaries — so "Solis" finds
    Marta Solis Vega and "manana" does not find Ana. Accents and case are folded, because the
    directory spells "Solís" and nobody types it.

    **A whole name beats the parts of another.** "Marta Solis Vega" contains "Marta", which is also
    Marta Ruiz Gomez's name, so matching on parts alone made the sentence that *resolves* the
    ambiguity report it again. When any contact is named in full, only those are returned — the same
    longest-match rule the category vocabulary uses, for the same reason.
    """
    haystack = spanish.folded(sentence)

    def names(contact: Contact) -> bool:
        whole = spanish.folded(contact.display_name)
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(whole)}(?![a-z0-9])", haystack))

    def names_a_part(contact: Contact) -> bool:
        # Each word of the name on its own, so a first name or a surname is enough. The particles
        # are skipped by name rather than by length: "Ana" is three letters and a real first name,
        # while "del" is three letters and appears in every other Spanish sentence.
        parts = [
            part
            for part in spanish.folded(contact.display_name).split()
            if len(part) > 2 and part not in _PARTICLES
        ]
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(part)}(?![a-z0-9])", haystack) for part in parts
        )

    exact = tuple(contact for contact in directory if names(contact))
    return exact or tuple(contact for contact in directory if names_a_part(contact))
