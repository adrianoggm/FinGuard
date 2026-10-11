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
                 and the reading are disjoint by construction rather than by luck, and the
                 `leaving` entry of `explicit_lexicon` argues where that boundary is and what it
                 refuses to be widened to.
    categories   `CATEGORY_MISREAD` refuses a plan filtered by something the sentence did not
                 name. *Setting* the filter instead would drop the categories the model read
                 correctly out of words this vocabulary does not know — the subset rule that
                 module argues for, in reverse.

**`group_by` is not on that list, and it was asked the same question before being widened.** The
test both refusals pass is "would stating this repair a disagreement a control exists to report?"
For `direction` the answer is yes and the control is `SPENDING_ANSWERED_AS_EVERYTHING`; for
`categories` it is `CATEGORY_MISREAD`. `plan_verifier` has exactly one rule about a grouping,
`BREAKDOWN_ANSWERED_AS_ONE_GROUP`, and it only fires on a sentence that *asks how something divides
up* — so the two are disjoint everywhere except one corner, and `_combined_total` gives that corner
away rather than taking it. This module already imposes the field four ways besides
(`_COUNTERPARTY`, `ACCOUNT`, `CATEGORY` and the temporal groups), so the question was never whether
the grouping may be stated; it was whether `NONE` may be, and `NONE` alone among the five is stated
from an *absence* of instruction rather than the presence of one. That is the whole reason
`_combined_total` is three vetoes and a floor rather than a word list.

**A fifth way used to be listed here — `MONTH` for a cumulative — and it was the one entry on that
list the sentence had not written down.** Every other reading here is a phrase somebody typed; that
one was a default, supplied because the domain refuses `CUMULATIVE` without a calendar bucket. It
is gone, and the cascade in `read` argues where the default belongs instead. The paragraph above is
the test it failed: a value invented here inherits this module's authority to overrule a plan built
by a reader that saw the whole sentence, and «el gasto acumulado por gasolina» is what that costs —
a figure returned as a month-by-month series.

What is left is the window, which nothing checked at all, and the shape of the answer to a
question about people -- and one filter, the kind of thing a category was named by: «el gimnasio» is
the category's shops whose name says so, not the whole category (`of_its_kind`). It is the one
positive filter here, and it is not the category itself: the model's category stands, and the
kind only narrows it to what the sentence said.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Final

from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.services import (
    account_words,
    category_words,
    explicit_lexicon,
    merchant_words,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.services.window_words import Window
from finguard_orchestrator.domain.value_objects.financial_plan import (
    RelativePeriod,
)

# "¿A qué personas les he hecho Bizum?" does not ask for a total. It asks who, so its answer shape
# is a list of ledger counterparties. Keeping this named separately prevents PERSON from falling
# back to the compatibility-only merchant dimension.
_COUNTERPARTY: Final = GroupBy.COUNTERPARTY
_ASKS_WHO: Final = explicit_lexicon.compiled("asks_who")
_MERCHANT_BREAKDOWN: Final = explicit_lexicon.compiled("merchant_breakdown")

_WIDENS: Final = explicit_lexicon.compiled("widens")

# The anchor and the `.+` are the rule, and they are why this is not simply a list of words: what
# makes the turn a correction-with-replacement is that the rejection *opens* the sentence and
# something follows it. The openings themselves are vocabulary and live in the lexicon.
_REJECTS: Final = re.compile(
    rf"^({explicit_lexicon.alternation('rejects_opening')})\b.+"
    rf"|{explicit_lexicon.alternation('rejects_replacement')}"
)

_ASKS_WHEN: Final = explicit_lexicon.compiled("asks_when")

_RECURRENCE: Final = explicit_lexicon.compiled("recurrence")
_DIRECT_DEBIT_QUERY: Final = explicit_lexicon.compiled("direct_debit_query")

_AVERAGE: Final = explicit_lexicon.compiled("average")
# Was written inline inside `asks_recurrence`, which is why nothing could enumerate it and nothing
# noticed it is a strict subset of `_ASKS_AN_AMOUNT` below. Named here so both are countable; the
# two are still separate, because merging them would move sentences.
_AGGREGATE_WORDS: Final = re.compile(rf"\b(?:{explicit_lexicon.alternation('aggregate_words')})\b")
_CUMULATIVE: Final = explicit_lexicon.compiled("cumulative")
_COUNT: Final = explicit_lexicon.compiled("count")
_TYPICAL_AMOUNT: Final = explicit_lexicon.compiled("typical_amount")
_PER_MOVEMENT_AVERAGE: Final = explicit_lexicon.compiled("per_movement_average")
# Read inside `read`, where "anual ... por mes" supplies both the horizon and the divisor. Inline
# before, so it too was a phrase the system knew and nothing could list.
_ANNUAL: Final = explicit_lexicon.compiled("annual")
# The order is the rule and so it stays here: `read` takes the first group whose words appear, so
# a sentence saying both "al día" and "mensual" is a daily question. The words themselves are three
# lexicon entries.
_TEMPORAL_GROUPS: Final[tuple[tuple[GroupBy, re.Pattern[str]], ...]] = (
    (GroupBy.DAY, explicit_lexicon.compiled("temporal_day")),
    (GroupBy.WEEK, explicit_lexicon.compiled("temporal_week")),
    (GroupBy.MONTH, explicit_lexicon.compiled("temporal_month")),
)
_TOP_CATEGORY: Final = explicit_lexicon.compiled("top_category")
_ACCOUNT_BREAKDOWN: Final = explicit_lexicon.compiled("account_breakdown")
# The three vetoes on the coordinated-list reading below, and the reason it is safe to state
# anything about a grouping the sentence did not spell out. Each is argued at `_combined_total`.
_CATEGORY_BREAKDOWN: Final = explicit_lexicon.compiled("category_breakdown")
_MEMBERS_APART: Final = explicit_lexicon.compiled("members_apart")
_DIVIDES_UP: Final = explicit_lexicon.compiled("divides_up")
_COMPARISON_REFERENCE: Final = explicit_lexicon.compiled("comparison_reference")
_COMPARES: Final = explicit_lexicon.compiled("compares")
_INCOME: Final = explicit_lexicon.compiled("income")
_EXPENSE: Final = explicit_lexicon.compiled("expense")
# Which of these two may *assert* a direction and which may only *veto* one is the reader's
# decision, not the vocabulary's, and it is argued at `_direction` below. The lexicon carries why
# each phrase sits on the side it does.
_LEAVING: Final = explicit_lexicon.compiled("leaving")
# **Not vocabulary, and the one pattern here that would be actively wrong to tabulate.** It is the
# module's only capture group, and what it captures depends on the *order* its markers are tried in.
# `sin(?:\s+contar|\s+incluir)?` is greedy, so «sin contar el alquiler» takes the longer marker and
# leaves "el alquiler" as the tail; on «sin contar», with nothing after the verb, `\s+(?P<tail>.+)$`
# cannot match and the optional group backtracks to a bare `sin`, leaving "contar" as the tail —
# which is exactly the case `_NOT_A_MERCHANT` names "contar" for.
#
# Split into a list of markers, that behaviour survives only as long as nobody reorders the list,
# and a lexicon whose rows must stay in a particular order to keep working is a parser with a
# table's manners. So the markers stay welded to the extractor that reads them.
_EXCLUSION_TAIL: Final = re.compile(
    r"\b(?:sin(?:\s+contar|\s+incluir)?|quitando|excepto|salvo|menos"
    r"|no\s+(?:contamos|cuentes|incluimos|incluyas|metas))\s+(?P<tail>.+)$"
)
# Also not vocabulary: a rewriter rather than a recogniser, and the words in it are the date grammar
# `window_words` owns. The lexicon is for words a sentence *says*; this is for a clause it strips.
_TRAILING_WINDOW: Final = re.compile(
    r"\s+(?:durante\s+|en\s+)?(?:este|esta|el|la|los|las)\s+"
    r"(?:mes|ano|semana|dia|ultim\w+).*$"
)
_NOT_A_MERCHANT: Final = frozenset(explicit_lexicon.NOT_A_MERCHANT)
# The anchor is the rule: these words disqualify a tail by *opening* it, and one appearing later is
# no evidence at all — «sin contar El Corte Inglés algo» is not a vague exclusion. The words are a
# lexicon entry; `^` and the boundary are not.
_VAGUE_TAIL: Final = re.compile(rf"^(?:{explicit_lexicon.alternation('vague_tail')})\b")


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
    if account_words.named_in(tail) or account_words.explicitly_names_a_custom_account(tail):
        # An account, not a shop (ruling R48): «sin contar la hucha» left the hucha out as a shop
        # called «la hucha», which filters nothing, and the answer then said «sin la hucha» over a
        # figure that held it. Which accounts a figure covers is `_scoped`'s to decide, where the
        # holder's own account names and aliases are known too.
        return ()
    if merchant_words.never_a_merchant(tail):
        # N-H1 (Wave 3 hardening, round 2): the closed stop-list guarded the *inclusion* reader
        # only. "Sin límite" folds to "sin" + tail "limite", and "limite" is a stop word this
        # service already knows names no shop -- without this check the exclusion reader claimed
        # it anyway, and the answer told the holder a total was computed excluding a shop called
        # "limite" that does not exist.
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
        _AVERAGE.search(folded) or _COUNT.search(folded) or _AGGREGATE_WORDS.search(folded)
    )
    return not asks_for_aggregate


def asks_direct_debits(sentence: str) -> bool:
    """Whether the user named bank direct debits, not merely periodic card charges."""
    return bool(_DIRECT_DEBIT_QUERY.search(spanish.folded(sentence)))


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
    # The kinds of thing the sentence named inside a category, by category: «el gimnasio» is
    # `(("SUBSCRIPTIONS", ("gimnasio",)),)`. Read by `category_words.kinds_named_in`, which is where
    # «suscripciones, gimnasio incluido», «sin el gimnasio» and «metro y tren» are told apart from
    # a question about the kind; `of_its_kind` is what a plan does with it.
    kinds: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def kinds_of(self, category: str) -> tuple[str, ...]:
        """The kinds this sentence named inside `category`, or () when it named none."""
        return next((kinds for named, kinds in self.kinds if named == category), ())


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


def _combined_total(folded: str, *, named_count: int) -> bool:
    r"""Whether a coordinated list of categories is asking for one figure rather than one each.

    «¿Cuánto gasté en gasolina y transporte público?» was answered with one figure or with two
    depending on the wording, because nothing arbitrated it. The sentence is genuinely ambiguous in
    Spanish and there was no rule, so the two halves of a robustness pair disagreed *by
    construction*: «¿Cuánto llevo entre gasolina y transporte público este mes?» was pinned to a
    two-row breakdown by the reader, while every rewording of it that dropped the «entre» — and
    none of the 36 kept it — was left to the model, which planned a plain total. Twenty-four of that
    group's thirty-one robustness breaks were the single field `groupBy`, and most of them were this
    one disagreement counted once per paraphrase.

    **A coordinated list with no grouping instruction is one total.** Not because the sentence says
    so — it does not, and that is the whole difficulty — but because the two errors are not
    symmetric. A total the user wanted split is recovered by asking «¿y por separado?»; a split
    nobody asked for makes somebody read five rows and add them by hand, and it costs that turn
    every single time. So the tie goes to the total, and the sentence decides instead of the model.

    **This is the one field here that states something from an absence, so the absence is checked
    three ways rather than assumed.** Everything above this in `read`'s cascade already claims the
    grouping when it is spelled out — «a qué personas», «por comercio», «por cada cuenta», «compara»
    / «frente a» / «por separado», «¿en qué categoría gasté más?», «al mes», «gasto acumulado». What
    those miss is three things, and each was found by measurement rather than guessed:

    `_CATEGORY_BREAKDOWN`
        A grouping preposition before the dimension noun, which is `_ACCOUNT_BREAKDOWN`'s shape and
        deliberately so. The noun alone proves nothing: «en las categorías de súper, gasolina y
        farmacia» enumerates three filters and wants one figure, and two corpus paraphrases say
        exactly that. Requiring the preposition is the half `_GROUP_RANKING_DIMENSION` was missing
        when «en todas mis **cuentas**» silently supplied a grouping dimension.
    `_MEMBERS_APART`
        The coordination asked for apart with no dimension named at all — «¿cuánto fue en gasolina
        y cuánto en súper?», where the second interrogative is the whole instruction. `dev`'s
        `multi_with_grouping` is that sentence, and it is the one case in the corpus whose written
        expectation names `groupBy: CATEGORY` for a bare coordination. Without this veto the rule
        would contradict a case already in the repository.
    `_DIVIDES_UP`
        The verbs of dividing. Here the reading stands aside not because it would be wrong but
        because it would be a *repair*: `plan_verifier`'s `BREAKDOWN_ANSWERED_AS_ONE_GROUP` refuses
        a plan that groups by a dimension it filtered to one value, and its docstring names
        dropping the grouping as the tempting wrong way out. Silently doing that here would settle
        a disagreement before the control could report it, which is the argument this module's
        docstring makes for refusing `direction` — so the same answer is given.

    `named_count` rather than the names, because which categories they are is `plan_verifier`'s
    business and not this rule's. Two is the floor: one category is not a coordination, and
    claiming a shape for it would be claiming something from silence alone.
    """
    if named_count < 2:
        return False
    return not (
        _CATEGORY_BREAKDOWN.search(folded)
        or _MEMBERS_APART.search(folded)
        or _DIVIDES_UP.search(folded)
    )


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
    combined = _combined_total(folded, named_count=len(named))
    per_movement = bool(_PER_MOVEMENT_AVERAGE.search(folded))
    window = window_words.window_in(sentence, today=today)
    # "anual ... por mes" gives both the horizon and the divisor. It means the trailing twelve
    # months unless a more precise year/window was written, in which case that explicit window won
    # above. This is a duration, not a forecast.
    if window is None and asks_average and _ANNUAL.search(folded):
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
                    # `GroupBy.MONTH if asks_cumulative` stood here, and **`MONTH` was not in
                    # the sentence.** It was supplied because the domain refuses `CUMULATIVE`
                    # without a calendar bucket, so the reader filled the hole — and this
                    # module's whole licence is that it reports what the sentence states. When
                    # the guess was right nobody noticed; when the sentence said «por
                    # gasolina», a figure came back a month-by-month series. It also *lost*
                    # groupings: «el importe acumulado por categoría» named a dimension and
                    # got `MONTH` instead, because nothing below consults
                    # `_CATEGORY_BREAKDOWN`.
                    #
                    # Nothing needs it here. `readers/metrics._cumulative_plan` already spells
                    # `facts.group_by or GroupBy.MONTH` at the point it builds a plan, which is
                    # where a default belongs — visible, local, and owned by whoever decided
                    # the shape. Everywhere else the pair is offered through `reshaped`, which
                    # keeps the plan's own grouping when the domain refuses the pair, and
                    # `financial_plan.metric_without_a_bucket` settles `CUMULATIVE` beside
                    # `NONE` to `SUM` — the same rows, the same window, the same filters, and
                    # the figure the sentence actually asked for.
                    #
                    # Last, so every grouping the sentence spells out wins over it. See
                    # `_combined_total`: it states a shape from an absence, and standing at
                    # the bottom of this cascade is most of what makes that safe.
                    else (temporal_group or (GroupBy.NONE if combined else None))
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
        kinds=tuple(
            (category, kinds)
            for category in category_words.categories_with_kinds()
            if (kinds := category_words.kinds_named_in(sentence, category))
        ),
    )
