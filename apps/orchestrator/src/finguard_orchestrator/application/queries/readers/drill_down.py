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
from datetime import date

from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.visual_reference import Ambiguous, NoMatch
from finguard_orchestrator.domain.services import answer_writer, category_words, explicit_facts
from finguard_orchestrator.domain.services.plan_digest import plan_digest
from finguard_orchestrator.domain.services.visual_reference import parsed_month, resolve_reference
from finguard_orchestrator.domain.services.window_words import whole_month
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan

# The grouping shapes a chart's own labels can ever be drilled by. Read before `resolve_reference`
# runs at all (I-4): a chart whose plan cannot be drilled -- MERCHANT, COUNTERPARTY, ACCOUNT, DAY,
# WEEK, NONE -- must never ask which bar was meant, because no reply to that ask could be acted on
# either (the reader's own next lines refuse every one of those groupings just the same).
_DRILLABLE_GROUPINGS = frozenset({GroupBy.MONTH, GroupBy.CATEGORY})


@dataclass(frozen=True)
class DrillDown:
    """The bounded plan behind one bar of the chart in view, and the bar's label as drawn."""

    plan: FinancialPlan
    label: str


@dataclass(frozen=True)
class DrillAsk:
    """Two or more bars fit: the question that names them, in the chart's order.

    `suggestions` (item 5, Wave 3 hardening, T8b) is empty for that ordinary case -- the question
    already names its own alternatives in words -- and non-empty only for the NONE-grouped ask
    below, whose two chips are the breakdown itself rather than a bar's label.
    """

    text: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DrillUnbound:
    """A-2 (Wave 3 hardening): the chart in view is not this plan's own -- `metric_definition_
    borrowed` says so directly, where a bare digest comparison against the same back-filled field
    cannot. The honest ask, never a silent narrow of whatever `conversation.last_plan` happens to
    hold."""

    text: str


_UNBOUND_TEXT = (
    "Ese gráfico no es de esta consulta: no puedo profundizar en él sin volver a calcularlo. "
    "¿Quieres que repita la pregunta sobre el gráfico en pantalla?"
)


def _candidate_name(key: str) -> str | None:
    """One word for a candidate, in the chart's own order: a month a person would say, or a
    category's one-word name -- `None` when `key` is neither (M9, security, fix wave). It used to
    fall back to `key` itself, and the reachable source there is the ledger's own data: a merchant
    or counterparty name is a payee's own display string, up to 64 characters, and it belongs in
    nobody's assistant's own question unreviewed. `category_words.name_of` cannot be trusted to
    say so on its own -- for an unglossed key it falls back to the identifier the same way this
    function used to -- so `words_for` is what actually gates it: `None` there is the only honest
    "this is not one of ours".
    """
    parsed = parsed_month(key)
    if parsed is not None:
        return answer_writer.month_group_name(key)
    if category_words.words_for(key) is not None:
        return category_words.name_of(key)
    return None


def _ask(candidates: tuple[str, ...]) -> str:
    """Name up to four candidates as alternatives, in the chart's own rank order -- and only when
    `_candidate_name` recognises every one of them. More than four, or any candidate it does not
    recognise (M9: a raw ledger key would otherwise be echoed straight into the question), names
    nothing and asks for the label instead. Never fewer than two: `candidates` comes from an
    `Ambiguous`, and `Ambiguous.__post_init__` already refuses fewer than two.
    """
    generic = "¿A qué barra te refieres? Dime su etiqueta."
    if len(candidates) > 4:
        return generic
    named = [_candidate_name(key) for key in candidates]
    if any(name is None for name in named):
        return generic
    names = [name for name in named if name is not None]
    # `answer_writer.listed` joins a *stated* list with "y" ("a, b y c"); a question naming
    # alternatives needs "o" instead ("a, b o c"), so the join is built here rather than reused.
    alternatives = f"{', '.join(names[:-1])} o {names[-1]}"
    return f"¿A qué te refieres: {alternatives}?"


def read_drill_down(
    sentence: str, conversation: Conversation, *, today: date
) -> DrillDown | DrillAsk | DrillUnbound | None:
    """The bounded plan `sentence` drills into, off the newest trusted chart in `conversation` --
    an ambiguous question when several bars fit, or `None` for today's path.

    Trust first (R16 depends on the plan the chart was drawn from, and an unsigned chart's labels
    decide nothing): only the newest context, and only when it is trusted, is ever read. Binding is
    next, replacing the by-construction argument the fix wave found wrong: a label is inside the
    window its OWN rows cover, always -- but that is a fact about the rows, not about the whole
    calendar month a month label names, and the chart's own rows are only known to be *this plan's*
    once `newest.plan_digest` is checked against `plan_digest(plan)` rather than assumed from
    `conversation.last_plan` alone (I-1: a KPI turn after a chart, a restyle of an older chart, or
    a client-edited `last_plan` all fail this check rather than narrowing the wrong query).
    Feasibility is checked before any question is asked (I-4): a chart whose plan can never be
    drilled -- every
    grouping outside `_DRILLABLE_GROUPINGS`, or a MONTH chart with no known window -- returns `None`
    before `resolve_reference` ever runs, so an ambiguous label on an undrillable chart never asks a
    question no reply to it could act on. A MERCHANT-grouped plan is never drilled in 4.3 for the
    same reason `_DRILLABLE_GROUPINGS` excludes it -- the labels are merchants, which the resolver
    does not read -- so the second drill of a chain is today's path.

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
    if newest.plan_digest != plan_digest(plan):
        # I-1: the chart in view is not this plan's -- a different turn's chart (a KPI answered
        # after it, an older chart a restyle put back in view) or a client that edited `last_plan`
        # underneath an unrelated chart. Refuse, never guess which plan the chart was really drawn
        # from.
        return None
    if newest.metric_definition_borrowed:
        # A-2 (Wave 3 hardening): the digest check above just passed, but it is tautological here
        # -- a comparison chart's `plan_digest` fingerprints `conversation.last_plan` AFTER
        # `stream()`'s own back-fill, so a later re-derivation of the same field always matches,
        # whether or not the chart actually drew that plan. `metric_definition_borrowed` is the one
        # fact only the producer holds; ask honestly rather than narrow the latest window as if it
        # were the chart's own.
        return DrillUnbound(_UNBOUND_TEXT)
    if plan.group_by is GroupBy.NONE:
        # Item 5 (Wave 3 hardening, T8b): a chart drawn as one undivided total has nothing this
        # reader could narrow to -- there is no bar to resolve `resolve_reference` against -- but
        # "¿por qué tanto?" over one is not an unrelated question either, so it asks which
        # breakdown the holder wants rather than falling through to the planner and answering the
        # same total again. Read before `_DRILLABLE_GROUPINGS` on purpose: NONE is not in that set
        # and would otherwise fall into its ordinary "cannot be drilled at all" `None`.
        return DrillAsk(
            "¿Tanto en qué? Puedo desglosarlo por categoría o por mes.",
            suggestions=("Desglósalo por categoría", "Desglósalo por mes"),
        )
    if plan.group_by not in _DRILLABLE_GROUPINGS:
        return None
    if plan.group_by is GroupBy.MONTH and newest.window is None:
        # A month drill needs the dates the chart's rows cover to intersect against; a category
        # drill does not. Refuse rather than fall back to the calendar month alone -- the very
        # widening C-1 exists to close.
        return None

    reference = resolve_reference(sentence, newest)
    if isinstance(reference, NoMatch):
        return None
    if isinstance(reference, Ambiguous):
        # Reached only for a chart that could be drilled (I-4): the two checks above already
        # refused every plan shape no reply to this question could ever act on.
        return DrillAsk(_ask(reference.candidates))

    # The only case left in `Reference` is `Resolved`. The label's own shape decides what it is --
    # a "rank" reading needs no extra rule, because a rank is read off the chart's own order, not
    # named, and the label it lands on is a month or a category exactly like a named one.
    label = reference.label
    parsed = parsed_month(label)
    if parsed is not None:
        if plan.group_by is not GroupBy.MONTH or newest.window is None:
            return None
        year, month = parsed
        month_window = whole_month(year, month)
        # C-1: intersect with the window the chart's rows actually cover before naming a window at
        # all -- a plan's own `date_range` can be a slice of a calendar month (`del 10 de junio al
        # 20 de julio` draws a July bar whose rows cover 1-20 July only), and naming the whole
        # month here would speak days the chart never drew.
        #
        # N-1 (fix round 2): the two dates are compared BEFORE `AbsoluteRange` is built, not after
        # -- `AbsoluteRange.__post_init__` raises when its own `end < start`, so building the value
        # first and checking it second never reaches the check at all. This branch is reachable: a
        # ledger can report a month bucket outside the window it also reports (a boundary-timezone
        # bucketing, or a bucket beyond `periodEnd`/before `periodStart`), and a corpus episode can
        # inject a month past the harness's own `TODAY`. Today's path is the honest answer to a
        # label the chart's own window does not actually cover, never an HTTP 422.
        start = max(month_window.start, newest.window.start)
        end = min(month_window.end, newest.window.end)
        if end < start:
            return None
        window = AbsoluteRange(start, end)
        if window.start <= today <= window.end:
            # Design §3.3: never widen the window silently. The chart drew this bar through
            # whatever of the month had happened by then; naming the whole calendar month here,
            # once more of it has since happened, would speak days the chart never covered.
            window = AbsoluteRange(window.start, today)
        bounded = replace(plan, period=None, date_range=window, group_limit=None)
    else:
        if plan.group_by is not GroupBy.CATEGORY:
            return None
        unfiltered_or_named = not plan.categories or label in plan.categories
        if not unfiltered_or_named:
            return None
        bounded = replace(plan, categories=(label,), group_limit=None)

    # Never widened: the account scope, direction, exclusions and every other filter travel
    # unchanged on both `replace` calls above and the one below -- only the window (or the
    # category filter) narrows, and the grouping and metric change to answer "which merchant".
    metric = plan.metric if plan.metric in {Metric.SUM, Metric.COUNT} else Metric.SUM
    bounded = explicit_facts.reshaped(bounded, group_by=GroupBy.MERCHANT, metric=metric)
    return DrillDown(bounded, label)
