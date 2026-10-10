"""What the person reads: banking's figures in the existing writers' words, and a closed set of
templates for every turn that is not a figure.

No figure here comes from a model, and no prose either. An answer is `answer_writer` or
`ratio_writer` over what the executor ran; a question back, a courtesy or a failure is a template,
filled only with names from the holder's own catalogues or with the name an operation quotes
(`Clarify.about`).

The rule every answer keeps: when the bank was asked, the text says what it answered. A
percentage with no base still says both amounts and why there is no percentage, rather than only
that it is undefined.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Final
from uuid import UUID

from finguard_orchestrator.application.engine.executor import (
    BalanceRun,
    Executed,
    MixedCurrenciesError,
    MovementListRun,
    NoMoreMovementsRun,
    PairRun,
    QueryRun,
    RankingRun,
    RatioRun,
)
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenMovement,
)
from finguard_orchestrator.domain.engine.operations import Clarify, Smalltalk
from finguard_orchestrator.domain.engine.state import (
    ComparisonFocus,
    DialogueState,
    Missing,
    Pending,
)
from finguard_orchestrator.domain.enums import (
    Direction,
    Metric,
    Period,
    PlanningOutcome,
    Spoken,
)
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    DomainError,
    InvalidValueError,
)
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.ports.turn_operations import InterpretationFailedError
from finguard_orchestrator.domain.services import answer_writer, category_words, ratio_writer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind

_PLANNED: Final = PlanningOutcome.PLANNED.value
_DIRECTION_WORDS: Final = {
    Direction.EXPENSE: "gastos",
    Direction.INCOME: "ingresos",
    Direction.ALL: "movimientos",
}

# --- answers ----------------------------------------------------------------------------------


def answered(
    run: Executed,
    *,
    scope: tuple[UUID, ...],
    account_names: Mapping[UUID, str],
    reporting_timezone: str,
) -> AskFinancialQuestionResult:
    """The answer to work that ran, with the accounts it was read from said under it."""
    match run:
        case BalanceRun():
            # The balance names its accounts itself.
            return _balance(run)
        case QueryRun():
            result = _query(run, reporting_timezone)
        case RankingRun():
            result = _ranking(run)
        case MovementListRun():
            result = _movement_list(run)
        case NoMoreMovementsRun():
            result = AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text="No hay movimientos más antiguos en esta lista.",
                plan="fin del listado de movimientos",
                plan_in_words="fin del listado de movimientos",
            )
        case PairRun():
            result = _pair(run)
        case RatioRun():
            result = _ratio(run)
    return replace(result, text=f"{result.text}\n{scope_note(scope, account_names)}")


def _query(run: QueryRun, reporting_timezone: str) -> AskFinancialQuestionResult:
    plan, answer = run.plan, run.answer
    if plan.metric is Metric.FREQUENCY:
        text = answer_writer.when(answer, plan, reporting_timezone=reporting_timezone)
    elif plan.metric is Metric.RECURRENCE:
        text = answer_writer.recurring_expenses(answer, plan, reporting_timezone=reporting_timezone)
    else:
        text = answer_writer.write(answer, plan)
    # A top-N of groups is answered about those N, so the figures beside it are theirs too.
    limited = plan.group_limit is not None
    ordered = answer_writer.ordered_groups(answer, plan)
    groups = ordered[: plan.group_limit] if limited else ordered
    return AskFinancialQuestionResult(
        outcome=_PLANNED,
        text=text,
        plan_in_words=answer_writer.plan_in_words(plan),
        currency=answer.currency,
        total_minor=sum(g.total_minor for g in groups) if limited else answer.total_minor,
        movement_count=sum(g.movement_count for g in groups) if limited else answer.movement_count,
        groups=[_spoken_group(g.key, g, plan) for g in groups],
        movements=[] if limited else [_spoken(m) for m in answer.movements],
    )


def _ranking(run: RankingRun) -> AskFinancialQuestionResult:
    ranking, found = run.ranking, run.movements
    selected = found[ranking.rank - 1] if len(found) >= ranking.rank else None
    text = answer_writer.ranked(selected, ranking)
    in_words = answer_writer.plan_in_words(ranking)
    currency = selected.currency if selected is not None else None
    return AskFinancialQuestionResult(
        outcome=_PLANNED,
        text=text,
        plan_in_words=in_words,
        currency=currency,
        # The person asked for one rank, so the answer and its evidence expose only that row.
        # Earlier rows are read to establish its position but must not become a misleading total
        # or a list that looks like the answer was a top-N request.
        total_minor=selected.amount_minor if selected is not None else None,
        movement_count=int(selected is not None),
        movements=[_spoken(selected)] if selected is not None else [],
    )


def _pair(run: PairRun) -> AskFinancialQuestionResult:
    """Both sides in the order they happened, earlier first, then what changed between them."""
    earlier, latest = run.pair.earlier, run.pair.latest
    windows = answer_writer.compared_window(earlier), answer_writer.compared_window(latest)
    text = answer_writer.grouped_period_comparison(
        run.earlier,
        run.latest,
        earlier,
        latest,
        earlier_window=windows[0],
        latest_window=windows[1],
    ) or _pair_text(run, *windows)
    counted = run.pair.metric is Metric.COUNT
    return AskFinancialQuestionResult(
        outcome=_PLANNED,
        text=text,
        plan_in_words=(
            f"{answer_writer.plan_in_words(earlier)} frente a {answer_writer.plan_in_words(latest)}"
        ),
        currency=run.latest.currency,
        # No single leading figure: the sentence states both sides and the change between them,
        # and the change goes out as one. A count's change is movements, which `_minor` is not.
        total_minor=None,
        difference_minor=None if counted else run.difference,
        movement_count=run.earlier.movement_count + run.latest.movement_count,
        groups=[
            _side(windows[0], run.earlier, earlier),
            _side(windows[1], run.latest, latest),
        ],
        movements=[_spoken(m) for m in (*run.earlier.movements, *run.latest.movements)],
    )


def _pair_text(run: PairRun, earlier_window: str, latest_window: str) -> str:
    earlier, latest = run.pair.earlier, run.pair.latest
    counted = run.pair.metric is Metric.COUNT
    concept = answer_writer.comparison_concept(latest)
    about = answer_writer.compared_subject(latest)
    figures = (
        f"{earlier_window.capitalize()}: {answer_writer.comparison_figure(run.earlier, earlier)}; "
        f"{latest_window}: {answer_writer.comparison_figure(run.latest, latest)}."
    )
    if run.pair.metric is Metric.AVERAGE and (run.earlier.is_empty or run.latest.is_empty):
        empty = earlier_window if run.earlier.is_empty else latest_window
        conclusion = (
            f"No hubo movimientos{about} {empty}, así que la media de ese periodo no está definida."
        )
    elif run.difference == 0:
        conclusion = (
            f"Hubo el mismo número de movimientos{about} en ambos periodos."
            if counted
            else f"El {concept}{about} fue el mismo en ambos periodos."
        )
    else:
        higher = latest_window if run.difference > 0 else earlier_window
        change = answer_writer.comparison_difference(
            abs(run.difference), run.latest.currency, run.pair.metric
        )
        conclusion = (
            f"Hubo {change} más{about} {higher}."
            if counted
            else f"El {concept}{about} fue {change} mayor {higher}."
        )
    return f"{figures} {conclusion}"


def _ratio(run: RatioRun) -> AskFinancialQuestionResult:
    ratio = run.ratio
    if ratio.kind is RatioKind.PERCENT_CHANGE and not _has_percentage(run):
        text = _no_percentage(run)
    elif ratio.kind is RatioKind.SHARE:
        text = _share(run)
    else:
        text = ratio_writer.write(run.part, run.whole, ratio)
    # A share's whole contains its part: the part's movements are the evidence, once.
    share = ratio.kind is RatioKind.SHARE
    movements = run.part.movements if share else (*run.whole.movements, *run.part.movements)
    return AskFinancialQuestionResult(
        outcome=_PLANNED,
        text=text,
        plan_in_words=(
            f"{answer_writer.plan_in_words(ratio.part)} respecto a "
            f"{answer_writer.plan_in_words(ratio.whole)}"
        ),
        currency=run.part.currency,
        movement_count=(
            run.part.movement_count if share else run.part.movement_count + run.whole.movement_count
        ),
        movements=[_spoken(m) for m in movements],
    )


def _share(run: RatioRun) -> str:
    """`ratio_writer`'s sentence, except where its label would lose the part: it names categories
    and shops of spending, so a person was dropped («Tus gastos» for what went to Marta) and income
    was called spending («El gasto en nómina»). Those are said here the way the answers say them."""
    part = run.ratio.part
    if not part.counterparties and part.direction is Direction.EXPENSE:
        return ratio_writer.write(run.part, run.whole, run.ratio)
    noun = _DIRECTION_WORDS[part.direction]
    about = answer_writer.filter_of(part)
    window = answer_writer.window_of(part)
    share = ratio_writer.basis_points(run.part.total_minor, run.whole.total_minor)
    if share is None:
        return (
            f"No hubo {noun} {window}, así que no puedo calcular qué porcentaje representan "
            f"tus {noun}{about}."
        )
    return (
        f"Tus {noun}{about} representan el {ratio_writer.percentage(share)} de tus {noun} "
        f"totales {window}: {answer_writer.money(run.part.total_minor, run.part.currency)} de "
        f"{answer_writer.money(run.whole.total_minor, run.whole.currency)}."
    )


def _has_percentage(run: RatioRun) -> bool:
    """Whether a change between the two sides is a percentage at all. Not from a base of zero, and
    not for an average with a side that had no movements: that side has no average, and «a sin
    movimientos» is a change to the absence of a figure."""
    earlier, latest = run.ratio.whole, run.ratio.part
    if latest.metric is Metric.AVERAGE and (run.whole.is_empty or run.part.is_empty):
        return False
    return answer_writer.comparison_value(run.whole, earlier) != 0


# What there was none of, on a side of a change with no movements.
_NOTHING: Final = {
    Direction.EXPENSE: "no gastaste nada",
    Direction.INCOME: "no ingresaste nada",
    Direction.ALL: "no hubo movimientos",
}


def _no_percentage(run: RatioRun) -> str:
    """A change with no percentage still says both sides, and why: «No hay variación porcentual:
    en junio de 2026 no gastaste nada en ocio; en julio de 2026, 23,06 €.»"""
    earlier = _side_said(run.ratio.whole, run.whole, base=True)
    latest = _side_said(run.ratio.part, run.part, base=False)
    return f"No hay variación porcentual: {earlier}; {latest}."


def _side_said(plan: FinancialPlan, answer: QueryAnswer, *, base: bool) -> str:
    window = answer_writer.compared_window(plan)
    if answer.is_empty:
        # A count or an average of nothing is no movements; an amount of nothing is nothing spent.
        counted = plan.metric in (Metric.COUNT, Metric.AVERAGE)
        nothing = "no hubo movimientos" if counted else _NOTHING[plan.direction]
        return f"{window} {nothing}{answer_writer.filter_of(plan)}"
    figure = answer_writer.comparison_figure(answer, plan)
    if base and answer_writer.comparison_value(answer, plan) == 0:
        return f"{window} la cifra de partida fue {figure}"
    return f"{window}, {figure}"


def _balance(run: BalanceRun) -> AskFinancialQuestionResult:
    names = tuple(holding.display_name for holding in run.selected or ())
    # The line «Ver la consulta» shows, and the turn remembered: what was asked, and no figure.
    asked = f"saldo de {answer_writer.listed(names)}" if names else "saldo de tus cuentas"
    return AskFinancialQuestionResult(
        outcome=Spoken.BALANCE.value,
        text=answer_writer.balance(run.summary, selected=run.selected),
        plan_in_words=asked,
        currency=run.summary.currency,
        total_minor=answer_writer.leading_figure(run.summary, selected=run.selected),
    )


def scope_note(scope: tuple[UUID, ...], account_names: Mapping[UUID, str]) -> str:
    """Which accounts the figures came from, by the holder's names for them."""
    if not scope:
        return "Consultado en todas tus cuentas."
    names = tuple(account_names.get(account_id) for account_id in scope)
    if None in names:
        # An account selected earlier that the directory no longer lists.
        return "Consultado en las cuentas seleccionadas."
    return f"Consultado en {answer_writer.listed(tuple(n for n in names if n))}."


def _side(window: str, answer: QueryAnswer, plan: FinancialPlan) -> SpokenGroup:
    return _spoken_group(window, Group(window, answer.total_minor, answer.movement_count), plan)


def _spoken_group(key: str, group: Group, plan: FinancialPlan) -> SpokenGroup:
    """A row with its figure in the unit the plan measures, as the rules route reports it."""
    value = (
        answer_writer.comparison_value(
            QueryAnswer("", group.total_minor, group.movement_count), plan
        )
        if plan.metric in (Metric.COUNT, Metric.AVERAGE)
        else group.total_minor
    )
    return SpokenGroup(key, group.total_minor, group.movement_count, value)


def _spoken(movement: Movement) -> SpokenMovement:
    return SpokenMovement(
        occurred_at=movement.occurred_at.isoformat(),
        description=movement.description,
        amount_minor=movement.amount_minor,
        direction=movement.direction,
        transaction_id=movement.transaction_id,
        currency=movement.currency,
        merchant_name=movement.merchant_name,
        category=movement.category,
        transaction_type=movement.transaction_type.value if movement.transaction_type else None,
        counterparty_name=movement.counterparty_name,
        counterparty_type=movement.counterparty_type.value,
        receipt_category=movement.receipt_category.value if movement.receipt_category else None,
    )


def _movement_list(run: MovementListRun) -> AskFinancialQuestionResult:
    count = len(run.answer.movements)
    text = answer_writer.recent_movements(count)
    if run.answer.has_more:
        text += f' Dime "dame otros {run.plan.page_size}" para ver los siguientes.'
    direction = {
        Direction.ALL: "movimientos recientes",
        Direction.EXPENSE: "gastos recientes",
        Direction.INCOME: "ingresos recientes",
    }[run.plan.direction]
    details = [direction]
    if run.plan.period is not None:
        described = FinancialPlan(direction=run.plan.direction, period=run.plan.period)
        details.append(answer_writer.window_of(described))
    elif run.plan.date_from is not None and run.plan.date_to is not None:
        described = FinancialPlan(
            direction=run.plan.direction,
            date_range=AbsoluteRange(run.plan.date_from, run.plan.date_to),
        )
        details.append(answer_writer.compared_window(described))
    details.extend(category_words.name_of(category) for category in run.plan.categories)
    details.extend(f"comercios con {term}" for term in run.plan.merchant_terms)
    plan_in_words = ", ".join(details)
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=text,
        plan=plan_in_words,
        plan_in_words=plan_in_words,
        movements=[_spoken(movement) for movement in run.answer.movements],
    )


# --- turns that ran no work ---------------------------------------------------------------------

_SMALLTALK: Final = {
    "GREETING": (Spoken.COURTESY, "¡Hola! Pregúntame por tus gastos, tus ingresos o tu saldo."),
    "THANKS": (Spoken.COURTESY, "De nada. Si necesitas algo más de tus cuentas, dímelo."),
    "IDENTITY": (
        Spoken.IDENTITY,
        "Soy FinGuard, tu asistente financiero. Cada cifra que te doy viene de tu banco, "
        "y te enseño la consulta que he hecho.",
    ),
    "HELP": (
        Spoken.IDENTITY,
        "Puedo decirte cuánto gastaste o ingresaste en un periodo, por categoría, comercio o "
        "persona; tus mayores gastos; comparar dos periodos, también en porcentaje; qué parte de "
        "tus gastos se fue en algo; y tu saldo.",
    ),
}
_RESET: Final = "De acuerdo, empezamos de nuevo. ¿Qué quieres consultar?"


def replied(
    reply: Clarify | Smalltalk | None,
    state: DialogueState,
    account_names: Mapping[UUID, str],
    *,
    today: date,
    payments_available: bool = False,
) -> AskFinancialQuestionResult:
    """A turn the bank was not asked about: a question back, a courtesy, or a fresh start (no
    reply at all, which only a reset leaves)."""
    if isinstance(reply, Clarify):
        question = _question(reply, state, account_names, today)
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value, text=question, question=question
        )
    if isinstance(reply, Smalltalk):
        outcome, text = _SMALLTALK[reply.kind]
        if reply.kind == "HELP" and payments_available:
            text += (
                " También puedo preparar un Bizum para uno de tus contactos guardados y enseñarte "
                "la vista previa. No se envía hasta que confirmes."
            )
        return AskFinancialQuestionResult(outcome=outcome.value, text=text)
    return AskFinancialQuestionResult(outcome=Spoken.GUIDANCE.value, text=_RESET)


def _question(
    reply: Clarify, state: DialogueState, account_names: Mapping[UUID, str], today: date
) -> str:
    about = reply.about
    if len(reply.candidates) > 1:
        # Several of the holder's own entries fit the name: offering them is the question, and
        # «No encuentro a Marta» would be false with two Martas in the address book.
        names = (
            tuple(category_words.name_of(key) for key in reply.candidates)
            if reply.missing is Missing.SUBJECT
            else reply.candidates
        )
        return f"¿A cuál te refieres: {', '.join(names[:-1])} o {names[-1]}?"
    match reply.missing:
        case Missing.WINDOW:
            return _window_question(state.pending, today)
        case Missing.PERSON:
            return (
                f"No encuentro a {about} entre tus contactos."
                if about
                else "¿De qué persona me hablas?"
            )
        case Missing.ACCOUNT:
            unknown = f"No encuentro la cuenta «{about}». " if about else ""
            names = tuple(account_names.values())
            which = f": {answer_writer.listed(names)}" if names else ""
            return f"{unknown}¿En cuál de tus cuentas quieres consultarlo{which}?"
        case Missing.OPERAND:
            if isinstance(state.focus, ComparisonFocus):
                pair = state.focus.comparison
                return (
                    f"¿Cuál de los dos periodos: {answer_writer.compared_window(pair.earlier)} "
                    f"o {answer_writer.compared_window(pair.latest)}?"
                )
            return "¿Qué dos periodos quieres comparar?"
        case Missing.SUBJECT:
            if about:
                return f"No tengo ninguna categoría «{about}». ¿Sobre qué quieres preguntar?"
            return "¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo."
        case Missing.CORRECTION:
            return "¿Qué parte de la consulta anterior querías corregir?"
        case Missing.TASK_LIMIT:
            return (
                "Tienes varias consultas pendientes. Retoma, completa o cancela una antes de "
                "abrir otra."
            )


# Stands in for the window a period question asks for, only so the subject can be named in the
# answers' own words (`window_question`). Nothing is ever run with it.
_NAMING_WINDOW: Final = RelativePeriod(Period.THIS_MONTH)


def _window_question(pending: Pending | None, today: date) -> str:
    """«¿De qué periodo quieres ver tus gastos en restaurantes?», naming what was already said."""
    draft = pending.draft if pending else None
    if draft is None or draft.direction is None:
        return "¿De qué periodo quieres verlo?"
    try:
        named = FinancialPlan(
            direction=draft.direction,
            period=_NAMING_WINDOW,
            categories=draft.categories or (),
            excluded_categories=draft.excluded_categories or (),
            merchants=draft.merchants or (),
            excluded_merchants=draft.excluded_merchants or (),
            counterparties=draft.people or (),
        )
    except InvalidValueError:
        return "¿De qué periodo quieres verlo?"
    question = (
        answer_writer.window_question(named)
        or f"¿De qué periodo quieres ver tus {_DIRECTION_WORDS[draft.direction]}?"
    )
    # Said only of the window the open question is about, and only while it has not begun: the
    # draft of a question about somebody else can hold a past month.
    not_begun = (
        pending is not None
        and pending.missing is Missing.WINDOW
        and isinstance(draft.window, AbsoluteRange)
        and draft.window.start > today
    )
    return f"Ese periodo todavía no ha empezado. {question}" if not_begun else question


# --- turns that could not be answered -----------------------------------------------------------


def failed(error: DomainError) -> AskFinancialQuestionResult:
    """An honest answer to a turn that ran into a wall. Nothing is saved for it."""
    if isinstance(error, BankingUnavailableError):
        return AskFinancialQuestionResult(
            outcome="BANKING_UNAVAILABLE", text="El banco no ha respondido. Vuelve a intentarlo."
        )
    if isinstance(error, InterpretationFailedError):
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PROVIDER_ERROR.value,
            text="No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.",
        )
    if isinstance(error, MixedCurrenciesError):
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="No puedo relacionar importes en monedas distintas.",
        )
    if isinstance(error, InvalidValueError):
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="He entendido la pregunta, pero la consulta que he construido no es válida.",
        )
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.INVALID_DOMAIN.value,
        text="He entendido la pregunta, pero el banco ha rechazado la consulta.",
    )
