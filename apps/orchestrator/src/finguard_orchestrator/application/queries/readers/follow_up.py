"""The general reader: the turn that continues the question already on screen.

One reader, and its own module — which needs an argument, because one module per reader is the
thing this layout is supposed to avoid.

The argument is that this reader is not one of a kind of thing. Its precondition is the only one in
the table that is about the **turn** rather than the sentence — "a query is in view for this turn to
be about" — and A1 measured what follows from that: it is the *most general* reader in the table and
it wins three of the five contested pairs, against `_average_plan`, `_cumulative_plan` and
`_deterministic_breakdown_plan`, each of which knows something specific the sentence said. Ordering
by specificity would flip twelve sentences. A reader that contends with three others in three other
modules, and beats them, is not a member of any of their families.

Its vocabulary says the same thing. Nine of the patterns here are «¿y por semana?»-shaped — a
fragment that means nothing without the question behind it — and no other reader reads one.

`_account_follow_up_plan` comes with it: it is not a registered reader (the handler asks it
directly, and only on a `FINANCIAL_QUERY` or a `CORRECTION`), but it is the same idea about
accounts, this reader consults it, and its two patterns are read by nothing else.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Final

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    _query_for_reader,
    _starts_new_financial_scope,
    _window_of,
)
from finguard_orchestrator.application.queries.reader_registry import Precondition, Reader
from finguard_orchestrator.application.queries.readers.dimensions import (
    _GROUP_RANKING_SIGNAL,
    _group_dimension_in,
)
from finguard_orchestrator.application.queries.readers.seam import _recogniser
from finguard_orchestrator.application.queries.readers.subjects import (
    _internal_movement_request,
)
from finguard_orchestrator.application.queries.readers.windows import (
    _previous_calendar_window,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
)
from finguard_orchestrator.domain.models.planning_context import (
    ComparisonFocus,
    QueryFocus,
    focus_of,
)
from finguard_orchestrator.domain.models.result_reference import (
    result_reference_of,
)
from finguard_orchestrator.domain.services import (
    account_words,
    capability_guard,
    category_words,
    continuation_patch,
    explicit_facts,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

# This reader's own logger, so a `turn.window` record says which module emitted it. The name
# follows the module, which is the convention everywhere else in this service — the alternative
# was importing the handler's logger, and a reader module that imports the handler is the cycle
# this package exists to avoid.
logger = logging.getLogger(__name__)

# "¿Y el mes anterior?" — the same question, one calendar unit back from the window on screen.
#
# The year had this and nothing else did. "¿Y el año anterior?" after July 2026 has meant July 2025
# since it was written; "¿y el mes anterior?" after the same question was not read at all, so it
# went to the planner and came back as *last month* with the category dropped — a different month
# from the one asked for, and the petrol filter gone from a question about petrol.
#
# The unit is handed to `_previous_calendar_window`, which the comparison path already uses for
# "compruébalo con el mes anterior". One definition of what precedes a window, rather than this
# reader growing a second one that would answer June in one sentence and July in the other.
#
# **"Anterior" only, except for the year.** "Anterior" is relative to the window in view and says
# so; "el mes pasado" is relative to *today* and is how everybody names last month outright, so
# reading it as a step back would answer June to somebody who said July. The year keeps both words
# because "el año pasado" after a month means that month a year ago, which is what it already did.
_A_CALENDAR_UNIT_BACK: Final = re.compile(
    r"(?:y\s+)?(?:el\s+|la\s+)?(?:"
    r"(?P<unit>fin\s+de\s+semana|trimestre|semestre|quincena|mes|semana|periodo)\s+"
    r"(?:anterior|previo|previa)"
    r"|(?P<year>ano)\s+(?:anterior|pasado)"
    r")"
)


# The same words anywhere in a longer sentence, which is a different question: not "is this turn a
# step back" but "is a step back what this turn is about", and the answer keeps the generic window
# patch off it. "Vale, compruébalo con el mes anterior" is a comparison between two months and the
# comparison reader owns it; read as a plain new window it becomes one month with the other operand
# dropped.
#
# **This was written for the year alone**, as `\bano\s+(anterior|pasado)\b` inline, and the day the
# month learned the word "anterior" the corpus grew a turn whose reader was decided by the order of
# the cascade. Same mould as the four before it — a rule written for the member in front of somebody
# rather than for the class — and this time a parity test is what turned it up.
_A_UNIT_BACK_ANYWHERE: Final = re.compile(
    r"\b(?:fin\s+de\s+semana|trimestre|semestre|quincena|mes|semana|periodo)\s+"
    r"(?:anterior|previo|previa)\b"
    r"|\bano\s+(?:anterior|pasado)\b"
)


_SALARY_ACCOUNT_FOLLOW_UP: Final = re.compile(
    r"^(?:en\s+)?la\s+que\s+(?:ingres\w*|cobr\w*)\s+(?:la\s+)?nomina"
    r"(?:\s+(?:vaya|vamos))?(?:\s+[a-z])?$"
)


_ACCOUNT_SELECTION_FOLLOW_UP: Final = re.compile(
    r"^(?:ahora\s+)?(?:solo|solamente)\b|\b(?:no|menos|excepto|salvo)\s+"
    r"(?:el|la|los|las|mi|mis)?\s*(?:fondo|hucha|cuenta)\b"
    r"|^(?:no[\s,]+)?(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\b"
)


_CATEGORY_FILTER_FOLLOW_UP: Final = re.compile(
    r"^(?:(?:y|ahora)\s+)?(?:solo\s+)?en\s+"
    r"|^(?:ahora\s+)?(?:solo|solamente)\b"
    r"|^perdona\b.{0,35}\b(?:queria|refiero|referia)\b"
    r"|^(?:no[\s,]+)?(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\b"
    r"|^volvamos?\s+a\b"
    r"|^(?:quedate|dejame|deja)\s+solo\s+con\b"
)


_CATEGORY_REFERENCE: Final = re.compile(r"\b(?:esa|esta|aquella|la\s+misma)\s+categoria\b")


# The nouns that name a charge, from the vocabulary that owns them. They were written out here as
# well and the two copies stayed level only by accident: the rails were missing from both, and each
# was found on its own. "Bizum" and "transferencia" are also categories, which is what makes the
# named-category branch necessary — without it the sentence declines and loses the window rather
# than counting the kind it names.
_COUNT_FOLLOW_UP: Final = re.compile(
    rf"^(?:y\s+)?cuant[oa]s?\s+(?:{explicit_facts.CHARGE_NOUNS})\b"
)


_SUM_EXPENSE_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?cuanto\s+(?:he\s+)?(?:gaste|gastado|pague|pagado|compre|comprado)\b"
)


_SUM_INCOME_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?cuanto\s+(?:he\s+)?(?:ingrese|ingresado|cobre|cobrado|recibi|recibido)\b"
)


_DIRECTION_ONLY_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:ahora\s+)?(?:no[\s,]+)?"
    r"(?:(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\s+)?"
    r"(?:solo\s+)?(?:los?\s+)?(?P<direction>ingresos?|gastos?)$"
)


_TOP_MERCHANT_FOLLOW_UP: Final = re.compile(
    r"\ben\s+que\s+(?:comercio|tienda|sitio|establecimiento)\b.{0,45}"
    r"\b(?:gast|pag|compr)\w*\b.{0,18}\bmas\b"
)


# A name in the slot a filter goes in. "¿Cuántas compras hice en Mercadona?" is a question about a
# shop, and a shop is the one filter the ellipsis branch below cannot represent: it patches the
# previous query and keeps its categories, so the turn came back counting *restaurant* purchases —
# a real figure about a different question, which is the failure this service is built around.
#
# The length guard cannot see it. That sentence is five words, exactly the limit, and length was
# never what tells an ellipsis from a complete question: "¿y en gasolina?" is an ellipsis because
# it introduces nothing this path does not already know, and this one is not because it does.
#
# Capitalised, and that is the whole test. Spanish capitalises proper nouns and almost nothing else
# mid-sentence, so a capital in the "en X" slot is the sentence saying "this is a name" — no
# vocabulary to maintain and no shop list to be wrong about. Somebody typing all in lower case is
# not covered here; they are covered by declining to guess further down the path, and the cost of
# reading the capital as a name when it is not is a turn that reaches the planner, which is the
# harmless side.
_A_NAME_IN_A_FILTER_SLOT: Final = re.compile(
    r"\b(?:en|de|con)\s+(?:el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?([a-z][a-z0-9]{2,})"
)


def _names_a_filter_this_cannot_read(query: AskFinancialQuestionQuery) -> bool:
    """Whether the sentence names something in a filter slot that no vocabulary here claims."""
    for word in _A_NAME_IN_A_FILTER_SLOT.findall(spanish.folded(query.sentence)):
        if category_words.named_in(word) or account_words.named_in(word):
            continue
        if explicit_facts.read(word, today=query.today).window is not None:
            continue
        return True
    return False


@_recogniser
def contextual_follow_up_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Patch explicit filters and calculations onto the last validated financial query."""
    focus = focus_of(query.conversation)
    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    folded = re.sub(r"^(?:vale|bueno|ok|perfecto)\W+", "", folded)
    facts = explicit_facts.read(query.sentence, today=query.today)
    if isinstance(focus, QueryFocus):
        previous = focus.current
    elif (
        isinstance(focus, ComparisonFocus)
        and facts.window is not None
        and facts.metric is None
        and facts.group_by is None
        and facts.direction is None
        and not facts.names_a_category
        and not facts.excluded_categories
        and not facts.excluded_merchants
        and continuation_patch.read(query.sentence, today=query.today).status
        is not continuation_patch.PatchStatus.AMBIGUOUS
    ):
        # A window-only turn after a comparison selects one view of the same measure. This is a
        # comparison operation, not permission for generic patches to mutate one operand.
        previous = focus.value.latest
    else:
        previous = None
    if not isinstance(previous, FinancialPlan):
        return None

    # A follow-up over an answer that took two queries belongs to the path that ran both. This is
    # the general reader, and being general is what made it claim seven turns another recogniser
    # also claimed — the cascade's order picked the right one every time, and nothing said so.
    #
    # "¿Y el mes pasado?" after "¿qué porcentaje de mi nómina se va en alquiler?" is the whole
    # proportion moved to another month; read here it is the rent side alone, with the income
    # operand dropped and a real figure left standing where a percentage was asked for. Same for a
    # savings rate and a net cash flow.
    if query.conversation.last_ratio is not None or query.conversation.pending_ratio is not None:
        return None

    # And the sentence itself may belong to a reader that knows more about it than this one does.
    # "¿Cuánto he movido a la hucha este año?" is a movement between the holder's own accounts, and
    # read here it comes back EXPENSE where the internal reader says INCOME — the same figure with
    # the wrong sign in front of it. "En la que ingreso la nómina" names an account, and the
    # account reader is right about which one for the same reason.
    #
    # Asked of those readers rather than of a vocabulary of this one's own, which is the same
    # choice the comparison guard makes further down: two definitions of "this sentence is about
    # an internal movement" would drift apart, and only one of them would be maintained.
    #
    # Declining on the *state* was tried first and is wrong: `previous.internal_only` takes "¿y el
    # mes pasado?" away from the only reader that keeps an internal scope, and the answer comes
    # back as ordinary spending. `internal_movement` claims the sentence that names a movement, not
    # a bare window over a plan that happens to be internal.
    if _internal_movement_request(query) is not None or _account_follow_up_plan(query) is not None:
        return None

    if (
        previous.metric is Metric.CUMULATIVE
        and facts.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
        and len(folded.split()) <= 5
        and folded.startswith(("y ", "ahora ", "por "))
    ):
        return explicit_facts.enforce(replace(previous, group_by=facts.group_by), facts)

    if _CATEGORY_REFERENCE.search(folded):
        reference = result_reference_of(query.conversation, focus)
        referenced = reference.category if reference is not None else None
        if (
            referenced is None
            and isinstance(focus, ComparisonFocus)
            and len(focus.value.latest.categories) == 1
            and focus.value.latest.categories == focus.value.earlier.categories
        ):
            # This is common query scope, not a row referent borrowed from one comparison
            # operand. ResultReference remains unavailable for comparison focus.
            referenced = focus.value.latest.categories[0]
        if referenced is None or category_words.words_for(referenced) is None:
            return None
        return explicit_facts.enforce(
            explicit_facts.reshaped(
                replace(
                    previous,
                    categories=(referenced,),
                    excluded_categories=tuple(
                        category
                        for category in previous.excluded_categories
                        if category != referenced
                    ),
                    merchants=(),
                    excluded_merchants=(),
                ),
                group_by=facts.group_by or GroupBy.NONE,
                metric=facts.metric or previous.metric,
            ),
            facts,
        )

    if (
        facts.group_by
        in {GroupBy.CATEGORY, GroupBy.MERCHANT, GroupBy.COUNTERPARTY, GroupBy.ACCOUNT}
        and previous.metric not in {Metric.CUMULATIVE, Metric.PERIOD_AVERAGE}
        and len(folded.split()) <= 7
        and re.search(r"\b(?:separa|separalo|desglosa|desglosalo|agrupa|agrupalo|por)\b", folded)
    ):
        # "Sepáralo por comercio" changes only the presentation dimension of the query in view.
        # Sending that three-word patch to a model made it re-create the window and filters, which
        # is both slower and an opportunity to silently widen the result.
        return explicit_facts.enforce(
            replace(
                previous,
                group_by=facts.group_by,
                group_limit=(
                    None
                    if facts.group_by in {GroupBy.NONE, GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
                    else previous.group_limit
                ),
            ),
            facts,
        )

    if facts.excluded_categories or facts.excluded_merchants:
        # A negative filter patches the validated query in view. No model needs to re-emit the
        # window, direction and calculation merely to understand "sin alquiler".
        return explicit_facts.enforce(previous, facts)

    a_unit_back = _A_CALENDAR_UNIT_BACK.fullmatch(folded)
    if a_unit_back is not None:
        unit = a_unit_back.group("unit") or a_unit_back.group("year")
        earlier_window = _previous_calendar_window(previous, unit, query.today)
        if earlier_window is not None:
            return replace(
                previous,
                period=earlier_window if isinstance(earlier_window, RelativePeriod) else None,
                date_range=earlier_window if isinstance(earlier_window, AbsoluteRange) else None,
            )

    # A turn containing only a new window patches the query already on screen. Replanning
    # "en lo que llevamos de año" from scratch discarded both categories in a comparison.
    if (
        facts.window is not None
        and not facts.names_a_category
        and continuation_patch.read(query.sentence, today=query.today).status
        is not continuation_patch.PatchStatus.AMBIGUOUS
        and facts.metric is None
        and facts.direction is None
        and len(folded.split()) <= 8
        and not _starts_new_financial_scope(query.sentence, today=query.today)
        and not (_group_dimension_in(folded) and _GROUP_RANKING_SIGNAL.search(folded))
        and not _A_UNIT_BACK_ANYWHERE.search(folded)
        and capability_guard.unsupported_reason(query.sentence) is None
    ):
        patched = explicit_facts.enforce(previous, facts)
        logger.info(
            "turn.window",
            extra={
                "explicit": _window_of(patched),
                "planned": _window_of(previous),
                "inherited": _window_of(previous),
            },
        )
        return patched

    if facts.asks_comparison_reference and previous.group_by is not GroupBy.NONE:
        return previous

    if _TOP_MERCHANT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(
                previous,
                direction=Direction.EXPENSE,
                merchants=(),
                merchant_terms=(),
                group_by=GroupBy.MERCHANT,
                metric=Metric.SUM,
            ),
            facts,
        )

    named = category_words.precisely_named_in(query.sentence)
    if named and _CATEGORY_FILTER_FOLLOW_UP.search(folded):
        group_by = (
            GroupBy.NONE
            if previous.group_by is GroupBy.CATEGORY and len(named) == 1
            else previous.group_by
        )
        return explicit_facts.enforce(
            replace(
                previous,
                categories=named,
                excluded_categories=(),
                merchants=(),
                merchant_terms=(),
                group_by=group_by,
            ),
            facts,
        )

    # These are deliberately short ellipses. A complete question can introduce a merchant or a
    # category the closed vocabulary does not know, and inheriting the old filters there would
    # answer a different question with a plausible figure.
    #
    # And a complete question is not an ellipsis however short it is, which the window branch above
    # already says and this one did not. "¿Cuánto gasté el mes pasado?" is five words, so it
    # reached the sum-follow-up below and became the previous plan with the direction flipped —
    # after "¿cuánto he invertido en el último año?", that plan is restricted to the fund and to
    # internal movements only, and flipping it to EXPENSE answers "what did you spend" with what
    # went into the fund. Two branches of one function disagreed about what an ellipsis is.
    if (
        _names_a_filter_this_cannot_read(query)
        or len(folded.split()) > 5
        or _starts_new_financial_scope(query.sentence, today=query.today)
    ):
        return None

    # Counting a kind the vocabulary *does* know is the exception to the rule below, and it is the
    # difference between the two reasons a follow-up gets refused. Declining protects the turn that
    # introduces something this path cannot represent; a named category is not that — it is a
    # filter, and replacing one filter is what this whole function does.
    #
    # Without this, "¿cuántos bizums tengo?" declines the way "¿cuántas compras hice en Mercadona?"
    # does, and reaches the planner having dropped the window the conversation was in. Two turns
    # earlier the same question about compras kept it.
    if named and _COUNT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(
                previous,
                categories=named,
                excluded_categories=(),
                merchants=(),
                merchant_terms=(),
                group_by=GroupBy.NONE,
                metric=Metric.COUNT,
            ),
            facts,
        )
    if named:
        return None
    if _COUNT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(replace(previous, metric=Metric.COUNT), facts)
    if _SUM_EXPENSE_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(previous, direction=Direction.EXPENSE, metric=Metric.SUM), facts
        )
    if _SUM_INCOME_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(previous, direction=Direction.INCOME, metric=Metric.SUM), facts
        )
    direction_only = _DIRECTION_ONLY_FOLLOW_UP.fullmatch(folded)
    if direction_only:
        direction = (
            Direction.INCOME
            if direction_only.group("direction").startswith("ingreso")
            else Direction.EXPENSE
        )
        return replace(previous, direction=direction)
    return None


def _account_follow_up_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Focus the previous query on an account named as a short conversational reply."""
    context = _query_for_reader(query)
    previous = (
        None
        if not context.returned_from_balance
        and _starts_new_financial_scope(context.wording, today=query.today)
        else context.plan
    )
    if not isinstance(previous, FinancialPlan) or not account_words.named_in(context.wording):
        return None
    folded = spanish.folded(context.wording).strip(" ¿?¡!.,")
    if not _SALARY_ACCOUNT_FOLLOW_UP.fullmatch(folded) and not _ACCOUNT_SELECTION_FOLLOW_UP.search(
        folded
    ):
        if len(folded.split()) > 4:
            return None
        if window_words.window_in(context.wording, today=query.today) is not None:
            return None
    selected = replace(previous, group_by=GroupBy.NONE)
    window = window_words.window_in(context.wording, today=query.today)
    if window is None:
        return selected
    return replace(
        selected,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
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
        name="contextual_follow_up_plan",
        read=contextual_follow_up_plan,
        stood_at=10,
        when=Precondition(
            "a query is in view for this turn to be about",
            lambda query: isinstance(focus_of(query.conversation), QueryFocus | ComparisonFocus),
        ),
    ),
)
