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
#
# A length of time is the one this had never been asked about. "En los últimos 4 meses" came back
# as four euros, found by a measurement that consulted this reader outside the slot it is normally
# gated behind — so it costs nothing today and says something worth fixing anyway: what the reader
# answers about is digits, and a digit in front of "meses" is a period.
#
# Adjacent only, and that is the whole care in it. Allowing a word between would swallow "le paso
# 100 al mes", which is an amount and a common way to say one.
_NOT_AN_AMOUNT: Final = re.compile(
    r"\+?\d[\d\s]{7,}"  # a phone number, with or without spaces
    r"|\bel\s+\d{1,2}\s+de\s+\w+"  # "el 15 de julio"
    r"|\b\d{1,2}/\d{1,2}"  # a date written with slashes
    r"|\b\d{1,2}:\d{2}"  # a time
    r"|\b\d{1,3}\s+(?:d[ií]as?|semanas?|mes|meses|a[nñ]os?|trimestres?|quincenas?)\b",
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

# Natural replacement after a preview: "A Marta 500 euros". It is deliberately paired with an
# explicit amount in `changes_recipient`; without that condition, the conversational filler
# "a ver" would be mistaken for a recipient correction and discard a valid preview.
_BARE_RECIPIENT_CORRECTION: Final = re.compile(r"^\s*a\s+\S", re.IGNORECASE)

_CLEAR_CONCEPT: Final = re.compile(
    r"\b(?:quita|quitar|elimina|eliminar|borra|borrar)\s+(?:el\s+)?concepto\b"
    r"|\b(?:sin|ningun)\s+concepto\b",
    re.IGNORECASE,
)

# Words that appear inside Spanish names and inside every Spanish sentence. Matching a contact on
# one of these would make "no sé qué hacer" name María del Carmen.
_PARTICLES: Final = frozenset({"del", "las", "los", "san", "santa", "von", "van"})


# Words a Spanish sentence ends with that are not part of anybody's name. Reported: "hazle un bixum
# a aitor anda" was read as a payment to *"aitor anda"*, the question came back "¿de cuánto quieres
# hacerle el bixum a aitor anda?", and the contact lookup then found nobody — two turns lost to a
# word that means "go on".
#
# Stripped rather than tolerated in the lookup, because the name is echoed back at the holder. A
# question naming somebody who does not exist reads as the assistant having misheard, which is
# exactly what happened.
_TRAILING_FILLER: Final = re.compile(
    r"[\s,]+(?:anda|venga|va|vale|porfa|por\s+favor|ya|hombre|mujer|tio|tia"
    r"|gracias|pues|eh|oye|please)\s*$",
    re.IGNORECASE,
)


# The words that name a way of moving money. A closed set of three, and the only closed set in this
# file that people cannot spell — "bizum" is a brand most holders have never had to type. It arrived
# as `bixum`, `bisum` and `vizum` in three separate reported conversations, and each miss made the
# whole payment path silently unreachable.
#
# Matched one edit apart rather than enumerated. `bi[xz]um` was the enumeration, and it was already
# wrong twice: an open set cannot be listed, and every unlisted spelling costs a whole conversation.
_RAIL_LEXICON: Final = ("bizum", "transferencia")
_WORD: Final = re.compile(r"[a-z]+")


def names_a_rail(sentence: str) -> bool:
    """Whether the sentence names a way of moving money, however it was spelled."""
    return any(
        spanish.one_edit_apart(token, rail)
        for token in _WORD.findall(spanish.folded(sentence))
        for rail in _RAIL_LEXICON
    )


# What a whole turn may consist of and still be an answer rather than an order: the rail noun, and
# at most an article or a discourse particle around it.
_ONLY_A_PARTICLE: Final = frozenset(
    {"pues", "y", "o", "un", "una", "el", "la", "lo", "de", "vale", "ok", "eso", "si"}
)


def is_only_a_rail_word(sentence: str) -> bool:
    """Whether the whole turn is the name of a rail and nothing else.

    The inversion this rule needed. "Pues bizum", offered as the answer to "¿de qué comercio o
    categoría?", must belong to that question — and the previous test for it was to enumerate the
    ways of *instructing*, which is an open set: every form missed ("que le hagas", "hagamos")
    became a turn the open question swallowed, and one holder spent seven of them shouting.

    So this enumerates the other side, which is closed and tiny. A turn that is a rail noun with an
    article or a particle around it is a reply; a turn with anything else in it is not. The set of
    Spanish ways to say "do it" is unbounded; the set of ways to say only "bizum" is not.
    """
    tokens = _WORD.findall(spanish.folded(sentence))
    if not tokens:
        return False
    named = [
        token
        for token in tokens
        if any(spanish.one_edit_apart(token, rail) for rail in _RAIL_LEXICON)
    ]
    if not named:
        return False
    return all(token in _ONLY_A_PARTICLE for token in tokens if token not in named)


def without_trailing_filler(name: str) -> str:
    """A read name with the conversational tail taken off.

    Repeated, because "a Aitor anda por favor" has two of them. Never down to nothing: if every word
    is in the list then the whole thing was filler and no name was read, and returning the original
    lets the layer above refuse it as the non-name it is rather than crash on an empty string.
    """
    trimmed = name.strip()
    while True:
        shorter = _TRAILING_FILLER.sub("", trimmed)
        if shorter == trimmed:
            return trimmed or name.strip()
        if not shorter.strip():
            return trimmed
        trimmed = shorter.strip()


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


def changes_recipient(sentence: str, *, previewed: str) -> bool:
    """Whether a reply to a preview explicitly replaces who receives the payment.

    `previewed` is the recipient on the preview being answered. The correction forms above need no
    name to compare with — they say outright that the person changes — but an order does:
    «mándale 20 € a Marta» to Marta's preview revises the amount, and «mándale 20 € a Javi Nuñez»
    is a payment to somebody else. Read as a revision, the second one became «Vas a enviar 20,00 €
    a Marta Solis Vega», and a «sí» would have paid Marta for an order that named Javi.
    """
    folded = spanish.folded(sentence)
    return (
        _RECIPIENT_CORRECTION.search(folded) is not None
        or (
            _BARE_RECIPIENT_CORRECTION.search(folded) is not None
            and amount_in(sentence) is not None
        )
        or _orders_somebody_else(sentence, previewed)
    )


# An order with somebody to give it to: the imperative («mándale», «envía», «hazle», «dale»), the
# infinitive after a volitional verb («quiero mandarle») and the subjunctive («que le mandes»). The
# person is the «a <name>» that follows; the verb alone names nobody, so «mándale 20 €» to an open
# preview stays the revision it always was.
_ORDER_TO_SOMEBODY: Final = re.compile(
    r"\b(?:(?:manda|envia|paga|pasa|transfiere|ingresa|devuelve)(?:le|les)?"
    r"|(?:da|haz)(?:le|les)"
    r"|(?:mandar|enviar|pagar|pasar|dar|hacer|transferir|devolver)(?:le|les)"
    r"|mandes|envies|pagues|pases|hagas|transfieras|devuelvas)\b"
)
_TO_SOMEBODY: Final = re.compile(r"\ba\s+(?P<name>[a-z]+(?:\s+[a-z]+)*)")

# Where the words after «a» stop being a name: what an order says next («por la cena», «con
# concepto…», «hoy»), the fillers `_TRAILING_FILLER` strips, and the pronouns that point back at
# the preview's own person («a ella»). A first word nobody listed here — «mi madre» — counts as a
# different person: the cost of that mistake is a question about who, and the cost of the other one
# is money to the person the holder just named away from. What comes after the first word is not
# decided here at all (ruling R18): see `_orders_somebody_else`.
_AFTER_THE_NAME: Final = frozenset(
    {
        "por",
        "con",
        "de",
        "para",
        "que",
        "y",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "al",
        "en",
        "hoy",
        "manana",
        "ahora",
        "ya",
        "concepto",
        "anda",
        "venga",
        "va",
        "vale",
        "porfa",
        "gracias",
        "pues",
        "eh",
        "oye",
        "ella",
        "ellos",
        "ellas",
    }
)


def _orders_somebody_else(sentence: str, previewed: str) -> bool:
    """Whether `sentence` orders a payment to a person whose name is not the preview's.

    **The first word of the name decides** (ruling R18). «Javi» is not a word of Marta Solis Vega's
    name, so «paga 20 € a Javi» is somebody else; «Marta» is, so «mándale 20 € a Marta esta noche»
    is her — what follows a name is not part of it, and reading «esta noche» as a surname cancelled
    Marta's own preview for being named. Deciding by rule rather than by listing every word that
    can follow a name: that list is the open set, and it was already missing «cuando puedas».

    One thing after the first word still counts, because it is written as a name: a capitalised
    word the preview's name does not have. «Marta Ruiz» is the other Marta, and the first word alone
    would read her as the preview's — a revision addressed to the wrong person, the failure R14
    exists to stop. Lower case is read by the first word only, so «marta ruiz» is that failure
    still; the cost is a preview naming Marta Solis Vega that the holder has to decline.

    The words are compared folded, and the casing is read off the sentence as typed, at the same
    positions: folding keeps one character per character for Spanish. When it does not, the
    casing is not read and the first word decides alone.
    """
    folded = spanish.folded(sentence)
    order = _ORDER_TO_SOMEBODY.search(folded)
    if order is None:
        return False
    target = _TO_SOMEBODY.search(folded, order.end())
    if target is None:
        return False
    name: list[re.Match[str]] = []
    for word in _WORD.finditer(folded, target.start("name"), target.end("name")):
        if word.group() in _AFTER_THE_NAME:
            break
        if word.group() not in _PARTICLES:
            name.append(word)
    if not name:
        return False
    previewed_name = set(spanish.folded(previewed).split())
    first, *after = name
    if first.group() not in previewed_name:
        return True
    aligned = len(folded) == len(sentence)
    for word in after:
        if not (aligned and sentence[word.start()].isupper()):
            # Not written as a name, so not part of one: the rest of the sentence.
            break
        if word.group() not in previewed_name:
            return True
    return False


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


def name_as_written(sentence: str, directory: tuple[Contact, ...]) -> str | None:
    """How much of a matched contact's name this sentence spells, in the directory's own casing.

    The ledger stores what the bank wrote on the movement, and the holder types a fraction of it.
    Filtering by the full display name would find nothing for somebody who wrote "Marta", and
    filtering by an invented choice between two contacts called Marta would find the wrong person —
    so what comes back is exactly the part they wrote, which is the widest honest filter.

    None when nothing in the directory is named, which is the answer that keeps a reader from
    building a query about a person the sentence never mentioned.

    Lives here rather than beside either reader that asks it. Both directions of the same question
    need it — "¿me ha llegado un bizum de Marta?" and "le he mandado 20 € a Marta Solis Vega" — and
    the second copy of a rule like this is the one that drifts.
    """
    folded = spanish.folded(sentence)
    mentioned: list[str] = []
    for contact in contacts_in(sentence, directory):
        for original in contact.display_name.split():
            word = spanish.folded(original)
            named = re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", folded)
            if len(word) > 2 and named:
                if word not in {item.casefold() for item in mentioned}:
                    mentioned.append(original)
    return " ".join(mentioned) or None


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
