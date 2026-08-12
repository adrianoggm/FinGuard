"""Deterministic vocabulary for questions about money another person sent.

These sentences share the word ``bizum`` with an instruction to pay, but describe the opposite
direction. The payment router already prevents them from sending money; this module preserves the
other half of their meaning so a later period reply cannot inherit unrelated merchant filters.
"""

import re
from typing import Final

from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import payment_words, spanish

_INCOMING: Final = re.compile(
    r"\b(?:bizum|transferencia)\b.{0,55}\b(?:recibid|llegad|mandad|enviad|hech)\w*\b"
    r"|\b(?:recib|lleg|mand|envi|hic|hiz)\w*\b.{0,55}\b(?:bizum|transferencia)\b"
    r"|\bme\s+(?:ha\s+|han\s+|habia\s+|habian\s+)?"
    r"(?:hecho|hizo|hicieron|mandado|mando|mandaron|enviado|envio|enviaron|llegado|llego)\b"
    r".{0,35}\b(?:bizum|transferencia)\b"
)

_BEFORE_ME: Final = re.compile(
    r"\b(?P<name>[a-z][a-z'-]{1,39}(?:\s+[a-z][a-z'-]{1,39}){0,2})\s+me\s+"
    r"(?:ha\s+|habia\s+)?(?:hecho|hizo|mandado|mando|enviado|envio)\b"
)
_AFTER_FROM: Final = re.compile(
    r"\b(?:bizum|transferencia)\s+de\s+"
    r"(?P<name>[a-z][a-z'-]{1,39}(?:\s+[a-z][a-z'-]{1,39}){0,2})"
    r"(?=\s+(?:este|esta|el|la|en|durante|hoy|ayer|recientemente|ultim)|$)"
)


def is_incoming(sentence: str) -> bool:
    """Whether the sentence asks about a Bizum or transfer arriving from somebody."""
    return _INCOMING.search(spanish.folded(sentence)) is not None


def counterparty_in(sentence: str, directory: tuple[Contact, ...] = ()) -> str | None:
    """The grounded spoken name, preferring the holder's contact vocabulary when possible."""
    if not is_incoming(sentence):
        return None
    folded = spanish.folded(sentence)
    grounded = payment_words.name_as_written(sentence, directory)
    if grounded is not None:
        return grounded

    for pattern in (_BEFORE_ME, _AFTER_FROM):
        match = pattern.search(folded)
        if match is not None:
            words = match.group("name").strip().split()
            if "si" in words:
                words = words[words.index("si") + 1 :]
            while words and words[0] in {"si", "que", "oye", "pues"}:
                words.pop(0)
            return " ".join(words) or None
    return None
