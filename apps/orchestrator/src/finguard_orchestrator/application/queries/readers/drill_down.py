"""§3.3's act: one resolved bar of the chart in view becomes a bounded plan, run once by merchant.

`resolve_reference` (Task 2, `domain/services/visual_reference.py`) says *which* bar a sentence
means -- one, several, or none. This module says what running that bar looks like: the plan in
view narrowed to it, never widened, regrouped by merchant -- design §3.3's own third sentence is
«¿Qué comercio es responsable?» -- or, when several bars fit, the Spanish question that names them
so the handler can ask instead of guessing.

Every `None` below means the same thing: today's path, unchanged. The planner answers as it always
has, whether that is because there is no chart, no plan, no match, or a plan shape the chart cannot
narrow.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.visual_reference import Ambiguous, NoMatch
from finguard_orchestrator.domain.services import answer_writer, category_words, explicit_facts
from finguard_orchestrator.domain.services.visual_reference import parsed_month, resolve_reference
from finguard_orchestrator.domain.services.window_words import whole_month
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


@dataclass(frozen=True)
class DrillDown:
    """The bounded plan behind one bar of the chart in view, and the bar's label as drawn."""

    plan: FinancialPlan
    label: str


@dataclass(frozen=True)
class DrillAsk:
    """Two or more bars fit: the question that names them, in the chart's order."""

    text: str


def _candidate_name(key: str) -> str:
    """One word for a candidate, in the chart's own order: a month a person would say, a
    category's one-word name, or the key itself when it is neither."""
    parsed = parsed_month(key)
    if parsed is not None:
        return answer_writer.month_group_name(key)
    if category_words.words_for(key) is not None:
        return category_words.name_of(key)
    return key


def _ask(candidates: tuple[str, ...]) -> str:
    """Name up to four candidates as alternatives, in the chart's own rank order; more than four
    names nothing and asks for the label instead."""
    if len(candidates) > 4:
        return "¿A qué barra te refieres? Dime su etiqueta."
    names = [_candidate_name(key) for key in candidates]
    # `answer_writer.listed` joins a *stated* list with "y" ("a, b y c"); a question naming
    # alternatives needs "o" instead ("a, b o c"), so the join is built here rather than reused.
    alternatives = f"{', '.join(names[:-1])} o {names[-1]}"
    return f"¿A qué te refieres: {alternatives}?"


def read_drill_down(sentence: str, conversation: Conversation) -> DrillDown | DrillAsk | None:
    """The bounded plan `sentence` drills into, off the newest trusted chart in `conversation` --
    an ambiguous question when several bars fit, or `None` for today's path.

    Trust first (R16 depends on the plan the chart was drawn from, and an unsigned chart's labels
    decide nothing): only the newest context, and only when it is trusted, is ever read.

    The regrouping below never refuses: the metric it hands `explicit_facts.reshaped` is always
    normalised to `SUM` or `COUNT` first, and the domain only ever couples a metric to a grouping
    for `PERIOD_AVERAGE`/`CUMULATIVE` -- so a merchant regrouping here is always accepted, and there
    is no case left to guard against.
    """
    if not conversation.visual_contexts:
        return None
    newest = conversation.visual_contexts[-1]
    if not newest.trusted:
        return None
    plan = conversation.last_plan
    if not isinstance(plan, FinancialPlan):
        return None

    reference = resolve_reference(sentence, newest)
    if isinstance(reference, NoMatch):
        return None
    if isinstance(reference, Ambiguous):
        return DrillAsk(_ask(reference.candidates))

    # The only case left in `Reference` is `Resolved`. The label's own shape decides what it is --
    # a "rank" reading needs no extra rule, because a rank is read off the chart's own order, not
    # named, and the label it lands on is a month or a category exactly like a named one.
    label = reference.label
    parsed = parsed_month(label)
    if parsed is not None:
        if plan.group_by is not GroupBy.MONTH:
            return None
        year, month = parsed
        # The chart's bars are the plan's own month buckets, so the label is inside the plan's
        # window by construction and no date arithmetic beyond `whole_month` is needed (R16).
        bounded = replace(plan, period=None, date_range=whole_month(year, month), group_limit=None)
    else:
        unfiltered_or_named = not plan.categories or label in plan.categories
        if plan.group_by is not GroupBy.CATEGORY or not unfiltered_or_named:
            return None
        bounded = replace(plan, categories=(label,), group_limit=None)

    # Never widened: the account scope, direction, exclusions and every other filter travel
    # unchanged on both `replace` calls above and the one below -- only the window (or the
    # category filter) narrows, and the grouping and metric change to answer "which merchant".
    metric = plan.metric if plan.metric in {Metric.SUM, Metric.COUNT} else Metric.SUM
    bounded = explicit_facts.reshaped(bounded, group_by=GroupBy.MERCHANT, metric=metric)
    return DrillDown(bounded, label)
