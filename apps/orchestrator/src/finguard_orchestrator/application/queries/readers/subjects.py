"""The four readers that recognise what the sentence is about, before anything is in view.

A betting merchant, a set of payees to count, an account or an internal transfer, a proportion
between two figures. These four stood first in the cascade (`stood_at` 1 to 4) and they have the
property that put them there: each reads a **closed vocabulary naming its own subject**, so each
can answer a turn that continues nothing and needs no plan on screen. None of the other fifteen can
say that — they need a metric, a dimension, an order, a second window or a question in view.

That is also why they are together rather than four modules: the thing they share is the shape of
the reading, and the thing they do *not* share is the dispatch — `_ratio_request` answers with a
`RatioPlan`, `_how_many_payees_plan` with a count, `_internal_movement_request` with a plan and no
`planned` stage, `_gambling_plan` with a draft that asks for a window. Four dispatches and one
kind of reader; the package is grouped by the second.

`_ratio_request` consults `_direct_period_comparison`, in `comparisons`, and keeps calling the
decorated function so the gate and the memo still apply — it is the one path where two readers both
record a claim, and both are true.
"""

from __future__ import annotations

import re
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _clean_turn,
    _same_window,
    _the_pair_in_view,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.comparisons import (
    _DIRECT_PERIOD_COMPARISON,
    _direct_period_comparison,
)
from finguard_orchestrator.application.queries.readers.seam import _recogniser
from finguard_orchestrator.application.queries.readers.windows import _the_earlier_first
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.models.planning_context import (
    PendingRatioFocus,
    QueryFocus,
    RatioFocus,
    focus_of,
)
from finguard_orchestrator.domain.services import (
    account_words,
    category_words,
    explicit_facts,
    financial_relationships,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan

_RATIO_WORDS: Final = re.compile(r"\bporcentaje\b|\bque\s+parte\s+de\b|\bque\s+proporcion\s+de\b|%")


# The percentage of the pair in view, asked as the whole turn: «en porcentaje», «¿cuánto es eso en
# porcentaje?», and since validation fix 3 (G4) «y eso en %?», «en %», «¿y el porcentaje?» and
# «¿cuánto en %?». Read over `_in_percent_words`, which spells «%» as the word: on 2026-10-08
# «y eso en %?» was folded to «y eso en», matched nothing here, and was refused as a proportion
# with no operands about the two figures the holder had just been shown.
_PERCENT_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:(?:cuanto\s+(?:es|fue|supone)\s+)?eso\s+|cuanto\s+)?(?:en\s+)?(?:el\s+)?"
    r"porcentaje$"
    r"|^(?:y\s+)?(?:cual|cuanto)\s+fue\s+(?:la\s+)?(?:diferencia|variacion)\s+porcentual$"
)


def _in_percent_words(sentence: str) -> str:
    """`_clean_turn`, with «%» said as «porcentaje» rather than dropped as punctuation."""
    return _clean_turn(sentence.replace("%", " porcentaje "))


_RATIO_WINDOW_REPLY: Final = re.compile(
    r"^(?:(?:vale|bueno|perfecto|pues)\s+)?(?:en\s+|durante\s+)?(?:"
    r"este\s+(?:mes|ano)|esta\s+semana|el\s+(?:mes|ano)\s+(?:pasado|anterior)"
    r"|la\s+semana\s+(?:pasada|anterior)|los\s+ultimos\s+\w+\s+(?:dias|meses|anos)"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre"
    r"|noviembre|diciembre|desde\s+.+|entre\s+.+)\s*$"
)


_INTERNAL_HISTORY_WORDS: Final = re.compile(
    r"\b(?:movimientos?|movimeintos?|traspasos?|transferencias?|movid[oa]s?|traspasad[oa]s?|"
    r"pasad[oa]s?|metid[oa]s?|aportad[oa]s?)\b"
)


_INTERNAL_BETWEEN_ACCOUNTS: Final = re.compile(
    r"\b(?:entre|de\s+una\s+(?:de\s+)?mis)\s+(?:mis|tus|sus|las)?\s*cuentas\b"
    r"|\bde\s+una\s+cuenta\s+a\s+otra\b"
)


_INVESTMENT_HISTORY: Final = re.compile(
    r"\b(?:cuanto|caunto|cuantos|que|cual|dime|dame|muestrame|ensename)\b.{0,55}"
    r"\b(?:(?:invertid|aportad|metid|traspasad)\w*|gast\w*\s+en\s+inversion(?:es)?)\b"
    r"|\b(?:mis\s+)?(?:aportaciones?|inversiones?)\b.{0,40}\b(?:hech|realiz|este|ultimo)\w*\b"
)


_GAMBLING: Final = re.compile(r"\b(?:apuest\w*|bet(?:365|fair)?|casino\w*|codere|bwin|sportium)\b")


@_recogniser
def _ratio_request(query: AskFinancialQuestionQuery) -> RatioPlan | RatioDraft | None:
    """A proportion stated completely, or the exact pair waiting for one shared window."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    folded = spanish.folded(query.sentence)
    cleaned = _clean_turn(query.sentence)

    focus = focus_of(query.conversation)
    pending = focus.value if isinstance(focus, PendingRatioFocus) else None
    if (
        pending is not None
        and facts.window is not None
        and len(cleaned.split()) <= 10
        and _RATIO_WINDOW_REPLY.fullmatch(cleaned) is not None
    ):
        return pending.with_window(facts.window)

    previous_ratio = focus.value if isinstance(focus, RatioFocus) else None
    if (
        previous_ratio is not None
        and previous_ratio.kind is not RatioKind.PERCENT_CHANGE
        and facts.window is not None
        and len(cleaned.split()) <= 6
        and re.search(r"\b(?:cuanto|cual|que|gast|ingres|cobr|movimientos?)\b", cleaned) is None
    ):
        return previous_ratio.with_window(facts.window)

    if _PERCENT_FOLLOW_UP.fullmatch(_in_percent_words(query.sentence)):
        comparison = _stored_percentage_pair(query)
        if comparison is not None:
            return RatioPlan(
                part=comparison.latest,
                whole=comparison.earlier,
                kind=RatioKind.PERCENT_CHANGE,
            )
        return None

    asks_ratio = _RATIO_WORDS.search(folded) is not None
    asks_savings = financial_relationships.asks_savings_rate(query.sentence)
    asks_net_cash_flow = financial_relationships.asks_net_cash_flow(query.sentence)
    if not asks_ratio and not asks_savings and not asks_net_cash_flow:
        return None

    if asks_ratio and _DIRECT_PERIOD_COMPARISON.search(folded):
        comparison = _direct_period_comparison(query)
        if comparison is not None:
            return RatioPlan(
                part=comparison.latest,
                whole=comparison.earlier,
                kind=RatioKind.PERCENT_CHANGE,
            )

    window = facts.window
    if asks_net_cash_flow:
        draft = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.NET_CASH_FLOW,
        )
        return draft.with_window(window) if window is not None else draft
    if asks_savings:
        draft = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.SAVINGS_RATE,
        )
        return draft.with_window(window) if window is not None else draft

    named = category_words.precisely_named_in(query.sentence)
    part_categories = tuple(category for category in named if category != "SALARY")
    names_salary = bool(re.search(r"\b(?:nomina|sueldo|salario)\b", folded))
    names_income = names_salary or bool(re.search(r"\bingresos?\b", folded))
    names_expenses = bool(re.search(r"\b(?:mis|los|tus)\s+gastos\b|\bgasto\s+total\b", folded))
    if not names_income and not names_expenses:
        return None
    if not part_categories:
        # Without the numerator this would silently divide all expenses by themselves and answer
        # 100 %. An open merchant name also belongs to the semantic path until it can be verified
        # against actual ledger merchants.
        return None
    draft = RatioDraft(
        part=FinancialPlanDraft(
            direction=Direction.EXPENSE,
            categories=part_categories,
        ),
        whole=FinancialPlanDraft(
            direction=Direction.INCOME if names_income else Direction.EXPENSE,
            categories=("SALARY",) if names_salary else (),
        ),
        kind=RatioKind.SHARE,
    )
    return draft.with_window(window) if window is not None else draft


# Before the breakdown and before the model, because both answer it with the wrong number: the
# breakdown leads with the money and the planner reads "¿cuántas suscripciones?" as a count of
# movements. Named categories only, so "¿qué gastos fijos tengo?" still reaches the recurrence
# discovery further down.
#
# That comment was on this reader's call site in `_steps`, and it is here now because the position
# it argued for is no longer a line number. It is `stood_at=3` against the breakdown reader's 14,
# with
# `test_the_tiebreak_is_never_the_thing_that_decides` saying the two never both claim — which is
# what makes the argument still true and no longer load-bearing on where anything sits.
@_recogniser
def _how_many_payees_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """How many distinct payees a kind of spending has, and which they are.

    Reported. "¿Qué suscripciones tengo activas?" was answered "36 movimientos en suscripciones en
    los últimos 12 meses", and the holder said what anybody would: "yo pensaba que tenía solo
    netflix altafit spoti y steam". Both figures are true and only one is the answer — thirty-six is
    how many times they were charged, four is how many things they are paying for.

    No new metric and nothing new asked of banking. A breakdown by merchant already returns one
    group per payee, so the count is the number of groups and the names are their keys. What was
    missing was a question that asks for it and a sentence that leads with it.

    Exactly one named category, because the question is "how many of *these*". A sentence naming
    none is either the recurring-expenses discovery below — "¿qué gastos fijos tengo?" — or not
    this at all, and grouping every merchant the holder ever paid is a list dump, not an answer.
    """
    if not explicit_facts.asks_how_many_payees(query.sentence):
        return None
    named = category_words.precisely_named_in(query.sentence)
    if len(named) != 1:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    # A default rather than a question, exactly like the recurring-expenses discovery below.
    # "Activas ahora mismo" is not a window this service can turn into dates, and the reported
    # conversation shows what asking costs: "¿de qué periodo?" was answered "en el último año" and
    # the figure that came back was one the holder did not recognise. The answer names the window,
    # so narrowing it is one short turn.
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named,
        # By merchant rather than by counterparty: a subscription is a shop that charges you, and
        # this is the same dimension the shop rankings already group by.
        group_by=GroupBy.MERCHANT,
        metric=Metric.SUM,
    )


def read_internal_movement_request(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """A read of transfers that stayed inside the holder's own account set.

    Undecorated on purpose: `application/queries/readers/follow_up.py`'s
    `read_contextual_follow_up` consults this reader too, to decide whether a reshape is one it
    should decline in favour of an internal-movement request — and it must do so without recording
    a cascade decision or memoising onto the query. `_internal_movement_request` below is the
    cascade's own name for this same body, wrapped once in the seam that call site needs.
    """
    folded = spanish.folded(query.sentence)
    facts = explicit_facts.read(query.sentence, today=query.today)
    between_accounts = _INTERNAL_BETWEEN_ACCOUNTS.search(folded) is not None
    investment = _INVESTMENT_HISTORY.search(folded) is not None
    named_accounts = account_words.named_in(query.sentence)
    asks = (
        re.search(
            r"\b(?:cuanto|caunto|cuantos|que|cual|cuales|dime|dame|lista|muestrame|ensename)\b",
            folded,
        )
        is not None
    )
    names_transfer_history = (
        re.search(
            # Singular "traspaso" is also the present-tense verb in "cuánto traspaso al fondo".
            # Plural nouns and completed forms are history; the ambiguous verb stays on the
            # established habitual-query path instead of inventing an internal-only request.
            r"\b(?:traspasos|transferencias?|movid|traspasad|metid|aportad)\w*\b",
            folded,
        )
        is not None
        # "Pasado" on its own is usually the calendar adjective in "el mes pasado". Only its
        # completed verb form means money moved to the named account.
        or re.search(r"\b(?:he|has|ha|hemos|han)\s+pasad\w*\b", folded) is not None
    )
    if not investment and not (
        (between_accounts and _INTERNAL_HISTORY_WORDS.search(folded))
        or (named_accounts and asks and names_transfer_history)
    ):
        return None

    destination = account_words.destination_in(query.sentence)
    direction = Direction.INCOME if investment or destination else Direction.EXPENSE
    metric = facts.metric or Metric.SUM
    group_by = (
        facts.group_by
        if metric is Metric.PERIOD_AVERAGE and facts.group_by is not None
        else GroupBy.NONE
    )
    if window := facts.window:
        return FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            group_by=group_by,
            metric=metric,
            include_internal=True,
            internal_only=True,
        )
    return FinancialPlanDraft(
        direction=direction,
        group_by=group_by,
        metric=metric,
        include_internal=True,
        internal_only=True,
    )


@_recogniser
def _internal_movement_request(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """The cascade's recogniser for `read_internal_movement_request` — see its docstring."""
    return read_internal_movement_request(query)


def _stored_percentage_pair(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """The two compatible windows behind a bare "en porcentaje".

    The pair in view first — a comparison's, or the one a percentage was already taken over, so
    «en porcentaje» after «y eso en %?» is that percentage again (`_the_pair_in_view`).
    """
    pair = _the_pair_in_view(query.conversation)
    if pair is not None:
        return pair
    focus = focus_of(query.conversation)
    if not isinstance(focus, QueryFocus):
        return None
    latest = focus.current
    earlier = focus.previous
    if not isinstance(latest, FinancialPlan) or not isinstance(earlier, FinancialPlan):
        return None
    if _same_window(latest, earlier) or latest.metric is not earlier.metric:
        return None
    scope = (
        "direction",
        "categories",
        "excluded_categories",
        "merchants",
        "merchant_terms",
        "excluded_merchants",
        "counterparties",
        "excluded_counterparties",
        "counterparty_types",
        "include_internal",
        "internal_only",
        "across_accounts",
        "only_account_id",
        "only_account_ids",
        "transaction_ids",
        "transaction_types",
        "group_limit",
    )
    if any(getattr(latest, field) != getattr(earlier, field) for field in scope):
        return None
    # The two answers in the order their windows happened (fix round 1, M-3): June, then «¿y en
    # mayo?», then «el porcentaje» is the change from May to June. Taken in the order they were
    # answered, it ran from June to May and said its word about time running backwards.
    first, second = _the_earlier_first(earlier, latest, query.today)
    return ComparisonPlan.between(first, second)


@_recogniser
def _gambling_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Expenses at betting merchants, with explicit substring rather than exact-name semantics."""
    folded = spanish.folded(query.sentence)
    if _GAMBLING.search(folded) is None:
        return None
    if not re.search(r"\b(?:gast|pag|movim|operacion|dinero|comercio|casa|ocio)\w*\b", folded):
        return None

    terms: list[str] = []
    if re.search(r"\bapuest\w*\b", folded):
        # The product family, not two exact shops: this covers Casa de Apuestas and Bet365.
        terms.extend(("apuest", "bet"))
    for term in ("bet", "casino", "codere", "bwin", "sportium"):
        if re.search(rf"\b{re.escape(term)}\w*\b", folded) and term not in terms:
            terms.append(term)
    window = window_words.window_in(query.sentence, today=query.today)
    if window is None:
        return FinancialPlanDraft(
            direction=Direction.EXPENSE,
            categories=("LEISURE",),
            merchant_terms=tuple(terms),
        )
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=("LEISURE",),
        merchant_terms=tuple(terms),
    )


def _asks_a_proportion(query: AskFinancialQuestionQuery) -> bool:
    focus = focus_of(query.conversation)
    if isinstance(focus, PendingRatioFocus | RatioFocus):
        return True
    folded = spanish.folded(query.sentence)
    return bool(
        _PERCENT_FOLLOW_UP.fullmatch(_in_percent_words(query.sentence))
        or _RATIO_WORDS.search(folded)
        or financial_relationships.asks_savings_rate(query.sentence)
        or financial_relationships.asks_net_cash_flow(query.sentence)
    )


def _names_an_account_movement(query: AskFinancialQuestionQuery) -> bool:
    folded = spanish.folded(query.sentence)
    return bool(
        _INVESTMENT_HISTORY.search(folded)
        or _INTERNAL_BETWEEN_ACCOUNTS.search(folded)
        or account_words.named_in(query.sentence)
    )


# The rows for the readers above, and they live here rather than in one table because a row is
# mostly a *precondition*, and a precondition has to read the vocabulary its reader reads. Kept
# somewhere else, the two drift and nothing says so: the reader learns a phrasing, its precondition
# does not, and the sentence is refused before the reader that would have claimed it is asked. That
# failure is silent by construction, which is why the row is here where the pattern it reads is.
#
# `readers/__init__` concatenates these into the one `Registry`, which validates the whole table at
# import and derives the order from it. `stood_at` is unchanged from the cascade this replaced —
# see the note there for why it is not renumbered.
ROWS: Final = (
    Reader(
        name="_ratio_request",
        read=_ratio_request,
        stood_at=1,
        when=Precondition(
            "the sentence asks for a proportion, or a proportion is in view",
            _asks_a_proportion,
        ),
    ),
    Reader(
        name="_gambling_plan",
        read=_gambling_plan,
        stood_at=2,
        when=Precondition(
            "the sentence names a betting merchant",
            lambda query: _GAMBLING.search(spanish.folded(query.sentence)) is not None,
        ),
    ),
    Reader(
        name="_how_many_payees_plan",
        read=_how_many_payees_plan,
        stood_at=3,
        when=Precondition(
            "the sentence asks how many things it is paying for",
            lambda query: explicit_facts.asks_how_many_payees(query.sentence),
        ),
        yields_to=("_ratio_request",),
        because=(
            "«¿qué porcentaje de mis gastos fue supermercado este mes?» is a proportion, and "
            "`asks_how_many_payees` reads its «qué parte de» as a count of payees. Both claim "
            "and only one is the question. Measured on two corpus turns."
        ),
    ),
    Reader(
        name="_internal_movement_request",
        read=_internal_movement_request,
        stood_at=4,
        when=Precondition(
            "the sentence names an account, an internal transfer or an investment",
            _names_an_account_movement,
        ),
    ),
)
