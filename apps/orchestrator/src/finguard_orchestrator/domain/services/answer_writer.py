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

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    ReceiptCategory,
    TransactionType,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import (
    category_words,
    frequency_analyzer,
    recurrence_analyzer,
    spanish,
)
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
    "THIS_QUARTER": "en lo que va de trimestre",
    "LAST_QUARTER": "el trimestre pasado",
    "THIS_SEMESTER": "en lo que va de semestre",
    "LAST_SEMESTER": "el semestre pasado",
    "THIS_FORTNIGHT": "en lo que va de quincena",
    "LAST_FORTNIGHT": "la quincena pasada",
    "THIS_WEEKEND": "este fin de semana",
    "LAST_WEEKEND": "el fin de semana pasado",
    "THIS_YEAR": "en lo que va de año",
}

_WEEKDAYS: Final = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábados",
    "domingos",
)


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


def filter_of(plan: FinancialPlan | TopMovements, shops: tuple[str, ...] = ()) -> str:
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

    **An exclusion that cannot have changed the figure is not named here.** It used to be, and the
    sentence claimed a filter the ledger could not act on: "sin contar el alquiler" over a
    supermarket ranking produced ", sin alquiler" above three identical rows. What the plan is
    still carrying is said by `inert_exclusion_note` instead, as the truth about the figure rather
    than as a filter on it.

    `shops` are the shops a merchant-term figure turned out to be about (`_shops_shown`), named in
    place of the term when the answer can name every one of them.
    """
    transaction_types = getattr(plan, "transaction_types", ())
    direct_debits_only = transaction_types == (TransactionType.DIRECT_DEBIT,)
    counterparties = getattr(plan, "counterparties", ())
    excluded_counterparties = getattr(plan, "excluded_counterparties", ())
    if counterparties:
        included = f" con {listed(counterparties)}"
    elif plan.merchants:
        included = f" en {listed(plan.merchants)}"
    elif isinstance(plan, FinancialPlan) and plan.merchant_terms:
        included = f" en {listed(shops)}" if shops else f" en {carrying(plan.merchant_terms)}"
    elif plan.categories:
        included = f" en {listed(tuple(category_words.name_of(c) for c in plan.categories))}"
    elif direct_debits_only:
        included = " entre tus recibos domiciliados"
    else:
        included = ""
    inert = inert_exclusions(plan)
    excluded_names = (
        *tuple(
            category_words.name_of(category)
            for category in plan.excluded_categories
            if category not in inert
        ),
        *plan.excluded_merchants,
        *excluded_counterparties,
    )
    if not excluded_names:
        return included
    return (
        f"{included}, sin {listed(excluded_names)}"
        if included
        else f" sin {listed(excluded_names)}"
    )


def inert_exclusions(plan: FinancialPlan | TopMovements) -> tuple[str, ...]:
    """The excluded categories that cannot have changed this figure, as ledger identifiers.

    `categories` is an inclusive filter: a plan carrying one counts nothing outside it, so
    excluding a category it does not contain cannot remove a movement. `check_filters` already
    refuses a category that is both included and excluded, so in practice a category filter makes
    every exclusion beside it inert — and the membership test is still written out, as a second
    lock on that door. If a plan ever did carry both, calling it inert would deny a change the
    reader can see in the number.

    Only `categories` decides it, and that restraint is the load-bearing half. With no category
    filter, "sin contar el alquiler" removes rent for real: 975,97 € against 1.895,97 €, 27
    movements against 28. A plan filtered by shop or by counterparty says nothing about which
    categories its movements carry, so "sin alquiler" over "en Mercadona" is not provably inert
    either. Both keep today's behaviour, which names the exclusion and claims nothing more.

    The patch still applies the exclusion — `PlanPatch.apply_to` is unchanged, because the
    instruction is real and matters the day the category filter widens. This is only what lets the
    answer stop claiming a filter that cannot have moved the figure.
    """
    if not plan.categories:
        return ()
    return tuple(
        category for category in plan.excluded_categories if category not in plan.categories
    )


def inert_exclusion_note(plan: FinancialPlan | TopMovements) -> str:
    """Why this figure is the same as the one above it, or "" when the exclusion could have bitten.

    Said in the same category vocabulary as the answer, and deliberately without an article:
    `name_of` returns "alquiler", "nómina" and "suscripciones", and no single article agrees with
    all three. Joined with "ni", which is how Spanish enumerates under a negation — "no hay
    alquiler y ocio" reads as a claim about the pair rather than about each of them.
    """
    inert = inert_exclusions(plan)
    if not inert:
        return ""
    left_out = " ni ".join(category_words.name_of(category) for category in inert)
    counted = listed(tuple(category_words.name_of(c) for c in plan.categories))
    return f"No hay {left_out} en {counted}, así que la cifra no cambia."


def _inert_note(plan: FinancialPlan | TopMovements) -> str:
    """The note as its own line, the way every other note is appended under an answer.

    Empty for an answer that states no number: "no he encontrado movimientos" has no figure for
    "la cifra no cambia" to be about, and the invitation to widen the search is the whole answer.
    """
    note = inert_exclusion_note(plan)
    return f"\n{note}" if note else ""


def carrying(terms: tuple[str, ...]) -> str:
    """The shops a merchant term stands for, as a person says it: «comercios con «gimnasio» en el
    nombre».

    "Comercios cuyo nombre contiene gimnasio" was the line, and it read as a query nobody had typed.
    """
    return f"comercios con {terms_said(terms)} en el nombre"


def terms_said(terms: tuple[str, ...]) -> str:
    """Merchant terms as a person reads them: «gimnasio», «apuest» o «bet».

    Joined with «o»: the terms are alternatives, and a shop that carries either one is counted --
    «apuest» or «bet», not a shop that carries both. One spelling of a word is said once: the plan
    carries «óptica» and «optica» because the bank does not fold accents and a shop may write
    either, and the person asked about one word.
    """
    spelled: dict[str, str] = {}
    for term in terms:
        spelled.setdefault(spanish.folded(term), term)
    said = [f"«{term}»" for term in spelled.values()]
    return said[0] if len(said) == 1 else f"{', '.join(said[:-1])} o {said[-1]}"


def _shops_shown(answer: QueryAnswer, plan: FinancialPlan) -> tuple[str, ...]:
    """The shops a merchant-term answer is about, by name -- when its evidence shows all of them.

    «29,90 € en Gimnasio Altafit» rather than «en comercios con «gimnasio» en el nombre»: the
    holder asked about their gym, and the answer knows which one it is. Only when the movements
    beside the figure are every movement behind it, though. A year of gym is twelve charges against
    the ten the deployment sends, and naming the shops among the ten would leave out a second gym
    that only appears in the other two -- a list that reads complete and is not.
    """
    if (
        not plan.merchant_terms
        or plan.merchants
        or answer.movement_count == 0
        or len(answer.movements) < answer.movement_count
        # A movement with no shop's name is one the list cannot name, and would leave out.
        or any(not movement.merchant_name for movement in answer.movements)
    ):
        return ()
    return tuple(dict.fromkeys(movement.merchant_name or "" for movement in answer.movements))


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
        about = f"{filter_of(ranking)} {_conversational_window_of(ranking)}"
        note = _inert_note(ranking)
        return f"Tu mayor {noun}{about} fue de {amount}: {what}, el {when}.{note}"
    # No note here for the same reason there is no filter and no window: "el segundo" is read
    # directly under the answer it follows, which already said what the exclusion did.
    return f"{_position(ranking.rank)} fue de {amount}: {what}, el {when}."


def ranked_many(movements: tuple[Movement, ...], ranking: TopMovements, *, requested: int) -> str:
    """Several concrete ranking rows, each already selected by banking."""
    about = f"{filter_of(ranking)} {_conversational_window_of(ranking)}"
    if not movements:
        return f"No he encontrado movimientos{about} para construir ese ranking."
    noun = "gasto" if ranking.direction is Direction.EXPENSE else "ingreso"
    if len(movements) == requested:
        head = f"Tus {requested} mayores {noun}s{about} fueron:"
    else:
        head = (
            f"He encontrado {len(movements)} {noun}s{about}; "
            f"no hay {requested} para completar el ranking:"
        )
    lines = []
    for position, movement in enumerate(movements, start=1):
        amount = money(movement.amount_minor, movement.currency)
        what = movement.merchant_name or movement.description
        lines.append(f"  {position}. {what}: {amount}, el {_spoken_date(movement.occurred_at)}")
    return f"{head}\n" + "\n".join(lines) + _inert_note(ranking)


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


def _conversational_window_of(plan: FinancialPlan | TopMovements) -> str:
    """Use a natural month label only in concrete movement-ranking answers."""
    if plan.date_range is not None:
        start = plan.date_range.start
        end = plan.date_range.end
        if (
            start.day == 1
            and (end + timedelta(days=1)).day == 1
            and start.month == end.month
            and start.year == end.year
        ):
            return f"en {month_name(start.year, start.month)}"
    return window_of(plan)


# What a counterparty type is called in a sentence. Here rather than beside the alias answers that
# used it first, because the query line says the same thing and a second vocabulary for one fact
# is how two screens come to disagree.
COUNTERPARTY_LABELS: Final[dict[CounterpartyType, str]] = {
    CounterpartyType.MERCHANT: "comercio",
    CounterpartyType.PERSON: "persona",
    CounterpartyType.EMPLOYER: "empresa pagadora",
    CounterpartyType.BILLER: "emisor de recibos",
    CounterpartyType.ACCOUNT: "cuenta contraparte",
    CounterpartyType.BANK: "banco",
    CounterpartyType.UNKNOWN: "contraparte",
}

# The rest of the line's identifiers, in the words already on screen: the direction and the
# calendar buckets as the chart titles name them ("Gastos por mes"), the other dimensions as the
# answer sentences do ("tus 3 comercios con…"), and the two measures `describe` left raw as the
# answers state them — a count of movements, and an average amount per movement.
_DIRECTION_WORDS: Final[dict[Direction, str]] = {
    Direction.EXPENSE: "gastos",
    Direction.INCOME: "ingresos",
    Direction.ALL: "movimientos",
}
_GROUPING_WORDS: Final[dict[GroupBy, str]] = {
    GroupBy.DAY: "día",
    GroupBy.WEEK: "semana",
    GroupBy.MONTH: "mes",
    GroupBy.CATEGORY: "categoría",
    GroupBy.MERCHANT: "comercio",
    GroupBy.COUNTERPARTY: "contraparte",
    GroupBy.ACCOUNT: "cuenta",
}
_METRIC_WORDS: Final[dict[Metric, str]] = {
    Metric.COUNT: "número de movimientos",
    Metric.AVERAGE: "importe medio por movimiento",
}


def describe(plan: FinancialPlan | TopMovements) -> str:
    """The plan in one line, in the ledger's own identifiers: the line the planner is shown.

    `prompt.py` quotes this into the planner's prompt as the previous question, and counts on
    every token in it coming from a closed set — enum values, ISO dates, category identifiers.
    The planner's evaluations were measured against that prompt, so this line stays exactly what
    it was. It used to be the person's line too, and that is how «Ver la consulta» came to read
    "en el último mes, expense, subscriptions": a person reads `plan_in_words` now.
    """
    return _one_line(plan, in_words=False)


def plan_in_words(plan: FinancialPlan | TopMovements) -> str:
    """The plan in one readable line, for the person who asked: «Ver la consulta».

    Shown with every answer on purpose. A figure whose derivation the user cannot
    inspect is a number to be believed, and there is a language model in this pipeline —
    showing the question it actually asked is the difference between a tool and an
    oracle.

    Field for field the same line as `describe`, with every identifier said the way the answers
    already say it — "suscripciones" from `category_words`, "por comercio", "gastos" — rather
    than in a second vocabulary written for this line. An answer that says «suscripciones» above
    a query line saying «subscriptions» reads as two different questions.
    """
    return _one_line(plan, in_words=True)


def _one_line(plan: FinancialPlan | TopMovements, *, in_words: bool) -> str:
    """Both lines, from one walk over the plan, so they cannot disagree about which fields exist.

    `in_words` decides only how an identifier is said. Everything already Spanish — the window,
    "solo traspasos", "media por periodo" — is the same text in both.
    """
    parts = [
        window_of(plan),
        _DIRECTION_WORDS[plan.direction] if in_words else plan.direction.value.lower(),
    ]
    if isinstance(plan, FinancialPlan) and plan.internal_only:
        parts.append("solo traspasos entre cuentas")
    if isinstance(plan, FinancialPlan) and plan.transaction_types == (
        TransactionType.DIRECT_DEBIT,
    ):
        parts.append("solo recibos domiciliados")
    if isinstance(plan, TopMovements):
        # The rank is the whole point of a ranking, so a description without it would show the same
        # line for "el más caro" and "el segundo más caro".
        parts.append(f"nº {plan.rank} por importe")
    if plan.categories:
        parts.append(
            "/".join(category_words.name_of(category) for category in plan.categories)
            if in_words
            else "/".join(plan.categories).lower()
        )
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
    if isinstance(plan, FinancialPlan) and plan.merchant_terms:
        # The identifier line keeps the words the planner and the turn interpreter were measured
        # on; the person reads the shops the terms stand for, as the answer above says them.
        parts.append(
            carrying(plan.merchant_terms)
            if in_words
            else "comercio contiene " + "/".join(plan.merchant_terms)
        )
    if plan.excluded_merchants:
        parts.append("sin " + "/".join(plan.excluded_merchants))
    if isinstance(plan, FinancialPlan):
        if plan.counterparties:
            parts.append("/".join(plan.counterparties))
        if plan.excluded_counterparties:
            parts.append("sin " + "/".join(plan.excluded_counterparties))
        if plan.counterparty_types:
            parts.append(
                "tipo "
                + "/".join(
                    COUNTERPARTY_LABELS[item] if in_words else item.value.lower()
                    for item in plan.counterparty_types
                )
            )
        # The grouping and the metric belong to an aggregate. A ranking has neither, and its rank is
        # appended above — where the reader is looking for what makes the two lines different.
        if plan.group_by.value != "NONE":
            grouping = _GROUPING_WORDS[plan.group_by] if in_words else plan.group_by.value.lower()
            parts.append(f"por {grouping}")
        if plan.group_limit is not None:
            parts.append(f"top {plan.group_limit}")
        if plan.metric is Metric.PERIOD_AVERAGE:
            parts.append("media por periodo")
        elif plan.metric is Metric.CUMULATIVE:
            parts.append("acumulado")
        elif plan.metric is Metric.FREQUENCY:
            parts.append("frecuencia")
        elif plan.metric is Metric.RECURRENCE:
            parts.append("gastos recurrentes")
        elif plan.metric.value != "SUM":
            parts.append(
                _METRIC_WORDS.get(plan.metric, plan.metric.value.lower())
                if in_words
                else plan.metric.value.lower()
            )
    return ", ".join(parts)


def _listed(names: tuple[str, ...]) -> str:
    """Names joined the way Spanish joins them: commas, and "y" before the last."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} y {names[-1]}"


def how_many_payees(answer: QueryAnswer, plan: FinancialPlan) -> str:
    """How many distinct payees, and who they are.

    The number of groups rather than the number of movements, which is the whole point: thirty-six
    charges from four shops is four subscriptions, and answering "36" to "¿cuántas suscripciones
    tengo?" is a true figure for a question nobody asked.

    It says "te han cobrado" and never "activas", however the question was phrased. What the ledger
    knows is who charged this holder inside a window; whether a subscription is still live is not in
    it, and a sentence claiming otherwise would be inventing the one fact the holder is asking for.
    """
    kind = category_words.name_of(plan.categories[0]) if plan.categories else "gastos"
    window = window_of(plan)
    payees = tuple(group.key for group in answer.groups if group.key)
    if not payees:
        return f"No te han cobrado nada en {kind} {window}."
    count = len(payees)
    noun = kind if count > 1 else _singular(kind)
    # No adjective. "Distintas" would need to agree with a noun whose gender varies by category, and
    # a gender table is more code than the names that follow already make unambiguous.
    return f"Te han cobrado {count} {noun} {window}: {_listed(payees)}."


def _singular(kind: str) -> str:
    """Enough Spanish to not write "1 suscripciones". Only the plural this vocabulary produces."""
    if kind.endswith("ones"):
        return kind[:-4] + "ón"
    return kind[:-1] if kind.endswith("s") else kind


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
    accumulating = plan.metric is Metric.CUMULATIVE
    # What the figure is of. Empty for a question that named no filter, which is most of them.
    about = filter_of(plan, _shops_shown(answer, plan))
    # Why the figure is the same as the one above it, when a follow-up excluded something the
    # category filter was never counting. Empty for every other answer, which is most of them.
    inert_note = _inert_note(plan)

    if answer.is_empty:
        # "Gastaste 0" and "no encontré nada" read the same and mean different things.
        # The second is usually a plan that was wrong, and saying so lets the user
        # correct it instead of believing a zero.
        if plan.categories and (plan.merchants or plan.merchant_terms or plan.counterparties):
            next_step = "buscar sin categoría, buscar sin comercio o ampliar el periodo"
        elif plan.categories:
            next_step = "buscar sin categoría o ampliar el periodo"
        elif plan.merchants or plan.merchant_terms or plan.counterparties:
            next_step = "buscar sin comercio o ampliar el periodo"
        else:
            next_step = "ampliar el periodo o consultar las categorías disponibles"
        return f"No he encontrado movimientos{about} {window}. Puedes {next_step}."

    noun = "movimiento" if answer.movement_count == 1 else "movimientos"
    ranked_groups = answer.groups[: plan.group_limit] if plan.group_limit is not None else None
    if plan.group_limit is not None:
        count = len(ranked_groups or ())
        dimension = {
            GroupBy.CATEGORY: "categoría" if count == 1 else "categorías",
            GroupBy.MERCHANT: "comercio" if count == 1 else "comercios",
            GroupBy.COUNTERPARTY: "contraparte" if count == 1 else "contrapartes",
            GroupBy.ACCOUNT: "cuenta" if count == 1 else "cuentas",
        }[plan.group_by]
        criterion = {
            Metric.COUNT: "más operaciones",
            Metric.AVERAGE: "mayor importe medio por movimiento",
        }.get(plan.metric, "mayor importe total")
        if count == 1:
            article = "El" if plan.group_by is GroupBy.MERCHANT else "La"
            head = f"{article} {dimension} con {criterion}{about} {window} es:"
        else:
            head = f"Tus {count} {dimension} con {criterion}{about} {window} son:"
    elif plan.group_by is GroupBy.ACCOUNT and len(answer.groups) == 1:
        account = answer.groups[0]
        if answer.movement_count == 1:
            return (
                f"El movimiento encontrado {window} está en "
                f"{account.key}: {_figure(account, answer, plan.metric)}.{inert_note}"
            )
        return (
            f"Todos los {answer.movement_count} {noun} encontrados {window} están en "
            f"{account.key}: {_figure(account, answer, plan.metric)}.{inert_note}"
        )
    elif plan.group_by is GroupBy.ACCOUNT:
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
    elif accumulating:
        concept = {
            Direction.EXPENSE: "gasto",
            Direction.INCOME: "ingreso",
            Direction.ALL: "importe neto",
        }[plan.direction]
        head = (
            f"El {concept} acumulado{about} {window} terminó en "
            f"{money(answer.total_minor, answer.currency)}. Evolución:"
        )
    else:
        total = money(answer.total_minor, answer.currency)
        head = f"{total}{about} {window}, en {answer.movement_count} {noun}."

    if not _is_breakdown(answer):
        return f"{head}{inert_note}"

    visible = ranked_groups if ranked_groups is not None else answer.groups
    shown = visible[:MAX_SPOKEN_GROUPS]
    lines = "\n".join(
        f"  · {_group_name(group.key, plan)}: {_figure(group, answer, plan.metric)}"
        for group in shown
    )
    rest = len(visible) - len(shown)
    tail = f"\n  · y {rest} más" if rest > 0 else ""
    return f"{head}\n{lines}{tail}{inert_note}"


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


def when(
    answer: QueryAnswer, plan: FinancialPlan, *, reporting_timezone: str = "Europe/Madrid"
) -> str:
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

    The calculation lives in `frequency_analyzer`: this writer only turns its typed result into
    Spanish. Monthly patterns require consecutive months; other cadences use the integer median of
    every interval, never a sample or a model estimate.
    """
    window = window_of(plan)
    about = filter_of(plan, _shops_shown(answer, plan))
    note = _inert_note(plan)
    if answer.is_empty:
        return f"No he encontrado movimientos{about} {window}."

    pattern = frequency_analyzer.analyse(answer, reporting_timezone=reporting_timezone)
    count = pattern.count
    noun = "vez" if count == 1 else "veces"
    if not pattern.complete:
        return (
            f"{count} {noun}{about} {window}, pero son demasiados movimientos para calcular "
            f"la frecuencia sin extrapolar. Prueba con un periodo más corto.{note}"
        )
    if count == 1:
        return (
            f"Sólo ocurrió una vez{about} {window}; todavía no hay intervalos que comparar.{note}"
        )
    if pattern.monthly:
        assert pattern.day_of_month_min is not None and pattern.day_of_month_max is not None
        on = (
            f"normalmente el día {pattern.day_of_month_min}"
            if pattern.day_of_month_min == pattern.day_of_month_max
            else (
                f"normalmente entre el día {pattern.day_of_month_min} "
                f"y el {pattern.day_of_month_max}"
            )
        )
        return f"{count} {noun}{about} {window}: una al mes, {on}.{note}"

    interval = pattern.median_interval_days
    assert interval is not None
    if count == 2:
        cadence = f"separadas por {interval} días"
    elif interval == 0:
        cadence = "habitualmente varias veces en un mismo día"
    elif interval == 1:
        cadence = "aproximadamente cada día"
    else:
        cadence = f"aproximadamente cada {interval} días"
    weekday = (
        f", sobre todo los {_WEEKDAYS[pattern.usual_weekday]}"
        if pattern.usual_weekday is not None
        else ""
    )
    return f"{count} {noun}{about} {window}: {cadence}{weekday}.{note}"


def recurring_expenses(
    answer: QueryAnswer, plan: FinancialPlan, *, reporting_timezone: str = "Europe/Madrid"
) -> str:
    """Describe only periodic patterns supported by the complete bounded movement set."""
    window = window_of(plan)
    about = filter_of(plan, _shops_shown(answer, plan))
    note = _inert_note(plan)
    if answer.is_empty:
        return f"No he encontrado movimientos{about} {window} con los que buscar recurrencias."

    analysis = recurrence_analyzer.analyse(answer, reporting_timezone=reporting_timezone)
    if not analysis.complete:
        return (
            f"Hay {analysis.movement_count} movimientos{about} {window}, demasiados para detectar "
            f"gastos recurrentes sin extrapolar. Prueba con un periodo más corto o una "
            f"categoría.{note}"
        )
    if not analysis.candidates:
        return (
            f"No he encontrado ningún gasto con un patrón periódico suficientemente regular"
            f"{about} {window}."
        )

    shown = analysis.candidates[:MAX_SPOKEN_GROUPS]
    lines = "\n".join(
        f"  · {candidate.counterparty}: {_recurrence_cadence(candidate)}, "
        f"{_recurrence_kind(candidate)}, {_recurrence_amount(candidate, answer.currency)}; "
        f"{candidate.occurrences} cargos, {money(candidate.total_minor, answer.currency)} en total"
        for candidate in shown
    )
    rest = len(analysis.candidates) - len(shown)
    tail = f"\n  · y {rest} más" if rest else ""
    noun = "patrón" if len(analysis.candidates) == 1 else "patrones"
    return (
        f"He detectado {len(analysis.candidates)} {noun} de gasto recurrente{about} {window}:\n"
        f"{lines}{tail}{note}"
    )


def _recurrence_cadence(candidate: recurrence_analyzer.RecurringExpense) -> str:
    if candidate.cadence == "MONTHLY":
        assert candidate.day_of_month_min is not None
        assert candidate.day_of_month_max is not None
        if candidate.day_of_month_min == candidate.day_of_month_max:
            return f"mensual, normalmente el día {candidate.day_of_month_min}"
        return (
            "mensual, normalmente entre los días "
            f"{candidate.day_of_month_min} y {candidate.day_of_month_max}"
        )
    return {
        "WEEKLY": "semanal",
        "FORTNIGHTLY": "quincenal",
        "QUARTERLY": "trimestral",
    }[candidate.cadence]


def _recurrence_amount(candidate: recurrence_analyzer.RecurringExpense, currency: str) -> str:
    amount = money(candidate.average_minor, currency)
    return f"{amount} por cargo" if candidate.stable_amount else f"una media de {amount} por cargo"


def _recurrence_kind(candidate: recurrence_analyzer.RecurringExpense) -> str:
    """Name the banking rail without promoting a card charge to a direct debit."""
    receipt_names = {
        ReceiptCategory.RENT: "recibo domiciliado de alquiler",
        ReceiptCategory.SUBSCRIPTION: "recibo domiciliado de suscripción",
        ReceiptCategory.UTILITIES: "recibo domiciliado de suministros",
        ReceiptCategory.INSURANCE: "recibo domiciliado de seguro",
        ReceiptCategory.LOAN: "recibo domiciliado de préstamo",
        ReceiptCategory.TAX: "recibo domiciliado de impuestos",
        ReceiptCategory.OTHER: "recibo domiciliado",
    }
    if candidate.receipt_category is not None:
        return receipt_names[candidate.receipt_category]
    if candidate.transaction_type is TransactionType.DIRECT_DEBIT:
        return "recibo domiciliado"
    if candidate.category == "SUBSCRIPTIONS":
        return (
            "suscripción con tarjeta"
            if candidate.transaction_type is TransactionType.CARD_PAYMENT
            else "suscripción"
        )
    if candidate.category == "HOUSING_RENT":
        return "alquiler periódico"
    return "cargo periódico"


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
    if metric is Metric.CUMULATIVE:
        return f"{money(group.total_minor, answer.currency)} acumulados ({group.movement_count})"
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
