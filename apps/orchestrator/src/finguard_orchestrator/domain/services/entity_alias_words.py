"""Explicit, typed names the user teaches for ledger counterparties."""

import re
from typing import Final

from finguard_orchestrator.domain.enums import CounterpartyType
from finguard_orchestrator.domain.services import spanish

_KINDS: Final[tuple[tuple[re.Pattern[str], CounterpartyType], ...]] = (
    (re.compile(r"(?:comercio|tienda|supermercado)\s+"), CounterpartyType.MERCHANT),
    (re.compile(r"(?:persona|contacto)\s+"), CounterpartyType.PERSON),
    (re.compile(r"(?:empresa|empleador)\s+"), CounterpartyType.EMPLOYER),
    (re.compile(r"(?:emisor|recibo|cobrador)\s+"), CounterpartyType.BILLER),
    (re.compile(r"(?:cuenta\s+destino|contracuenta)\s+"), CounterpartyType.ACCOUNT),
    (re.compile(r"banco\s+"), CounterpartyType.BANK),
)
_DEFINITIONS: Final = (
    re.compile(
        r"\bcuando\s+diga\s+(?P<alias>[a-z0-9][a-z0-9 -]{0,47}?)\s*,?\s*"
        r"(?:me\s+refiero\s+(?:al|a)|quiero\s+decir)\s+(?P<target>.+)$"
    ),
    re.compile(
        r"\b(?:llama|quiero\s+llamar)\s+(?P<alias>[a-z0-9][a-z0-9 -]{0,47}?)\s+"
        r"a\s+(?P<target>.+)$"
    ),
)
_NOISE: Final = re.compile(r"^(?:(?:el|la|al|a\s+la|a\s+el|mi)\s+)?")
_UNSAFE: Final = re.compile(
    r"\b(?:cuanto|saldo|gasto|ingreso|movimiento|bizum|transferencia|pago|cancelar|confirma)\b"
)


def alias_definition_in(sentence: str) -> tuple[str, str, CounterpartyType] | None:
    """Read only explicit definitions that state what kind of counterparty the target is."""
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:'\"")
    for definition in _DEFINITIONS:
        found = definition.search(folded)
        if found is None:
            continue
        alias = _NOISE.sub("", found.group("alias")).strip(" -'\"")
        raw_target = _NOISE.sub("", found.group("target")).strip(" -'\"")
        for kind_pattern, kind in _KINDS:
            typed = kind_pattern.match(raw_target)
            if typed is None:
                continue
            target = raw_target[typed.end() :].strip(" -'\"")
            if alias and target and len(alias.split()) <= 5 and not _UNSAFE.search(alias):
                return alias, target, kind
        return None
    return None


def learned_aliases_in(sentence: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """Known phrases present in a question, longest first and on word boundaries."""
    folded = spanish.folded(sentence)
    found: list[tuple[int, str]] = []
    for alias in sorted(aliases, key=len, reverse=True):
        match = re.search(
            rf"(?<![a-z0-9]){re.escape(spanish.folded(alias))}(?![a-z0-9])",
            folded,
        )
        if match is not None:
            found.append((match.start(), alias))
    return tuple(alias for _, alias in sorted(found))
