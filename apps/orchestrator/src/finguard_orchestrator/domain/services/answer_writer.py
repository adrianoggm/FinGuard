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

from datetime import datetime, timedelta
from typing import Final

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

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


def money(amount_minor: int, currency: str | None) -> str:
    """Minor units in Spanish notation. Integer arithmetic the whole way: a float here
    is how a cent goes missing between the ledger and the screen.

    An unknown currency prints no unit at all. It cannot happen through any adapter here — every
    figure arrives from banking with the account's currency beside it — and if it ever does, a
    bare "82,40" is a number somebody will ask about, while "82,40 €" on a dollar account is a
    number they will believe. A missing unit is a worse-looking answer and a safer one.
    """
    sign = "-" if amount_minor < 0 else ""
    units, cents = divmod(abs(amount_minor), 100)
    grouped = f"{units:,}".replace(",", ".")
    # A space before the symbol, which is what Spanish typography asks for and what every other
    # currency here already got — EUR was the odd one out at "159,46€". It matters beyond taste: the
    # web client formats its own figures and the two appeared side by side on one screen, spelling
    # the same amount two ways.
    #
    # The symbol is a fact about the currency; where it sits and how the digits are grouped are
    # facts about the locale. They are one function today because there is one locale. See ADR-008.
    if currency is None:
        symbol = ""
    else:
        symbol = " €" if currency == "EUR" else f" {currency}"
    return f"{sign}{grouped},{cents:02d}{symbol}"


def balance(
    summary: AccountSummary,
    named: tuple[str, ...] = (),
    *,
    selected: tuple[Holding, ...] | None = None,
) -> str:
    """How much money there is, in words.

    `named` is what `account_words` read out of the sentence — the kinds of account it mentioned,
    empty when it mentioned none, which is the ordinary case.
    """
    if not summary.holdings:
        # One account, or a banking that has not been deployed yet. The sentence this service
        # always gave, unchanged, rather than a degraded version of the new one.
        return _one_account(summary)
    wanted = (
        list(selected)
        if selected is not None
        else ([h for h in summary.holdings if h.kind in named] if named else list(summary.holdings))
    )
    if (named or selected is not None) and not wanted:
        return _no_such_account(summary)
    return _across(wanted, everything=not named and selected is None)


def leading_figure(
    summary: AccountSummary,
    named: tuple[str, ...] = (),
    *,
    selected: tuple[Holding, ...] | None = None,
) -> int | None:
    """The number `balance()` leads with, as data beside the prose.

    Its own function and not the handler's own arithmetic, because the two would then be two
    representations of one decision — and a client that renders the field beside the sentence would
    show 2.270,42 € under a sentence saying 7.755,92 €. Both go through `_total_of`, so they cannot
    disagree about which accounts were counted.

    `None` when there is no single figure, and every case of that is real: accounts in more than
    one currency have no total, and a kind the holder does not have has no figure at all. A client
    that gets `None` renders the sentence, which was the whole answer either way.
    """
    if not summary.holdings:
        return summary.available_minor
    if selected is not None:
        return _total_of(list(selected)) if selected else None
    if not named:
        return _total_of(list(summary.holdings))
    wanted = [holding for holding in summary.holdings if holding.kind in named]
    return _total_of(wanted) if wanted else None


def _spendable(holdings: list[Holding]) -> list[Holding]:
    """The accounts a total may add up.

    Investments are out, unless they are all there is — "¿y en el fondo?" is asking about exactly
    that one, and answering it with nothing would be worse than answering it with a caveat.
    """
    without_assets = [holding for holding in holdings if holding.kind != "ASSET"]
    return without_assets or holdings


def _total_of(holdings: list[Holding]) -> int | None:
    """What the accounts add up to, or `None` when they do not add up to anything.

    Adding minor units across currencies produces a perfectly valid integer that means nothing, so
    two currencies have no total rather than a misleading one.
    """
    countable = _spendable(holdings)
    if not countable or len({holding.currency for holding in countable}) > 1:
        return None
    return sum(holding.booked_minor for holding in countable)


def _one_account(summary: AccountSummary) -> str:
    """Two figures or one, and the difference matters. Booked is what the ledger has settled and
    available is what can be spent; when something is pending they differ, and showing one under
    the other's name tells somebody they have money they do not. When they agree, saying the same
    number twice under two labels reads like a discrepancy the user has to work out — so it is
    said once."""
    available = money(summary.available_minor, summary.currency)
    if summary.available_minor == summary.booked_minor:
        return f"Tienes {available} en tu cuenta."
    booked = money(summary.booked_minor, summary.currency)
    return f"Tienes {available} disponibles en tu cuenta. El saldo contabilizado es de {booked}."


def _name_of(holding: Holding) -> str:
    """What to call an account the holder never named. The kind, never the reference: "ES-0001-2"
    is not something anybody recognises as theirs."""
    return holding.display_name or _KIND_NAMES.get(holding.kind, "otra cuenta")


_KIND_NAMES: Final[dict[str, str]] = {
    "CURRENT": "tu cuenta corriente",
    "SAVINGS": "tus ahorros",
    "ASSET": "tu fondo",
}


def _with_amounts(holdings: list[Holding]) -> str:
    """ "2.270,42 € en Cuenta corriente y 5.485,50 € en Hucha", through the same enumeration the
    rest of this module uses — Spanish joins the last item with "y", and a comma there reads as
    one more item."""
    return listed(tuple(f"{money(h.booked_minor, h.currency)} en {_name_of(h)}" for h in holdings))


def _across(holdings: list[Holding], *, everything: bool) -> str:
    """The figure, with the accounts it came from under it.

    **Investments are not added in, and this is the decision the whole sentence turns on.** An
    `ASSET` balance is what was paid, not what it is worth — there is no price source (ADR-009 §1)
    — so folding it into "tienes X" reports a purchase price as somebody's money. It is said
    afterwards, in its own clause, with what it actually is.

    No total when the currencies differ, either. Adding minor units across currencies produces a
    perfectly valid integer that means nothing, and the breakdown alone is the honest answer.
    """
    spendable = _spendable(holdings)
    invested = [h for h in holdings if h.kind == "ASSET"]

    if not [h for h in holdings if h.kind != "ASSET"]:
        return _invested_only(invested)

    # A total needs more than one account to be a total, and one currency to be a number. Without
    # either, the list on its own is the whole answer and no figure is invented to lead it.
    total_minor = _total_of(holdings) if len(spendable) > 1 else None
    if total_minor is None:
        said = f"Tienes {_with_amounts(spendable)}."
    else:
        between = "entre tus cuentas" if everything else "entre esas cuentas"
        total = money(total_minor, spendable[0].currency)
        said = f"Tienes {total} {between}: {_with_amounts(spendable)}."

    if invested and everything:
        said += f" Aparte, {_invested_clause(invested)}"
    return said


def _invested_only(invested: list[Holding]) -> str:
    return f"En inversión {_invested_clause(invested)}"


def _invested_clause(invested: list[Holding]) -> str:
    """The caveat is not decoration. Somebody reading "2.700 € en tu fondo" beside their current
    account will read it as money they have, and it is what they paid — the fund may be worth more
    or less today and this service has no way to know which."""
    listed = _with_amounts(invested)
    return f"tienes {listed}, que es lo que invertiste y no lo que vale hoy."


def which_account(candidates: list[Holding]) -> str:
    """Two accounts of one kind, so the word did not pick one.

    It asks for the missing word and no more, which it can only do because the turn leaves a
    `TransferInProgress` behind. For one release it asked for the whole instruction again: the path
    held no state, so a reply naming only the account arrived with the amount already gone and "¿a
    cuál lo muevo?" answered with "a Hucha" reached the financial planner, which asked which period.

    The names are listed because they are what the reply has to contain, and a holder with two
    piggy banks does not necessarily remember what they called them.
    """
    names = listed(tuple(_name_of(holding) for holding in candidates))
    return f"¿A cuál de tus cuentas lo muevo? Tienes {names}."


def how_much_to_move(target: Holding) -> str:
    """The amount, when the sentence named an account and no figure."""
    return f"¿Cuánto quieres mover a {_name_of(target)}?"


def ready_to_move(target: Holding, amount_minor: int) -> str:
    """What was understood, and where the confirmation lives.

    It does not prepare the transfer. The chat route reads sentences and this one has an account
    and an amount in it — but preparing is a `payments:prepare` scope on a different route, and a
    read path that quietly acquired the ability to stage a payment would be the seam between asking
    and paying disappearing. So it says what it heard and points at the screen that does it.
    """
    return (
        f"Puedes mover {money(amount_minor, target.currency)} a {_name_of(target)} desde "
        f"Cuentas › Mover dinero entre cuentas. Te pediré que lo confirmes antes de moverlo."
    )


def no_such_account_to_move_to(summary: AccountSummary) -> str:
    have = ", ".join(_name_of(holding) for holding in summary.holdings)
    return f"No tienes ninguna cuenta de ese tipo. Tienes: {have}."


def unknown_named_account_to_move_to(candidates: list[Holding]) -> str:
    return f"No encuentro una cuenta con ese nombre. {which_account(candidates)}"


def _no_such_account(summary: AccountSummary) -> str:
    """A kind the holder does not have. Answering with the total instead would be answering a
    question they did not ask, and they would have no way to tell."""
    have = ", ".join(_name_of(h) for h in summary.holdings)
    return f"No tienes ninguna cuenta de ese tipo. Tienes: {have}."


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


def cards(wallet: Wallet) -> str:
    """The wallet, and what each card has taken since the window opened.

    Both questions from the reported conversation are answered by one sentence, because both were
    already one query: "¿qué tarjetas tengo de crédito?" and "¿cuánto he gastado con cada tarjeta
    este mes?" differ only in which column of `Card` the reader cares about, and the row carries
    both. The first used to come back "eso no puedo calcularlo todavía" and the second came back
    with the account total, 868,97 €, wearing the label of an answer about cards.

    The amount is included unconditionally rather than only when asked. A card with a figure beside
    it answers the second question for somebody who asked the first, and the alternative is two
    almost-identical sentences and a rule deciding between them — which is the kind of rule that
    ends up answering the wrong one.

    No currency symbol, and that is this module's own rule rather than an oversight: banking's card
    contract does not carry one — `CardResponse` has eleven fields and none of them is a currency —
    so there is nothing to print. `money` prints a bare figure when the currency is unknown for the
    reason written there: "82,40" is a number somebody asks about and "82,40 €" on a dollar account
    is a number they believe.

    ponytail: the gap is banking's, and the ranked adapter already solved the same one by attaching
    the answer's currency to each row. The upgrade is for the card views to carry it, not for this
    to assume euros.

    Kind rather than brand, because "de crédito" is what people filter by and Visa is not an
    answer to it. The last four digits are what tells two cards of the same kind apart, which is
    the same job the phone number does for two contacts named Marta.
    """
    if not wallet.cards:
        return "No tienes tarjetas asociadas a esta cuenta."
    lines = [
        f"· {card.display_name} ({_KINDS.get(card.kind, card.kind.lower())} ··{card.last_four}): "
        f"{money(card.spent_minor, None)} en {card.charge_count} "
        f"{'cargo' if card.charge_count == 1 else 'cargos'}"
        for card in wallet.cards
    ]
    plural = "estas tarjetas" if len(wallet.cards) > 1 else "esta tarjeta"
    # Through this module's own month table rather than `strftime`. `%-d` is a GNU extension that
    # does not exist on Windows, and `%B` answers in whatever locale the process happens to have —
    # two ways for a date to come out wrong that a nine-element tuple does not have.
    since = f"{wallet.since.day} de {_MONTHS[wallet.since.month - 1]}"
    opening = f"Tienes {plural}, con lo gastado desde el {since}:"
    return "\n".join([opening, *lines])


# The card kinds, in the words a person uses to ask for one. `kind` is the ledger's vocabulary and
# "DEBIT" is not something anybody types.
_KINDS: Final[dict[str, str]] = {"DEBIT": "débito", "CREDIT": "crédito", "PREPAID": "prepago"}


def filter_of(plan: FinancialPlan | TopMovements) -> str:
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
        included = f" en {listed(plan.merchants)}"
    elif plan.categories:
        included = f" en {listed(tuple(category_words.name_of(c) for c in plan.categories))}"
    else:
        included = ""
    excluded_names = (
        *tuple(category_words.name_of(category) for category in plan.excluded_categories),
        *plan.excluded_merchants,
    )
    if not excluded_names:
        return included
    return (
        f"{included}, sin {listed(excluded_names)}"
        if included
        else f" sin {listed(excluded_names)}"
    )


def listed(names: tuple[str, ...]) -> str:
    """Spanish enumeration: "a", "a y b", "a, b y c".

    Public because the handler needs it too, to offer the shops a misspelt merchant might have
    meant. Spanish joins the last item with "y" and a comma there reads as one more item — a rule
    worth having in one place rather than in every sentence that lists something.
    """
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} y {names[-1]}"


def month_name(year: int, month: int) -> str:
    """A calendar month in the language used by conversational comparisons."""
    if not 1 <= month <= 12:
        return f"{year}-{month:02d}"
    return f"{_MONTHS[month - 1]} de {year}"


def month_group_name(key: str) -> str:
    """Turn banking's stable YYYY-MM group key into a month a person would say."""
    try:
        year, month = (int(part) for part in key.split("-", maxsplit=1))
    except (TypeError, ValueError):
        return key
    return month_name(year, month)


# Spanish months, for naming the day a movement happened. Written out rather than taken from the
# locale: a service's locale is a deployment accident, and "14 de marzo" should not depend on it.
_MONTHS: Final = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# Ordinals a person would actually say. They stop being useful somewhere around the tenth, and
# "duodécimo" reads as a machine trying too hard — past this the position is said as a number.
_ORDINALS: Final = {
    2: "El segundo",
    3: "El tercero",
    4: "El cuarto",
    5: "El quinto",
    6: "El sexto",
    7: "El séptimo",
    8: "El octavo",
    9: "El noveno",
    10: "El décimo",
}


def ranked(movement: Movement | None, ranking: TopMovements) -> str:
    """The nth largest movement, in words.

    Names the shop and the date, not just the figure. A bare amount would send the user to the
    movement list to work out which charge it was, which is most of the work they asked to avoid.

    The first answer restates the question — the window and the filter — because it stands alone.
    The ones after it do not: "el segundo" is read directly under the answer it follows, and
    repeating the whole question there is how an assistant starts sounding like a form.
    """
    if movement is None:
        # "El quinto" of three movements has no answer. Saying which position could not be found
        # beats reporting a zero, which reads as a movement of nothing.
        return (
            f"No hay {_position(ranking.rank).lower()} movimiento en ese periodo. "
            f"Puedo ampliar el periodo o quitar algún filtro de la búsqueda."
        )

    # The ledger's, like every other figure this module writes. It was spelled "EUR" here, so the
    # sentence a person actually reads went on claiming euros after the `currency` field beside it
    # had been fixed — and nobody reads the field.
    amount = money(movement.amount_minor, movement.currency)
    what = movement.merchant_name or movement.description
    when = _spoken_date(movement.occurred_at)

    if ranking.rank == 1:
        noun = "ingreso" if ranking.direction is Direction.INCOME else "gasto"
        about = f"{filter_of(ranking)} {window_of(ranking)}"
        return f"Tu mayor {noun}{about} fue de {amount}: {what}, el {when}."
    return f"{_position(ranking.rank)} fue de {amount}: {what}, el {when}."


def recent_movements(count: int) -> str:
    if count == 0:
        return "No he encontrado movimientos recientes en tus cuentas."
    noun = "movimiento" if count == 1 else "movimientos"
    return f"Estos son tus últimos {count} {noun}."


def movement_example(movement: Movement | None, currency: str) -> str:
    if movement is None:
        return "No hay ningún movimiento que pueda mostrarte como ejemplo."
    amount = abs(movement.amount_minor)
    if movement.direction == Direction.EXPENSE.value:
        amount = -amount
    what = movement.merchant_name or movement.description
    return (
        f"Por ejemplo: {what}, {money(amount, currency)}, el {_spoken_date(movement.occurred_at)}."
    )


def movement_examples(movements: tuple[Movement, ...], currency: str) -> str:
    """One or several concrete rows, respecting the number the user asked to see."""
    if not movements:
        return "No hay ningún movimiento que pueda mostrarte como ejemplo."
    if len(movements) == 1:
        return movement_example(movements[0], currency)
    lines = []
    for movement in movements:
        amount = abs(movement.amount_minor)
        if movement.direction == Direction.EXPENSE.value:
            amount = -amount
        what = movement.merchant_name or movement.description
        lines.append(
            f"  · {what}: {money(amount, currency)}, el {_spoken_date(movement.occurred_at)}"
        )
    return "Por ejemplo:\n" + "\n".join(lines)


def movement_date(occurred_at: datetime | str) -> str:
    """The date of the ranked movement a pronoun points at."""
    if isinstance(occurred_at, str):
        occurred_at = datetime.fromisoformat(occurred_at)
    return f"Fue el {_spoken_date(occurred_at)}."


def _position(rank: int) -> str:
    return _ORDINALS.get(rank, f"El número {rank}")


def _spoken_date(moment: datetime) -> str:
    """ "14 de marzo", with no year: the window already said which one, and repeating it makes the
    sentence read like a receipt."""
    return f"{moment.day} de {_MONTHS[moment.month - 1]}"


def window_of(plan: FinancialPlan | TopMovements) -> str:
    """The window in words. Names it, never computes it: what LAST_MONTH covers is
    banking's business, and saying it twice is how the two start disagreeing."""
    if plan.date_range is not None:
        return f"del {plan.date_range.start.isoformat()} al {plan.date_range.end.isoformat()}"

    assert plan.period is not None  # the plan's own invariant guarantees one of the two
    name = plan.period.period.value
    if name in _WINDOWS:
        return _WINDOWS[name]
    count = plan.period.n or 0
    day = name == "LAST_N_DAYS"
    if count == 1:
        # "En los últimos 1 meses" was on screen under every answer about the last month, which is
        # the sort of thing that makes a person stop trusting the figure above it. Spanish does not
        # pluralise one, and the singular is also what they said: nobody asks for "the last 1
        # months".
        return "en el último día" if day else "en el último mes"
    return f"en los últimos {count} {'días' if day else 'meses'}"


def describe(plan: FinancialPlan | TopMovements) -> str:
    """The plan in one readable line.

    Shown with every answer on purpose. A figure whose derivation the user cannot
    inspect is a number to be believed, and there is a language model in this pipeline —
    showing the question it actually asked is the difference between a tool and an
    oracle.
    """
    parts = [window_of(plan), plan.direction.value.lower()]
    if isinstance(plan, FinancialPlan) and plan.internal_only:
        parts.append("solo traspasos entre cuentas")
    if isinstance(plan, TopMovements):
        # The rank is the whole point of a ranking, so a description without it would show the same
        # line for "el más caro" and "el segundo más caro".
        parts.append(f"nº {plan.rank} por importe")
    if plan.categories:
        parts.append("/".join(plan.categories).lower())
    if plan.excluded_categories:
        parts.append(
            "sin "
            + "/".join(category_words.name_of(category) for category in plan.excluded_categories)
        )
    if plan.merchants:
        # Shown because a typo is the failure mode here: "Mercadonna" returns an empty answer,
        # and a reader who sees the name that was searched for can tell that from "you spent
        # nothing there".
        parts.append("/".join(plan.merchants))
    if plan.excluded_merchants:
        parts.append("sin " + "/".join(plan.excluded_merchants))
    if isinstance(plan, FinancialPlan):
        # The grouping and the metric belong to an aggregate. A ranking has neither, and its rank is
        # appended above — where the reader is looking for what makes the two lines different.
        if plan.group_by.value != "NONE":
            parts.append(f"por {plan.group_by.value.lower()}")
        if plan.metric is Metric.PERIOD_AVERAGE:
            parts.append("media por periodo")
        elif plan.metric.value != "SUM":
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
    averaging = plan.metric is Metric.AVERAGE
    averaging_periods = plan.metric is Metric.PERIOD_AVERAGE
    # What the figure is of. Empty for a question that named no filter, which is most of them.
    about = filter_of(plan)

    if answer.is_empty:
        # "Gastaste 0" and "no encontré nada" read the same and mean different things.
        # The second is usually a plan that was wrong, and saying so lets the user
        # correct it instead of believing a zero.
        if plan.categories and plan.merchants:
            next_step = "buscar sin categoría, buscar sin comercio o ampliar el periodo"
        elif plan.categories:
            next_step = "buscar sin categoría o ampliar el periodo"
        elif plan.merchants:
            next_step = "buscar sin comercio o ampliar el periodo"
        else:
            next_step = "ampliar el periodo o consultar las categorías disponibles"
        return f"No he encontrado movimientos{about} {window}. Puedes {next_step}."

    noun = "movimiento" if answer.movement_count == 1 else "movimientos"
    if plan.group_by is GroupBy.ACCOUNT and len(answer.groups) == 1:
        account = answer.groups[0]
        if answer.movement_count == 1:
            return (
                f"El movimiento encontrado {window} está en "
                f"{account.key}: {_figure(account, answer, plan.metric)}."
            )
        return (
            f"Todos los {answer.movement_count} {noun} encontrados {window} están en "
            f"{account.key}: {_figure(account, answer, plan.metric)}."
        )
    if plan.group_by is GroupBy.ACCOUNT:
        head = f"El desglose por cuenta {window} es:"
    elif counting:
        head = f"{answer.movement_count} {noun}{about} {window}."
    elif averaging:
        average = money(_rounded_mean(answer.total_minor, answer.movement_count), answer.currency)
        head = (
            f"El importe medio por movimiento{about} {window} fue de {average}, "
            f"calculado sobre {answer.movement_count} {noun}."
        )
    elif averaging_periods:
        periods = _period_count(answer, plan)
        average = money(_rounded_mean(answer.total_minor, periods), answer.currency)
        concept = {
            Direction.EXPENSE: "gasto",
            Direction.INCOME: "ingreso",
            Direction.ALL: "importe",
        }[plan.direction]
        unit = {
            GroupBy.DAY: ("día", "días"),
            GroupBy.WEEK: ("semana", "semanas"),
            GroupBy.MONTH: ("mes", "meses"),
        }[plan.group_by]
        period_noun = unit[0] if periods == 1 else unit[1]
        head = (
            f"El {concept} medio por {unit[0]}{about} {window} fue de {average}, "
            f"calculado sobre {periods} {period_noun}, incluidos los periodos sin movimientos."
        )
    else:
        total = money(answer.total_minor, answer.currency)
        head = f"{total}{about} {window}, en {answer.movement_count} {noun}."

    if not _is_breakdown(answer):
        return head

    shown = answer.groups[:MAX_SPOKEN_GROUPS]
    lines = "\n".join(
        f"  · {_group_name(group.key, plan)}: {_figure(group, answer, plan.metric)}"
        for group in shown
    )
    rest = len(answer.groups) - len(shown)
    tail = f"\n  · y {rest} más" if rest > 0 else ""
    return f"{head}\n{lines}{tail}"


def internal_transfers(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """Describe money that moved inside the holder's accounts without calling it spending."""
    window = window_of(plan)
    if answer.is_empty:
        return f"No he encontrado traspasos entre tus cuentas {window}."
    noun = "traspaso" if answer.movement_count == 1 else "traspasos"
    if plan.metric is Metric.COUNT:
        return f"Hiciste {answer.movement_count} {noun} {window}."
    if plan.metric is Metric.AVERAGE:
        average = money(_rounded_mean(answer.total_minor, answer.movement_count), answer.currency)
        return f"El importe medio de tus {noun} {window} fue de {average}."
    if plan.metric is Metric.PERIOD_AVERAGE:
        average = money(
            _rounded_mean(answer.total_minor, _period_count(answer, plan)), answer.currency
        )
        unit = {
            GroupBy.DAY: "diario",
            GroupBy.WEEK: "semanal",
            GroupBy.MONTH: "mensual",
        }[plan.group_by]
        return f"El importe medio {unit} de tus traspasos {window} fue de {average}."
    amount = money(answer.total_minor, answer.currency)
    selected = plan.only_account_id is not None or bool(plan.only_account_ids)
    if plan.direction is Direction.INCOME and selected:
        return (
            f"Moviste {amount} hacia la cuenta consultada {window}, "
            f"en {answer.movement_count} {noun}."
        )
    if selected:
        return (
            f"Moviste {amount} desde la cuenta consultada {window}, "
            f"en {answer.movement_count} {noun}."
        )
    return f"Moviste {amount} entre tus cuentas {window}, en {answer.movement_count} {noun}."


def grouped_period_comparison(
    earlier_answer: QueryAnswer,
    latest_answer: QueryAnswer,
    earlier_plan: FinancialPlan,
    latest_plan: FinancialPlan,
    *,
    earlier_window: str,
    latest_window: str,
) -> str | None:
    """Compare the same account rows across two windows instead of collapsing their totals."""
    if latest_plan.group_by is not GroupBy.ACCOUNT or earlier_plan.group_by is not GroupBy.ACCOUNT:
        return None
    earlier = {group.key: group for group in earlier_answer.groups if group.key != "total"}
    latest = {group.key: group for group in latest_answer.groups if group.key != "total"}
    keys = tuple(dict.fromkeys((*latest, *earlier)))
    if not keys:
        return None

    lines: list[str] = []
    for key in keys:
        earlier_group = earlier.get(key, Group(key, 0, 0))
        latest_group = latest.get(key, Group(key, 0, 0))
        earlier_value = _group_comparison_value(earlier_group, earlier_plan.metric)
        latest_value = _group_comparison_value(latest_group, latest_plan.metric)
        earlier_figure = _group_comparison_figure(
            earlier_group, earlier_plan.metric, earlier_answer.currency
        )
        latest_figure = _group_comparison_figure(
            latest_group, latest_plan.metric, latest_answer.currency
        )
        difference = abs(latest_value - earlier_value)
        if difference == 0:
            change = "sin cambios"
        else:
            sign = "+" if latest_value > earlier_value else "−"
            difference_figure = comparison_difference(
                difference, latest_answer.currency, latest_plan.metric
            )
            change = f"{sign}{difference_figure}"
        lines.append(
            f"  · {key}: {latest_window}, {latest_figure}; "
            f"{earlier_window}, {earlier_figure}; {change}."
        )

    concept = comparison_concept(latest_plan)
    return f"Comparación del {concept} por cuenta:\n" + "\n".join(lines)


def _group_comparison_value(group: Group, metric: Metric) -> int:
    if metric is Metric.COUNT:
        return group.movement_count
    if metric is Metric.AVERAGE:
        return _rounded_mean(group.total_minor, group.movement_count)
    return group.total_minor


def _group_comparison_figure(group: Group, metric: Metric, currency: str) -> str:
    value = _group_comparison_value(group, metric)
    if metric is Metric.COUNT:
        noun = "movimiento" if value == 1 else "movimientos"
        return f"{value} {noun}"
    if metric is Metric.AVERAGE and group.movement_count == 0:
        return "sin movimientos"
    return money(value, currency)


def compare_averages(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """Answer a category comparison with category means, never their combined mean."""
    usable = [group for group in answer.groups if group.movement_count > 0]
    if answer.is_empty or len(usable) < 2:
        return write(answer, plan)

    figures = [(group, _rounded_mean(group.total_minor, group.movement_count)) for group in usable]
    details = "; ".join(
        f"{_group_name(group.key, plan)}: {money(average, answer.currency)} "
        f"({group.movement_count} {'movimiento' if group.movement_count == 1 else 'movimientos'})"
        for group, average in figures
    )
    highest = max(figures, key=lambda item: item[1])
    lowest = min(figures, key=lambda item: item[1])
    if highest[1] == lowest[1]:
        conclusion = "Todas tuvieron la misma media."
    else:
        conclusion = (
            f"La más alta fue la de {_group_name(highest[0].key, plan)} y la más baja, "
            f"la de {_group_name(lowest[0].key, plan)}."
        )
    return f"Las medias por movimiento {window_of(plan)} fueron: {details}. {conclusion}"


def top_category(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """Name the leading category instead of making the user infer it from a breakdown."""
    if answer.is_empty or not answer.groups:
        return write(answer, plan)
    leading = answer.groups[0]
    category = _group_name(leading.key, plan)
    amount = money(leading.total_minor, answer.currency)
    movements = (
        "1 movimiento" if leading.movement_count == 1 else f"{leading.movement_count} movimientos"
    )
    return (
        f"La categoría en la que más gastaste {window_of(plan)} fue {category}: "
        f"{amount}, en {movements}."
    )


def when(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """The same figures, answering *when* instead of how much.

    From a real conversation:

        "¿Cuándo me suelen ingresar la nómina?"  → "17.383,97 € en nómina en lo que va de año."

    A correct number and not an answer. What makes it worth its own writer rather than a refusal is
    that the answer was already on screen: the seven payslips were listed underneath with their
    dates, 28 jan, 28 feb, 25 mar… The question had been answered and the headline said something
    else, which is the same defect as a count reported as an amount, one level up.

    **A pattern is only claimed when the evidence supports one.** Monthly means as many movements as
    distinct months — seven payslips across seven months. Sixty-four supermarket trips across seven
    months are not monthly, and only ten of them came back, so inferring "between the 6th and the
    25th" from a sample would be a shape invented out of whatever the limit happened to include.

    ponytail: monthly is the only recurrence this recognises, by counting months. Weekly,
    fortnightly and "every other Tuesday" need a domain concept for recurrence rather than a wider
    heuristic here — this one is right because it is arithmetic on complete evidence, and a
    fourth branch guessing at cadence would not be.
    """
    window = window_of(plan)
    about = filter_of(plan)
    if answer.is_empty:
        return f"No he encontrado movimientos{about} {window}."

    count = answer.movement_count
    noun = "vez" if count == 1 else "veces"
    # `movements` is capped by the deployment's evidence limit, so fewer than the count means this
    # is a sample and any pattern read off it is a fact about the limit.
    complete = len(answer.movements) == count
    months = {(m.occurred_at.year, m.occurred_at.month) for m in answer.movements}

    if complete and count >= 2 and len(months) == count:
        days = sorted({m.occurred_at.day for m in answer.movements})
        on = f"el día {days[0]}" if len(days) == 1 else f"entre el día {days[0]} y el {days[-1]}"
        return f"{count} {noun}{about} {window}, una al mes, {on}."
    if complete:
        return f"{count} {noun}{about} {window}. Tienes las fechas debajo."
    return (
        f"{count} {noun}{about} {window} — demasiados para verte un patrón. "
        f"Prueba con un periodo más corto."
    )


def _rounded_mean(total_minor: int, count: int) -> int:
    """Mean in minor units, rounded half away from zero without using a float."""
    if count <= 0:
        return 0
    units, remainder = divmod(abs(total_minor), count)
    rounded = units + int(remainder * 2 >= count)
    return -rounded if total_minor < 0 else rounded


def comparison_value(answer: QueryAnswer, plan: FinancialPlan) -> int:
    """The scalar a comparison must relate, in the unit the question requested.

    Aggregate answers deliberately carry both money and movement count. Picking
    `total_minor` unconditionally turns "where did I make more purchases?" into "where
    did I spend more money?" while still returning a plausible number. Keep the choice
    here beside the arithmetic used by the ordinary answer so both paths agree.
    """
    if plan.metric is Metric.COUNT:
        return answer.movement_count
    if plan.metric is Metric.AVERAGE:
        return _rounded_mean(answer.total_minor, answer.movement_count)
    if plan.metric is Metric.PERIOD_AVERAGE:
        return _rounded_mean(answer.total_minor, _period_count(answer, plan))
    return answer.total_minor


def comparison_figure(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """One side of a comparison, formatted in its real unit."""
    if plan.metric is Metric.AVERAGE and answer.is_empty:
        return "sin movimientos"
    value = comparison_value(answer, plan)
    if plan.metric is Metric.COUNT:
        noun = "movimiento" if value == 1 else "movimientos"
        return f"{value} {noun}"
    return money(value, answer.currency)


def comparison_difference(value: int, currency: str, metric: Metric) -> str:
    """An absolute delta in the same unit as both comparison operands."""
    if metric is Metric.COUNT:
        noun = "movimiento" if value == 1 else "movimientos"
        return f"{value} {noun}"
    return money(value, currency)


def comparison_concept(plan: FinancialPlan) -> str:
    """The noun phrase whose value is being compared."""
    if plan.metric is Metric.COUNT:
        return "número de movimientos"
    if plan.metric is Metric.AVERAGE:
        return "importe medio por movimiento"
    if plan.metric is Metric.PERIOD_AVERAGE:
        unit = {
            GroupBy.DAY: "diario",
            GroupBy.WEEK: "semanal",
            GroupBy.MONTH: "mensual",
        }[plan.group_by]
        concept = {
            Direction.EXPENSE: "gasto",
            Direction.INCOME: "ingreso",
            Direction.ALL: "importe",
        }[plan.direction]
        return f"{concept} medio {unit}"
    return {
        Direction.EXPENSE: "gasto",
        Direction.INCOME: "ingreso",
        Direction.ALL: "importe",
    }[plan.direction]


def _period_count(answer: QueryAnswer, plan: FinancialPlan) -> int:
    """Calendar buckets touched by the exact range banking executed, including empty ones."""
    group_by = plan.group_by
    if (
        group_by is GroupBy.MONTH
        and plan.period is not None
        and plan.period.period is Period.LAST_N_MONTHS
        and plan.period.n is not None
    ):
        # A rolling twelve-month window touches thirteen YYYY-MM labels when both endpoint months
        # are partial. The requested duration is still twelve months, so its monthly mean has
        # twelve equal units rather than thirteen uneven calendar fragments.
        return plan.period.n
    start = answer.period_start
    end = answer.period_end
    if start is None or end is None:
        # Compatibility for in-memory ports and older recordings. A real HTTP answer carries the
        # exact resolved bounds; grouped rows are the safest fallback available without them.
        return max(1, len(answer.groups))
    if group_by is GroupBy.DAY:
        return (end - start).days + 1
    if group_by is GroupBy.WEEK:
        first_monday = start - timedelta(days=start.weekday())
        return (end - first_monday).days // 7 + 1
    if group_by is GroupBy.MONTH:
        return (end.year - start.year) * 12 + end.month - start.month + 1
    return 1


def _figure(group: Group, answer: QueryAnswer, metric: Metric) -> str:
    """A breakdown of a count is counts. Amounts under a "how many" question would be
    the same mistake again, one row at a time."""
    if metric is Metric.COUNT:
        return f"{group.movement_count}"
    if metric is Metric.AVERAGE:
        average = _rounded_mean(group.total_minor, group.movement_count)
        return f"media {money(average, answer.currency)} ({group.movement_count})"
    return f"{money(group.total_minor, answer.currency)} ({group.movement_count})"


def _group_name(key: str, plan: FinancialPlan) -> str:
    """What to call a row of a breakdown.

    A category key is the ledger's vocabulary and it reached the screen raw: somebody asking where
    their money goes read `HOUSING_RENT`, `TRANSPORT_FUEL` and `TRANSFER_BIZUM` down the side of
    their own answer. The translation existed and `filter_of` was already using it two hundred
    lines up — this was the one place that printed the key instead.

    Only for categories. A merchant key is already a name and a month key is already a date; both
    are the ledger writing something a person reads, and putting either through a category glossary
    would be looking up a word that is not in it.
    """
    return category_words.name_of(key) if plan.group_by is GroupBy.CATEGORY else key


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
