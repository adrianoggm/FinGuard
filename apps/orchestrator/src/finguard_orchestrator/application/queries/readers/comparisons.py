"""The five readers that answer with two figures instead of one.

Grouped by what they *build*, which here is also what they share: a comparison has two operands,
and everything hard about these five is deciding what the second one is. «Compáralo con el mes
anterior» names it relative to the window in view, «compáralo con junio» names it outright,
«compáralos» names neither and both come off the conversation, and the salary reader compares one
payroll against its own history.

They are one module because they share their vocabulary, and the vocabulary is the part that goes
wrong. `_A_COMPARISON_IMPERATIVE`'s own comment says it: "the same list spelled twice is how two
readers come to disagree about which sentences are comparisons at all". Two of these five read that
list, one reads the other's answer (`_comparison_with_a_named_window` asks
`_comparison_with_previous_period` whether it claims first), and `_a_comparison_owns_it` is
`_direct_period_comparison` under another name, asked by four readers in three other modules.
Splitting them would put that mutual reading across module boundaries for no gain.

**What this module refuses:** it does not own `_previous_calendar_window`. The reader in `follow_up`
needs the same answer for «¿y el mes anterior?», so it lives in `windows` where both read it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _a_plan_is_in_view,
    _across_every_account,
    _clean_turn,
    _direction_of,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.seam import _recogniser
from finguard_orchestrator.application.queries.readers.windows import (
    _calendar_month_window,
    _previous_calendar_window,
    _the_earlier_first,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.models.planning_context import (
    ComparisonFocus,
    QueryFocus,
    focus_of,
)
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    merchant_words,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

# A comparison imperative wearing the pronoun, with the second operand still to come: "compáralo
# con …", "vale, compruébalo con …", "¿me lo podrías comparar con …?".
#
# Two recognisers need it — one reads a calendar unit relative to the window in view, the other a
# window named outright — and it is spelled once because the same list spelled twice is how two
# readers come to disagree about which sentences are comparisons at all. Same mould as
# `MOVEMENT_NOUNS`, and for the same reason.
_A_COMPARISON_IMPERATIVE: Final = (
    r"(?:(?:vale|bueno|ok|perfecto)(?:\s+genial)?\s+|pues\s+)?(?:y\s+)?(?:"
    r"(?:me\s+lo\s+)?podrias\s+comparar(?:lo|la)?"
    r"|compara(?:lo|la|melo|mela)?"
    r"|comparame\s+(?:eso|esto|el\s+resultado|la\s+consulta)"
    r"|comprueba(?:lo|la)?"
    r"|revisa(?:lo|la)?"
    r"|hazme\s+una\s+comparacion"
    r")"
)


_COMPARE_WITH_PREVIOUS_PERIOD: Final = re.compile(
    rf"^{_A_COMPARISON_IMPERATIVE}\s+con\s+(?:el\s+|la\s+)?"
    r"(?P<unit>fin\s+de\s+semana|trimestre|semestre|quincena|mes|semana|ano|periodo)\s+"
    r"(?:anterior|pasad[oa])$"
)


# The same imperative with a window named outright as its second operand: "compáralo con junio",
# "compáralo con julio de 2025", "compáralo con la semana pasada".
#
# The tail is deliberately anything at all, because what makes it a window is `window_words` and
# not a second vocabulary here. A tail that reads as no window — "compáralo con mi nómina" — leaves
# the sentence exactly where it was, which is with the guard.
_COMPARE_WITH_A_NAMED_WINDOW: Final = re.compile(
    rf"^{_A_COMPARISON_IMPERATIVE}\s+con\s+(?P<window>\S.*)$"
)


_DIRECT_PERIOD_COMPARISON: Final = re.compile(
    r"\b(?:compara\w*|comparacion|frente\s+a(?:l)?|contra|versus|vs"
    r"|mas\b.{0,55}\b(?:que|o)|menos\b.{0,55}\b(?:que|o)"
    r"|(?:cual|que|donde)\b.{0,50}\b(?:mas|menos|mayor|menor)"
    r"|mayor|menor)\b"
)


# Where a comparison says the shop it is about: after «en» or «de» — «junio vs julio en Mercadona»,
# «mis gastos de Spotify» — with an article or a possessive between. Only the words
# `merchant_words.shop_words_in` already takes for a shop's name count, so «en junio», «de este
# mes» and «en la hucha» say none; and a name starts with a letter, so «de 2025» is a year.
_A_PLACE_SAID: Final = re.compile(
    r"\b(?:en|de|del)\s+(?:(?:el|la|los|las|mis?|tus?)\s+)?([a-z][a-z0-9]*)"
)


def _shops_said(sentence: str) -> tuple[str, ...]:
    """The words the sentence says a shop with, folded: «junio vs julio en Mercadona» is
    ("mercadona",)."""
    candidates = frozenset(merchant_words.shop_words_in(sentence))
    return tuple(
        word for word in _A_PLACE_SAID.findall(spanish.folded(sentence)) if word in candidates
    )


_SALARY_HISTORY_COMPARISON: Final = re.compile(
    r"\b(?:cobr|recib|ingres)\w*\b.{0,55}\b(?:mas|menos)\b.{0,55}"
    r"\b(?:anteriores?|otras?)\b"
    r"|\b(?:esa|esta|ultima)\s+nomina\b.{0,55}\b(?:mas|menos)\b.{0,55}"
    r"\b(?:anteriores?|otras?)\b"
)


_PERIOD_COMPARISON_REFERENCE: Final = re.compile(
    r"^(?:vale\s+|bueno\s+|entonces\s+|y\s+)?(?:"
    r"compara(?:los|las|melos|melas)?"
    r"(?:\s+(?:esos?|esas?|los|las)?\s*dos\s+(?:periodos?|meses?|semanas?))?"
    r"(?:\s+otra\s+vez)?"
    r"|hazme\s+una\s+comparacion(?:\s+de\s+(?:los|esos)\s+dos)?"
    r"|(?:en\s+)?(?:cual|que)\s+(?:de\s+los\s+dos\s+)?"
    r"(?:gaste|he\s+gastado|ingrese|he\s+ingresado|cobre|he\s+cobrado|recibi|he\s+recibido|hubo)\s+mas"
    r"|(?:cual|que)\s+(?:he\s+)?(?:cobrado|recibido|ingresado|gastado)\s+mas"
    r"|(?:del|entre\s+el)\s+anterior\s+(?:a|y)\s+este"
    r")(?:\s+por\s+favor)?$"
)


@_recogniser
def _period_comparison_plans(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Resolve a comparison pronoun only when the stored plans differ solely by window."""
    if not _PERIOD_COMPARISON_REFERENCE.fullmatch(_clean_turn(query.sentence)):
        return None
    focus = focus_of(query.conversation)
    if isinstance(focus, ComparisonFocus):
        return focus.value
    if not isinstance(focus, QueryFocus):
        return None
    latest = focus.current
    earlier = focus.previous
    if not isinstance(latest, FinancialPlan) or not isinstance(earlier, FinancialPlan):
        return None
    if (latest.period, latest.date_range) == (earlier.period, earlier.date_range):
        return None

    # Every field a question can state. `across_accounts` is deliberately not among them: a plan
    # is what was *asked*, and after the turn that settled the scope no later sentence names it
    # again, so the second of two answered periods carries `across_accounts=False` even though its
    # figures came from every account. Requiring the two to agree refused a comparison the
    # conversation had already answered twice, over a field neither question mentioned.
    #
    # `only_account_id` and `only_account_ids` stay here. Those the sentence *can* state — «cuánto
    # gasté en la Hucha» — so a disagreement there is two different questions, and relaxing them
    # too would compare the piggy bank against every account. A scope field the sentence can state
    # stays strict; a scope field only the conversation can settle does not.
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
        "only_account_id",
        "only_account_ids",
        "transaction_ids",
        "transaction_types",
        "group_limit",
    )
    if any(getattr(latest, field_name) != getattr(earlier, field_name) for field_name in scope):
        return None
    if latest.transaction_ids:
        return None
    if earlier.across_accounts != latest.across_accounts:
        # One comparison has one scope. `_compare_periods` scopes both operands through `_scoped`
        # with the same conversation, so the widening either question asked for is what both sides
        # actually read; `ComparisonPlan` requires its operands to say so, and rightly. Widened
        # with the helper rather than by setting the flag, so the narrowing being given up goes
        # with it -- the two agreed on `only_account_id` above, and they still do.
        earlier = _across_every_account(earlier)
        latest = _across_every_account(latest)

    # A metric is part of the question, not presentation. Turning both sides into SUM made
    # "¿en cuál hice más compras?" compare euros and made "¿qué ticket medio fue mayor?" compare
    # totals. Different metrics are two incompatible questions, so they stay on the ordinary
    # interpretation path rather than undergoing a conversion nobody requested.
    if latest.metric is not earlier.metric:
        return None
    if latest.metric is Metric.PERIOD_AVERAGE and latest.group_by is not earlier.group_by:
        return None
    # The two answers in the order they happened, not the order they were asked in (validation fix
    # 2, I-3): «¿y en junio?» after July, then «compáralos», made July the reference and «en
    # porcentaje» said «disminuyó» about June to July, which went up 55 %.
    return ComparisonPlan.between(*_the_earlier_first(earlier, latest, query.today))


@_recogniser
def _salary_history_plans(
    query: AskFinancialQuestionQuery,
) -> tuple[FinancialPlan, FinancialPlan] | None:
    """The payroll in view and the twelve complete calendar months immediately before it."""
    if _SALARY_HISTORY_COMPARISON.search(spanish.folded(query.sentence)) is None:
        return None
    latest = query.conversation.last_plan
    if (
        not isinstance(latest, FinancialPlan)
        or latest.categories != ("SALARY",)
        or latest.transaction_ids
    ):
        return None
    current_window = _calendar_month_window(latest, query.today)
    if current_window is None:
        return None
    history = replace(
        latest,
        period=None,
        date_range=AbsoluteRange(
            start=date(current_window.start.year - 1, current_window.start.month, 1),
            end=current_window.start - timedelta(days=1),
        ),
        group_by=GroupBy.MONTH,
        metric=Metric.SUM,
    )
    current = replace(
        latest,
        period=None,
        date_range=current_window,
        group_by=GroupBy.NONE,
        metric=Metric.SUM,
    )
    return current, history


@_recogniser
def _comparison_with_previous_period(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Compare the plan in view with the immediately preceding calendar period.

    The pronoun supplies the query scope and the sentence supplies only the other window. This is
    deliberately narrower than inventing a generic date shift: months, weeks and years have
    calendar boundaries the product already exposes; rolling N-day windows do not have one obvious
    meaning for "the previous period".
    """
    found = _COMPARE_WITH_PREVIOUS_PERIOD.fullmatch(_clean_turn(query.sentence))
    latest = query.conversation.last_plan
    if found is None or not isinstance(latest, FinancialPlan) or latest.transaction_ids:
        return None
    earlier_window = _previous_calendar_window(latest, found.group("unit"), query.today)
    if earlier_window is None:
        return None
    earlier = replace(
        latest,
        period=earlier_window if isinstance(earlier_window, RelativePeriod) else None,
        date_range=earlier_window if isinstance(earlier_window, AbsoluteRange) else None,
    )
    return ComparisonPlan.between(earlier, latest)


@_recogniser
def _comparison_with_a_named_window(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Compare the plan in view against the one window the sentence names.

    «Compáralo con junio», «compáralo con julio de 2025», «compáralo con la semana pasada». The
    pronoun is one operand and the sentence supplies the other, and that shape had no reader at
    all: `_period_comparison_plans` needs both operands already answered in the conversation,
    `_direct_period_comparison` needs both windows in the sentence, and
    `_comparison_with_previous_period` — the only one that paired the plan in view with a window
    from the sentence — reads a closed list of calendar-unit nouns followed by "anterior". A month
    name is not one of them, so a sentence anybody would say fell through to `capability_guard`,
    whose refusal then recommended «compara mis gastos de julio con junio»: a form that works,
    which is the tell that what was missing was a shape and not a word.

    **It stands aside for the two readers whose phrases it can also match**, rather than relying
    on being last in the cascade. Its regex has to overlap theirs — "compáralo con la semana
    pasada" is one of the sentences this exists for and it matches
    `_COMPARE_WITH_PREVIOUS_PERIOD`'s unit alternation too, so a disjoint pattern would refuse the
    property — and an overlap the order silently resolves is the one thing that cascade is
    measured for not having. So the overlap is removed here instead:

    - `_comparison_with_previous_period` owns "con el mes anterior", which is a unit back from the
      window in view. Read as a window named outright it would be *last month relative to today*,
      which after a question about July is July — the same window, and not a comparison. It only
      declines when the unit does not fit the window in view, and that is exactly when the
      sentence means the window rather than the step, which is what leaves "con la semana pasada"
      over a monthly question here.
    - `_a_comparison_owns_it` owns a tail that names two windows of its own — "compáralo con junio
      de 2025 y julio" — where taking the first and dropping the second answers half the question
      with a real figure on it. The same predicate `average`, `natural_period` and
      `deterministic_breakdown` ask, so "this sentence compares two periods" keeps one definition.

    The pair runs from the window that came first (validation fix 2, I-3), as every comparison
    reader's does now: the plan in view was always `latest`, the side whose change is reported,
    and after a question about June «compáralo con julio» then reported June's change from July
    — «disminuyó» about a month that went up. `_comparison_with_previous_period`'s window named
    is always the earlier one, so it was right by construction.

    **Two shapes it refuses**, and neither refusal is spelled out below. Nothing in view is caught
    by the `isinstance` — one operand of two is stated and the other would have to be invented,
    which is a clarification and the guard's to make. The same window twice is caught by
    `ComparisonPlan`, because deriving the second operand from the first makes them identical and
    `between` returns None; a copy of that invariant here would be a second definition of it.
    """
    found = _COMPARE_WITH_A_NAMED_WINDOW.fullmatch(_clean_turn(query.sentence))
    latest = query.conversation.last_plan
    if found is None or not isinstance(latest, FinancialPlan) or latest.transaction_ids:
        return None
    if latest.period is None and latest.date_range is None:
        # A plan a model returned with no window at all is no first operand, whatever else it says.
        return None
    if _comparison_with_previous_period(query) is not None or _a_comparison_owns_it(query):
        return None

    said = found.group("window")
    # "Con el año pasado" after a question about July means July of last year — which is what
    # `a_year_earlier` says and what "¿y el año anterior?" already answers. Read as a window named
    # outright it would be the whole of 2025: a real figure, twelve times the question. So the
    # reader that owns the phrase is asked first, rather than this one growing its own opinion.
    earlier_window: window_words.Window | None = window_words.a_year_earlier(
        said, latest.date_range
    )
    if earlier_window is None:
        # A window preposition, because the shape supplies one and `window_words` requires one. A
        # bare "junio" is not a window to that reader on purpose — in the middle of a sentence it
        # is as likely to be part of a name — but the fullmatch above has already established that
        # this fragment is the second operand and nothing else. Same reasoning as `_MONTH_MENTION`,
        # which accepts bare month names because the connector between two of them supplies the
        # structure the general reader is missing.
        earlier_window = window_words.window_in(f"en {said}", today=query.today)
    if earlier_window is None:
        return None

    earlier = replace(
        latest,
        period=earlier_window if isinstance(earlier_window, RelativePeriod) else None,
        date_range=earlier_window if isinstance(earlier_window, AbsoluteRange) else None,
    )
    # Deriving the second operand from the first is what keeps the pair comparable: every field in
    # `ComparisonPlan._SAME_SCOPE` agrees by construction, filters included, so the pronoun means
    # *that* query and not two bare months. It is also what leaves the same-window refusal where it
    # belongs — «compáralo con julio» about July builds two identical windows and `between` returns
    # None, which is `ComparisonPlan`'s invariant and does not want a second copy here.
    #
    # And in the order the two windows happened (I-3): the reference is the earlier one, whichever
    # the pronoun and the name are. «Compáralo con julio» after June made June the side that
    # changed, and «en porcentaje» said «disminuyó» about a month that went up.
    return ComparisonPlan.between(*_the_earlier_first(earlier, latest, query.today))


@_recogniser
def _direct_period_comparison(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Build the common two-window comparison stated completely in one sentence.

    Only closed, paired phrases are accepted. A sentence naming arbitrary dates still belongs to
    the planner until the date-range grammar can represent both sides without guessing.
    """
    folded = spanish.folded(query.sentence)
    if _DIRECT_PERIOD_COMPARISON.search(folded) is None:
        return None
    windows: tuple[window_words.Window, window_words.Window] | None = None
    named = (
        window_words.compared_date_ranges(query.sentence, today=query.today)
        or window_words.compared_calendar_months(query.sentence, today=query.today)
        or window_words.compared_calendar_years(query.sentence, today=query.today)
        or window_words.compared_natural_periods(query.sentence, today=query.today)
    )
    if named is not None:
        # The window that came first is the reference, whichever was said first (validation fix
        # 1, F3; P-1). This used to reverse the order they were said in, so that `_compare_periods`
        # — which writes `latest` first — would follow the person's words; «junio vs julio» then
        # had July as the reference and June as the side whose change is reported, and «en
        # porcentaje» said «disminuyó» about a month that went up 55 %. The change is a fact about
        # time, so the pair is ordered by time — as the other two readers that build a pair order
        # theirs since round 2 (I-3); before it, they did not either. Two windows this cannot place
        # keep the order they always had here.
        windows = _the_earlier_first(named[1], named[0], query.today)
    elif re.search(r"\beste\s+mes\b", folded) and re.search(
        r"\b(?:(?:el\s+)?mes\s+(?:pasado|anterior)|el\s+(?:pasado|anterior))\b",
        folded,
    ):
        windows = (
            RelativePeriod(Period.LAST_MONTH),
            RelativePeriod(Period.THIS_MONTH),
        )
    elif re.search(r"\besta\s+semana\b", folded) and re.search(
        r"\b(?:la\s+)?semana\s+(?:pasada|anterior)\b", folded
    ):
        windows = (
            RelativePeriod(Period.LAST_WEEK),
            RelativePeriod(Period.THIS_WEEK),
        )
    elif re.search(r"\beste\s+ano\b|\ben\s+lo\s+que\s+va\s+de\s+ano\b", folded) and re.search(
        r"\b(?:el\s+)?ano\s+(?:pasado|anterior)\b", folded
    ):
        windows = (
            window_words.whole_year(query.today.year - 1),
            RelativePeriod(Period.THIS_YEAR),
        )
    if windows is None:
        return None

    facts = explicit_facts.read(query.sentence, today=query.today)
    named_categories = category_words.precisely_named_in(query.sentence)
    if len(named_categories) > 1:
        # Two categories and two windows describe four possible comparisons. Do not choose one.
        return None
    states_subject = re.search(
        r"\bgast|\bingres|\bincome|\bcobr|\brecib|\bcompr|\bpagu|\bcargo"
        r"|\bnomina|\bsueldo|\bsalario|\bmovimientos?|\boperaciones?"
        r"|\bmedia|\bmedio|\bpromedio|\bticket",
        folded,
    )
    previous = (
        query.conversation.last_plan
        if states_subject is None and isinstance(query.conversation.last_plan, FinancialPlan)
        else None
    )
    categories = named_categories or (previous.categories if previous is not None else ())
    metric = facts.metric or (previous.metric if previous is not None else Metric.SUM)
    group_by = (
        GroupBy.ACCOUNT
        if facts.asks_account_breakdown
        else (
            facts.group_by
            if metric is Metric.PERIOD_AVERAGE and facts.group_by is not None
            else (
                previous.group_by
                if metric is Metric.PERIOD_AVERAGE and previous is not None
                else GroupBy.NONE
            )
        )
    )
    if metric is Metric.PERIOD_AVERAGE and group_by not in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        return None
    direction = facts.direction
    if direction is None:
        if re.search(r"\bgast|\bpagu|\bcompr|\bcargo", folded):
            direction = Direction.EXPENSE
        elif named_categories:
            direction = _direction_of(named_categories)
        elif previous is not None:
            direction = previous.direction
        else:
            direction = Direction.ALL
    base = explicit_facts.enforce_exclusions(
        FinancialPlan(
            direction=direction,
            period=windows[1] if isinstance(windows[1], RelativePeriod) else None,
            date_range=windows[1] if isinstance(windows[1], AbsoluteRange) else None,
            categories=categories,
            excluded_categories=(
                tuple(
                    category
                    for category in previous.excluded_categories
                    if category not in named_categories
                )
                if previous is not None
                else ()
            ),
            merchants=(
                () if named_categories else (previous.merchants if previous is not None else ())
            ),
            excluded_merchants=(
                ()
                if named_categories
                else (previous.excluded_merchants if previous is not None else ())
            ),
            group_by=group_by,
            metric=metric,
            include_internal=previous.include_internal if previous is not None else False,
            internal_only=previous.internal_only if previous is not None else False,
            across_accounts=previous.across_accounts if previous is not None else False,
            only_account_id=previous.only_account_id if previous is not None else None,
            only_account_ids=previous.only_account_ids if previous is not None else (),
        ),
        facts,
    )
    carried = tuple(spanish.folded(merchant) for merchant in base.merchants)
    if any(not any(word in shop for shop in carried) for word in _shops_said(query.sentence)):
        # A shop this reader does not carry (final review, I-5). It reads no shop from the sentence
        # — it has no list of them to match a name against — and the pair without it compared
        # every movement for «junio vs julio en Mercadona». The planner reads the shop.
        return None
    earlier = replace(
        base,
        period=windows[0] if isinstance(windows[0], RelativePeriod) else None,
        date_range=windows[0] if isinstance(windows[0], AbsoluteRange) else None,
    )
    return ComparisonPlan.between(earlier, base)


def _a_comparison_owns_it(query: AskFinancialQuestionQuery) -> bool:
    """Whether the sentence states a two-window comparison, which no single-period reader may take.

    Four of the turns the cascade's order decides are this shape. "¿En qué mes fue mayor mi gasto
    medio por movimiento, julio o junio?" is read by `average` as July alone; "compara mis gastos
    de este semestre con el anterior" is read by `natural_period` as this semester alone. Each
    loser answers about one of the two periods the sentence names — half the question, with a real
    figure on it, and nothing downstream can tell that the other half is missing.

    Asked of the comparison reader itself rather than of a vocabulary of its own, so "this sentence
    compares two periods" has one definition instead of two that drift apart. It is a pure function
    over the query and the cascade already runs it first on these turns: this is that ordering said
    out loud, in the readers it was silently deciding for.
    """
    return _direct_period_comparison(query) is not None


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
        name="_salary_history_plans",
        read=_salary_history_plans,
        stood_at=5,
        when=Precondition(
            "the sentence asks to compare a payroll with its history, over a plan in view",
            lambda query: (
                _SALARY_HISTORY_COMPARISON.search(spanish.folded(query.sentence)) is not None
                and _a_plan_is_in_view(query)
            ),
        ),
    ),
    Reader(
        name="_period_comparison_plans",
        read=_period_comparison_plans,
        stood_at=6,
        when=Precondition(
            "the whole turn is a comparison pronoun",
            lambda query: (
                _PERIOD_COMPARISON_REFERENCE.fullmatch(_clean_turn(query.sentence)) is not None
            ),
        ),
    ),
    Reader(
        name="_comparison_with_previous_period",
        read=_comparison_with_previous_period,
        stood_at=7,
        when=Precondition(
            "the whole turn asks for the period before the plan in view",
            lambda query: (
                _COMPARE_WITH_PREVIOUS_PERIOD.fullmatch(_clean_turn(query.sentence)) is not None
                and _a_plan_is_in_view(query)
            ),
        ),
    ),
    Reader(
        name="_direct_period_comparison",
        read=_direct_period_comparison,
        stood_at=8,
        when=Precondition(
            "the sentence compares two periods outright",
            lambda query: (
                _DIRECT_PERIOD_COMPARISON.search(spanish.folded(query.sentence)) is not None
            ),
        ),
    ),
    Reader(
        name="_comparison_with_a_named_window",
        read=_comparison_with_a_named_window,
        stood_at=9,
        when=Precondition(
            "the whole turn compares the plan in view with one named window",
            lambda query: (
                _COMPARE_WITH_A_NAMED_WINDOW.fullmatch(_clean_turn(query.sentence)) is not None
                and _a_plan_is_in_view(query)
            ),
        ),
    ),
)
