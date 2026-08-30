"""What the sentence says outright, and which of it the model may not contradict.

The pipeline asks a model three things at once: whether this turn continues the last one, which
fields it changes, and what the new values are. The first two are judgements and the model is good
at them. The third is not a judgement at all when the user has written the answer down:

    "¿Cuánto he gastado en total en los últimos 3 meses en alquiler?"

emitted `changed: ["categories"]`, so the merge kept the previous window — five months — and the
answer was a real figure about a period nobody asked for. Nothing downstream could tell. The plan
was well formed, the domain accepted it, banking executed it, and the verifier had no rule about
windows because the window it produced was perfectly valid.

The defect is not the regex that was missing. It is that **the model was allowed to decide whether
an explicit fact counted**, and a model that says `changed: []` about a sentence containing "los
últimos 3 meses" has contradicted the user rather than interpreted them.

So this module holds the small set of things a sentence states beyond interpretation, and the
merged plan is made to agree with them. The model still decides everything genuinely ambiguous —
whether "comida" is the supermarket or a restaurant, whether a turn is a correction or a new
question, whether an unfamiliar word looks like a shop. It no longer decides what "3 meses" means.

**Narrow on purpose, like every deterministic reader here.** A fact that is only usually right
would overwrite a correct plan, which is worse than the inheritance bug: the model can be
overruled by the sentence, and there is nothing left to overrule this.

Two facts the review asked for are deliberately *not* enforced here, because refusing is already
the better answer for them and refusing is what happens today:

    direction    `SPENDING_ANSWERED_AS_EVERYTHING` refuses a spending question planned as ALL.
                 Silently rewriting it to EXPENSE would also be the pipeline quietly repairing a
                 prompt injection instead of reporting it, and the refusal is the control that
                 test exists for. **Still true of every sentence that rule can see**: it reads the
                 stem `gast`, and the only thing this module states EXPENSE for is `_LEAVING` —
                 «me cobraron» and «me he dejado», neither of which contains `gast`. The refusal
                 and the reading are disjoint by construction rather than by luck, and `_LEAVING`
                 argues where that boundary is and what it refuses to be widened to.
    categories   `CATEGORY_MISREAD` refuses a plan filtered by something the sentence did not
                 name. *Setting* the filter instead would drop the categories the model read
                 correctly out of words this vocabulary does not know — the subset rule that
                 module argues for, in reverse.

What is left is the window, which nothing checked at all, and the shape of the answer to a
question about people.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Final

from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services import category_words, spanish, window_words
from finguard_orchestrator.domain.services.window_words import Window
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# "¿A qué personas les he hecho Bizum?" does not ask for a total. It asks who, so its answer shape
# is a list of ledger counterparties. Keeping this named separately prevents PERSON from falling
# back to the compatibility-only merchant dimension.
_COUNTERPARTY: Final = GroupBy.COUNTERPARTY
_ASKS_WHO: Final = re.compile(
    r"\ba\s+(?:que\s+(?:personas?|gente|contactos?)|quien(?:es)?)\b"
    r"|\bde\s+que\s+empresa\b"
    r"|\bque\s+empresas?\b"
)
_MERCHANT_BREAKDOWN: Final = re.compile(
    r"\b(?:desglos\w*|separ\w*|agrup\w*)\s+(?:lo\s+)?por\s+"
    r"(?:comercios?|tiendas?|establecimientos?|negocios?)\b"
    r"|\bpor\s+(?:cada\s+)?(?:comercio|tienda|establecimiento|negocio)\b"
)

# A correction that widens rather than narrows. "¿Y en general?" is a real thing to say, and it is
# the one case where a filter disappearing is what the user asked for.
_WIDENS: Final = re.compile(
    r"\ben general\b|\ben total\b|\bsin filtr|\bde todo\b|\btodas? las categor"
)

# A turn that rejects the previous answer and supplies the replacement in the same breath: "no, los
# últimos 12 meses no; durante todo el año 2025". The intent router's `_CORRECTING` deliberately
# does not cover this — it looks for a rejection *without* a replacement — so the sentence is
# planned as an ordinary follow-up, which is right. What it also needs is for the fields it did not
# mention to survive, and that is what this flag is for.
_REJECTS: Final = re.compile(r"^(no|mejor|en realidad|que va)\b.+|\bsino\b|\bmas bien\b")

# A question about *when*, not about how much. Two of them in one reported conversation:
#
#     "¿Cuándo me suelen ingresar la nómina?"        → 17.383,97 €
#     "¿Cuándo suelo ir al supermercado a comprar?"  → 3.508,91 €
#
# Both figures were correct and neither was an answer. The dates were on screen underneath, in the
# movement list — the question had been answered and the headline said something else.
#
# "Cuánto" is excluded by the word boundary rather than by luck: they differ by one letter and the
# sentence is folded before it gets here, so `cuando` cannot match inside `cuanto`.
_ASKS_WHEN: Final = re.compile(
    r"\bcuando\b|\bcada\s+cuanto\b|\bque\s+dias?\b|\bcon\s+que\s+frecuencia\b"
    r"|\bcada\s+cuantos\s+dias\b|\bsuelo\s+ir\b|\ben\s+que\s+fechas?\b"
)

_RECURRENCE: Final = re.compile(
    r"\b(?:gastos?|pagos?|cargos?|recibos?|cobros?|cuotas?)\s+"
    r"(?:fijos?|recurrentes?|periodicos?)\b"
    r"|\b(?:que|cuales)\b.{0,35}\b(?:gastos?|pagos?|cargos?|recibos?)\b.{0,25}"
    r"\b(?:se\s+repiten|son\s+recurrentes?|son\s+periodicos?)\b"
    r"|\b(?:suscripciones?|alquiler(?:es)?|recibos?)\b.{0,35}"
    r"\b(?:cada|todos?)\s+(?:los\s+)?mes(?:es)?\b"
    r"|\b(?:recibos?\s+domiciliados?|domiciliaciones?|adeudos?\s+domiciliados?)\b"
    r"|\b(?:que|cuales|muestra(?:me)?|ensena(?:me)?)\b.{0,25}"
    r"\b(?:mis\s+)?(?:recibos?|adeudos?)\b"
    # "¿Qué suscripciones tengo?" used to be here, and it is answered better one recogniser up:
    # `asks_how_many_payees` names them and leads with how many there are, where this discovers a
    # pattern and leads with money. Two recognisers claiming one sentence is a tie decided by the
    # order of a cascade, and the measurement that counts those exists so it stays at twelve.
    r"|\b(?:que|cuales)\b.{0,25}\b(?:pagos?|cargos?)\b.{0,25}"
    r"\btodos?\s+los\s+meses\b"
)
_DIRECT_DEBIT_QUERY: Final = re.compile(
    r"\b(?:recibos?\s+domiciliados?|domiciliaciones?|adeudos?\s+domiciliados?)\b"
    r"|\b(?:mis|los|estos|esos)\s+recibos?\b"
)

_AVERAGE: Final = re.compile(r"\b(media|medio|promedio)\b|\bimporte\s+medio\b|\bticket\s+medio\b")
_CUMULATIVE: Final = re.compile(
    r"\b(?:gasto|ingreso|importe|total)\s+acumulad[oa]\b"
    r"|\bacumulad[oa]\s+(?:por|a\s+lo\s+largo\s+de|mes\s+a\s+mes|semana\s+a\s+semana)\b"
    r"|\bcuanto\s+llevaba\s+(?:gastado|ingresado|acumulado)\b"
    r"|\bcomo\s+(?:iba|fue|ha\s+ido)\s+(?:mi\s+)?(?:gasto|ingreso)\b"
)
# **The number has to agree, because three of these nouns are also verbs.** The interrogative and
# the noun each carried their own optional `s`, so a singular "cuánto" beside a singular "pago"
# matched — and "¿cuánto pago de alquiler?" came back "3 movimientos en alquiler el mes pasado".
# Asked how much, told how many, with a real figure of the wrong kind on it. The same held for
# "cuánto ingreso" and "cuánto cargo": in each, the noun the count rule is looking for is spelled
# exactly like the first person of the verb that asks for an amount.
#
# So the plural is required on both sides. "Cuántos pagos" counts, "cuánto pago" does not, and
# nothing else has to be enumerated for it: the agreement *is* the rule. `veces` needs no partner
# spelling because it has no singular anybody says.
_COUNT: Final = re.compile(
    r"\bcuant[oa]s\s+(?:veces|movimientos|operaciones|compras|pagos|cargos|ingresos)\b"
    r"|\b(?:numero|cantidad)\s+de\s+"
    r"(?:movimientos?|operaciones?|compras?|pagos?|cargos?|ingresos?)\b"
    r"|\b(?:mas|menos)\s+(?:movimientos?|operaciones?|compras?|pagos?|cargos?)\b"
)
_TYPICAL_AMOUNT: Final = re.compile(
    r"\b(?:suelo|solemos)\s+(?:gast|pag|compr|cobr)\w*\b"
    r"|\b(?:gast|pag|compr|cobr)\w*\s+(?:normalmente|habitualmente)\b"
)
_PER_MOVEMENT_AVERAGE: Final = re.compile(
    r"\b(?:media|promedio|importe\s+medio|ticket\s+medio)\s+"
    r"(?:por|de\s+cada)\s+(?:cada\s+)?(?:movimiento|operacion|compra|pago|cargo|ingreso)s?\b"
)
_TEMPORAL_GROUPS: Final[tuple[tuple[GroupBy, re.Pattern[str]], ...]] = (
    (
        GroupBy.DAY,
        re.compile(
            r"\b(?:por|al|cada)\s+dia\b|\b(?:media|promedio)\s+diari[oa]\b"
            r"|\b(?:gasto|importe|coste)\s+diari[oa]\s+medi[oa]\b"
            r"|\b(?:y\s+)?(?:el\s+)?diari[oa]\b"
        ),
    ),
    (
        GroupBy.WEEK,
        re.compile(
            r"\b(?:por|a\s+la|cada)\s+semana\b|\b(?:media|promedio)\s+semanal\b"
            r"|\b(?:gasto|importe|coste)\s+semanal\s+medio\b"
            r"|\b(?:y\s+)?(?:el\s+)?(?:semanal|semanl|semnal)\b"
        ),
    ),
    (
        GroupBy.MONTH,
        re.compile(
            r"\b(?:por|al|cada)\s+mes\b|\bmes\s+a\s+mes\b|"
            r"\b(?:media|promedio|gasto\s+medio|importe\s+medio|coste\s+medio)\s+mensual\b"
            r"|\b(?:gasto|importe|coste)\s+mensual\s+medio\b|\bmensualmente\b"
            r"|\b(?:movimientos?|operaciones?|compras?|pagos?|cargos?)\s+mensual(?:es)?\b"
            r"|\b(?:y\s+)?(?:el\s+)?(?:mensual|mensula|mesnual)\b"
        ),
    ),
)
_TOP_CATEGORY: Final = re.compile(
    r"\b(?:en\s+)?qu[ée]\s+categor[íi]a\b.{0,55}\b(?:gast|pag|compr)\w*\b.{0,20}\bm[áa]s\b"
    r"|\bqu[ée]\s+categor[íi]a\s+es\s+la\s+que\b.{0,45}\bm[áa]s\b"
    r"|\bd[oó]nde\b.{0,35}\b(?:gast|pag|compr)\w*\b.{0,15}\bm[áa]s\b"
)
_ACCOUNT_BREAKDOWN: Final = re.compile(
    r"\bdesglos\w*\s+por\s+(?:cada\s+)?cuentas?\b"
    r"|\b(?:por|separad[oa]\s+por)\s+cada\s+cuenta\b"
    r"|\b(?:ingresos?|income|gastos?|movimientos?)\s+de\s+cada\s+cuenta\b"
    r"|\bseparad[oa]\s+por\s+cuentas?\b"
    r"|\bsepar\w*\s+por\s+cuentas?\b"
    r"|\b(?:en|a)\s+(?:que|cual)\s+cuenta\b"
)
_COMPARISON_REFERENCE: Final = re.compile(
    r"\bcu[áa]l\s+de\s+(?:las|estas|esas)\s+dos\b.{0,30}"
    r"\b(?:mayor|menor|m[áa]s\s+alt[oa]|m[áa]s\s+baj[oa])\b"
    r"|\bcu[áa]l\s+(?:es|fue)\s+(?:la\s+)?(?:mayor|menor)\s+de\s+las\s+dos\b"
)
_COMPARES: Final = re.compile(
    r"\bcompara|\bcomparacion\b|\bfrente\s+a\b|\bpor\s+separado\b|\buna\s+y\s+otra\b"
    r"|\bentre\b|\bcual\b.{0,35}\b(?:mayor|menor|mas\s+alt[oa]|mas\s+baj[oa])\b"
    r"|\bmas\b.{0,45}\b(?:o|que)\b"
)
_INCOME: Final = re.compile(
    r"\bingres(?:ar|amos|o|os|e|ado|aron)\b|\bcobr(?:ar|amos|e|ado|aron|os?)\b"
    r"|\brecibi(?:do)?\b"
    r"|\bincome\b|\bnomina\b|\bsueldo\b|\bsalario\b|\bme\s+(?:ha\s+)?entrado\b"
)
_EXPENSE: Final = re.compile(r"\bgast|\bpagu|\bcompr|\bcargo|\bme\s+cobr")
# `_EXPENSE` **vetoes** an income reading; `_LEAVING` **asserts** a spending one. Two patterns
# because the two powers are not worth the same. A veto costs a `None`: the model keeps whichever
# direction it chose and `plan_verifier` still gets to look at it. An assertion overwrites a field
# the model stated, on every sentence that matches, and nothing downstream re-examines it.
#
# `gast|pagu|compr|cargo` are good enough to veto — they say the sentence is not purely about money
# arriving — and deliberately not good enough to assert. They are topic words: «¿en qué categoría
# gasto más?» is about spending without stating a direction to impose. And `\bgast` is the exact
# trigger of `plan_verifier._spending_answered_as_everything`, so a reader that forced EXPENSE on
# every `gast` sentence would repair that disagreement before the verifier could ever see it —
# which is the refusal this module's docstring makes, and would leave the one guard written
# against the live prompt injection unreachable on the sentences it was written for.
#
# What is here instead is the first-person constructions that can only mean money left this
# account: «me cobraron» — somebody charged me — and «me he dejado» — what I dropped on something.
# Neither contains `gast`, so this and that rule cannot collide on any sentence either could be
# about. The one overlap is a compound «¿cuánto me he dejado y cuánto gasté?», which states two
# questions and belongs to the clarification path rather than to either rule.
#
# Refused, all for one reason — no dataset says what they should read as, so adding them would be
# vocabulary nobody measured: «me clavaron», «me crujieron», and «se me ha ido», which the model
# already gets right unaided and which would be this module overruling it on a guess.
_LEAVING: Final = re.compile(r"\bme\s+cobr|\bme\s+he\s+dejado\b")
_EXCLUSION_TAIL: Final = re.compile(
    r"\b(?:sin(?:\s+contar|\s+incluir)?|quitando|excepto|salvo|menos"
    r"|no\s+(?:contamos|cuentes|incluimos|incluyas|metas))\s+(?P<tail>.+)$"
)
_TRAILING_WINDOW: Final = re.compile(
    r"\s+(?:durante\s+|en\s+)?(?:este|esta|el|la|los|las)\s+"
    r"(?:mes|ano|semana|dia|ultim\w+).*$"
)
_NOT_A_MERCHANT: Final = frozenset(
    {
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
        # `(?:\s+contar|\s+incluir)?` in `_EXCLUSION_TAIL` backtracks to nothing whenever the verb
        # ends the sentence, so «sin contar» left "contar" as the name of a shop to exclude — and
        # «sin contar el alquiler» is the phrase the refusal message itself recommends to users.
        #
        # Listed here rather than anchored to the start of the tail because the membership test
        # below needs *every* word to be one of these: «sin meter Cepsa» has no `sin meter`
        # alternative in `_EXCLUSION_TAIL`, so its tail is "meter cepsa", and Cepsa is a real shop.
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
    }
)
# A merchant name is a name. These openings announce a quantity, or point back at something said
# earlier, so what follows one is an exclusion nobody can execute rather than a shop — «sin contar
# algunas cosas», «si no contamos lo que ya sabes», «quitando lo de siempre».
#
# Determiners are deliberately absent. "el" and "la" open real shop names — «sin El Corte Inglés»,
# «sin contar La Sirena» — and rejecting those would break the case this reader exists for.
_VAGUE_TAIL: Final = re.compile(
    r"^(?:algun[oa]s?|algo|un[oa]s|ciert[oa]s?|varias|varios|demas|resto"
    r"|lo\s+(?:que|de|mismo|demas)|los\s+que|las?\s+que"
    r"|es[oa]s?|aquell[oa]s?|siempre)\b"
)


def _excluded_merchants(sentence: str, excluded_categories: tuple[str, ...]) -> tuple[str, ...]:
    """Read a short free-text name after an exclusion marker.

    Merchant names are data, so there is no catalogue to match. This therefore accepts only a
    compact tail and leaves longer clauses to the planner. Category exclusions win when present;
    otherwise "sin Mercadona" is the unambiguous free-text case this path exists for.

    A tail that names no shop is not a merchant exclusion, and saying so here is what keeps
    `capability_guard`'s CATEGORY_EXCLUSION family honest: the guard exempts itself whenever this
    returns something, so anything vague accepted here becomes an unrefused query carrying a filter
    that matches nothing. See `_NOT_A_MERCHANT` and `_VAGUE_TAIL`.
    """
    if excluded_categories:
        return ()
    folded = spanish.folded(sentence)
    found = _EXCLUSION_TAIL.search(folded)
    if found is None:
        return ()
    tail = _TRAILING_WINDOW.sub("", found.group("tail"))
    tail = re.sub(
        r"^(?:(?:los?|las?)\s+)?(?:movimientos?\s+(?:de|en)|gastos?\s+(?:de|en)"
        r"|compras?\s+en|comercios?|tiendas?)\s+",
        "",
        tail,
    )
    tail = re.sub(r"\s+por\s+favor$", "", tail).strip(" ¿?¡!.,;:")
    if not tail or len(tail) > 140 or len(tail.split()) > 5:
        return ()
    if set(tail.split()) <= _NOT_A_MERCHANT:
        return ()
    if _VAGUE_TAIL.match(tail):
        return ()
    return (tail,)


def excluded_merchants_in(sentence: str) -> tuple[str, ...]:
    """Grounded free-text merchant exclusions, for guards as well as plan enrichment.

    The capability guard runs before a plan exists. Exposing the same narrow reader used by
    :func:`read` lets it distinguish ``excepto Carrefour`` from an exclusion too vague to execute,
    without maintaining a second regex that will drift from the value eventually applied.
    """
    return _excluded_merchants(sentence, category_words.precisely_excluded_in(sentence))


def asks_when(sentence: str) -> bool:
    """Whether the sentence asks *when* something happens rather than how much of it there was.

    Separate from `read` because it changes the sentence the assistant writes rather than the query
    it runs. The plan is the same either way — the same movements over the same window — and only
    the headline is wrong.
    """
    return bool(_ASKS_WHEN.search(spanish.folded(sentence)))


def asks_recurrence(sentence: str) -> bool:
    """Whether this asks to discover periodic commitments rather than total a category."""
    folded = spanish.folded(sentence)
    if _RECURRENCE.search(folded) is None:
        return False
    # "Cuánto pago en recibos domiciliados" names the same banking rail but asks for a sum, not
    # pattern discovery. The direct-debit fact is retained separately and applied to that plan.
    asks_for_aggregate = bool(
        _AVERAGE.search(folded)
        or _COUNT.search(folded)
        or re.search(r"\b(?:cuanto|importe|total|suma)\b", folded)
    )
    return not asks_for_aggregate


def asks_direct_debits(sentence: str) -> bool:
    """Whether the user named bank direct debits, not merely periodic card charges."""
    return bool(_DIRECT_DEBIT_QUERY.search(spanish.folded(sentence)))


# "How many X do I have" and "which X do I have", where X is a kind of spending. The thing being
# counted is the payee, not the charge: somebody with Netflix, Spotify, a gym and Steam has four
# subscriptions and thirty-six movements, and answering the second is a real figure for a question
# nobody asked. It was reported exactly that way — "yo pensaba que tenía solo netflix altafit spoti
# y steam".
_HOW_MANY_PAYEES: Final = re.compile(r"\b(?:cuantas|cuantos|cuales|que)\b(?![a-z])")

# The verbs that make it a question about *having* rather than about spending. Without one of these
# "¿qué gasté en suscripciones?" would be read as a list of payees.
_HAS_THEM: Final = re.compile(
    r"\b(?:tengo|tienes|tenemos|hay|mantengo|pago|pagas|activas?|activos?"
    # The possessive is one of these too: "¿cuáles son mis suscripciones?" asks exactly this and
    # names no verb of having at all.
    r"|mis|contratadas?|contratados?|dadas?\s+de\s+alta)\b"
)

# Words naming the charge rather than the payee. "¿Cuántos pagos tengo en suscripciones?" is asking
# how many times, which is COUNT and already answered.
#
# The rails belong here and were missing. "¿Cuántos bizums tengo?" is how many times, and it came
# back as how many *people* this holder has ever sent one to, over twelve months instead of the
# window the conversation was in — a real figure about something else, in the middle of an episode
# whose whole subject is that "tengo" counts.
#
# Only the rails are added, and "domiciliaciones" and "suscripciones" deliberately are not: those
# name the arrangement at least as often as the charge, and a question about how many of *them* you
# have is the payee count this path exists for. A bizum is never an arrangement.
#
# Exported, because the same nouns decide the same thing in the query handler's count follow-up and
# were kept there by hand. They stayed level only because one change happened to touch both: the
# rails were missing from each, and each was found separately.
CHARGE_NOUNS: Final = (
    r"movimientos?|veces|compras?|pagos?|cargos?|recibos?|cobros?|operaciones?|transacciones?"
    r"|bi[xz]ums?|transferencias?|traspasos?"
)
_THE_CHARGE_NOT_THE_PAYEE: Final = re.compile(rf"\b(?:{CHARGE_NOUNS})\b")

# Asking for money, which is never this however it is phrased.
_ASKS_AN_AMOUNT: Final = re.compile(r"\b(?:cuanto|importe|total|suma|gaste|gastado|gasto|pague)\b")


def asks_how_many_payees(sentence: str) -> bool:
    """Whether this counts the distinct payees of a kind of spending rather than its movements.

    All four conditions, because each one alone lets a different question through: the interrogative
    without the possession verb reads "¿qué gasté en suscripciones?" as a list; the possession verb
    without the interrogative reads "¿cuánto pago en suscripciones?"; and either without the two
    exclusions reads "¿cuántos pagos tengo?" — which really is a count of movements and really is
    already answered.

    The category itself is not checked here. This says what shape the question is; which kind of
    spending it is about is `category_words`' job, and a sentence naming none of them cannot become
    this plan.
    """
    folded = spanish.folded(sentence)
    if _THE_CHARGE_NOT_THE_PAYEE.search(folded) or _ASKS_AN_AMOUNT.search(folded):
        return False
    return bool(_HOW_MANY_PAYEES.search(folded) and _HAS_THEM.search(folded))


@dataclass(frozen=True)
class ExplicitFacts:
    """What this turn states for certain. Every field is None when the sentence did not say."""

    window: Window | None = None
    group_by: GroupBy | None = None
    metric: Metric | None = None
    direction: Direction | None = None
    compared_categories: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    excluded_merchants: tuple[str, ...] = ()
    # Whether the sentence named a category at all. Not *which* — that is the verifier's business.
    # What this answers is "did the user touch the filter", which is what decides whether a
    # correction about the period is allowed to drop it.
    names_a_category: bool = False
    widens: bool = False
    corrects: bool = False
    # Whether the question is about *when* rather than how much. It changes the answer rather than
    # the plan, so `enforce` does nothing with it and the writer reads it.
    asks_when: bool = False
    asks_top_category: bool = False
    asks_account_breakdown: bool = False
    asks_comparison_reference: bool = False
    asks_direct_debits: bool = False


def _direction(folded: str, *, says_income: bool, says_expense: bool) -> Direction | None:
    r"""Which way the money moved, or no opinion — the specific reading asked first.

    The order is the whole rule. «me cobraron» matches `_INCOME` on `cobraron` and `_EXPENSE` on
    `me cobr`, so **both flags are true**, and any rule phrased as "X and not Y" answers None for
    it whichever way round X and Y are put. The symmetric version of the line this replaced is not
    a fix for it; it is the same `None` reached from the other side.

    What separates the two readings is not which pattern fired but which one read more of the
    sentence. `\bme\s+cobr` names the pronoun that reverses the verb and `\bcobr…` does not, so
    «cobré» is money arriving and «me cobraron» is money leaving on the strength of two characters
    the general pattern cannot see. Hence: consult the specific pattern, and only then the pair.

    The second clause is the old line, unchanged, and its `not says_expense` still does exactly the
    work it always did — «compara mis ingresos y gastos» states both directions and gets None,
    because a reader with no way to tell which was meant has no business picking one.
    """
    if _LEAVING.search(folded):
        return Direction.EXPENSE
    return Direction.INCOME if says_income and not says_expense else None


def read(sentence: str, *, today: date | None = None) -> ExplicitFacts:
    """The facts this sentence states outright.

    `today` is what "el año pasado" is relative to. It travels in rather than being read from a
    clock, like everywhere else in this pipeline, so the evaluation can replay a turn.
    """
    folded = spanish.folded(sentence)
    named = category_words.precisely_named_in(sentence)
    excluded_categories = category_words.precisely_excluded_in(sentence)
    comparison = bool(_COMPARES.search(folded)) and len(named) >= 2
    says_income = bool(_INCOME.search(folded)) or "SALARY" in named
    says_expense = bool(_EXPENSE.search(folded))
    temporal_group = next(
        (group for group, pattern in _TEMPORAL_GROUPS if pattern.search(folded)), None
    )
    asks_top_category = bool(_TOP_CATEGORY.search(folded))
    asks_account_breakdown = bool(_ACCOUNT_BREAKDOWN.search(folded))
    asks_merchant_breakdown = bool(_MERCHANT_BREAKDOWN.search(folded))
    asks_average = bool(_AVERAGE.search(folded)) or (
        temporal_group is not None and bool(_TYPICAL_AMOUNT.search(folded))
    )
    asks_count = bool(_COUNT.search(folded))
    asks_cumulative = bool(_CUMULATIVE.search(folded))
    per_movement = bool(_PER_MOVEMENT_AVERAGE.search(folded))
    window = window_words.window_in(sentence, today=today)
    # "anual ... por mes" gives both the horizon and the divisor. It means the trailing twelve
    # months unless a more precise year/window was written, in which case that explicit window won
    # above. This is a duration, not a forecast.
    if window is None and asks_average and re.search(r"\banual(?:es)?\b", folded):
        window = RelativePeriod(period=Period.LAST_N_MONTHS, n=12)
    return ExplicitFacts(
        window=window,
        group_by=(
            _COUNTERPARTY
            if _ASKS_WHO.search(folded) or asks_merchant_breakdown
            else (
                GroupBy.ACCOUNT
                if asks_account_breakdown
                else (
                    GroupBy.CATEGORY
                    if comparison or asks_top_category
                    else (temporal_group or (GroupBy.MONTH if asks_cumulative else None))
                )
            )
        ),
        metric=(
            Metric.CUMULATIVE
            if asks_cumulative
            else (
                Metric.RECURRENCE
                if asks_recurrence(sentence)
                else (
                    Metric.FREQUENCY
                    if asks_when(sentence)
                    else (
                        Metric.AVERAGE
                        if asks_average and (per_movement or temporal_group is None)
                        else (
                            Metric.PERIOD_AVERAGE
                            if asks_average
                            else (Metric.COUNT if asks_count else None)
                        )
                    )
                )
            )
        ),
        direction=_direction(folded, says_income=says_income, says_expense=says_expense),
        compared_categories=named if comparison else (),
        excluded_categories=excluded_categories,
        excluded_merchants=excluded_merchants_in(sentence),
        names_a_category=bool(named),
        widens=bool(_WIDENS.search(folded)),
        corrects=bool(_REJECTS.search(folded.strip(" ¿?¡!.,"))),
        asks_when=asks_when(sentence),
        asks_top_category=asks_top_category,
        asks_account_breakdown=asks_account_breakdown,
        asks_comparison_reference=bool(_COMPARISON_REFERENCE.search(folded)),
        asks_direct_debits=asks_direct_debits(sentence),
    )


def reshaped[P: FinancialPlan | FinancialPlanDraft](
    plan: P, *, group_by: GroupBy, metric: Metric
) -> P:
    """`plan` under this grouping and this metric, or unchanged where the domain refuses the pair.

    **A grouping and a metric are decided together or not at all.** Setting one and inheriting the
    other builds a shape nobody asked for, and the domain is right to refuse it: `PERIOD_AVERAGE`
    and `CUMULATIVE` both spend the plan's only grouping axis on their own calendar buckets, so
    either one beside a merchant grouping is a request for a cross-tabulation the schema cannot
    hold. It takes no contradictory sentence to reach — after "¿cuál es mi gasto medio mensual?"
    the follow-up "esos cinco" resets the grouping to `NONE` while the average is inherited, and
    the two arrive one at a time from two different turns.

    `group_limit` is a rider on `group_by` rather than a value of its own: "los 5 comercios donde
    más gasto" limits *that* grouping, and the domain refuses a limit beside a temporal one. So it
    travels with the field it qualifies, exactly as the window's two fields already do — offered
    first with the limit, then without it.

    Which shape the domain accepts is the domain's to say, which is why this offers rather than
    decides. Restating "PERIOD_AVERAGE needs a temporal grouping" here would be a second copy of
    an invariant to keep level with the first, and this file's whole subject is readers that drift.

    Unchanged is the answer of last resort, and `enforce` argues why it is the right one.
    """
    for group_limit in (plan.group_limit, None):
        try:
            return replace(plan, group_by=group_by, metric=metric, group_limit=group_limit)
        except InvalidValueError:
            continue
    return plan


def enforce[T: FinancialPlan | FinancialPlanDraft | TopMovements](
    plan: T, facts: ExplicitFacts
) -> T:
    """The plan, agreeing with what the sentence stated.

    Applied after the merge and before the verifier, so a field the user wrote down survives both
    the model's opinion about whether it changed and the previous plan it would otherwise be
    inherited from.

    A ranking takes the window and not the grouping: `TopMovements` has none, and "a qué personas"
    is not a question about one movement.

    **This is total: it returns a plan for every sentence, and never raises.** It used to raise,
    and «gasto medio mensual por comercio» is the sentence that showed it. The reader takes both
    halves of that sentence — a per-period average, and a grouping by shop — and the domain refuses
    them together, because `PERIOD_AVERAGE` spends the plan's only grouping axis on its own buckets
    and a question needing two axes has no representation here. Nothing about the Spanish is
    confused; it is the schema that cannot hold the answer.

    Raising was wrong wherever it happened. Twenty of the twenty-five call sites are deterministic
    recognisers whose contract on failure is "return None and let the planner try", so an
    `InvalidValueError` from here does not become a refusal — it becomes a 422 on a sentence
    somebody typed, from a layer whose whole job is to make the plan agree with them.

    What happens when the sentence and the plan cannot both be satisfied is argued at the end.
    """
    if facts.window is not None:
        # As a pair, never one at a time: a plan carries exactly one of the two, so setting a range
        # while leaving an inherited period behind builds something the domain refuses outright.
        plan = replace(
            plan,
            period=facts.window if isinstance(facts.window, RelativePeriod) else None,
            date_range=facts.window if isinstance(facts.window, AbsoluteRange) else None,
        )
    plan = enforce_exclusions(plan, facts)
    if isinstance(plan, FinancialPlan | FinancialPlanDraft):
        if facts.asks_direct_debits:
            plan = replace(plan, transaction_types=(TransactionType.DIRECT_DEBIT,))
        if facts.direction is not None:
            plan = replace(plan, direction=facts.direction)
        if facts.compared_categories:
            plan = replace(
                plan,
                categories=tuple(
                    category
                    for category in facts.compared_categories
                    if category not in plan.excluded_categories
                ),
            )
    if (facts.group_by is not None or facts.metric is not None) and isinstance(
        plan, FinancialPlan | FinancialPlanDraft
    ):
        return reshaped(
            plan,
            group_by=plan.group_by if facts.group_by is None else facts.group_by,
            metric=plan.metric if facts.metric is None else facts.metric,
        )
    # Reached when the sentence stated no shape at all, when the plan is a ranking that has none —
    # and, the case this is written for, when the domain refused every shape offered above. Then
    # there is no plan that agrees with the sentence and nothing here to enforce, so **the plan's
    # own grouping and metric stand, together.**
    #
    # The alternative is to keep the half of the pair that fits and drop the other, and that is
    # the one outcome worth refusing outright. After "¿cuál es mi gasto medio mensual?" → 850 €/mes,
    # the follow-up "¿y por comercio?" would keep the grouping, drop `PERIOD_AVERAGE` to a total,
    # and answer with per-shop *totals over the window* — a real figure, correctly computed, for a
    # question nobody asked, and indistinguishable from the right answer to everything downstream.
    # `plan_verifier` has no rule that compares the sentence's grouping to the plan's, so nothing
    # would catch it. Leaving the pair alone answers the previous question again instead, which is
    # visibly unhelpful rather than quietly wrong — and `answer_writer` describes the plan, so the
    # headline still says "gasto medio mensual" and the user can see they were not split by shop.
    #
    # Which half is "stated more strongly" is not a question the sentence answers, so ranking them
    # would be a guess wearing this module's authority — and this module exists precisely because
    # the model was allowed to guess about things the user had written down.
    #
    # The cost is real and is not hidden: a fact the sentence stated is ignored. It is bounded by
    # who reaches here. A sentence that states the whole forbidden pair by itself is a capability
    # the domain does not have, and is refused before a plan exists — `capability_guard`'s AVERAGE
    # family already refuses every `PERIOD_AVERAGE` one, and `_cumulative_plan` declines the
    # `CUMULATIVE` ones rather than building them. What is left here is the other case: one half
    # inherited from an earlier turn, where the plan being kept came from a reader that saw the
    # whole sentence, and the honest report is "I have not changed the question" rather than a
    # number about a different one.
    return plan


def enforce_exclusions[T: FinancialPlan | FinancialPlanDraft | TopMovements](
    plan: T, facts: ExplicitFacts
) -> T:
    """Apply only negative-filter facts to a plan already built deterministically."""
    if facts.widens:
        plan = replace(plan, excluded_categories=(), excluded_merchants=())
    if facts.excluded_categories:
        excluded = tuple(dict.fromkeys((*plan.excluded_categories, *facts.excluded_categories)))
        plan = replace(
            plan,
            categories=tuple(category for category in plan.categories if category not in excluded),
            excluded_categories=excluded,
        )
    if facts.excluded_merchants:
        # Stripped as well as folded, because `check_filters` compares the two lists that way. A
        # plan carrying " Mercadona " kept it while the exclusion was added beside it, and the
        # invariant that refuses a name on both lists then rejected a plan this had just built.
        folded_excluded = {merchant.strip().casefold() for merchant in facts.excluded_merchants}
        plan = replace(
            plan,
            merchants=tuple(
                merchant
                for merchant in plan.merchants
                if merchant.strip().casefold() not in folded_excluded
            ),
            excluded_merchants=tuple(
                dict.fromkeys((*plan.excluded_merchants, *facts.excluded_merchants))
            ),
        )
    return plan


def without_the_inherited_category(
    plan: FinancialPlan, previous: FinancialPlan | None, facts: ExplicitFacts
) -> FinancialPlan | None:
    """The same query without a category the user never named, or None to leave it alone.

    From the report. Five turns after a question about health, and through a clarification loop
    that answered nothing:

        "¿Qué gastos tengo históricamente en el Mercadona?"
        → "No he encontrado movimientos en Mercadona en los últimos 3 meses."
        → the plan was `expense, health, Mercadona`

    `health` came from a question five turns earlier. The user then pressed "buscar sin categoría"
    and got their answer, so the machinery was right and the work was theirs.

    **Only on an empty result, and only when the sentence introduced a shop.** Both conditions
    carry their weight. Dropping an inherited category whenever the sentence does not name one
    would break the ordinary follow-up — "¿y la semana pasada?" after a question about fuel keeps
    the fuel, and answering it about everything would be a different question. Introducing a
    merchant is what makes the pair suspicious: a shop the previous question did not have, and
    nothing found, is the shape of two filters that do not belong together.

    Nothing is guessed. This is the button the user pressed, pressed for them.
    """
    if facts.names_a_category or not plan.categories:
        return None
    introduced_a_shop = bool(plan.merchants) and not (previous and previous.merchants)
    if not introduced_a_shop:
        return None
    return replace(plan, categories=())


def restored_filters(
    plan: FinancialPlan, previous: FinancialPlan, facts: ExplicitFacts
) -> FinancialPlan:
    """The correction's plan, with the filters the correction never mentioned put back.

    "No, los últimos 12 meses no; durante todo el año 2025" is about the window. It came back with
    the window unchanged *and* the restaurant filter gone, so a question about restaurants in one
    year was answered as every expense in another. Two failures, and this is the second: the model
    emitted `categories: []` and the merge had no way to tell "the filter is gone" from "I did not
    copy it", which is the ambiguity `plan_merger` names in its own docstring.

    A correction patches. Widening is still sayable and still honoured — "¿y en general?" clears
    the filter, because that sentence is about the filter.
    """
    if facts.widens or facts.names_a_category:
        return plan
    return replace(
        plan,
        categories=plan.categories or previous.categories,
        excluded_categories=plan.excluded_categories or previous.excluded_categories,
        merchants=plan.merchants or previous.merchants,
        merchant_terms=plan.merchant_terms or previous.merchant_terms,
        excluded_merchants=plan.excluded_merchants or previous.excluded_merchants,
    )
