"""What a voice client says instead of reading the screen, over an accepted chart.

Design §6: "the spoken summary is derived from the chart, not from the table" -- so this module
reads exactly three value-free facts an accepted `ChartSpec`/`VisualProfile` pair already carries
(the mark, the dimension's type, and Task 1's `labels`/`ranked` off the dataset that drew it) plus
the plan's own `group_by`/`direction`, and nothing else. It never opens `result.groups`,
`result.total_minor` or a single row: the rows are what the screen is for.

One sentence, said the way a caption sits under a figure in an article: what the chart shows, and
its leader when it has one -- "Tus gastos por categoría, con restaurantes en cabeza." Never an
amount, a count, or the name of every category: the rows are on screen, and reading them out was
what made a spoken chart tiring. The leader is named only when the chart is actually ranked (a tie
at the top is not a fact the chart shows).
"""

from __future__ import annotations

from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.models.chart import ChartSpec, VisualProfile
from finguard_orchestrator.domain.services import answer_writer, category_words, visual_reference
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan

# A closed mapping over EVERY `GroupBy` member -- singular/plural, or `None` when the grouping
# names no noun a top sentence could use. Checked against the vocabulary at import time (below)
# so a `GroupBy` member added later without an entry here fails loudly instead of silently
# falling back to the generic noun.
_NOUNS: dict[GroupBy, tuple[str, str] | None] = {
    GroupBy.NONE: None,
    GroupBy.DAY: ("el día", "los días"),
    GroupBy.WEEK: ("la semana", "las semanas"),
    GroupBy.MONTH: ("el mes", "los meses"),
    GroupBy.CATEGORY: ("la categoría", "las categorías"),
    GroupBy.MERCHANT: ("el comercio", "los comercios"),
    GroupBy.COUNTERPARTY: ("la contraparte", "las contrapartes"),
    GroupBy.ACCOUNT: ("la cuenta", "las cuentas"),
}
assert set(_NOUNS) == set(GroupBy)

_MEASURES: dict[Direction, tuple[str, str]] = {
    Direction.EXPENSE: ("gasto", "gastos"),
    Direction.INCOME: ("ingreso", "ingresos"),
    Direction.ALL: ("movimiento", "movimientos"),
}

# The unit of a time axis, for the plan groupings that are one.
_TIME_UNITS: dict[GroupBy, str] = {
    GroupBy.DAY: "día",
    GroupBy.WEEK: "semana",
    GroupBy.MONTH: "mes",
}


def _label(label: str, group_by: GroupBy) -> str:
    """The leader, said the way a person would -- never a raw wire value."""
    if visual_reference.parsed_month(label) is not None:
        return answer_writer.month_group_name(label)
    if group_by in (GroupBy.DAY, GroupBy.WEEK):
        # "2026-08-03" read aloud is eight digits and two dashes.
        return answer_writer.said_dates(label)
    if group_by is GroupBy.CATEGORY:
        return category_words.name_of(label)
    return label


def spoken_summary(
    spec: ChartSpec,
    profile: VisualProfile,
    labels: tuple[str, ...],
    ranked: bool,
    plan: FinancialPlan | ComparisonPlan | RatioPlan | None,
    *,
    comparison: bool = False,
) -> str | None:
    """One Spanish sentence, a caption for the chart on screen -- never an amount.

    `comparison` is the dataset's own word that the chart is two periods: a comparison of two
    totals draws no series, so the profile alone called it a breakdown.
    """
    if profile.comparison or comparison:
        return "Comparación de los dos periodos."

    financial = plan if isinstance(plan, FinancialPlan) else None
    group_by = financial.group_by if financial is not None else None
    measure, measures = _MEASURES[financial.direction] if financial is not None else ("", "")
    subject = f"tus {measures}" if financial is not None else "lo que has pedido"
    nouns = _NOUNS.get(group_by) if group_by is not None else None
    noun = nouns[0].split(" ", 1)[1] if nouns is not None else None

    if profile.temporal():
        unit = _TIME_UNITS.get(group_by) if group_by is not None else None
        caption = f"Evolución de {subject} " + (f"por {unit}" if unit else "en el tiempo")
    elif spec.mark == "donut":
        caption = f"Reparto de {subject}" + (f" por {noun}" if noun else "")
    elif noun is not None:
        caption = f"{subject} por {noun}"
    else:
        caption = f"Desglose de {subject}"

    if ranked and labels and nouns is not None and group_by is not None and financial is not None:
        leader = _label(labels[0], group_by)
        if profile.temporal():
            caption += f", con {leader} como {nouns[0]} de más {measure}"
        else:
            caption += f", con {leader} en cabeza"
    return caption[:1].upper() + caption[1:] + "."
