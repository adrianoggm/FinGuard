"""Which shop the sentence was asking about, read from the answer it got.

Reported from the demo:

    "¿cuánto llevo gastado en spotify este año?"
    "421,79 € en suscripciones en lo que va de año, en 23 movimientos."
      · Netflix · Gimnasio Altafit · Spotify · Steam · Netflix …

Five times the real figure — Spotify alone is 76,93 € — and the evidence underneath visibly
contradicts the label. The plan filtered by the *category* the shop belongs to, which is the one
mistake the planning prompt warns about in as many words: "«¿cuánto gasté en Mercadona?» is the
merchant Mercadona and no category at all, because that category would also cover every other shop
in it." The model had the instruction and did it anyway.

**Nothing could catch it, and the reason is a missing symmetry.** Categories are enumerated in the
schema, glossed in the prompt, read by `category_words` and guarded by `plan_verifier`. Merchants
are a free string with no vocabulary and no rule, so `_category_misread` has nothing to compare —
its own docstring says a surplus category cannot be told from a word the vocabulary does not know.

The obvious mirror rule — *the sentence must justify the category* — was measured against the
planning dataset before being written, and it refuses 7 of 49 correct plans: "salir a comer", "fui
al súper", "he repostado", "de renta", "cenando fuera" and one misspelt "alqiler". 14 % of correct
answers turned into dead ends is not a control, so it was not built.

**What is built instead needs no vocabulary at all.** The answer already carries the merchants
behind the figure, so the question "did the sentence name one of these?" is answerable with what is
already in hand — no list to maintain, no second call, and no way to be wrong about a shop this
ledger does not have. It is the same evidence a reader uses to notice the answer is wrong; the
service just stopped being the last to see it.
"""

import difflib
from typing import Final

from finguard_orchestrator.domain.services import category_words, spanish

# Below this, a token is a coincidence rather than a name. "Bar" appears in "Bar Manolo" and in half
# the questions about eating out; "Dia" is a supermarket and a word. Four is what keeps "Manolo",
# "Cepsa" and "Spotify" while dropping "de", "bar" and "dia" — and a shop nobody can name in four
# letters is a shop this cannot help with, which is a smaller loss than a wrong figure.
_SHORTEST: Final = 4

# Words that appear inside merchant names and are not names. Without these, "¿cuánto gasté en el
# centro?" would claim to be about "Arrendamiento Centro", and "la tagliatella" is a restaurant
# whose category is also what somebody means by "restaurante".
_NOT_A_NAME: Final = frozenset(
    {
        "centro",
        "arrendamiento",
        "compra",
        "compras",
        "pago",
        "pagos",
        "cargo",
        "cargos",
        "tarjeta",
        "cuenta",
        "banco",
        "gasto",
        "gastos",
        "dinero",
        "euros",
        "mes",
        "meses",
        "año",
        "años",
        "semana",
        "este",
        "esta",
        "pasado",
        "pasada",
        "ultimo",
        "ultima",
        "total",
    }
)


def close_to(searched: str, merchants: tuple[str, ...], limit: int = 3) -> tuple[str, ...]:
    """The shops that look like what was asked for, best first.

    A merchant filter matches exactly, so a near miss is an empty answer — and the advice under it
    ("puedes buscar sin comercio o ampliar el periodo") is useless, because neither would ever
    help. Reported from the demo, three turns in a row:

        "cuanto he gastado en el spoti este año"   No he encontrado movimientos en spoti.
        "En el spotiy"                             No he encontrado movimientos en spotiy.
        "spotify !!"                               …

    Somebody typing the name of a shop they pay every month should not have to spell it the way
    the ledger does. The names are not ours to guess at, though, so nothing here invents one: the
    candidates are the merchants that actually appear in this account, fetched only on the turn
    that found nothing.

    `difflib` rather than a distance of our own. It is in the standard library, it is what
    everybody's spell-check uses, and a hand-rolled Levenshtein here would be a second thing to be
    wrong about.

    A prefix counts as well as a ratio, because abbreviation is not misspelling and the two look
    different to a sequence matcher: "spoti" is 0.83 against "spotify" and "merca" only 0.71
    against "mercadona", which is under any cutoff loose enough to be safe elsewhere.
    """
    if not searched.strip() or not merchants:
        return ()
    wanted = spanish.folded(searched).strip()
    by_folded = {spanish.folded(name): name for name in merchants}
    if wanted in by_folded:
        # The shop exists and was spelled correctly, so the emptiness came from something else —
        # the window, the direction, a category beside it. Suggesting it back would read as "no he
        # encontrado nada en Mercadona, ¿te refieres a Mercadona?", and the honest answer is the
        # ordinary empty one, which at least says which window it looked in.
        return ()
    starts = [name for folded, name in by_folded.items() if folded.startswith(wanted)]
    # 0.72 keeps "spotiy" and "mercadonna" and drops "lidl" against "aldi". Chosen against the
    # ledger's own 29 names rather than picked from the air, and the cost of it being wrong is one
    # extra suggestion the user ignores.
    like = difflib.get_close_matches(wanted, list(by_folded), n=limit, cutoff=0.72)
    found = dict.fromkeys([*starts, *(by_folded[folded] for folded in like)])
    return tuple(found)[:limit]


def almost_named_in(sentence: str, merchants: tuple[str, ...], limit: int = 3) -> tuple[str, ...]:
    """The shops this sentence nearly names, for a turn that answered the category instead.

    The exact reading above and the fuzzy one below the answer are the same question asked twice,
    and this is the half that was missing. "Cuanto he gastado en el spoti este año" was planned as
    the SUBSCRIPTIONS category, answered 421,79 €, and listed Spotify among the evidence — so the
    shop was right there and "spoti" matched none of its tokens.

    **It suggests and never narrows.** An exact mention is the user naming a shop, and answering
    about it with a note is honest. A near miss is a guess, and a guess that produces a figure is
    the one thing this service does not do — so this returns candidates for a question and the
    caller has to ask.
    """
    said = _tokens(sentence) - _NOT_A_NAME
    if not said or not merchants or category_words.named_in(sentence):
        return ()
    found: dict[str, None] = {}
    for word in sorted(said):
        for name in close_to(word, merchants, limit):
            found[name] = None
    return tuple(found)[:limit]


def _tokens(text: str) -> set[str]:
    folded = spanish.folded(text)
    return {
        word.strip(".,;:¿?¡!()")
        for word in folded.split()
        if len(word.strip(".,;:¿?¡!()")) >= _SHORTEST
    }


def named_in(sentence: str, merchants: tuple[str, ...]) -> str | None:
    """The merchant this sentence names, out of the ones the answer actually contains.

    A whole token has to match a whole token — substrings would make "¿cuánto gasté en el bar?"
    an answer about "Bar Manolo", and "el súper" an answer about whichever supermarket happened to
    come back first. Both are the failure this exists to stop, pointing the other way.

    A word the category vocabulary already explains is never a merchant here, however well it
    matches. "Gimnasio Altafit" is a merchant and "gimnasio" is how somebody says SUBSCRIPTIONS,
    so a question about the gym is about the category and this must not narrow it to one shop.

    Returns the merchant as the ledger spells it, because that is what a filter has to be given
    and what the user should read back.
    """
    if not merchants:
        return None
    said = _tokens(sentence) - _NOT_A_NAME
    if not said or category_words.named_in(sentence):
        return None
    for merchant in merchants:
        if _tokens(merchant) & said:
            return merchant
    return None
