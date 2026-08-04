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

from finguard_orchestrator.domain.enums import Metric
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

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
    # A space before the symbol, which is what Spanish typography asks for and what every other
    # currency here already got — EUR was the odd one out at "159,46€". It matters beyond taste: the
    # web client formats its own figures and the two appeared side by side on one screen, spelling
    # the same amount two ways.
    symbol = " €" if currency == "EUR" else f" {currency}"
    return f"{sign}{grouped},{cents:02d}{symbol}"


def balance(summary: AccountSummary) -> str:
    """How much money there is, in words.

    Two figures or one, and the difference matters. Booked is what the ledger has settled and
    available is what can be spent; when something is pending they differ, and showing one under
    the other's name tells somebody they have money they do not. When they agree, saying the same
    number twice under two labels reads like a discrepancy the user has to work out — so it is
    said once.
    """
    available = money(summary.available_minor, summary.currency)
    if summary.available_minor == summary.booked_minor:
        return f"Tienes {available} en tu cuenta."
    booked = money(summary.booked_minor, summary.currency)
    return f"Tienes {available} disponibles en tu cuenta. El saldo contabilizado es de {booked}."


def contacts(found: tuple[Contact, ...]) -> str:
    """Who can be paid, in words.

    Names only. The phone number travels beside this as data — it is what tells two Martas
    apart — but a chat line carrying both numbers buries the answer in digits nobody asked
    for. Disambiguating is the client's job, with the identifiers it also receives.
    """
    if not found:
        return "Todavía no tienes contactos guardados para enviar dinero."
    names = ", ".join(contact.display_name for contact in found)
    plural = "estos contactos" if len(found) > 1 else "este contacto"
    return f"Tienes {plural} para enviar dinero: {names}."


def filter_of(plan: FinancialPlan) -> str:
    """What the figure is a figure *of*, in words, or "" when the question named no filter.

    Two answers in a row that differ only in a number nobody can attribute is what made the
    conversation feel poor while being correct: "449,62 € el mes pasado" and "44,59 € el mes
    pasado" were the supermarket and the cinema, and neither said so. The filter was in the plan
    and only visible behind "Ver la consulta", spelled GROCERIES.

    A merchant wins over a category when both are present. "En Mercadona y en supermercado" reads
    as two unrelated things when it is one filter narrowing another, and the shop is the more
    specific of the two.

    Spanish, from the vocabulary the prompt already uses: GROCERIES is an identifier and
    "supermercado" is what the user said. A category nobody wrote words for falls back to its
    identifier — worse than a name, much better than an invented one.
    """
    if plan.merchants:
        return f" en {_listed(plan.merchants)}"
    if plan.categories:
        return f" en {_listed(tuple(category_words.name_of(c) for c in plan.categories))}"
    return ""


def _listed(names: tuple[str, ...]) -> str:
    """Spanish enumeration: "a", "a y b", "a, b y c"."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} y {names[-1]}"


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
    if plan.merchants:
        # Shown because a typo is the failure mode here: "Mercadonna" returns an empty answer,
        # and a reader who sees the name that was searched for can tell that from "you spent
        # nothing there".
        parts.append("/".join(plan.merchants))
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
    # What the figure is of. Empty for a question that named no filter, which is most of them.
    about = filter_of(plan)

    if answer.is_empty:
        # "Gastaste 0" and "no encontré nada" read the same and mean different things.
        # The second is usually a plan that was wrong, and saying so lets the user
        # correct it instead of believing a zero.
        return f"No he encontrado movimientos{about} {window}."

    noun = "movimiento" if answer.movement_count == 1 else "movimientos"
    if counting:
        head = f"{answer.movement_count} {noun}{about} {window}."
    else:
        total = money(answer.total_minor, answer.currency)
        head = f"{total}{about} {window}, en {answer.movement_count} {noun}."

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
