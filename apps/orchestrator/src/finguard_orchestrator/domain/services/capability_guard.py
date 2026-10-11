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
#
# The words for a movement, as somebody names one. Exported because two recognisers need the same
# list and a second copy is a second thing to forget: this one decides that a superlative is a
# ranking rather than a comparison, and `_SELF_CONTAINED_FINANCIAL_QUESTION` in `turn_arbiter` uses
# it to decide that a sentence naming one of these plus a window is a question and not an ellipsis.
#
# A fragment rather than a compiled pattern: no group, no anchor, no `\b`, so each caller keeps its
# own boundaries and its own surrounding context.
MOVEMENT_NOUNS: Final = (
    "gasto|compra|pago|movimiento|cargo|importe|factura|bizum|transferencia|ingreso|cobro|recibo"
)


_PER_MOVEMENT_AVERAGE: Final = re.compile(
    r"\b(importe|ticket)\s+medio\b"
    r"|\b(media|promedio)\s+(?:por|de\s+cada)\s+"
    r"(movimiento|operacion|compra|pago|cargo|ingreso|nomina)s?\b",
    re.IGNORECASE,
)
