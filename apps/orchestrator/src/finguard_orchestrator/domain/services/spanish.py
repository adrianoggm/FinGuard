"""Reading Spanish the way people type it.

Nobody types accents into a chat box, and a rule that needs them is a rule that does not fire.
From the reported conversation:

    "De esos gastos cuantos fueron en el supermercado ?"
    → "449,62 € el mes pasado, en 8 movimientos."

`COUNT_ANSWERED_AS_AMOUNT` exists to refuse exactly that, and it did not fire: its pattern was
`\\bcuánt[oa]s\\b` and the user wrote "cuantos". The answer was a total presented as an answer to
"how many" — a real number about a different question.

The capability guard's families survived only because whoever wrote them happened to spell the
alternatives out as `[áa]`. Relying on remembering that in every future pattern is a habit, not a
rule. So the sentence is folded once here and the patterns are written unaccented.

This existed three times already — in the category vocabulary, in the window vocabulary, and
privately in each — and neither of the two rule sets that most needed it had a copy.
"""

import unicodedata


def folded(text: str) -> str:
    """Lower case, no accents, ñ as n.

    `ñ → n` is deliberate and load-bearing for the window vocabulary: "este ano" is what somebody
    typing quickly writes, and it has to match "este año". The cost is that a word distinguished
    only by the tilde would collide, which in this vocabulary is nothing — there is no pair of
    categories, periods or rules where "n" and "ñ" mean different things.
    """
    stripped = unicodedata.normalize("NFD", text.casefold())
    return "".join(character for character in stripped if not unicodedata.combining(character))
