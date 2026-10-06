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
import re
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


# N-H1 (Wave 3 hardening, round 2): the words F-2 added above, and *only* those. Most of
# `_NEVER_A_MERCHANT_WORDS` are conversational control -- "sí", "no", "gracias", "vale", "déjalo" --
# and each already has its own deliberate fallback when nothing is pending (a cancelled workflow's
# stray "sí" becomes an ordinary LAST_MONTH query; the corpus pins exactly that in
# `07-cancellations.yaml` and `08-confirmations.yaml`). "Todos"/"todas"/"todo"/"límite" and "sin
# límite" carry no such second job: nobody types them to close a workflow, so there is nothing lost
# in asking instead of guessing, in *every* state -- not only while a plan is pending, which is what
# `never_a_merchant` alone reaches (via `_a_bare_shop_name`, gated on `was.last_plan`).
_NAMES_NO_SCOPE_AT_ALL_WORDS: Final = frozenset({"todos", "todas", "todo", "limite"})
_NAMES_NO_SCOPE_AT_ALL_PHRASES: Final = frozenset({"sin limite"})


def names_no_scope_at_all(sentence: str) -> bool:
    """Whether `sentence` folds to exactly one of F-2's own words -- never a shop, never a window,
    never a direction, and never anything else's answer either.

    Narrower than `never_a_merchant` on purpose: that function's word list also covers "sí", "no"
    and the rest of conversation control, and those must keep falling through to whatever state was
    waiting on them (a cancelled payment, a repeated clarification) rather than being asked about
    here unconditionally. This is the subset safe to gate the planner on from *any* state.
    """
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    if not folded:
        return False
    if folded in _NAMES_NO_SCOPE_AT_ALL_PHRASES:
        return True
    words = folded.split()
    return len(words) == 1 and words[0] in _NAMES_NO_SCOPE_AT_ALL_WORDS


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

    The words the category vocabulary claims are dropped rather than the sentence, for the reason
    `named_in` gives above and one of its own: a fuzzy reader let loose on «suscripciones» would
    go looking for a shop whose name is one letter from a word the holder used to mean the
    category, and that guess is worse than the silence it replaces.
    """
    said = _shop_words(sentence)
    if not said or not merchants:
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
    matches. "Gimnasio Altafit" is a merchant and "gimnasio" is a kind of subscription, and this
    returns one shop -- the first that matches -- where the holder may pay two gyms. The gym is
    `called` below instead, which answers with every shop the word names.

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


def could_name_a_shop(sentence: str) -> bool:
    """Whether the sentence says any word `named_in` could match a shop's name by.

    For a caller about to spend a query on the list of shops to match against: «¿suscripciones?»
    says nothing but a category, and no list could find a shop in it.
    """
    return bool(_shop_words(sentence))


def _shop_words(sentence: str) -> set[str]:
    """The sentence's words a shop's name could carry: long enough, not a word that is never a
    name, and not a word the category vocabulary already explains."""
    return _tokens(sentence) - _NOT_A_NAME - category_words.words_named_in(sentence)


def called(words: tuple[str, ...], merchants: tuple[str, ...]) -> tuple[str, ...]:
    """Every shop whose name carries one of these words, in the order the ledger gave them.

    For a kind of thing a category holds (`category_words.kinds_named_in`): «gimnasio» is "Gimnasio
    Altafit" *and* "Gimnasio Municipal", and an answer about one of them would be the old defect
    pointing the other way -- a true figure for part of what was asked.

    A whole word, folded the way the vocabulary folds, or that word's Spanish plural -- and nothing
    looser. Shops name themselves in the plural as often as not: whole words alone answered «¿cuánto
    gasté en el cine?» with every leisure shop and a note that none was called «cine», above "Cines
    Yelmo" in the evidence. "Gimnasiolandia" is still not called «gimnasio», nor "Bar Manolo"
    «bares»; a shop this misses keeps the category's figure, which is the answer the holder had
    before, while a shop it wrongly claimed would be a real figure about a shop nobody named.
    """
    wanted = [
        re.compile(rf"(?<![a-z0-9]){re.escape(spanish.folded(word))}(?:e?s)?(?![a-z0-9])")
        for word in words
    ]
    return tuple(
        merchant
        for merchant in dict.fromkeys(merchants)
        if any(pattern.search(spanish.folded(merchant)) for pattern in wanted)
    )
