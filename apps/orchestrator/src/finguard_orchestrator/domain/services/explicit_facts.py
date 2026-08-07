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
                 test exists for.
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

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.services import category_words, spanish, window_words
from finguard_orchestrator.domain.services.window_words import Window
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# "¿A qué personas les he hecho Bizum?" does not ask for a total. It asks who, and the answer shape
# is a list of counterparties — which the domain reaches through MERCHANT, because that is the
# column the ledger's counterparty names are in today.
#
# ponytail: MERCHANT is the counterparty column under another name. It is right today because a
# Bizum to Elena stores "Elena" there, and wrong as vocabulary — a person is not a shop. Upgrade
# path is `counterparty_name` and `counterparty_type` in banking plus `GroupBy.COUNTERPARTY` here,
# at which point this constant changes and nothing else does.
_COUNTERPARTY: Final = GroupBy.MERCHANT
_ASKS_WHO: Final = re.compile(
    r"\ba\s+(?:que\s+(?:personas?|gente|contactos?)|quien(?:es)?)\b"
    r"|\bde\s+que\s+empresa\b"
    r"|\bque\s+empresas?\b"
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

_AVERAGE: Final = re.compile(r"\b(media|medio|promedio)\b|\bimporte\s+medio\b|\bticket\s+medio\b")
_PER_MOVEMENT_AVERAGE: Final = re.compile(
    r"\b(?:media|promedio|importe\s+medio|ticket\s+medio)\s+"
    r"(?:por|de\s+cada)\s+(?:cada\s+)?(?:movimiento|operacion|compra|pago|cargo|ingreso)s?\b"
)
_TEMPORAL_GROUPS: Final[tuple[tuple[GroupBy, re.Pattern[str]], ...]] = (
    (
        GroupBy.DAY,
        re.compile(r"\b(?:por|al|cada)\s+dia\b|\b(?:media|promedio)\s+diari[oa]\b"),
    ),
    (
        GroupBy.WEEK,
        re.compile(r"\b(?:por|a\s+la|cada)\s+semana\b|\b(?:media|promedio)\s+semanal\b"),
    ),
    (
        GroupBy.MONTH,
        re.compile(
            r"\b(?:por|al|cada)\s+mes\b|\bmes\s+a\s+mes\b|"
            r"\b(?:media|promedio|gasto\s+medio|importe\s+medio)\s+mensual\b|\bmensualmente\b"
            r"|\b(?:movimientos?|operaciones?|compras?|pagos?|cargos?)\s+mensual(?:es)?\b"
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
    r"|\bseparad[oa]\s+por\s+cuentas?\b"
    r"|\bsepar\w*\s+por\s+cuentas?\b"
)
_COMPARES: Final = re.compile(
    r"\bcompara|\bcomparacion\b|\bfrente\s+a\b|\bpor\s+separado\b|\buna\s+y\s+otra\b"
)
_INCOME: Final = re.compile(
    r"\bingres(?:o|os|e|ado|aron)\b|\bcobr(?:e|ado|aron|os?)\b|\brecibi(?:do)?\b"
    r"|\bnomina\b|\bsueldo\b|\bsalario\b|\bme\s+(?:ha\s+)?entrado\b"
)
_EXPENSE: Final = re.compile(r"\bgast|\bpagu|\bcompr|\bcargo|\bme\s+cobr")


def asks_when(sentence: str) -> bool:
    """Whether the sentence asks *when* something happens rather than how much of it there was.

    Separate from `read` because it changes the sentence the assistant writes rather than the query
    it runs. The plan is the same either way — the same movements over the same window — and only
    the headline is wrong.
    """
    return bool(_ASKS_WHEN.search(spanish.folded(sentence)))


@dataclass(frozen=True)
class ExplicitFacts:
    """What this turn states for certain. Every field is None when the sentence did not say."""

    window: Window | None = None
    group_by: GroupBy | None = None
    metric: Metric | None = None
    direction: Direction | None = None
    compared_categories: tuple[str, ...] = ()
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


def read(sentence: str, *, today: date | None = None) -> ExplicitFacts:
    """The facts this sentence states outright.

    `today` is what "el año pasado" is relative to. It travels in rather than being read from a
    clock, like everywhere else in this pipeline, so the evaluation can replay a turn.
    """
    folded = spanish.folded(sentence)
    named = category_words.precisely_named_in(sentence)
    comparison = bool(_COMPARES.search(folded)) and len(named) >= 2
    says_income = bool(_INCOME.search(folded)) or "SALARY" in named
    says_expense = bool(_EXPENSE.search(folded))
    temporal_group = next(
        (group for group, pattern in _TEMPORAL_GROUPS if pattern.search(folded)), None
    )
    asks_average = bool(_AVERAGE.search(folded))
    asks_top_category = bool(_TOP_CATEGORY.search(folded))
    asks_account_breakdown = bool(_ACCOUNT_BREAKDOWN.search(folded))
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
            if _ASKS_WHO.search(folded)
            else (
                GroupBy.ACCOUNT
                if asks_account_breakdown
                else (GroupBy.CATEGORY if comparison or asks_top_category else temporal_group)
            )
        ),
        metric=(
            Metric.AVERAGE
            if asks_average and (per_movement or temporal_group is None)
            else (Metric.PERIOD_AVERAGE if asks_average else None)
        ),
        direction=Direction.INCOME if says_income and not says_expense else None,
        compared_categories=named if comparison else (),
        names_a_category=bool(named),
        widens=bool(_WIDENS.search(folded)),
        corrects=bool(_REJECTS.search(folded.strip(" ¿?¡!.,"))),
        asks_when=asks_when(sentence),
        asks_top_category=asks_top_category,
        asks_account_breakdown=asks_account_breakdown,
    )


def enforce[T: FinancialPlan | TopMovements](plan: T, facts: ExplicitFacts) -> T:
    """The plan, agreeing with what the sentence stated.

    Applied after the merge and before the verifier, so a field the user wrote down survives both
    the model's opinion about whether it changed and the previous plan it would otherwise be
    inherited from.

    A ranking takes the window and not the grouping: `TopMovements` has none, and "a qué personas"
    is not a question about one movement.
    """
    if facts.window is not None:
        # As a pair, never one at a time: a plan carries exactly one of the two, so setting a range
        # while leaving an inherited period behind builds something the domain refuses outright.
        plan = replace(
            plan,
            period=facts.window if isinstance(facts.window, RelativePeriod) else None,
            date_range=facts.window if isinstance(facts.window, AbsoluteRange) else None,
        )
    if facts.group_by is not None and isinstance(plan, FinancialPlan):
        plan = replace(plan, group_by=facts.group_by)
    if isinstance(plan, FinancialPlan):
        if facts.metric is not None:
            plan = replace(plan, metric=facts.metric)
        if facts.direction is not None:
            plan = replace(plan, direction=facts.direction)
        if facts.compared_categories:
            plan = replace(plan, categories=facts.compared_categories)
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
        merchants=plan.merchants or previous.merchants,
    )
