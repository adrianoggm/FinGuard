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


# B-2(a) (Wave 3 hardening, R-H1): words a bare-word turn says that are never a shop's name — a
# discard, a correction, a control word, a pleasantry, a bare intensifier. `nada` is the audit's own
# example: "¿cuánto gasté el mes pasado?" answered `merchants: ['nada']`, three ledger calls, a real
# total for a word that named nothing. The 10 pinned corpus turns Task 3 found (six episode files —
# `13-messy.yaml`'s "quería"/"es que"/"pues eso"/"nada"; `02-corrections.yaml`'s "ahora sí";
# `05-typos.yaml`'s "gracais"; `06-colloquial.yaml`'s "ah vale"; `07-cancellations.yaml`'s "cancelar
# consulta" ×2/"cambiar consulta"/"déjalo"; `08-confirmations.yaml`'s "confirmo") decompose into
# these tokens once folded — no phrase beyond the four idioms below needs its own entry.
#
# A closed list on purpose, not a heuristic: Task 3's general rule (require a capitalised,
# merchant-shaped token) fixed the same bug but reopened all ten pinned turns as an open-ended
# judgement call. This list only ever grows by a name proven, the same way this file's own merchant
# vocabulary does — never by a pattern that could also match a shop nobody has named yet.
_NEVER_A_MERCHANT_PHRASES: Final = frozenset(
    {
        "otra vez",
        "en euros",
        "es que",
        "pues eso",
        # F-2 (fix wave, Important 3): C-3's own named words -- folded, so "sin límite" hits.
        "sin limite",
        # A reset, like «olvídalo» below. `turn_arbiter` answers the whole-turn reset before any
        # reader looks; this keeps the bare-shop guess from ever being the one that reads it.
        "no importa",
    }
)

_NEVER_A_MERCHANT_WORDS: Final = frozenset(
    {
        "deshaz",
        "vuelve",
        "nada",
        "vale",
        "ok",
        "si",
        "no",
        "gracias",
        "gracais",  # the typo the corpus itself types (05-typos.yaml)
        "mas",
        "menos",
        "atras",
        "cancela",
        "cancelar",
        "consulta",
        "cambiar",
        "repite",
        "eso",
        "esto",
        "ahi",
        "ya",
        "ah",
        "ahora",
        "bien",
        "hecho",
        "listo",
        "queria",
        "dejalo",
        # Measured 2026-10-07: «olvidalo» after an answer ran the last question again with
        # `merchants: ['olvidalo']`, the defect «nada» and «déjalo» were added here for. One entry
        # covers «olvídalo» too: the sentence is folded before it is looked up, so an accented
        # spelling in this set could never match anything.
        "olvidalo",
        "confirmo",
        # F-2 (fix wave, Important 3): C-3's own named words -- a bare "todos"/"todas"/"todo" or
        # "límite" ran the merchant guess (three ledger calls, a confident total) for a word that
        # named nothing, the exact defect B-2(a)'s stop-list exists to stop.
        "todos",
        "todas",
        "todo",
        "limite",
    }
)


def never_a_merchant(sentence: str) -> bool:
    """Whether `sentence` is nothing but words this service already knows are not a shop's name.

    Folded and stripped first, then matched whole against the four multi-word idioms
    (`_NEVER_A_MERCHANT_PHRASES`) before falling back to a per-word check against
    `_NEVER_A_MERCHANT_WORDS` — so "ahora sí" reads as "ahora" + "sí" (both single words) while
    "es que" and "pues eso" stay the two-word units they are, neither half meaning anything alone.

    A sentence with even one word this does not recognise is not this — the caller falls through to
    the ordinary bare-name guess, exactly as before this rule existed.
    """
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    if not folded:
        return False
    if folded in _NEVER_A_MERCHANT_PHRASES:
        return True
    words = folded.split()
    return bool(words) and all(word in _NEVER_A_MERCHANT_WORDS for word in words)


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
    matches. "Gimnasio Altafit" is a merchant and "gimnasio" is a kind of subscription, and this
    returns one shop -- the first that matches -- where the holder may pay two gyms. The gym is a
    merchant term on the plan instead (`explicit_facts.of_its_kind`), which the bank answers with
    every shop whose name contains the word.

    **Those words are dropped, not the sentence.** Until 2026-09-14 a sentence that named any
    category at all was refused here outright, which is a different and much wider rule: it also
    gave up on «¿cuánto he gastado en suscripciones de Netflix este mes?», where the shop is named
    in words the category vocabulary does not claim. The plan that answered SUBSCRIPTIONS entire
    passed the verifier -- the category the sentence named really is there -- so this was the only
    reader left that could notice, and it had switched itself off. What came back was a true
    figure for a wider question than the one asked, which is the failure this whole path exists to
    stop. Reported by review; the gym above keeps working because «gimnasio» is still dropped.

    Returns the merchant as the ledger spells it, because that is what a filter has to be given
    and what the user should read back.
    """
    if not merchants:
        return None
    said = _shop_words(sentence)
    if not said:
        return None
    for merchant in merchants:
        if _tokens(merchant) & said:
            return merchant
    return None


def _shop_words(sentence: str) -> set[str]:
    """The sentence's words a shop's name could carry: long enough, not a word that is never a
    name, and not a word the category vocabulary already explains."""
    return _tokens(sentence) - _NOT_A_NAME - category_words.words_named_in(sentence)
