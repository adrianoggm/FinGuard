"""What a voice client says instead of reading the screen, over an accepted chart.

Design §6: "the spoken summary is derived from the chart, not from the table" -- so this module
reads exactly three value-free facts an accepted `ChartSpec`/`VisualProfile` pair already carries
(the mark, the dimension's type, and Task 1's `labels`/`ranked` off the dataset that drew it) plus
the plan's own `group_by`/`direction`, and nothing else. It never opens `result.groups`,
`result.total_minor` or a single row: the rows are what the screen is for.

What it deliberately never says: an amount, a count, or the name of every category on the chart --
only the top one, and only when the chart is actually ranked (a tie at the top is not a fact the
chart shows, so an unranked chart gets no top sentence at all).
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

_MEASURES: dict[Direction, str] = {
    Direction.EXPENSE: "gasto",
    Direction.INCOME: "ingreso",
    Direction.ALL: "movimiento",
}

# The temporal unit named in the second sentence, keyed the same way as `_NOUNS` -- only the three
# grouping kinds a temporal dimension can actually be. "meses" is the fallback for a temporal
# dimension whose plan is not a `FinancialPlan` naming one of the three (nothing in the pipeline
# produces that combination today, but the sentence still needs a word).
_TIME_UNITS: dict[GroupBy, str] = {
    GroupBy.DAY: "días",
    GroupBy.WEEK: "semanas",
    GroupBy.MONTH: "meses",
}


def _top_label(label: str, group_by: GroupBy) -> str:
    """The top label, said the way a person would -- capitalised, never a raw wire value."""
    if visual_reference.parsed_month(label) is not None:
        name = answer_writer.month_group_name(label)
    elif group_by is GroupBy.CATEGORY:
        name = category_words.name_of(label)
    else:
        name = label
    return name[:1].upper() + name[1:] if name else name


def spoken_summary(
    spec: ChartSpec,
    profile: VisualProfile,
    labels: tuple[str, ...],
    ranked: bool,
    plan: FinancialPlan | ComparisonPlan | RatioPlan | None,
) -> str | None:
    """One Spanish sentence pair naming the chart's top group and its shape -- never an amount."""
    if profile.comparison:
        return (
            "En pantalla tienes la comparación de los dos periodos; puedes preguntarme por "
            "cualquiera de los dos."
        )
    if spec.mark == "donut":
        return "En pantalla tienes el reparto."

    group_by = plan.group_by if isinstance(plan, FinancialPlan) else None
    nouns = _NOUNS.get(group_by) if group_by is not None else None

    top_sentence = None
    if ranked and labels and nouns is not None and group_by is not None:
        singular, _ = nouns
        assert isinstance(plan, FinancialPlan)
        measure = _MEASURES[plan.direction]
        top_sentence = f"{_top_label(labels[0], group_by)} fue {singular} con mayor {measure}."

    if profile.temporal():
        unit = _TIME_UNITS.get(group_by, "meses") if group_by is not None else "meses"
        second = (
            f"En pantalla tienes la evolución por {unit}, y puedes preguntarme por "
            "cualquiera de ellos."
        )
    elif nouns is not None:
        _, plural = nouns
        second = f"En pantalla tienes el desglose, y puedes preguntarme por cualquiera de {plural}."
    else:
        second = "En pantalla tienes el desglose."

    return f"{top_sentence} {second}" if top_sentence else second
