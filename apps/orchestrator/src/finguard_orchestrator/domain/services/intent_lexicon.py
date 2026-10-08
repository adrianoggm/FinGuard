"""The Spanish `intent_router` knows, as a table somebody can read and a script can count.

`explicit_facts` went first and the argument is the same one: every defect fixed on 2026-08-29 was
one module knowing a phrase another did not, and none of them was hard to fix — they were hard to
*find*, because "what does this system know how to say?" had no answer short of reading regex and
imagining sentences.

It matters more here than it did there. The router sits upstream of everything: it decides whether
a turn is a financial question, a payment, a courtesy or out of domain, so a phrase it does not
know is a turn that never reaches the handler that could have answered it. Every story in the
comments below is that failure — a real figure about something nobody asked, or a refusal about
averages to somebody who said hello.

The line between what moved and what stayed is the one `explicit_lexicon` drew:

    vocabulary   an alternative in a top-level `|` — one way of saying a thing. Adding one is a
                 coverage decision, and a Spanish speaker is the right reviewer.
    structure    the frame each concept is read inside, the flags, and the order the router
                 consults them in. Adding one is a routing decision, and the reviewer is whoever
                 owns the cascade. All of it stayed in `intent_router`, beside the `route` whose
                 order is the design.

**A phrasing is a regex fragment, verbatim.** Storing a bare phrase and gluing the word boundaries
on at load moves sentences, because today's frames disagree about the glue: `moving` is framed
`\\b(…)\\b` and its rows carry no boundary of their own, while `about_the_balance` has no frame at
all and every row spells its own `\\b`. Normalising either way would move a sentence, and this
table's whole licence is that it moves none. `tests/test_intent_lexicon.py` asserts that all 23
patterns rebuild byte-for-byte from these rows.

**Where the router forced a deviation.** `explicit_lexicon` holds nothing but literals, and four
rows here cannot be literals: «cuánto» is spelled by a rule rather than by a list. The verb beside
it already carried three misspellings by hand — `tiengo`, `tieno`, `tngo` — and the question word
carried none, so "cunato tengo" was a balance question answered by the financial planner.
`spanish.transposed` fixed that by generating the transpositions, and a generated set is the exact
opposite of a table row: enumerating it here would repeat what the rule already teaches and would
go stale the moment the rule changed. So those four rows carry a `<how_much>` slot that
`intent_router` fills with the rule's output.

The slot is `<how_much>` and not `{how_much}` for a reason `explicit_lexicon` measured: a
`str.format` hole makes a repetition count look like a placeholder, and the ninth row of that very
concept contains `.{0,30}`. One slot, one filler, substituted by `str.replace`. It is deliberately
not a way for one row to reference another concept — that would be the rule engine the design
refuses, and the second thing anybody would have to build is a parser for it.

**Nothing stayed behind as a parser, and that is a real difference from `explicit_facts`.** Two
patterns stayed welded to that reader because a capture group was read and *which* alternative won
decided what got captured — a table whose rows must keep their order is a parser with a table's
manners. No pattern here reads a group. All 23 are consulted for truth only, and the 61 capture
groups they contain are `(` written where `(?:` was meant: inert, never inspected, and preserved
exactly as they are because removing them is a change to make on purpose and measure.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Phrasing:
    """One way of saying something, and the sentences that show it said that way.

    `says` is the alternation branch exactly as the router's frame receives it. `like` and `unlike`
    are real sentences, asserted by `tests/test_intent_lexicon.py` — `unlike` is how an argument in
    prose becomes a check, and most of the ones below were exactly that before this file.

    Deliberately its own five lines rather than an import of `explicit_lexicon.Phrasing`. The two
    are identical today and the duplication is real, but the alternative is a module the *intent*
    router depends on importing its type from a module about the *financial* reader downstream of
    it, and a layering inversion costs more than five lines. `scripts/lexicon_coverage.py` reads
    both tables through the two fields they share.
    """

    says: str
    like: tuple[str, ...] = ()
    unlike: tuple[str, ...] = ()
    why: str = ""


# The filler for the one slot these rows use. Substituted by `intent_router`, which owns the rule
# that produces it; see the module docstring for why it is not a `str.format` hole.
HOW_MUCH_SLOT: Final = "<how_much>"


# Sentences reach the router folded — lowercased and stripped of accents by `spanish.folded`. Many
# phrasings below still carry an accented alternative, `qu[ée]` and `cu[áa]nto` among them, and
# those alternatives are dead: no folded sentence can contain the accent. They are left exactly as
# they were, because removing them is a change to make on purpose and measure, not a tidy-up to
# smuggle in beside a move. The same is true of the `re.IGNORECASE` on twenty of the frames.
# Enumerable now, which is the point.
PHRASINGS: Final[dict[str, tuple[Phrasing, ...]]] = {
    # How much money there is, which is a state and not a sum over a window.
    #
    # The first question of the reported conversation — "¿podrías decirme cuánto dinero tengo en
    # mis cuentas?" — came back as *"no he encontrado movimientos del 2026-08-04 al 2026-08-04"*.
    # There were five intents and none of them was the balance, so it fell through to the financial
    # planner, and `FinancialPlan` cannot express a balance at all. The model built the nearest
    # thing available to it, which was today's movements.
    #
    # **The discriminator is the verb.** "Tengo", "queda", "dispongo" and "saldo" are about a
    # state; "gasté", "ingresé" and "he gastado" are sums over a period. Where the two overlap is
    # `not_the_balance` below, which the router consults first.
    "about_the_balance": (
        Phrasing(r"\bsaldos?\b", like=("¿Cuál es el saldo de mi cuenta?",)),
        Phrasing(
            r"\b(?:<how_much>)\s+(dinero\s+)?"
            r"(tengo|tiengo|tieno|tngo|ten[íi]a|me\s+queda|hay)\b",
            like=(
                "¿Podrías decirme cuánto dinero tengo en mis cuentas?",
                "cunato tengo",
            ),
            unlike=("¿Cuándo cobro la nómina?",),
            why="both halves have to survive a slip for the sentence to, so the question word is "
            "spelled by `spanish.transposed` and the verb by hand. Transpositions and not one "
            "whole edit: one substitution from «cuanto» is «cuando», and reading «¿cuándo cobro "
            "la nómina?» as a balance would be a real figure about a different question",
        ),
        # «¿Qué tengo en total?», «q tengo entre todo», «¿y qué tengo en total entre todas?»: the
        # same question with «qué» for «cuánto» (validation fix 3, G2). Said as «bueno da igual. q
        # tengo en total entre todo?» on 2026-10-08, it was the reply to an open question once and
        # the net of the month's movements the next time — neither of them money held.
        Phrasing(
            r"\b(?:que|q)\s+(dinero\s+)?tengo\s+(en\s+total|entre\s+tod[oa]s?)\b",
            like=("bueno da igual. q tengo en total entre todo?", "¿Qué tengo en total?"),
            unlike=("¿Qué tengo que pagar este mes?", "¿Qué gastos tengo en total este mes?"),
            why="«qué tengo» alone is also what is owed or what is pending, so the whole of it is "
            "required: «en total» or «entre todo», the words that ask for every account",
        ),
        Phrasing(r"\b(?:<how_much>)\s+dinero\s+dispongo\b", like=("¿De cuánto dinero dispongo?",)),
        Phrasing(
            r"\b(?:<how_much>)\s+(tengo\s+)?disponible\b",
            like=("¿Cuánto tengo disponible?",),
        ),
        Phrasing(r"\bde\s+(?:<how_much>)\s+dispongo\b", like=("¿De cuánto dispongo?",)),
        Phrasing(r"\bdinero\s+(disponible|tengo)\b", like=("¿Qué dinero tengo?",)),
        # "¿Qué cuentas tengo?" — the accounts themselves, which is what a balance answer already
        # is: a total with every account and its figure under it. It was excluded rather than
        # answered, and nothing else answered it either, so it reached the planner and came back
        # "eso no puedo calcularlo todavía" about a list this service holds and had printed one
        # turn earlier.
        Phrasing(r"\bqu[ée]\s+cuentas\s+(tengo|tiene|hay)\b", like=("¿Qué cuentas tengo?",)),
        Phrasing(
            r"\bcu[áa]ntas\s+cuentas\s+(tengo|tiene|hay)\b",
            like=("¿Cuántas cuentas tengo?",),
        ),
        # "El estado actual de mi hucha", "¿cómo está mi cuenta corriente?" — a state, asked
        # without the word "cuánto". It reached the planner and came back "¿de qué periodo te
        # gustaría saber el estado actual de tu hucha de ahorros?": a question about a window, to
        # somebody asking about a balance, quoting their own sentence back at them.
        #
        # The account noun beside it is what keeps "¿en qué estado está el pago?" and "¿cómo va el
        # mes?" out — neither is about how much money there is.
        Phrasing(
            r"\b(estado\s+(actual\s+)?(de|en)|c[óo]mo\s+(est[áa]|va|anda))\b.{0,30}"
            r"\b(cuenta|hucha|ahorros?|fondo|n[óo]mina|corriente)\b",
            like=("El estado actual de mi hucha", "¿Cómo está mi cuenta corriente?"),
            unlike=("¿En qué estado está el pago?", "¿Cómo va el mes?"),
            why="both halves are required: a state without an account is not about how much money "
            "there is",
        ),
    ),
    # Asking which accounts exist is served by the same deterministic summary as a balance. Every
    # row needs a catalogue word — list, open, active, how many — so «gastos de mis cuentas» stays
    # a ledger query.
    "about_accounts": (
        Phrasing(
            r"\b(?:lista|listado)\s+de\s+(?:mis\s+)?cuentas(?:\s+bancarias)?\b",
            like=("Dame la lista de mis cuentas",),
        ),
        Phrasing(
            r"\b(?:cu[áa]les|quales)\s+(?:son\s+)?(?:las\s+|mis\s+)?cuentas"
            r"(?:\s+bancarias)?\b.{0,45}\b(?:tengo|abiertas|activas|actuales)\b",
            like=("¿Cuáles son mis cuentas abiertas?",),
        ),
        Phrasing(
            r"\b(?:qu[eé]|cu[áa]ntas)\s+cuentas(?:\s+bancarias|\s+bancarais)?\s+"
            r"(?:tengo|tiene|hay)\b",
            like=("¿Qué cuentas bancarias tengo?",),
            why="«bancarais» is a misspelling somebody typed, and a typo is not a change of intent",
        ),
        Phrasing(
            r"\bmis\s+cuentas\b.{0,35}\b(?:cu[áa]les|abiertas|activas)\b",
            like=("De mis cuentas, ¿cuáles están activas?",),
        ),
    ),
    # What "tengo" means when it is not about the balance. The router checks this first, so a
    # question that counts movements or lists things keeps going to the path that can answer it.
    #
    # "Cuentas" left this list when the balance learned to answer about them. Contacts and cards
    # stay: both have a screen of their own, neither has an answer on the balance path, and a
    # balance would be a real figure about something nobody asked for.
    #
    # Read by `asks_for_something_else` as well as by the balance, because the balance ellipsis in
    # the handler needs exactly this judgement. One vocabulary for one question: a second list of
    # nouns over there would drift from this one, which is precisely how the model's prompt drifted
    # from these rules.
    "not_the_balance": (
        Phrasing(
            r"\bcu[áa]nt[oa]s?\s+(movimientos|compras|cargos|pagos|operaciones|veces|bizums?)\b",
            like=("¿Cuántos movimientos tengo este mes?",),
        ),
        Phrasing(r"\bgast[éeoa]", like=("¿Cuánto gasté el mes pasado?",)),
        Phrasing(r"\bingres[éeoa]", like=("¿Cuánto ingresé en julio?",)),
        Phrasing(r"\bhe\s+gastado\b", like=("¿Cuánto he gastado este mes?",)),
        Phrasing(r"\bme\s+he\s+gastado\b", like=("¿Cuánto me he gastado en el súper?",)),
        Phrasing(r"\bqu[ée]\s+(contactos|tarjetas)\b", like=("¿Qué contactos tengo?",)),
        # The noun on its own, not only after "cuántos". A balance is one figure; the moment a
        # sentence names the *things* that make it up, it is asking for a list however it is
        # phrased.
        #
        # "En el fondo Indexado que movimientos tengo en el utlimo año?" came back as the fund's
        # balance. Eleven words, a verb, and `movimientos` in the middle of it — taken by the
        # balance ellipsis because a misspelt "utlimo" hid the period, and with the period hidden
        # every remaining condition read like a bare account name.
        Phrasing(
            r"\b(movimientos|compras|cargos|operaciones)\b",
            like=("En el fondo Indexado que movimientos tengo en el utlimo año?",),
        ),
    ),
    # The cards in the wallet, and what has been charged to each.
    #
    # The data was always there — `ListCardsQuery` is registered on the bus, the wallet screen
    # draws it, and `CardRow` carries `spent_minor` per card. The conversation could not reach any
    # of it, so "¿qué tarjetas tengo de crédito?" came back "eso no puedo calcularlo todavía" about
    # a screen the user can open, and "¿cuánto he gastado con cada tarjeta este mes?" came back
    # 868,97 € — the account total, labelled as an answer about cards.
    #
    # A plan has no card dimension at all, so the planner could only ever approximate, and an
    # approximation with a euro sign on it is the failure this service is built around not having.
    #
    # Narrow on purpose. Every row needs the noun, so a question about spending that merely
    # mentions how it was paid is not caught.
    "about_cards": (
        Phrasing(
            r"\b(qu[ée]|cu[áa]ntas)\s+tarjetas\b",
            like=("¿Qué tarjetas tengo de crédito?",),
            unlike=("¿Cuánto gasté con la tarjeta en Mercadona?",),
            why="the plural noun is required, so a spending question that names the rail it was "
            "paid on stays a spending question",
        ),
        Phrasing(
            r"\btarjetas\s+(tengo|tiene|hay|tengo\s+activas)\b",
            like=("¿Cuántas tarjetas tengo activas?",),
        ),
        Phrasing(r"\bmis\s+tarjetas\b", like=("Enséñame mis tarjetas",)),
        Phrasing(
            r"\bcon\s+cada\s+tarjeta\b",
            like=("¿Cuánto he gastado con cada tarjeta este mes?",),
        ),
        Phrasing(r"\bpor\s+tarjeta\b", like=("Dame el gasto por tarjeta",)),
        Phrasing(
            r"\bcu[áa]nto\s+.{0,20}\bcada\s+tarjeta\b",
            like=("¿Cuánto llevo en cada tarjeta?",),
        ),
    ),
    "about_cards_by_each": (
        Phrasing(
            r"\b(?:tarjetas|tarjtas)\b.{0,100}"
            r"\b(?:cada\s+una|gastos?\s+asociados?|gasto\s+total)\b",
            like=("¿Qué tarjetas tengo y el gasto total de cada una?",),
            why="«tarjtas» is a misspelling somebody typed",
        ),
        Phrasing(
            r"\bcada\s+una\s+de\s+(?:mis|las)\s+tarjetas\b",
            like=("Dame el gasto de cada una de mis tarjetas",),
        ),
    ),
    # Who can be paid, which is a question and not an order.
    #
    # From the reported conversation: "¿podrías decirme qué contactos tengo para mandar bizum?" was
    # read as a payment — "bizum" is a payment word — so the assistant asked for the recipient of a
    # payment nobody had asked to make. The list has been on /v1/contacts since it was written; the
    # conversation could not reach it.
    #
    # The router checks this *before* the payment check, reversing the rule that payments come
    # first, and the argument for why that is safe lives beside `route`. What makes it safe is the
    # narrowness of these two rows: they need the word "contactos", or an explicit "a quién puedo",
    # and no order to pay somebody contains either.
    "about_contacts": (
        Phrasing(
            r"\bcontactos?\b",
            like=("¿Podrías decirme qué contactos tengo para mandar bizum?",),
        ),
        Phrasing(
            r"\ba\s+qui[ée]n\s+(le\s+)?(puedo|podr[íi]a|puedes|se\s+(le\s+)?puede)\b",
            like=("¿A quién puedo enviar dinero?",),
        ),
    ),
    "about_agenda": (
        Phrasing(
            r"\b(?:quiero|quieo|necesito|puedo)\s+saber\b.{0,90}"
            r"\b(?:agend(?:a)?|personas)\b",
            like=("Quiero saber qué personas tengo en la agenda",),
            why="«quieo» is a misspelling somebody typed",
        ),
        Phrasing(
            r"\b(?:lista|listado)\s+de\s+(?:las\s+)?personas\s+de\s+mi\s+agend(?:a)?\b",
            like=("Dame la lista de las personas de mi agenda",),
        ),
        Phrasing(
            r"\ba\s+qui(?:[ée]n|e)\s+de\s+mi\s+agend(?:a)?\b",
            like=("¿A quién de mi agenda puedo pagar?",),
        ),
    ),
    # About the assistant rather than about the account.
    #
    # The list grew from a real conversation where three turns in a row asked what this thing does
    # and all three were answered with "¿de qué periodo?":
    #
    #     "Oye, ¿con qué cosas me puedes asistir / ayudar?"
    #     "Del banco me refiero"
    #     "Vale, ¿puedes hacer transferencias o solo bizums?"
    #
    # None of them matched, because the pattern wanted the literal "qué puedes hacer". The answer
    # already existed and was good — `explainer.IDENTITY` says exactly what this does — so the
    # whole defect was the recogniser, and the whole cost of it was a user concluding the assistant
    # is broken. That is the cheapest kind of failure to fix and the most expensive to leave.
    #
    # Three shapes. **What can you do**: a verb of helping, with or without "qué". **How do I use
    # you**: "¿Cómo puedo utilizarte?" was refused with "eso no puedo calcularlo todavía" and the
    # very next turn, "¿qué eres capaz de hacer?", was answered in full — the same question twice,
    # and the difference was which verb the person reached for. **Can you do X**: `puedes` followed
    # by an operation rather than by a verb of telling.
    "about_itself": (
        Phrasing(
            r"c[óo]mo\s+(puedo|se\s+puede|te\s+puedo)\s+(usar|utilizar|usarte|utilizarte)",
            like=("¿Cómo puedo utilizarte?",),
        ),
        Phrasing(r"c[óo]mo\s+(te\s+)?(usas|utilizas)", like=("¿Cómo te usas?",)),
        Phrasing(r"para\s+qu[ée]\s+(vales|te\s+uso)", like=("¿Para qué vales?",)),
        Phrasing(r"qu[ée]\s+eres\s+capaz\s+de\s+hacer", like=("¿Qué eres capaz de hacer?",)),
        Phrasing(r"qui[ée]n\s+eres", like=("¿Quién eres?",)),
        Phrasing(r"qu[ée]\s+eres", like=("¿Qué eres?",)),
        Phrasing(r"c[óo]mo\s+funcionas", like=("¿Cómo funcionas?",)),
        Phrasing(r"c[óo]mo\s+te\s+llamas", like=("¿Cómo te llamas?",)),
        Phrasing(
            r"eres\s+(una?\s+)?(ia|bot|robot|m[áa]quina|humano|persona)",
            like=("¿Eres un bot?",),
        ),
        Phrasing(
            r"qu[ée]\s+(puedes|sabes|sabr[íi]as)\s+hacer",
            like=("¿Qué sabes hacer?",),
        ),
        Phrasing(r"para\s+qu[ée]\s+sirves", like=("¿Para qué sirves?",)),
        # The one row here that carries its own anchors, and the frame is `\b(…)` — so the leading
        # `\b` requires a word character at position zero and the `^\s*` can therefore only ever
        # match the empty string. "ayuda" alone reaches this; " ayuda" does not. Left exactly as it
        # was: the anchors are how a bare cry for help is kept from swallowing "no me ayuda esta
        # respuesta", and tightening the interaction is a change to make on purpose and measure.
        Phrasing(
            r"^\s*ayuda\s*[?!.]*$",
            like=("ayuda",),
            unlike=("no me ayuda esta respuesta",),
            why="anchored end to end, inside a frame that already required a word character first",
        ),
        Phrasing(r"qu[ée]\s+es\s+finguard", like=("¿Qué es FinGuard?",)),
        Phrasing(
            r"(qu[ée]|c[óo]mo)\s+.{0,20}\bme\s+(puedes|podr[íi]as)\s+"
            r"(ayudar|asistir|echar)",
            like=(
                "Oye, ¿con qué cosas me puedes asistir?",
                "¿Con qué cosas me puedes ayudar?",
                "¿En qué me puedes asistir?",
            ),
        ),
        Phrasing(
            r"\bme\s+(puedes|podr[íi]as)\s+(ayudar|asistir)\b",
            like=("¿Me puedes ayudar?",),
        ),
        # An operation, not a request to look something up. Listed rather than a wildcard:
        # "¿puedes hacer una consulta de julio?" is a question about money, and a bare
        # `puedes hacer` would swallow it. `puedes hacer` and not a bare `puedes`, because
        # "¿puedes decirme cuánto gasté?" is a question about money phrased politely.
        Phrasing(
            r"\b(puedes|sabes|podr[íi]as)\s+(hacer\s+)?"
            r"(transferencias|transferir|bizums|pagos|pagar|ingresos|domiciliaciones)\b",
            like=(
                "Vale, ¿puedes hacer transferencias o solo bizums?",
                "¿Sabes hacer bizums?",
            ),
            unlike=(
                "¿Puedes decirme cuánto gasté?",
                "¿Puedes hacer una consulta de julio?",
                # **This one was listed as an example and never matched.** The comment this row
                # came from named "¿haces pagos?" among the sentences it catches, and the row asks
                # for `puedes`, `sabes` or `podrías` — "haces" is none of them. A claim in prose
                # that nothing checks, true-looking for as long as nobody tried it. Recorded as a
                # counter-example rather than fixed: adding the verb is a coverage decision to make
                # on purpose and measure, and this table may not move a sentence.
                "¿Haces pagos?",
            ),
            why="the operation is enumerated so that a bare «puedes hacer» cannot swallow a "
            "question about money",
        ),
    ),
    # The noun on its own. It means nothing without a purpose word beside it, and the router
    # requires both — which is why these are two concepts and not one row.
    "assistant_noun": (
        Phrasing(r"\b(?:este\s+)?asistente\b", like=("¿Qué hace este asistente?",)),
    ),
    "assistant_purpose": (
        Phrasing(
            r"\b(?:qu[eé]|qe)\s+(?:es|hace)\b",
            like=("¿Qué hace este asistente?",),
            why="«qe» is a misspelling somebody typed",
        ),
        Phrasing(
            r"\b(?:prop[oó]sito|objetivo)\b",
            like=("¿Cuál es el propósito de este asistente?",),
        ),
        Phrasing(
            r"\b(?:ayudar(?:me)?|echarme\s+una\s+mano|gestionar)\b",
            like=("¿Este asistente puede echarme una mano?",),
        ),
    ),
    # Product vocabulary rather than account data. Narrow enough not to capture "¿en qué categoría
    # has clasificado el cine?", which is about the previous answer and belongs to
    # `about_the_answer`.
    #
    # The one concept whose frame carries no `re.IGNORECASE`, and the only rows here spelled with no
    # accented alternative at all. Both are consistent with a folded sentence and neither is
    # deliberate; they are what this pattern happened to be written as.
    "category_catalogue": (
        Phrasing(r"\b(lista|listado)\s+de\s+categorias\b", like=("Dame la lista de categorías",)),
        Phrasing(
            r"\bdime\s+las\s+categorias(\s+disponibles)?\b",
            like=("Dime las categorías disponibles",),
        ),
        Phrasing(
            r"\bque\s+categorias\s+(entiendes|manejas|usas|hay|tienes|soportas)\b",
            like=("¿Qué categorías manejas?",),
        ),
        Phrasing(
            r"\bque\s+opciones\s+de\s+categoria\s+(hay|tienes)\b",
            like=("¿Qué opciones de categoría hay?",),
        ),
        # "¿Qué categorías puedo consultar?" — asked in the demo, and it fell through to the
        # planner, which answered a question about *which categories exist* with "¿de qué categoría
        # se trata?". A loop, and an insulting one: the list is repository data and was one branch
        # away.
        #
        # The verbs a person reaches for are about *them* doing something with the list rather than
        # the list existing, which is the shape the four rows above all miss.
        Phrasing(
            r"\bcategorias\s+(puedo|se\s+pueden|podria)\s+"
            r"(consultar|usar|ver|filtrar|pedir)\b",
            like=("¿Qué categorías puedo consultar?",),
        ),
        Phrasing(
            r"\bque\s+categorias\s+(puedo|se\s+pueden|podria)\b",
            like=("¿Qué categorías puedo?",),
        ),
        Phrasing(r"\bcategorias\s+(disponibles|existen|hay)\b", like=("¿Qué categorías existen?",)),
        Phrasing(
            r"\bcategorias(?:\s+de\s+gast[op])?\b.{0,110}"
            r"\b(hay|disponibles|seleccionar|utilizar|usar)\b",
            like=("¿Qué categorías de gasto puedo seleccionar?",),
            why="«gast[op]» carries a misspelling of «gasto» somebody typed",
        ),
        Phrasing(
            r"\bcategorias\s+de\s+gast[op]\b.{0,90}\bapp\b.{0,90}\bfiltrar\b",
            like=("¿Qué categorías de gasto tiene la app para filtrar?",),
        ),
        Phrasing(
            r"\b(?:cuales|quales)\s+son\s+las\s+categorias\b",
            like=("¿Cuáles son las categorías?",),
            why="«quales» is a misspelling somebody typed",
        ),
    ),
    # The user says the last answer was wrong. Two shapes: naming the misunderstanding, or
    # restating what they meant.
    #
    # **This and `bare_correction` are two hand-maintained lists of the same idea**, and putting
    # them side by side is the first time that has been visible. They disagree in three ways and
    # none of the three is a design: `correcting` spells «me refer[íi]a» with an accented
    # alternative that no folded sentence can reach and `bare_correction` spells it «referia»;
    # `correcting` has «no era eso» as its own row where `bare_correction` folds it into
    # «no (es|era) eso»; and `bare_correction` requires the object — «no te he preguntado eso» —
    # where `correcting` stops at the verb. They are left exactly as they are, because merging them
    # would move sentences and this table may not. The divergence is the shape of every defect
    # Section B exists to surface, and it is now countable rather than buried in two regexes 17
    # lines apart.
    "correcting": (
        Phrasing(r"no\s+me\s+has\s+entendido", like=("No me has entendido",)),
        Phrasing(r"no\s+es\s+eso", like=("No, no es eso",)),
        Phrasing(r"no\s+era\s+eso", like=("No era eso",)),
        Phrasing(r"eso\s+no\s+es\s+lo\s+que", like=("Eso no es lo que te pregunté",)),
        Phrasing(r"me\s+refer[íi]a", like=("Me refería al mes pasado",)),
        Phrasing(r"me\s+refiero", like=("Del banco me refiero",)),
        Phrasing(r"quer[íi]a\s+decir", like=("Quería decir el año pasado",)),
        Phrasing(r"te\s+has\s+equivocado", like=("Te has equivocado",)),
        Phrasing(r"no\s+te\s+he\s+preguntado", like=("No te he preguntado eso",)),
    ),
    # The noun on its own, and the rejection beside it. The router requires both, so "la respuesta
    # de julio" is not a complaint and "no me convence" alone is not about the answer.
    "answer_noun": (
        Phrasing(
            r"\brespues(?:ta|a)\b",
            like=("Esa respuesta no es correcta",),
            why="«respuesa» is a misspelling somebody typed",
        ),
    ),
    "answer_rejection": (
        Phrasing(
            r"\bno\b.{0,60}\b(?:correcta|adecuada|esperaba|resuelto|convence)\b",
            like=("Esa respuesta no es correcta",),
        ),
        Phrasing(
            r"\b(?:error|revisarla|intentarlo\s+de\s+nuevo)\b",
            like=("Esa respuesta tiene un error",),
        ),
        Phrasing(
            r"\b(?:perdido|repetirla|explicarla|explicarmelo)\b",
            like=("Me he perdido con esa respuesta",),
        ),
    ),
    # A rejection with no replacement in it. These start a correction workflow rather than asking
    # the planner to derive a new query from "no es eso", which contains no financial information
    # at all. Read under `fullmatch`, so a row here is a whole turn and not a phrase inside one —
    # which is why «no te he preguntado eso» carries its object and its twin in `correcting` does
    # not.
    "bare_correction": (
        Phrasing(r"no\s+me\s+has\s+entendido", like=("No me has entendido",)),
        Phrasing(r"no\s+(es|era)\s+eso", like=("No es eso",)),
        Phrasing(
            r"eso\s+no\s+es\s+lo\s+que(\s+te)?\s+pregunte",
            like=("Eso no es lo que te pregunte",),
        ),
        Phrasing(r"te\s+has\s+equivocado", like=("Te has equivocado",)),
        Phrasing(r"no\s+te\s+he\s+preguntado\s+eso", like=("No te he preguntado eso",)),
        Phrasing(r"me\s+(referia|refiero)", like=("Me referia",)),
        Phrasing(r"queria\s+decir", like=("Queria decir",)),
    ),
    # About the previous answer: how it was reached, or what it was filed under. The `por qué` case
    # is narrowed to what *the assistant* did — "¿por qué gasté tanto en junio?" is a question
    # about money, and it is not one this pipeline can answer either, but the refusal it deserves
    # is a different one.
    "about_the_answer": (
        Phrasing(r"qu[ée]\s+consulta", like=("¿Qué consulta has hecho?",)),
        Phrasing(
            r"c[óo]mo\s+(lo\s+)?has\s+(calculado|hecho|sacado)",
            like=("¿Cómo lo has calculado?",),
        ),
        Phrasing(
            r"(?:de\s+)?d(?:[óo]nde|ond)\s+(?:(?:has\s+)?(?:sacado|sacdo|sacao|sacas)"
            r"|(?:sale|viene)(?:\b.{0,35}\b(?:eso|n[úu]mero|cifra|cantidad|dato|respuesta)"
            r"|(?=\s*[?!.]*$)))",
            like=("¿De dónde has sacado eso?", "¿De dónde sale?"),
            why="the lookahead is what lets «¿de dónde sale?» end the sentence there, where "
            "«sale» otherwise needs a noun within 35 characters to name what came from where. "
            "«sacdo», «sacao» and «dond» are misspellings somebody typed",
        ),
        Phrasing(
            r"qu[ée]\s+(?:registros\s+)?has\s+(consultado|mirado)",
            like=("¿Qué registros has consultado?",),
        ),
        Phrasing(
            r"en\s+(?:qu[ée]|qeu)\s+te\s+basas",
            like=("¿En qué te basas?",),
            why="«qeu» is a misspelling somebody typed",
        ),
        Phrasing(r"c[óo]mo\s+has\s+llegado\s+a", like=("¿Cómo has llegado a esa cifra?",)),
        Phrasing(
            r"(por\s+qu[ée]|c[óo]mo)\s+(me\s+)?has\s+"
            r"(mostrado|ense[ñn]ado|clasificado|puesto|contado)",
            like=("¿Por qué me has mostrado eso?",),
            unlike=("¿Por qué gasté tanto en junio?",),
            why="narrowed to what the assistant did, so a question about money that opens the "
            "same way is not swallowed",
        ),
        Phrasing(
            r"en\s+qu[ée]\s+categor[íi]a\s+(?:lo\s+)?(?:has\s+)?"
            r"(?:clasificado|puesto|metido|contado)\b",
            like=("¿En qué categoría has clasificado el cine?",),
        ),
        Phrasing(
            r"no\s+entiendo\s+(el|este|ese)?\s*desglose",
            like=("No entiendo el desglose",),
        ),
        Phrasing(r"qu[ée]\s+has\s+entendido", like=("¿Qué has entendido?",)),
        # Asking whether two things are filed together. This is the shape the real question took —
        # "¿es que los restaurantes pertenecen a la misma categoría que el cine?" — and the first
        # version of this router missed it, so it reached the planner and came back asking which
        # period. A question about the catalogue does not have a period.
        Phrasing(r"misma\s+categor[íi]a", like=("¿Es la misma categoría que el cine?",)),
        Phrasing(
            r"pertenecen?\s+a",
            like=("¿Es que los restaurantes pertenecen a la misma categoría que el cine?",),
        ),
        Phrasing(r"cuenta[n]?\s+como", like=("¿Los restaurantes cuentan como ocio?",)),
        Phrasing(r"es\s+lo\s+mismo", like=("¿Ocio y restaurantes es lo mismo?",)),
        Phrasing(r"est[áa]n?\s+en\s+la\s+misma", like=("¿Están en la misma categoría?",)),
    ),
    # This turn reports a broken conversation rather than correcting a plan field. Read under
    # `fullmatch` inside a frame anchored end to end, so every row here is a whole turn: the point
    # is that nothing else was said. "Esto no funciona porque gasté demasiado" is not this.
    "frustration": (
        Phrasing(r"que\s+te\s+pasa", like=("¿Qué te pasa?",)),
        Phrasing(r"estas\s+bien", like=("¿Estás bien?",)),
        Phrasing(r"por\s+que\s+no\s+me\s+entiendes", like=("¿Por qué no me entiendes?",)),
        Phrasing(r"no\s+me\s+entiendes", like=("No me entiendes",)),
        Phrasing(r"esto\s+no\s+funciona", like=("Esto no funciona",)),
        Phrasing(r"te\s+has\s+quedado\s+pillad[oa]", like=("Te has quedado pillado",)),
    ),
    # Moving money between the holder's own accounts.
    #
    # `mandar` and `enviar` are deliberately absent: they are what a person says about paying
    # somebody, and `payment_router` sees the sentence first anyway. What is here is the vocabulary
    # of moving something from one place to another that is still yours. That a verb is never
    # enough on its own — it needs a destination or a sum, and it must not be a question — is
    # `_moving_money_inside`'s rule and lives there.
    "moving": (
        Phrasing(r"mueve", like=("Mueve 200 € a la hucha",)),
        Phrasing(r"mover", like=("Quiero mover dinero a la hucha",)),
        Phrasing(r"muevo", like=("¿Muevo 50 al fondo?",)),
        Phrasing(r"mueva", like=("Que se mueva 50 a la hucha",)),
        Phrasing(r"pasa", like=("Pasa 100 a la hucha",)),
        Phrasing(r"pasar", like=("Quiero pasar 100 a la hucha",)),
        Phrasing(r"paso", like=("¿Cuánto paso a la hucha cada mes?",)),
        Phrasing(r"traspasa", like=("Traspasa 100 a la hucha",)),
        Phrasing(r"traspasar", like=("Quiero traspasar 100 a la hucha",)),
        Phrasing(r"traspaso", like=("Hazme un traspaso a la hucha",)),
        Phrasing(r"trasvasar", like=("Quiero trasvasar 100 a la hucha",)),
        Phrasing(r"mete", like=("Mete 50 en la hucha",)),
        Phrasing(r"meter", like=("Quiero meter 50 en la hucha",)),
        Phrasing(r"meto", like=("¿Meto 50 en la hucha?",)),
        Phrasing(r"ingresa", like=("Ingresa 50 en la hucha",)),
        Phrasing(r"ingresar", like=("Quiero ingresar 50 en la hucha",)),
        Phrasing(r"guarda", like=("Guarda 50 en la hucha",)),
        Phrasing(r"guardar", like=("Quiero guardar 50 en la hucha",)),
        Phrasing(r"ahorra", like=("Ahorra 50 en la hucha",)),
        Phrasing(r"ahorrar", like=("Quiero ahorrar 50 en la hucha",)),
        # Moving money to *oneself*. `enviar` and `mandar` are what a person says about paying
        # somebody else and are deliberately absent — but the reflexive is the opposite:
        # "enviarme dinero a la cuenta corriente" has exactly one possible destination and it is
        # the holder's own account. Without these the sentence fell through and was answered with
        # that account's balance.
        Phrasing(r"enviarme", like=("Quiero enviarme dinero a la cuenta corriente",)),
        Phrasing(r"mandarme", like=("Quiero mandarme dinero a la cuenta corriente",)),
        Phrasing(r"pasarme", like=("Quiero pasarme dinero a la hucha",)),
        Phrasing(r"traspasarme", like=("Quiero traspasarme dinero a la hucha",)),
        Phrasing(r"transferirme", like=("Quiero transferirme dinero a la hucha",)),
        Phrasing(r"ingresarme", like=("Quiero ingresarme dinero en la hucha",)),
        # The imperative with the pronoun stuck on, which is how it is actually typed. Folded, so
        # the accent is already gone by the time this is matched.
        Phrasing(r"pasame", like=("Pásame 50 al fondo",)),
        Phrasing(r"mandame", like=("Mándame 50 a la hucha",)),
        Phrasing(r"enviame", like=("Envíame 50 a la hucha",)),
        Phrasing(r"muevelo", like=("Muévelo a la hucha",)),
        Phrasing(r"pasalo", like=("Pásalo a la hucha",)),
        Phrasing(r"metelo", like=("Mételo en la hucha",)),
        Phrasing(r"traspasame", like=("Traspásame 50 a la hucha",)),
        Phrasing(r"transfiereme", like=("Transfiéreme 50 a la hucha",)),
        # A transfer between one's own accounts, which the rail is named after. Safe here and not
        # in `payment_router` for the reason the whole recogniser is safe: it also needs one of
        # *this holder's* accounts by name, and a payment to a person names a person.
        Phrasing(r"transferencia", like=("Hazme una transferencia a la hucha",)),
        Phrasing(r"transferir", like=("Me gustaría transferir dinero entre cuentas",)),
        Phrasing(r"transfiere", like=("Transfiere 50 a la hucha",)),
        Phrasing(r"transfiero", like=("¿Transfiero 50 a la hucha?",)),
        Phrasing(
            r"tranferir",
            like=("Quiero tranferir 50 a la hucha",),
            why="what somebody typed, twice, and a typo is not a change of intent",
        ),
        Phrasing(r"tranferirme", like=("Quiero tranferirme 50 a la hucha",)),
    ),
    # What those same verbs mean when the sentence is asking rather than instructing. "¿Cuánto paso
    # a la hucha cada mes?" is a question about a habit and it carries a moving verb and an
    # account, so without this it read as an order to move money — and the answer to a question
    # would have been a preview asking somebody to confirm a payment they never asked for. That is
    # the worst shape a misreading can take here, which is why the exclusion is checked first
    # rather than tuned into the verb list.
    "asking": (
        Phrasing(r"cu[áa]nt[oa]s?", like=("¿Cuánto paso a la hucha cada mes?",)),
        Phrasing(r"qu[ée]", like=("¿Qué muevo a la hucha?",)),
        Phrasing(r"cu[áa]l", like=("¿Cuál es mi hucha?",)),
        Phrasing(r"cu[áa]ndo", like=("¿Cuándo paso dinero a la hucha?",)),
        Phrasing(r"c[óo]mo", like=("¿Cómo paso dinero a la hucha?",)),
        Phrasing(r"d[óo]nde", like=("¿Dónde guardo el dinero?",)),
        Phrasing(r"por\s+qu[ée]", like=("¿Por qué paso dinero a la hucha?",)),
    ),
    # "Entre mis cuentas" names no account and says which kind of movement this is. With a moving
    # verb in front of it the sentence is an instruction — "me gustaría transferir dinero entre
    # cuentas" — and it was reaching the capability guard, which refused it with a sentence about
    # *querying* the transfers. A refusal about reading, to somebody asking to write.
    #
    # One row rather than four, because the determiner is a slot inside one phrase and not four
    # ways of saying a thing.
    "between_accounts": (
        Phrasing(
            r"\bentre\s+(mis|tus|sus|las)?\s*cuentas\b",
            like=("Me gustaría transferir dinero entre cuentas",),
        ),
    ),
    "own_account_relation": (
        Phrasing(
            r"\bde\s+una\s+(?:de\s+)?mis\s+cuentas\s+a\s+otra\b",
            like=("Mueve dinero de una de mis cuentas a otra",),
        ),
        # Observed clipped ending: "mover dinero de una cuenta a otr". Safe as an internal
        # transfer signal only together with `moving`; this rail can never leave the holder's own
        # accounts.
        Phrasing(
            r"\bde\s+una\s+cuenta\s+a\s+otr(?:a)?\b",
            like=("mover dinero de una cuenta a otr",),
        ),
        Phrasing(
            r"\bde\s+una\s+cuenta\s+mia\s+a\s+otra\s+cuenta\b",
            like=("Mueve dinero de una cuenta mia a otra cuenta",),
        ),
        Phrasing(
            r"\bde\s+una\s+cuenta\s+a\s+otra\s+cuenta\b.{0,35}"
            r"\b(?:es\s+mia|tambien\s+es\s+mia|tengo\s+yo)\b",
            like=("Mueve dinero de una cuenta a otra cuenta que tambien es mia",),
        ),
        Phrasing(
            r"\bde\s+una\s+cuenta\s+a\s+otra\b.{0,30}\bambas\s+mias\b",
            like=("Mueve dinero de una cuenta a otra, ambas mias",),
        ),
    ),
    # A correction that supplies the replacement in the same breath, which makes it *not* a bare
    # one. Read only as a veto: `is_bare_correction` uses it to decide that a rejected answer named
    # what it wanted instead.
    "correction_replacement": (
        Phrasing(r"era", like=("No, era el mes pasado",)),
        Phrasing(r"sino", like=("No el mes pasado sino el anterior",)),
        Phrasing(r"me\s+refiero", like=("Me refiero al año pasado",)),
        Phrasing(r"me\s+referia", like=("Me referia al año pasado",)),
        Phrasing(r"queria\s+decir", like=("Queria decir el año pasado",)),
    ),
    # Saying out loud that this turn is an instruction, which is what lets a moving verb survive an
    # interrogative beside it. `quiero` and `necesito` carry an exception each, because "quiero
    # saber cuánto paso a la hucha" is a question about a habit wearing an instruction's opening.
    "action_intent": (
        Phrasing(
            r"quiero(?!\s+saber)",
            like=("Quiero mover 100 a la hucha",),
            unlike=("Quiero saber cuánto paso a la hucha",),
            why="«quiero saber» opens a question, so the verb of wanting is excluded in front of "
            "it rather than the whole row being dropped",
        ),
        Phrasing(
            r"necesito(?!\s+saber)",
            like=("Necesito mover 100 a la hucha",),
            unlike=("Necesito saber cuánto tengo",),
        ),
        Phrasing(r"me\s+gustaria", like=("Me gustaría mover 100 a la hucha",)),
        Phrasing(r"tengo\s+que", like=("Tengo que mover 100 a la hucha",)),
        Phrasing(r"estoy\s+intentando", like=("Estoy intentando mover 100 a la hucha",)),
        Phrasing(r"voy\s+a\s+intentar", like=("Voy a intentar mover 100 a la hucha",)),
    ),
}


def phrasings(concept: str) -> tuple[Phrasing, ...]:
    """Every way this table knows of saying `concept`."""
    return PHRASINGS[concept]


def alternation(concept: str, **slots: str) -> str:
    """The concept's phrasings as one alternation, for a caller that supplies its own frame.

    Returned as a string rather than compiled here, because the frame and the flags are
    `intent_router`'s to write: twenty of the twenty-three are read case-insensitively, three are
    not, and several are anchored or wrapped in a group whose absence would change what matches.
    The call sites use f-strings, so the frame stays visible beside the cascade that owns it.

    `slots` fills `<name>` holes — see the module docstring for why they are angle brackets and not
    `str.format`. A hole nobody fills and a filler that fits no hole are both mistakes, and both
    raise rather than silently producing a pattern that matches the wrong thing.
    """
    joined = "|".join(phrasing.says for phrasing in phrasings(concept))
    for name, value in slots.items():
        hole = f"<{name}>"
        if hole not in joined:
            raise KeyError(f"{concept} has no {hole} to fill")
        joined = joined.replace(hole, value)
    if "<" in joined and ">" in joined:
        unfilled = [phrasing.says for phrasing in phrasings(concept) if "<" in phrasing.says]
        if unfilled:
            raise KeyError(f"{concept} has an unfilled slot: {unfilled}")
    return joined
