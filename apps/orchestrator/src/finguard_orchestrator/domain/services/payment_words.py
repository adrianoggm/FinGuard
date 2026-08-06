"""Reading an amount and a recipient out of a reply, without a model.

The two slots a half-built payment can be missing, and both are decidable here — which is what makes
"¿de cuánto?" and "¿a quién?" answerable in one turn instead of restarting the conversation.

An **amount** is a closed grammar: digits with an optional decimal, or compositional Spanish
cardinals such as "treinta y cinco" and "mil doscientos". "15 euros" is unambiguous in every
dialect, and it is the exact sentence that broke the
reported conversation — a sentence with money and no payment verb is not a payment, so it
reached the financial planner and came back asking which month.

A **recipient** is a closed vocabulary *for this account*: the contact list. "Marta" names two
people and "Marta Solis Vega" names one, and both facts come from the directory rather than from a
guess. Same trick as the category and window vocabularies, with the same rule — nothing is matched
approximately, because paying the wrong person is the failure this whole path is built around.
"""

import re
from typing import Final

from finguard_orchestrator.domain.constants import (
    MAX_CONCEPT,
    MAX_PAYMENT_MINOR,
)
from finguard_orchestrator.domain.enums import Rail
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import spanish, spanish_numbers

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


# The explicit word that introduces a note. The misspellings are a finite allow-list taken from
# measured drift cases, never edit-distance matching: accepting an arbitrary near-match here could
# put unrelated user text on a payment. What follows is parsed in stages because ``a`` may be a
# recipient boundary (``comida a Marta``) or part of the note (``viaje a Malaga``).
_CONCEPT: Final = re.compile(
    r"\b(?:concepto|conecpto|concepyo|concepot|conzepto)\b\s*"
    r"(?:del\s+pago\s+(?:es|sea)\s+|(?:de|a|es|sea)\s+|:\s*)?"
    r"(?P<concept>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Phrases that qualify the instruction, not the ledger note. They are deliberately specific:
# stripping every trailing ``por ...`` would corrupt perfectly valid concepts.
_CONCEPT_SUFFIXES: Final = (
    re.compile(r"\s+por\s+bi(?:zum+|zm|zaum)\b.*$", re.IGNORECASE),
    re.compile(r"\s+por\s+favor(?:\s+lo\s+antes\s+posible)?\s*$", re.IGNORECASE),
    re.compile(r"\s+(?:ahora\s+mismo|lo\s+antes\s+posible|est[aá]\s+bien)\s*$", re.IGNORECASE),
    re.compile(r"\s*,\s*(?:gracias|oye|vale)\s*$", re.IGNORECASE),
)

_RECIPIENT_BOUNDARY: Final = re.compile(r"\s+(?:a|para|al)\s+", re.IGNORECASE)

# Changing a recipient while a preview is open must be explicit. A contact name alone may be a
# question or part of a concept; cancelling a held intent on that guess would be destructive. These
# forms name the relationship that changes and are narrow enough to act on deterministically.
_RECIPIENT_CORRECTION: Final = re.compile(
    r"\b(?:era|es)\s+(?:para|a)\s+\S"
    r"|\bmejor\s+(?:para|a)\s+\S"
    r"|\b(?:cambia|cambiar|corrige|corregir|rectifica|rectificar)"
    r"\s+(?:el\s+|la\s+)?(?:destinatario|contacto|persona)\s+(?:a|por)\s+\S",
    re.IGNORECASE,
)

_CLEAR_CONCEPT: Final = re.compile(
    r"\b(?:quita|quitar|elimina|eliminar|borra|borrar)\s+(?:el\s+)?concepto\b"
    r"|\b(?:sin|ningun)\s+concepto\b",
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
        return spanish_numbers.money_in(sentence)

    units = int(match.group(1).replace(".", ""))
    cents = int((match.group(2) or "0").ljust(2, "0"))
    minor = units * 100 + cents
    if minor <= 0 or minor > MAX_PAYMENT_MINOR:
        # Beyond the ceiling the assistant will offer, so not an amount this can act on. None sends
        # it to the model, which refuses it in a sentence — better than returning a number that
        # every layer after this one has to reject.
        return None
    return minor


def concept_in(sentence: str, directory: tuple[Contact, ...] = ()) -> str | None:
    """The note this sentence names, or None.

    Read here because a continuation never reaches the model: "pues 15 euros con concepto de comida
    a Marta" answered a question about the amount, and the amount and the name were read while the
    note the user wrote in the same breath was dropped.

    **Only the explicit form.** "Para Marta" is a recipient and "por fin" is filler, so guessing a
    concept out of any preposition would write arbitrary words onto a payment. It stops at the
    recipient — "de comida a Marta" is "comida", because the name is a different slot and a concept
    that ate it would put the recipient in the ledger's description.

    The original casing is kept: this is the user's own text, and folding it would send "Cena del
    Viernes" to the bank in lower case.
    """
    match = _CONCEPT.search(sentence)
    if match is None:
        return None
    written = match.group("concept").strip(" .,;:\"'")
    for suffix in _CONCEPT_SUFFIXES:
        written = suffix.sub("", written).strip(" .,;:\"'")

    if directory:
        # Last boundary first: ``viaje a Malaga a Marta`` keeps the place and removes the payee.
        for boundary in reversed(tuple(_RECIPIENT_BOUNDARY.finditer(written))):
            recipient = written[boundary.end() :]
            if contacts_in(recipient, directory):
                written = written[: boundary.start()].strip(" .,;:\"'")
                break
    if not written or len(written) > MAX_CONCEPT:
        # A note longer than the field is refused rather than cut: half a sentence is not what the
        # user wrote, and the model path can ask about it instead.
        return None
    return written


def clears_concept(sentence: str) -> bool:
    """Whether the user explicitly asks for the replacement payment to carry no concept."""
    return _CLEAR_CONCEPT.search(spanish.folded(sentence)) is not None


def changes_recipient(sentence: str) -> bool:
    """Whether a reply to a preview explicitly replaces who receives the payment."""
    return _RECIPIENT_CORRECTION.search(spanish.folded(sentence)) is not None


def contacts_in(sentence: str, directory: tuple[Contact, ...]) -> tuple[Contact, ...]:
    """Everybody in the directory whose name this sentence names.

    Zero, one or several, and several is not a failure: "Marta" really does name two people, and
    returning both is what lets the assistant offer the choice instead of guessing. Banking refuses
    to choose for exactly the same reason.

    Matches on the whole name or on any single part of it, with word boundaries — so "Solis" finds
    Marta Solis Vega and "manana" does not find Ana. Accents and case are folded, because the
    directory spells "Solís" and nobody types it.

    **The most specific match wins**, measured as how much of a name the sentence gets right. That
    rule is what makes the ambiguity resolvable, and the first version did not have it: matching on
    any single part made "Marta Vega" match Marta Ruiz Gomez on "marta" and Marta Solis Vega on
    "marta" and "vega", so the sentence that *resolves* which Marta reported the same ambiguity
    again, forever. Three turns of the reported conversation are that loop.

    Two parts beat one, so "Marta Vega" is Marta Solis Vega; "Marta" alone still names both,
    because they tie on one and the tie is real. Order does not matter — what is counted is the
    parts the sentence gets right, not the order it puts them in.
    """
    haystack = spanish.folded(sentence)

    def parts_of(contact: Contact) -> list[str]:
        # The whole name counts as one part beyond the words, so "Marta Solis Vega" written in full
        # outscores any partial reading of it. The particles are skipped by name rather than by
        # length: "Ana" is three letters and a real first name, while "del" is three letters and
        # appears in every other Spanish sentence.
        words = [
            part
            for part in spanish.folded(contact.display_name).split()
            if len(part) > 2 and part not in _PARTICLES
        ]
        return [*words, spanish.folded(contact.display_name)]

    def score(contact: Contact) -> int:
        return sum(
            1
            for part in parts_of(contact)
            if re.search(rf"(?<![a-z0-9]){re.escape(part)}(?![a-z0-9])", haystack)
        )

    scored = [(score(contact), contact) for contact in directory]
    best = max((points for points, _ in scored), default=0)
    if best == 0:
        return ()
    return tuple(contact for points, contact in scored if points == best)


# Which rail a sentence names. A closed vocabulary of two, so it is read here rather than asked of
# a model: choosing between a Bizum and a transfer is choosing how money leaves an account, and it
# is decidable from the words. The payment prompt is not told about rails at all.
#
# Bizum is the default and says so. Every sentence this product has ever handled was one, the word
# is what people say when they mean it, and an unrecognised sentence becoming a transfer would send
# money down a rail nobody named.
_A_TRANSFER: Final = re.compile(
    r"\btransferencia|\btransferir\b|\btransfi[eé]re|\btransfi[ée]rele\b|\bpor\s+banco\b"
)


def rail_in(sentence: str) -> Rail:
    """The rail this sentence asks for. BIZUM unless it names a transfer."""
    return Rail.TRANSFER if _A_TRANSFER.search(spanish.folded(sentence)) else Rail.BIZUM
