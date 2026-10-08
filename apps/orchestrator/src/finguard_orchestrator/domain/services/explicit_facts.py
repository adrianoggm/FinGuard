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


# "How many X do I have" and "which X do I have", where X is a kind of spending. The thing being
# counted is the payee, not the charge: somebody with Netflix, Spotify, a gym and Steam has four
# subscriptions and thirty-six movements, and answering the second is a real figure for a question
# nobody asked. It was reported exactly that way — "yo pensaba que tenía solo netflix altafit spoti
# y steam".
#
# The trailing `(?![a-z])` is structure, not a word: it stops a folded interrogative being found
# inside a longer token.
_HOW_MANY_PAYEES: Final = re.compile(
    rf"\b(?:{explicit_lexicon.alternation('how_many_payees')})\b(?![a-z])"
)
_HAS_THEM: Final = re.compile(rf"\b(?:{explicit_lexicon.alternation('has_them')})\b")

# Exported, because the same nouns decide the same thing in the query handler's count follow-up and
# were kept there by hand. They stayed level only because one change happened to touch both: the
# rails were missing from each, and each was found separately. `readers/follow_up.py` interpolates
# this string into a pattern of its own, so it stays a string and keeps its exact spelling; the
# nouns in it, and why the rails are there and the arrangements are not, are a lexicon entry.
CHARGE_NOUNS: Final = explicit_lexicon.alternation("charge_nouns")
_THE_CHARGE_NOT_THE_PAYEE: Final = re.compile(rf"\b(?:{CHARGE_NOUNS})\b")

# Asking for money, which is never this however it is phrased.
_ASKS_AN_AMOUNT: Final = re.compile(rf"\b(?:{explicit_lexicon.alternation('asks_an_amount')})\b")


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
    is not a question about one movement. Nor the kind (`of_its_kind`): a ranking that found no shop
    of the kind has no category figure to fall back to, and saying so is the handler's work for an
    aggregate alone.

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
        # After the exclusions and the categories, because both decide whether a kind may narrow:
        # «el gimnasio sin Altafit» leaves a shop out, and a category the sentence replaced takes
        # its kind with it.
        plan = of_its_kind(plan, facts)
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


def of_its_kind[P: FinancialPlan | FinancialPlanDraft | TopMovements](
    plan: P, facts: ExplicitFacts
) -> P:
    """The plan narrowed to the kind of thing the sentence named inside its category.

    Reported from a live session: «¿cuánto gasté en el gimnasio este año?» answered "421,79 € en
    suscripciones, en 23 movimientos" -- Netflix, Spotify and Steam with the gym, which alone is
    29,90 € a month. «Gimnasio» is a word of the SUBSCRIPTIONS vocabulary, so the planner, the gate
    and every reader agreed the sentence named the category, and every one of them was right about
    the category and wrong about the question (`category_words._KINDS`).

    **The kind is carried by the plan, as a merchant term beside the category.** A term is how a
    plan already says "the shops whose name contains this" -- «un nombre que contenga bet» is
    Bet365 and Betfair -- and a gym is exactly such a family: every shop of the holder's in the
    category whose name says «gimnasio», however many there are. Decided here, once, from the
    sentence that asks, when its plan is built; and from then on it travels wherever the plan
    does -- the draft a window question keeps, the plan the account question keeps, the plan a
    retry keeps, the plan a follow-up inherits. Four review rounds of the alternative, which read
    the asking sentence back out of the turn log on each of those paths after the bank had
    answered, kept finding a path where it read the wrong sentence.

    A plan with no shop of the kind is the handler's to answer (`_answer`): the bank finds
    nothing, and the category's figure is given with a line saying why.

    Left alone, each for a reason already decided:

    - more or fewer than one category: a plan cannot hold "these gyms, or else that pharmacy";
    - a shop named exactly, or a shop left out -- «el gimnasio McFit» is McFit, and which of the
      shops left out the kind should still cover is the holder's to say;
    - a family term of its own («apuest», «bet»), which this would replace;
    - a person, an own-account transfer or a list of movements picked by pronoun: none of them is
      a question about a kind of shop.

    And what `category_words.kinds_named_in` refuses: the category's own name or another word of
    it said beside the kind, the kind behind «sin», «aparte de» or «que no sea».

    **A kind this rule put on a plan leaves with its category.** «¿Y en salud?» after the gym is
    every health shop, but the merge that builds it from the gym's plan changes the category and
    keeps the terms, because to the model they are one field it did not say it changed -- and a
    question about health would then have asked the bank for health shops called «gimnasio». A
    term that is a kind of a category the plan no longer holds is dropped first, and the sentence
    may then name a kind of the new one. A kind already on the plan of its own category stays, so
    «¿y el año pasado?» is still the gym, and a kind the sentence names replaces it there: «la
    óptica» after «la farmacia» is the optician. The plan's own family terms are never touched.

    A ranking is returned as it came: see `enforce`.
    """
    if isinstance(plan, TopMovements):
        return plan
    kept = tuple(
        term
        for term in plan.merchant_terms
        if (kind_of := category_words.category_of_kind(term)) is None
        or not plan.categories
        or kind_of in plan.categories
    )
    if kept != plan.merchant_terms:
        plan = replace(plan, merchant_terms=kept)
    if (
        len(plan.categories) != 1
        or plan.merchants
        or plan.excluded_merchants
        or any(category_words.category_of_kind(term) is None for term in plan.merchant_terms)
        or plan.counterparties
        or plan.excluded_counterparties
        or plan.counterparty_types
        or plan.internal_only
        or plan.transaction_ids
    ):
        return plan
    # Each kind as the vocabulary spells it and without its accents, as alternatives: the bank
    # lowers a name but does not fold it, so «óptica» alone is not "Optica Roca", and the note
    # under an empty answer would then say no shop carries a word one plainly does.
    kinds = tuple(
        dict.fromkeys(
            spelling
            for kind in facts.kinds_of(plan.categories[0])
            for spelling in (kind, spanish.folded(kind))
        )
    )
    if not kinds or kinds == plan.merchant_terms:
        return plan
    return replace(plan, merchant_terms=kinds)


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
