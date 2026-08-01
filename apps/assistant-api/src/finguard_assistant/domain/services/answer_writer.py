"""Turns figures into the sentence the user reads.

A template, not a second model call. Every number here came from the ledger, and handing
a correct number to a language model to phrase is an opportunity for it to come back
different — rounded, re-scaled or quietly invented. The model chose the question; it
does not get to touch the answer.

A domain service rather than an adapter: it is pure, it imports no framework, and it
speaks about domain objects. Putting it under infrastructure would have made the
application layer import downwards to reach it.

The text is Spanish because it is product data, which is the one exception rule 14
makes.
"""

from finguard_assistant.domain.enums import Metric
from finguard_assistant.domain.models.query_answer import Group, QueryAnswer
from finguard_assistant.domain.value_objects.financial_plan import FinancialPlan

# How many rows of a breakdown to read out. Past this the answer stops being a sentence
# and becomes a table nobody reads in a chat.
MAX_SPOKEN_GROUPS = 5

_WINDOWS = {
    "TODAY": "hoy",
    "YESTERDAY": "ayer",
    "THIS_WEEK": "esta semana",
    "LAST_WEEK": "la semana pasada",
    "THIS_MONTH": "en lo que va de mes",
    "LAST_MONTH": "el mes pasado",
    "THIS_YEAR": "en lo que va de año",
}


def money(amount_minor: int, currency: str) -> str:
    """Minor units in Spanish notation. Integer arithmetic the whole way: a float here
    is how a cent goes missing between the ledger and the screen."""
    sign = "-" if amount_minor < 0 else ""
    units, cents = divmod(abs(amount_minor), 100)
    grouped = f"{units:,}".replace(",", ".")
    symbol = "€" if currency == "EUR" else f" {currency}"
    return f"{sign}{grouped},{cents:02d}{symbol}"


def window_of(plan: FinancialPlan) -> str:
    """The window in words. Names it, never computes it: what LAST_MONTH covers is
    banking's business, and saying it twice is how the two start disagreeing."""
    if plan.date_range is not None:
        return f"del {plan.date_range.start.isoformat()} al {plan.date_range.end.isoformat()}"

    assert plan.period is not None  # the plan's own invariant guarantees one of the two
    name = plan.period.period.value
    if name in _WINDOWS:
        return _WINDOWS[name]
    count = plan.period.n or 0
    unit = "días" if name == "LAST_N_DAYS" else "meses"
    return f"en los últimos {count} {unit}"


def describe(plan: FinancialPlan) -> str:
    """The plan in one readable line.

    Shown with every answer on purpose. A figure whose derivation the user cannot
    inspect is a number to be believed, and there is a language model in this pipeline —
    showing the question it actually asked is the difference between a tool and an
    oracle.
    """
    parts = [window_of(plan), plan.direction.value.lower()]
    if plan.categories:
        parts.append("/".join(plan.categories).lower())
    if plan.group_by.value != "NONE":
        parts.append(f"por {plan.group_by.value.lower()}")
    if plan.metric.value != "SUM":
        parts.append(plan.metric.value.lower())
    return ", ".join(parts)


def write(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """Takes the plan, not just the window, because the metric decides what the answer
    *is*.

    Passing only the window is how "¿cuántas veces fui al súper?" came back as
    "123,45 € el mes pasado, en 3 movimientos": the number that was asked for appeared,
    as an aside, under a headline about money. The evaluation could not see it — it
    scores plans, and that plan was correct.
    """
    window = window_of(plan)
    counting = plan.metric is Metric.COUNT

    if answer.is_empty:
        # "Gastaste 0" and "no encontré nada" read the same and mean different things.
        # The second is usually a plan that was wrong, and saying so lets the user
        # correct it instead of believing a zero.
        return f"No he encontrado movimientos {window}."

    noun = "movimiento" if answer.movement_count == 1 else "movimientos"
    if counting:
        head = f"{answer.movement_count} {noun} {window}."
    else:
        total = money(answer.total_minor, answer.currency)
        head = f"{total} {window}, en {answer.movement_count} {noun}."

    if not _is_breakdown(answer):
        return head

    shown = answer.groups[:MAX_SPOKEN_GROUPS]
    lines = "\n".join(f"  · {group.key}: {_figure(group, answer, counting)}" for group in shown)
    rest = len(answer.groups) - len(shown)
    tail = f"\n  · y {rest} más" if rest > 0 else ""
    return f"{head}\n{lines}{tail}"


def _figure(group: Group, answer: QueryAnswer, counting: bool) -> str:
    """A breakdown of a count is counts. Amounts under a "how many" question would be
    the same mistake again, one row at a time."""
    if counting:
        return f"{group.movement_count}"
    return f"{money(group.total_minor, answer.currency)} ({group.movement_count})"


def _is_breakdown(answer: QueryAnswer) -> bool:
    """Whether the groups are a real breakdown.

    An ungrouped query still comes back with one row, keyed "total", because the engine
    always groups by something. Reading that back as a breakdown produces "1.895,97 EUR
    ... total: 1.895,97 EUR", which says the same thing twice and looks like a bug.
    """
    if not answer.groups:
        return False
    return not (len(answer.groups) == 1 and answer.groups[0].key == "total")


def cite(answer: QueryAnswer, *, limit: int = 3) -> list[str]:
    """The movements behind the figure, as lines a caller can show.

    An answer that states a total without them asks the user to take it on faith, and
    faith is the one thing this pipeline should never ask for.
    """
    return [
        f"{movement.occurred_at.date().isoformat()}  "
        f"{movement.merchant_name or movement.description}  "
        f"{money(movement.amount_minor, answer.currency)}"
        for movement in answer.movements[:limit]
    ]
