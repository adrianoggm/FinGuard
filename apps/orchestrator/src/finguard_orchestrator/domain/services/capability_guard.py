"""What the domain cannot express, recognised before a model is asked.

A domain service for the same reason answer_writer is one: pure, no framework, and it
speaks about what this domain can and cannot represent.

The point is certainty, not coverage. Refusing what we cannot express was a disposition
of whichever model was configured — it moved from 0 % to 60 % held out by swapping model
— and a disposition is not a control. What follows is a control: it recognises a closed
list of capabilities we know we do not have, which is a far smaller job than reading the
sentence and the only one that can be done with certainty.

Two consequences of that framing are deliberate.

**A false refusal costs more than a miss.** Anything this returns never reaches the
model, so a rule that fires on a question the system *can* answer converts working
behaviour into a dead end. LARGEST_MOVEMENT lived here until the capability existed, and
removing it is what that change looks like from this side: a family here is a debt, not a
feature. Every family below is therefore narrower than the plain
reading of its name — the discriminating case is "más", which means a comparison in
"¿gasté más este mes que el anterior?" and a ranking in "¿dónde gasto más?", and only the
first is beyond us.

**It does not try to recognise "not about money".** That needs an allow-list of financial
vocabulary, and follow-ups like "¿y este mes?" contain none of it, so the allow-list
would refuse the conversation's most ordinary turn. It is also the cheap half of the
problem: "¿cuál es la capital de Francia?" answered with a spending figure is absurd on
sight, while "¿qué parte de mi sueldo se va en ocio?" answered with a real total is
believed. This guards the second kind.

The families are Spanish because the questions are — the same exception rule 14 makes for
product data.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy

# Each entry is a capability the domain has no representation for. The name travels with
# the refusal so the answer can say which one, rather than "no puedo con eso".
_FAMILIES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "AVERAGE",
        re.compile(r"\b(medio|media|promedio)\b", re.IGNORECASE),
    ),
    (
        "RATIO",
        re.compile(r"(\bporcentaje\b|\bque parte de\b|\bque proporcion\b|%)", re.IGNORECASE),
    ),
    (
        # "más" alone is not a comparison: "¿dónde gasto más?" asks which group is
        # largest inside one window, which groupBy expresses. What the domain cannot do
        # is relate two windows, and in Spanish that needs the second term — "más X *que*
        # Y". Comparing without "que" is possible but it is phrased with these words.
        #
        # `_A_SUPERLATIVE_NOUN` below is the exception, and it used to be a family of its
        # own: "el pago más grande *que* he hecho este año" carries "más … que" with a
        # relative pronoun rather than a second term. That knowledge was here to *refuse*
        # the sentence; now that rankings are answered it keeps this rule off one.
        "COMPARISON",
        re.compile(r"(\bmas\b.*\bque\b|\bcomparad|\bcomparacion\b|\bfrente a\b)", re.IGNORECASE),
    ),
    (
        # Only what already happened is in the ledger.
        "FORECAST",
        re.compile(
            r"(\bvoy a\b|\bllegare\b|\ba este ritmo\b|\bprevision\b|\bpreveo\b"
            r"|\bacabare\b|\bterminare\b)",
            re.IGNORECASE,
        ),
    ),
    (
        # Reading a ledger is not advising on it, and the distance between the two is
        # regulatory rather than technical.
        "ADVICE",
        re.compile(
            r"(\bdeberia\b|\bpodria ahorrar\b|\brecortar\b|\baconsej|\basesor"
            r"|\bque hacer con\b|\bme recomiendas\b|\blimite de gasto\b|\bpresupuesto\b)",
            re.IGNORECASE,
        ),
    ),
    (
        # A request to act, not to read. Phase 5 adds payments behind a confirmation
        # step; until it does, and after, this path only answers questions.
        "ACTION",
        re.compile(
            r"\b(hazle|haz un|hazme|envia|manda|mandale|transfiere|paga|cambiame|cambia)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Movements between the holder's own accounts, on their own. A plan cannot express it:
        # `include_internal` *widens* a query to take them in, and nothing asks for only them
        # because banking books an internal movement with no category to filter on.
        #
        # Refused rather than approximated, and this family has the clearest reason of any of them.
        # Asked "¿qué movimientos he tenido entre mis cuentas?" the assistant answered 14.019,05 €
        # over 194 movements — every movement the holder made that year. Correct arithmetic about a
        # different question, which is the one answer nothing downstream can contradict.
        #
        # The balance is routed before the guard, so "¿cuánto tengo entre mis cuentas?" is still a
        # balance. That sentence asks about a state and has a real answer.
        "INTERNAL_ONLY",
        re.compile(
            r"\bentre\s+(mis|tus|sus|las)\s+cuentas\b"
            r"|\bentre\s+cuentas\b"
            r"|\btraspasos?\s+entre\b",
            re.IGNORECASE,
        ),
    ),
)


# A sentence that is only a continuation: an ordinal, or a pronoun standing in for whatever the
# last turn was about. Anchored at both ends, so anything with content of its own falls through.
_ONLY_A_CONTINUATION: Final = re.compile(
    r"y\s+(?:el|la|los|las)?\s*"
    r"(?P<word>segundo|segunda|tercero|tercera|tercer|cuarto|cuarta|quinto|quinta"
    r"|sexto|sexta|septimo|septima|octavo|octava|noveno|novena|decimo|decima"
    r"|siguiente|siguientes|otro|otra|otros|otras|resto|demas)",
    re.IGNORECASE,
)

# Which position each word names. "El siguiente" and "otro" name no position at all — they mean
# one more than wherever we are — and that difference is the whole point of this table: reading
# "el tercero" as "one more than the first" answered "¿y el tercero?" with the second.
_POSITION: Final[dict[str, int]] = {
    "segundo": 2,
    "segunda": 2,
    "tercero": 3,
    "tercera": 3,
    "tercer": 3,
    "cuarto": 4,
    "cuarta": 4,
    "quinto": 5,
    "quinta": 5,
    "sexto": 6,
    "sexta": 6,
    "septimo": 7,
    "septima": 7,
    "octavo": 8,
    "octava": 8,
    "noveno": 9,
    "novena": 9,
    "decimo": 10,
    "decima": 10,
}


@dataclass(frozen=True)
class OrdinalReference:
    """Which row of a ranking a continuation asks for.

    Two modes rather than a number, because "el siguiente" genuinely has no number: it is one more
    than whatever was just answered, and resolving it needs the previous rank. `ABSOLUTE` carries
    the position the sentence stated and needs nothing else.

    This used to be a bare boolean and the caller always advanced by one, so "¿y el tercero?" after
    the largest expense answered with the second. The recogniser knew an ordinal was present and
    threw away which one.
    """

    mode: Literal["ABSOLUTE", "NEXT"]
    rank: int | None = None


# The names, for whoever needs to enumerate them: an ablation loop, a configuration file,
# and the policy that validates its own input.
RULES: Final = frozenset(name for name, _ in _FAMILIES)


# A superlative attached to a noun: one movement, not two windows compared. "El gasto más alto",
# "la compra más cara", "el pago de mayor importe".
#
# This was a refusal family until RANK_MOVEMENTS existed. It stays because COMPARISON would
# otherwise claim these sentences — they carry "más … que" where the "que" opens a relative
# clause — and a question with a real answer would come back as "no comparo dos periodos".
#
# The noun list is not decoration. It held only the generic words for money leaving an account —
# gasto, compra, pago, movimiento, cargo, importe, factura — so "¿cuál es el Bizum más alto que he
# realizado?" carried "más … que", matched nothing here, and came back as "todavía no comparo dos
# periodos" to a question containing one period. A ranking subject is whatever kind of movement the
# holder names, so the transfer words belong here as much as "compra" does.
_A_SUPERLATIVE_NOUN: Final = re.compile(
    r"\b(mayor|maxim[oa])\s+"
    r"(gasto|compra|pago|movimiento|cargo|importe|cantidad|bizum|transferencia|ingreso|cobro|recibo)"
    r"|\b(gasto|compra|pago|movimiento|cargo|importe|factura"
    r"|bizum|transferencia|ingreso|cobro|recibo)s?\s+"
    r"(mas|de\s+mayor)\s+(alt[oa]|car[oa]|grande|elevad[oa]|importe)",
    re.IGNORECASE,
)


def ordinal_in(sentence: str) -> OrdinalReference | None:
    """Which row of a ranking this sentence asks for, when it asks for nothing else.

    Written when "¿y el segundo?" could only be met with a repeated refusal — the capability did not
    exist, and the planner read the sentence as a follow-up missing a window, which started a
    clarification about a period that could never lead anywhere. Now it asks for a row of a ranking,
    which is the same reading with an answer behind it.

    **Narrow on purpose.** Only an ordinal or a bare pronoun with no content of its own. A sentence
    carrying its own question is a new question, and reading "¿cuánto gasté?" as a continuation
    would answer it with the second largest expense.
    """
    found = _ONLY_A_CONTINUATION.fullmatch(spanish.folded(sentence).strip(" ?¿.!¡"))
    if found is None:
        return None
    position = _POSITION.get(found.group("word"))
    if position is None:
        return OrdinalReference(mode="NEXT")
    return OrdinalReference(mode="ABSOLUTE", rank=position)


def unsupported_reason(sentence: str, policy: GuardPolicy = EVERY_RULE) -> str | None:
    """The capability the sentence needs and the domain does not have, or None.

    None means "no known-missing capability was named", not "answerable". The model still
    decides that, and everything after it still checks.

    `policy` exists so a rule can be switched off and the difference measured. It defaults
    to everything on, because a guard that is off by accident is worse than one that was
    never written — it reads like a control.
    """
    # Folded once, and every pattern above is written unaccented. Nobody types accents into a
    # chat box: the verifier's count rule needed one and therefore never fired on the sentence
    # that produced the worst answer in the reported conversation.
    folded = spanish.folded(sentence)
    ranking = bool(_A_SUPERLATIVE_NOUN.search(folded))
    for name, pattern in _FAMILIES:
        if not policy.allows(name):
            continue
        if ranking and name == "COMPARISON":
            # It is asking for one movement, not relating two windows. See the note on the family.
            continue
        if pattern.search(folded):
            return name
    return None
