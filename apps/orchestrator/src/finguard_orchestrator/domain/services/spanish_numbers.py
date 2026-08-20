"""Spanish cardinal amounts, parsed without asking a model to do arithmetic.

The vocabulary is intentionally finite and compositional.  A model may extract the same value,
but this reader is what proves that the value was actually present in the user's sentence before
it can reach a payment preview.
"""

import re
from typing import Final

from finguard_orchestrator.domain.constants import MAX_PAYMENT_MINOR
from finguard_orchestrator.domain.services import spanish

_SMALL: Final[dict[str, int]] = {
    "cero": 0,
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21,
    "veintiuno": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
_TENS: Final[dict[str, int]] = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
_HUNDREDS: Final[dict[str, int]] = {
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "doscientas": 200,
    "trescientos": 300,
    "trescientas": 300,
    "cuatrocientos": 400,
    "cuatrocientas": 400,
    "quinientos": 500,
    "quinientas": 500,
    "seiscientos": 600,
    "seiscientas": 600,
    "setecientos": 700,
    "setecientas": 700,
    "ochocientos": 800,
    "ochocientas": 800,
    "novecientos": 900,
    "novecientas": 900,
}
# Explicit aliases, not fuzzy matching. In a payment, silently turning an arbitrary near-match
# into a figure is more dangerous than asking again; aliases are admitted one by one from observed
# conversation failures.
_ALIASES: Final[dict[str, str]] = {"venti": "veinte"}
_WORDS: Final = frozenset({*_SMALL, *_TENS, *_HUNDREDS, *_ALIASES, "mil", "y"})
_WORD_PATTERN: Final = re.compile(
    rf"(?<![a-z0-9])(?:{'|'.join(sorted(_WORDS, key=len, reverse=True))})"
    rf"(?:\s+(?:{'|'.join(sorted(_WORDS, key=len, reverse=True))}))*(?![a-z0-9])"
)
_EUROS_AFTER: Final = re.compile(r"^\s*(?:(?:euros?|pavos?)\b|€)")
_CENTS_AFTER: Final = re.compile(rf"^\s*(?:y|con)\s+({_WORD_PATTERN.pattern})\s+c[eé]ntimos?\b")


def _under_hundred(tokens: list[str]) -> int | None:
    if len(tokens) == 1:
        return _SMALL.get(tokens[0]) or _TENS.get(tokens[0])
    if len(tokens) == 3 and tokens[0] in _TENS and tokens[1] == "y":
        unit = _SMALL.get(tokens[2])
        if unit is not None and 0 < unit < 10:
            return _TENS[tokens[0]] + unit
    return None


def _under_thousand(tokens: list[str]) -> int | None:
    if not tokens:
        return 0
    if tokens[0] in _HUNDREDS:
        rest = _under_hundred(tokens[1:]) if len(tokens) > 1 else 0
        return _HUNDREDS[tokens[0]] + rest if rest is not None else None
    return _under_hundred(tokens)


def integer(written: str) -> int | None:
    """Parse one complete Spanish cardinal phrase, or refuse the whole phrase."""
    tokens = [
        _ALIASES.get(token, token) for token in spanish.folded(written).replace("-", " ").split()
    ]
    if not tokens or any(token not in _WORDS for token in tokens):
        return None
    if tokens.count("mil") > 1:
        return None
    if "mil" not in tokens:
        return _under_thousand(tokens)
    at = tokens.index("mil")
    thousands = _under_thousand(tokens[:at]) if at else 1
    remainder = _under_thousand(tokens[at + 1 :])
    if thousands is None or remainder is None or not 0 < thousands < 1000:
        return None
    return thousands * 1000 + remainder


def money_in(sentence: str) -> int | None:
    """Return the first grounded written-out euro amount in minor units.

    A phrase touching ``euro``, ``pavo`` or the symbol wins over a bare number word. This avoids
    reading the article in «un bizum de veinte euros» as one euro. Bare cardinals remain useful as
    replies to an already-open amount slot; bare ``un/una`` do not, because they are articles in
    ordinary Spanish far more often than amounts.
    """
    folded = spanish.folded(sentence)
    if re.search(r"(?:euros?|pavos?|€)", folded) is None:
        cents_only = re.search(rf"({_WORD_PATTERN.pattern})\s+centimos?\b", folded)
        if cents_only is not None:
            cents = integer(cents_only.group(1))
            return cents if cents is not None and 0 < cents < 100 else None

    candidates: list[tuple[bool, int, re.Match[str], int]] = []
    for match in _WORD_PATTERN.finditer(folded):
        value = integer(match.group())
        if value is None:
            continue
        after = folded[match.end() :]
        has_currency = _EUROS_AFTER.search(after) is not None
        if not has_currency and match.group().strip() in {"un", "una"}:
            continue
        candidates.append((has_currency, match.start(), match, value))

    if not candidates:
        return None

    _, _, chosen, units = sorted(candidates, key=lambda item: (not item[0], item[1]))[0]
    cents = 0
    after = folded[chosen.end() :]
    currency = _EUROS_AFTER.search(after)
    if currency is not None:
        cents_match = _CENTS_AFTER.search(after[currency.end() :])
        if cents_match is not None:
            parsed = integer(cents_match.group(1))
            if parsed is None or not 0 <= parsed < 100:
                return None
            cents = parsed
    minor = units * 100 + cents
    return minor if 0 < minor <= MAX_PAYMENT_MINOR else None
