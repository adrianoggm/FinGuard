"""The Spanish `explicit_facts` knows, as a table somebody can read and a script can count.

Every defect fixed on 2026-08-29 was one module knowing a phrase another did not: `_EXPENSE` and
`_INCOME` matched the same words and nobody knew; `_CUMULATIVE` knew «acumulado» and not «acumulo».
None of those is a hard bug to fix. All of them are impossible to *find*, because the question "what
does this system know how to say?" had no answer short of reading nine files of regex.

So the vocabulary moves here and the structure stays in `explicit_facts`. The line between them is
not taste:

    vocabulary   an alternative in a top-level `|` — one way of saying a thing. Adding one is a
                 coverage decision, and a Spanish speaker is the right reviewer.
    structure    everything else: anchors, capture groups, lookarounds, the `\\b(?:…)\\b` frame, the
                 order two patterns are consulted in. Adding one is a parsing decision, and the
                 reviewer is whoever owns the reader.

**A phrasing is a regex fragment, verbatim, and that is deliberate.** The obvious next step is to
store «en general» and glue the `\\b`s on at load time, and it cannot be taken without changing what
matches. Today's patterns are not consistent about the glue: `widens` spells «en general» with a
literal space and `recurrence` spells «gastos fijos» as `gastos?\\s+fijos?`. Normalising either way
would move a sentence — the second form matches «gastos  fijos» with two spaces and the first does
not — and this table's whole licence is that it moves no sentence at all. The inconsistency is real
and worth fixing; it is not this change, and it is now visible enough to argue about.

What a reviewer who does not read regex reads instead is `like` and `unlike`. Those are sentences,
they are checked by the test that reads this file, and they are the part Section C can generate
from: a regex cannot be run backwards into an example, so a table of patterns alone could seed
nothing.

`like` is deliberately not a full example set. Of the 149 phrasings here, 97 are already exercised
by a sentence in `evaluation/planning` or `evaluation/conversations`, and those need no invented
example — the corpus is the example, and `scripts/lexicon_coverage.py` prints which sentence. The
52 that no measured sentence reaches are the ones carrying `like`, because for those there is
nothing to point at.

**That second number is the reason this file exists.** Thirty-five per cent of what this module
claims to understand is reached by no sentence in either evaluation tree — «con qué frecuencia», «en
general», «sino», every direct-debit phrase, and the whole `anual` rule. None of it is dead; users
say it, which is why somebody wrote it down. It is unwatched: it can break and every test stays
green. That is a question no pile of regexes can be asked, and the answer is what Section C's
generator needs, because these are exactly the rows where the corpus offers nothing to paraphrase.
"""

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Phrasing:
    """One way of saying something, and the sentences that show it said that way.

    `says` is the alternation branch exactly as the matcher receives it. `like` and `unlike` are
    real sentences, asserted by `tests/test_explicit_lexicon.py` — `unlike` is how an argument in
    prose becomes a check, and several of the comments below were exactly that before this file.
    """

    says: str
    like: tuple[str, ...] = ()
    unlike: tuple[str, ...] = ()
    why: str = ""


# Sentences reach every reader here folded — lowercased and stripped of accents by
# `spanish.folded`. Some phrasings below still carry an accented alternative, `qu[ée]` and `m[áa]s`
# among them, and those alternatives are dead: no folded sentence can contain the accent. They are
# left exactly as they were, because removing them is a change to make on purpose and measure, not
# a tidy-up to smuggle in beside a move. Enumerable now, which is the point.
PHRASINGS: Final[dict[str, tuple[Phrasing, ...]]] = {
    # The ways of asking *who* rather than how much. What that answer shape should be is argued at
    # `_COUNTERPARTY` in `explicit_facts`, which is the reader's decision rather than the phrase's.
    "asks_who": (
        Phrasing(r"\ba\s+(?:que\s+(?:personas?|gente|contactos?)|quien(?:es)?)\b"),
        Phrasing(r"\bde\s+que\s+empresa\b", like=("¿De qué empresa me llega la nómina?",)),
        Phrasing(r"\bque\s+empresas?\b", like=("¿Qué empresas me han cobrado este mes?",)),
    ),
    "merchant_breakdown": (
        Phrasing(
            r"\b(?:desglos\w*|separ\w*|agrup\w*)\s+(?:lo\s+)?por\s+"
            r"(?:comercios?|tiendas?|establecimientos?|negocios?)\b"
        ),
        Phrasing(r"\bpor\s+(?:cada\s+)?(?:comercio|tienda|establecimiento|negocio)\b"),
    ),
    # A correction that widens rather than narrows. "¿Y en general?" is a real thing to say, and it
    # is the one case where a filter disappearing is what the user asked for.
    "widens": (
        Phrasing(r"\ben general\b", like=("¿Y en general?",)),
        Phrasing(r"\ben total\b"),
        Phrasing(r"\bsin filtr", like=("Dámelo sin filtrar por categoría",)),
        Phrasing(r"\bde todo\b", like=("Ahora de todo, no solo del súper",)),
        Phrasing(r"\btodas? las categor", like=("Quiero todas las categorías",)),
    ),
    # A turn that rejects the previous answer and supplies the replacement in the same breath: "no,
    # los últimos 12 meses no; durante todo el año 2025". The intent router's `_CORRECTING`
    # deliberately does not cover this — it looks for a rejection *without* a replacement — so the
    # sentence is planned as an ordinary follow-up, which is right. What it also needs is for the
    # fields it did not mention to survive.
    #
    # These are openings, and the anchor and the "and then says something" are in `explicit_facts`
    # where the rest of the structure lives.
    "rejects_opening": (
        Phrasing("no"),
        Phrasing("mejor"),
        Phrasing("en realidad", like=("En realidad quería el año pasado",)),
        Phrasing("que va", like=("Que va, mejor el año pasado",)),
    ),
    # The same turn shape said mid-sentence, where there is no opening to anchor to.
    "rejects_replacement": (
        Phrasing(r"\bsino\b", like=("No el mes pasado sino el anterior",)),
        Phrasing(r"\bmas bien\b", like=("Más bien el trimestre entero",)),
    ),
    # A question about *when*, not about how much. Two of them in one reported conversation:
    #
    #     "¿Cuándo me suelen ingresar la nómina?"        → 17.383,97 €
    #     "¿Cuándo suelo ir al supermercado a comprar?"  → 3.508,91 €
    #
    # Both figures were correct and neither was an answer. The dates were on screen underneath, in
    # the movement list — the question had been answered and the headline said something else.
    #
    # "Cuánto" is excluded by the word boundary rather than by luck: they differ by one letter and
    # the sentence is folded before it gets here, so `cuando` cannot match inside `cuanto`.
    "asks_when": (
        Phrasing(
            r"\bcuando\b",
            like=("¿Cuándo me suelen ingresar la nómina?",),
            unlike=("¿Cuánto me ingresan de nómina?",),
            why="one letter from «cuánto», and separated by the boundary rather than by luck",
        ),
        Phrasing(r"\bcada\s+cuanto\b"),
        Phrasing(r"\bque\s+dias?\b"),
        Phrasing(r"\bcon\s+que\s+frecuencia\b", like=("¿Con qué frecuencia compro gasolina?",)),
        Phrasing(r"\bcada\s+cuantos\s+dias\b", like=("¿Cada cuántos días voy al súper?",)),
        Phrasing(r"\bsuelo\s+ir\b", like=("¿Cuándo suelo ir al supermercado a comprar?",)),
        Phrasing(r"\ben\s+que\s+fechas?\b", like=("¿En qué fechas me cobran el alquiler?",)),
    ),
    "recurrence": (
        Phrasing(
            r"\b(?:gastos?|pagos?|cargos?|recibos?|cobros?|cuotas?)\s+"
            r"(?:fijos?|recurrentes?|periodicos?)\b"
        ),
        Phrasing(
            r"\b(?:que|cuales)\b.{0,35}\b(?:gastos?|pagos?|cargos?|recibos?)\b.{0,25}"
            r"\b(?:se\s+repiten|son\s+recurrentes?|son\s+periodicos?)\b",
            like=("¿Qué gastos se repiten cada mes?",),
        ),
        Phrasing(
            r"\b(?:suscripciones?|alquiler(?:es)?|recibos?)\b.{0,35}"
            r"\b(?:cada|todos?)\s+(?:los\s+)?mes(?:es)?\b"
        ),
        Phrasing(
            r"\b(?:recibos?\s+domiciliados?|domiciliaciones?|adeudos?\s+domiciliados?)\b",
            like=("¿Qué recibos domiciliados tengo?",),
        ),
        Phrasing(
            r"\b(?:que|cuales|muestra(?:me)?|ensena(?:me)?)\b.{0,25}"
            r"\b(?:mis\s+)?(?:recibos?|adeudos?)\b",
            like=("Muéstrame mis recibos",),
        ),
        # "¿Qué suscripciones tengo?" used to be here, and it is answered better one recogniser up:
        # `asks_how_many_payees` names them and leads with how many there are, where this discovers
        # a pattern and leads with money. Two recognisers claiming one sentence is a tie decided by
        # the order of a cascade, and the measurement that counts those exists so it stays at
        # twelve.
        Phrasing(
            r"\b(?:que|cuales)\b.{0,25}\b(?:pagos?|cargos?)\b.{0,25}\btodos?\s+los\s+meses\b",
            like=("¿Qué pagos me hacen todos los meses?",),
        ),
    ),
    "direct_debit_query": (
        Phrasing(
            r"\b(?:recibos?\s+domiciliados?|domiciliaciones?|adeudos?\s+domiciliados?)\b",
            like=("¿Cuánto pago en recibos domiciliados?",),
        ),
        Phrasing(r"\b(?:mis|los|estos|esos)\s+recibos?\b", like=("¿Cuánto suman mis recibos?",)),
    ),
    "average": (
        Phrasing(r"\b(media|medio|promedio)\b"),
        Phrasing(r"\bimporte\s+medio\b"),
        Phrasing(r"\bticket\s+medio\b"),
    ),
    "cumulative": (
        Phrasing(r"\b(?:gasto|ingreso|importe|total)\s+acumulad[oa]\b"),
        Phrasing(
            r"\bacumulad[oa]\s+(?:por|a\s+lo\s+largo\s+de|mes\s+a\s+mes|semana\s+a\s+semana)\b"
        ),
        Phrasing(
            r"\bcuanto\s+llevaba\s+(?:gastado|ingresado|acumulado)\b",
            like=("¿Cuánto llevaba gastado a mitad de mes?",),
        ),
        Phrasing(
            r"\bcomo\s+(?:iba|fue|ha\s+ido)\s+(?:mi\s+)?(?:gasto|ingreso)\b",
            like=("¿Cómo ha ido mi gasto este año?",),
        ),
    ),
    # **The number has to agree, because three of these nouns are also verbs.** The interrogative
    # and the noun each carried their own optional `s`, so a singular "cuánto" beside a singular
    # "pago" matched — and "¿cuánto pago de alquiler?" came back "3 movimientos en alquiler el mes
    # pasado". Asked how much, told how many, with a real figure of the wrong kind on it. The same
    # held for "cuánto ingreso" and "cuánto cargo": in each, the noun the count rule is looking for
    # is spelled exactly like the first person of the verb that asks for an amount.
    #
    # So the plural is required on both sides. "Cuántos pagos" counts, "cuánto pago" does not, and
    # nothing else has to be enumerated for it: the agreement *is* the rule. `veces` needs no
    # partner spelling because it has no singular anybody says.
    "count": (
        Phrasing(
            r"\bcuant[oa]s\s+(?:veces|movimientos|operaciones|compras|pagos|cargos|ingresos)\b",
            unlike=(
                "¿cuánto pago de alquiler?",
                "¿cuánto ingreso al mes?",
                "¿cuánto cargo me han hecho?",
            ),
            why="the plural is required on both sides; three of these nouns are also verbs",
        ),
        Phrasing(
            r"\b(?:numero|cantidad)\s+de\s+"
            r"(?:movimientos?|operaciones?|compras?|pagos?|cargos?|ingresos?)\b"
        ),
        Phrasing(r"\b(?:mas|menos)\s+(?:movimientos?|operaciones?|compras?|pagos?|cargos?)\b"),
    ),
    "typical_amount": (
        Phrasing(r"\b(?:suelo|solemos)\s+(?:gast|pag|compr|cobr)\w*\b"),
        Phrasing(
            r"\b(?:gast|pag|compr|cobr)\w*\s+(?:normalmente|habitualmente)\b",
            like=("¿Cuánto gasto normalmente en el súper?",),
        ),
    ),
    "per_movement_average": (
        Phrasing(
            r"\b(?:media|promedio|importe\s+medio|ticket\s+medio)\s+"
            r"(?:por|de\s+cada)\s+(?:cada\s+)?"
            r"(?:movimiento|operacion|compra|pago|cargo|ingreso)s?\b"
        ),
    ),
    "temporal_day": (
        Phrasing(r"\b(?:por|al|cada)\s+dia\b"),
        Phrasing(r"\b(?:media|promedio)\s+diari[oa]\b", like=("¿Cuál es mi media diaria?",)),
        Phrasing(
            r"\b(?:gasto|importe|coste)\s+diari[oa]\s+medi[oa]\b",
            like=("¿Y el gasto diario medio?",),
        ),
        Phrasing(r"\b(?:y\s+)?(?:el\s+)?diari[oa]\b"),
    ),
    "temporal_week": (
        Phrasing(r"\b(?:por|a\s+la|cada)\s+semana\b"),
        Phrasing(r"\b(?:media|promedio)\s+semanal\b", like=("¿Cuál es mi promedio semanal?",)),
        Phrasing(r"\b(?:gasto|importe|coste)\s+semanal\s+medio\b"),
        # The misspellings are measured, not guessed: «semanl» and «semnal» arrived in the corpus.
        Phrasing(r"\b(?:y\s+)?(?:el\s+)?(?:semanal|semanl|semnal)\b"),
    ),
    "temporal_month": (
        Phrasing(r"\b(?:por|al|cada)\s+mes\b"),
        Phrasing(r"\bmes\s+a\s+mes\b"),
        Phrasing(r"\b(?:media|promedio|gasto\s+medio|importe\s+medio|coste\s+medio)\s+mensual\b"),
        Phrasing(r"\b(?:gasto|importe|coste)\s+mensual\s+medio\b"),
        Phrasing(r"\bmensualmente\b"),
        Phrasing(
            r"\b(?:movimientos?|operaciones?|compras?|pagos?|cargos?)\s+mensual(?:es)?\b",
            like=("¿Cuántos movimientos mensuales tengo?",),
        ),
        Phrasing(r"\b(?:y\s+)?(?:el\s+)?(?:mensual|mensula|mesnual)\b"),
    ),
    "top_category": (
        Phrasing(
            r"\b(?:en\s+)?qu[ée]\s+categor[íi]a\b.{0,55}\b(?:gast|pag|compr)\w*\b"
            r".{0,20}\bm[áa]s\b"
        ),
        Phrasing(r"\bqu[ée]\s+categor[íi]a\s+es\s+la\s+que\b.{0,45}\bm[áa]s\b"),
        Phrasing(r"\bd[oó]nde\b.{0,35}\b(?:gast|pag|compr)\w*\b.{0,15}\bm[áa]s\b"),
    ),
    "account_breakdown": (
        Phrasing(r"\bdesglos\w*\s+por\s+(?:cada\s+)?cuentas?\b"),
        Phrasing(
            r"\b(?:por|separad[oa]\s+por)\s+cada\s+cuenta\b",
            like=("Dámelo por cada cuenta",),
        ),
        Phrasing(r"\b(?:ingresos?|income|gastos?|movimientos?)\s+de\s+cada\s+cuenta\b"),
        Phrasing(r"\bseparad[oa]\s+por\s+cuentas?\b", like=("Separado por cuentas",)),
        Phrasing(r"\bsepar\w*\s+por\s+cuentas?\b"),
        Phrasing(r"\b(?:en|a)\s+(?:que|cual)\s+cuenta\b"),
    ),
    "comparison_reference": (
        Phrasing(
            r"\bcu[áa]l\s+de\s+(?:las|estas|esas)\s+dos\b.{0,30}"
            r"\b(?:mayor|menor|m[áa]s\s+alt[oa]|m[áa]s\s+baj[oa])\b"
        ),
        Phrasing(
            r"\bcu[áa]l\s+(?:es|fue)\s+(?:la\s+)?(?:mayor|menor)\s+de\s+las\s+dos\b",
            like=("¿Cuál es la mayor de las dos?",),
        ),
    ),
    "compares": (
        Phrasing(r"\bcompara"),
        Phrasing(r"\bcomparacion\b", like=("Hazme una comparación de alquiler y súper",)),
        Phrasing(r"\bfrente\s+a\b", like=("Alquiler frente a súper este año",)),
        Phrasing(r"\bpor\s+separado\b", like=("Dame alquiler y súper por separado",)),
        Phrasing(r"\buna\s+y\s+otra\b", like=("¿Cuánto en una y otra?",)),
        Phrasing(r"\bentre\b"),
        Phrasing(r"\bcual\b.{0,35}\b(?:mayor|menor|mas\s+alt[oa]|mas\s+baj[oa])\b"),
        Phrasing(r"\bmas\b.{0,45}\b(?:o|que)\b"),
    ),
    # `income` and `expense` matched the same words and nobody knew, which is the defect this table
    # is built to make visible. They still overlap on «me cobr…», and that overlap is deliberate
    # and argued at `_direction` in `explicit_facts`: both flags go true and the specific reading
    # wins.
    "income": (
        Phrasing(r"\bingres(?:ar|amos|o|os|e|ado|aron)\b"),
        Phrasing(r"\bcobr(?:ar|amos|e|ado|aron|os?)\b"),
        Phrasing(r"\brecibi(?:do)?\b"),
        Phrasing(r"\bincome\b"),
        Phrasing(r"\bnomina\b"),
        Phrasing(r"\bsueldo\b"),
        Phrasing(r"\bsalario\b", like=("¿Cuándo me entra el salario?",)),
        Phrasing(r"\bme\s+(?:ha\s+)?entrado\b"),
    ),
    # `expense` **vetoes** an income reading; `leaving` **asserts** a spending one. Two lists
    # because the two powers are not worth the same. A veto costs a `None`: the model keeps
    # whichever direction it chose and `plan_verifier` still gets to look at it. An assertion
    # overwrites a field the model stated, on every sentence that matches, and nothing downstream
    # re-examines it.
    #
    # These four are good enough to veto — they say the sentence is not purely about money
    # arriving — and deliberately not good enough to assert. They are topic words: «¿en qué
    # categoría gasto más?» is about spending without stating a direction to impose. And `\bgast` is
    # the exact trigger of `plan_verifier._spending_answered_as_everything`, so a reader that forced
    # EXPENSE on every `gast` sentence would repair that disagreement before the verifier could ever
    # see it — which would leave the one guard written against the live prompt injection unreachable
    # on the sentences it was written for.
    "expense": (
        Phrasing(
            r"\bgast",
            why="a topic word, and the exact trigger of the verifier's spending-answered-as-ALL "
            "refusal, so it may veto an income reading and may not assert a spending one",
        ),
        Phrasing(r"\bpagu"),
        Phrasing(r"\bcompr"),
        Phrasing(r"\bcargo"),
        Phrasing(r"\bme\s+cobr"),
    ),
    # The first-person constructions that can only mean money left this account: «me cobraron» —
    # somebody charged me — and «me he dejado» — what I dropped on something. Neither contains
    # `gast`, so this and the verifier's refusal cannot collide on any sentence either could be
    # about: they are disjoint by construction rather than by luck. The one overlap is a compound
    # «¿cuánto me he dejado y cuánto gasté?», which states two questions and belongs to the
    # clarification path rather than to either rule.
    #
    # Refused, all for one reason — no dataset says what they should read as, so adding them would
    # be vocabulary nobody measured: «me clavaron», «me crujieron», and «se me ha ido», which the
    # model already gets right unaided and which would be this module overruling it on a guess.
    "leaving": (
        Phrasing(r"\bme\s+cobr"),
        Phrasing(r"\bme\s+he\s+dejado\b"),
    ),
    # A merchant name is a name. These openings announce a quantity, or point back at something
    # said earlier, so what follows one is an exclusion nobody can execute rather than a shop —
    # «sin contar algunas cosas», «si no contamos lo que ya sabes», «quitando lo de siempre».
    #
    # Determiners are deliberately absent. "el" and "la" open real shop names — «sin El Corte
    # Inglés», «sin contar La Sirena» — and rejecting those would break the case the reader exists
    # for. The `unlike` below is that argument, checked.
    "vague_tail": (
        Phrasing(r"algun[oa]s?", like=("algunas cosas",), unlike=("El Corte Inglés",)),
        Phrasing(r"algo"),
        Phrasing(r"un[oa]s", like=("unas cuantas",)),
        Phrasing(r"ciert[oa]s?", like=("ciertos gastos",)),
        Phrasing(r"varias", like=("varias cosas",)),
        Phrasing(r"varios", like=("varios sitios",)),
        Phrasing(r"demas", like=("demas cosas",)),
        Phrasing(r"resto", like=("resto de cosas",)),
        Phrasing(r"lo\s+(?:que|de|mismo|demas)", unlike=("La Sirena",)),
        Phrasing(r"los\s+que", like=("los que ya sabes",)),
        Phrasing(r"las?\s+que", like=("las que te dije",)),
        Phrasing(r"es[oa]s?"),
        Phrasing(r"aquell[oa]s?", like=("aquellos gastos",)),
        Phrasing(r"siempre"),
    ),
    # "How many X do I have" and "which X do I have", where X is a kind of spending.
    "how_many_payees": (
        Phrasing(r"cuantas"),
        Phrasing(r"cuantos"),
        Phrasing(r"cuales"),
        Phrasing(r"que"),
    ),
    # The verbs that make it a question about *having* rather than about spending. Without one of
    # these "¿qué gasté en suscripciones?" would be read as a list of payees.
    "has_them": (
        Phrasing(r"tengo"),
        Phrasing(r"tienes"),
        Phrasing(r"tenemos", like=("¿Cuántas suscripciones tenemos?",)),
        Phrasing(r"hay"),
        Phrasing(r"mantengo", like=("¿Cuántas suscripciones mantengo?",)),
        Phrasing(r"pago"),
        Phrasing(r"pagas", like=("¿Cuántas cuotas pagas al mes?",)),
        Phrasing(r"activas?", like=("¿Cuántas suscripciones activas tengo?",)),
        Phrasing(r"activos?", like=("¿Cuántos recibos activos tengo?",)),
        # The possessive is one of these too: "¿cuáles son mis suscripciones?" asks exactly this
        # and names no verb of having at all.
        Phrasing(r"mis", why="the possessive asks the same question and names no verb of having"),
        Phrasing(r"contratadas?", like=("¿Qué suscripciones tengo contratadas?",)),
        Phrasing(r"contratados?", like=("¿Qué servicios tengo contratados?",)),
        Phrasing(r"dadas?\s+de\s+alta", like=("¿Cuáles tengo dadas de alta?",)),
    ),
    # Words naming the charge rather than the payee. "¿Cuántos pagos tengo en suscripciones?" is
    # asking how many times, which is COUNT and already answered.
    #
    # The rails belong here and were missing. "¿Cuántos bizums tengo?" is how many times, and it
    # came back as how many *people* this holder has ever sent one to, over twelve months instead of
    # the window the conversation was in — a real figure about something else, in the middle of an
    # episode whose whole subject is that "tengo" counts.
    #
    # Only the rails are added, and "domiciliaciones" and "suscripciones" deliberately are not:
    # those name the arrangement at least as often as the charge, and a question about how many of
    # *them* you have is the payee count that path exists for. A bizum is never an arrangement.
    #
    # `explicit_facts.CHARGE_NOUNS` is assembled from this and exported, because the same nouns
    # decide the same thing in the query handler's count follow-up and were kept there by hand.
    # They stayed level only because one change happened to touch both: the rails were missing from
    # each, and each was found separately.
    "charge_nouns": (
        Phrasing(r"movimientos?"),
        Phrasing(r"veces"),
        Phrasing(r"compras?"),
        Phrasing(r"pagos?"),
        Phrasing(r"cargos?"),
        Phrasing(r"recibos?"),
        Phrasing(r"cobros?", like=("¿Cuántos cobros me han hecho este mes?",)),
        Phrasing(r"operaciones?"),
        Phrasing(r"transacciones?"),
        Phrasing(r"bi[xz]ums?", why="a bizum is never an arrangement, so it is always the charge"),
        Phrasing(r"transferencias?"),
        Phrasing(r"traspasos?"),
    ),
    # Asking for money, which is never a payee count however it is phrased.
    #
    # This and `aggregate_words` below are two hand-maintained lists of the same idea, and one is a
    # strict subset of the other. Neither knew about the other until they were written down side by
    # side. They are left as they are — merging them would move sentences — but the divergence is
    # exactly the shape of every defect this table exists to surface.
    "asks_an_amount": (
        Phrasing(r"cuanto"),
        Phrasing(r"importe"),
        Phrasing(r"total"),
        Phrasing(r"suma"),
        Phrasing(r"gaste"),
        Phrasing(r"gastado"),
        Phrasing(r"gasto"),
        Phrasing(r"pague"),
    ),
    # "Cuánto pago en recibos domiciliados" names the same banking rail as a recurrence question
    # but asks for a sum, not pattern discovery.
    "aggregate_words": (
        Phrasing(r"cuanto"),
        Phrasing(r"importe"),
        Phrasing(r"total"),
        Phrasing(r"suma"),
    ),
    # "anual ... por mes" gives both the horizon and the divisor. It means the trailing twelve
    # months unless a more precise year or window was written. This is a duration, not a forecast.
    #
    # Reached by no sentence in either evaluation tree, so that whole rule is asserted by nothing
    # but the example below.
    "annual": (Phrasing(r"\banual(?:es)?\b", like=("¿Cuál es mi gasto medio anual por mes?",)),),
}


# A tail that names no shop is not a merchant exclusion. Not a regex — the membership test in
# `_excluded_merchants` needs *every* word of the tail to be one of these — so it is a word list
# rather than an alternation, and it is here so that enumerating what this module knows does not
# have to stop at the patterns.
NOT_A_MERCHANT: Final[tuple[str, ...]] = (
    "categoria",
    "categorias",
    "gasto",
    "gastos",
    "ingreso",
    "ingresos",
    "movimiento",
    "movimientos",
    "todo",
    "todos",
    "nada",
    # The exclusion marker's own verb. It reaches here because the optional
    # `(?:\s+contar|\s+incluir)?` in `_EXCLUSION_TAIL` backtracks to nothing whenever the verb ends
    # the sentence, so «sin contar» left "contar" as the name of a shop to exclude — and «sin
    # contar el alquiler» is the phrase the refusal message itself recommends to users.
    #
    # Listed here rather than anchored to the start of the tail because the membership test needs
    # *every* word to be one of these: «sin meter Cepsa» has no `sin meter` alternative in
    # `_EXCLUSION_TAIL`, so its tail is "meter cepsa", and Cepsa is a real shop.
    "contar",
    "contarlo",
    "contarlos",
    "contamos",
    "cuente",
    "cuentes",
    "incluir",
    "incluirlo",
    "incluirlos",
    "incluimos",
    "incluya",
    "incluyas",
    "meter",
    "metas",
)


def phrasings(concept: str) -> tuple[Phrasing, ...]:
    """Every way this table knows of saying `concept`."""
    return PHRASINGS[concept]


def alternation(concept: str) -> str:
    """The concept's phrasings as one alternation, for a caller that supplies its own frame.

    Returned as a string rather than interpolated into a template here, because a frame is
    `explicit_facts`' to write and several of these phrasings contain `{0,35}`: a `str.format`
    hole would make a repetition count look like a placeholder. The call sites use f-strings, so
    the frame stays visible beside the reader that owns it.
    """
    return "|".join(phrasing.says for phrasing in phrasings(concept))


def compiled(concept: str) -> re.Pattern[str]:
    """The concept as a pattern in its own right, where the vocabulary is the whole of it."""
    return re.compile(alternation(concept))
