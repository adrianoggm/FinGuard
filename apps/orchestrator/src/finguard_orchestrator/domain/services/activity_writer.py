"""What an activity search says, to the agents and to the holder (spec §5.1, §7).

Templates only. Every value in a sentence here is a verified field -- a slot's time, its places, the
activity as the agent wrote it once it was grounded -- or this service's own words; the text of a
reply never is (§6.4). The weekday and month names are `answer_writer`'s, imported, never copied.

`request_text` is the other side of the same rule: the one sentence an agent reads, filled from the
verified query alone. The holder's sentence is never in it (§5.4).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Final, Protocol, get_args

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentErrorCode,
    RejectionRule,
)
from finguard_orchestrator.domain.services.answer_writer import (
    _MONTHS,
    _WEEKDAYS,
    listed,
    month_name,
)
from finguard_orchestrator.domain.services.expert_agent_words import ACTIVITIES
from finguard_orchestrator.domain.services.spanish import folded

# The rule an agent's outcome carries when its budget ran out before it answered. Coined by the
# search (`application/queries/search_activities.py`), named here because this is where it is read.
BUDGET: Final = "budget"

# Why an agent did not answer validly: a verifier rule, a gateway code, a failed task, the model
# reading the reply failing, or the budget. Anything else an `unavailable` outcome carries is the
# gateway's reason for listing the agent unusable -- the agent's standing, not this turn's luck.
_NOT_ANSWERING: Final = frozenset(
    {
        *get_args(RejectionRule),
        *get_args(AgentErrorCode),
        "FAILED",
        "REJECTED",
        "CANCELED",
        ProviderFailedError.code,
        BUDGET,
    }
)
_AUTHENTICATION: Final = frozenset({"auth_unsupported", "credential_missing"})

# The lexicon is folded; a person reads the accents.
_SHOWN: Final = {"padel": "pádel", "natacion": "natación"}


class Notice(Protocol):
    """What a sentence about one agent needs: `search_activities.AgentOutcome` is one."""

    @property
    def agent_name(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def rule(self) -> str | None: ...


def request_text(query: ActivityQuery) -> str:
    """«Busco clases de spinning el jueves 1 de octubre de 2026 entre las 15:00 y las 21:00.»

    Over several days (ruling R15): «Busco clases de yoga entre el sábado 3 de octubre de 2026 y
    el domingo 4 de octubre de 2026, de 15:00 a 21:00.», the daily hours only when a band was
    asked for -- the same hours the data part's `band` carries.
    """
    first, last = query.start.date(), query.end.date()
    activity = shown(query.activity)
    if first == last:
        return (
            f"Busco clases de {activity} el {_dated(first)} "
            f"entre las {query.start:%H:%M} y las {query.end:%H:%M}."
        )
    hours = f", de {query.band[0]:%H:%M} a {query.band[1]:%H:%M}" if query.band is not None else ""
    return f"Busco clases de {activity} entre el {_dated(first)} y el {_dated(last)}{hours}."


def offers(
    slots: Sequence[ActivitySlot],
    notices: Sequence[Notice],
    names: Mapping[str, str],
    *,
    activity: str,
) -> str:
    """«He encontrado 2 clases de spinning el jueves 1 de octubre: 18:30 en Gimnasio Sur
    (6 plazas) y 19:00 en Gimnasio Norte (4 plazas).», then one sentence per notice."""
    by_day: dict[date, list[ActivitySlot]] = {}
    for slot in slots:
        by_day.setdefault(slot.start.date(), []).append(slot)
    count = "una clase" if len(slots) == 1 else f"{len(slots)} clases"
    lead = f"He encontrado {count} de {shown(activity)}"
    days = [
        (day, listed(tuple(_item(slot, names, activity) for slot in on_day)))
        for day, on_day in by_day.items()
    ]
    if len(days) == 1:
        [(day, items)] = days
        found = f"{lead} el {_day(day)}: {items}."
    else:
        found = f"{lead}: " + "; ".join(f"el {_day(day)}, {items}" for day, items in days) + "."
    return " ".join([found, *(notice(item) for item in notices)])


def none_found(activity: str, window: ActivityQuery) -> str:
    """«No he encontrado clases de spinning el jueves 1 de octubre entre las 15:00 y las 21:00.»"""
    first, last = window.start.date(), window.end.date()
    if first == last:
        hours = (
            f" entre las {window.start:%H:%M} y las {window.end:%H:%M}"
            if window.band is not None
            else ""
        )
        when = f"el {_day(first)}{hours}"
    else:
        hours = (
            f" entre las {window.band[0]:%H:%M} y las {window.band[1]:%H:%M}"
            if window.band is not None
            else ""
        )
        when = f"del {_day(first)} al {_day(last)}{hours}"
    return f"No he encontrado clases de {shown(activity)} {when}."


def notice(outcome: Notice) -> str:
    """One agent that did not answer with slots, in one sentence (§7)."""
    name = outcome.agent_name
    if outcome.kind == "link_required":
        return f"{name} necesita que vincules tu cuenta de socio."
    if outcome.kind == "unsupported_request":
        return f"{name} pide un dato que FinGuard no proporciona."
    if outcome.rule in _AUTHENTICATION:
        return f"{name} pide una autenticación que FinGuard no admite."
    if _did_not_answer(outcome):
        return f"{name} no ha respondido de forma válida."
    return f"{name} no cumple el estándar A2A que FinGuard exige."


def unavailable(notices: Sequence[Notice]) -> str:
    """No agent answered with a timetable. «Ningún gimnasio ha respondido de forma válida.» when
    any was asked and failed, and a sentence for each agent that said something more specific."""
    plain = [item for item in notices if _did_not_answer(item)]
    specific = [notice(item) for item in notices if not _did_not_answer(item)]
    lead = ["Ningún gimnasio ha respondido de forma válida."] if plain or not specific else []
    return " ".join([*lead, *specific])


def off() -> str:
    return "Esta instalación no tiene agentes externos configurados."


def forbidden() -> str:
    return "Tu acceso no permite consultar agentes externos."


def unreachable() -> str:
    return "No puedo contactar con los agentes externos ahora mismo."


def unclear(refusal: str) -> str:
    """The sentence for a search refused before any agent was asked."""
    match refusal:
        case "no_activity":
            known = listed(tuple(shown(activity) for activity in ACTIVITIES))
            return f"¿Qué actividad buscas? Puedo consultar clases de {known}."
        case "no_window":
            return (
                "Solo puedo buscar clases de hoy en adelante: dime qué día, por ejemplo "
                "«el jueves por la tarde»."
            )
        case "no_agents":
            return "Ningún agente configurado ofrece horarios de clases."
        case "none_named":
            return "Ninguno de los gimnasios configurados tiene ese nombre."
        case "unreachable":
            return unreachable()
        case _:
            return "No he entendido qué clase buscas."


def choose_suggestions(slots: Sequence[ActivitySlot]) -> list[str]:
    """«Apúntame a la de las 18:30», for the first three distinct times offered."""
    times = list(dict.fromkeys(f"{slot.start:%H:%M}" for slot in slots))[:3]
    return [f"Apúntame a la de las {at}" for at in times]


def shown(activity: str) -> str:
    """The lexicon's folded name as a person writes it: «natacion» is «natación»."""
    return _SHOWN.get(activity, activity)


def _did_not_answer(outcome: Notice) -> bool:
    return outcome.kind == "unavailable" and (
        outcome.rule is None or outcome.rule in _NOT_ANSWERING
    )


def _item(slot: ActivitySlot, names: Mapping[str, str], activity: str) -> str:
    """«19:00 en Gimnasio Norte (4 plazas)», naming the activity only when the agent's differs."""
    places = {None: "", 0: " (completa)", 1: " (1 plaza)"}.get(
        slot.free_places, f" ({slot.free_places} plazas)"
    )
    same = " ".join(re.findall(r"\w+", folded(slot.activity))) == activity
    named = "" if same else f" {slot.activity}"
    return f"{slot.start:%H:%M}{named} en {names.get(slot.agent_id, slot.agent_id)}{places}"


def _weekday(day: date) -> str:
    # `answer_writer` names weekdays for «sobre todo los sábados», so the weekend two are plural.
    name = _WEEKDAYS[day.weekday()]
    return name.removesuffix("s") if name.endswith("os") else name


def _day(day: date) -> str:
    """«jueves 1 de octubre»: no year, the holder is asking about this week."""
    return f"{_weekday(day)} {day.day} de {_MONTHS[day.month - 1]}"


def _dated(day: date) -> str:
    """«jueves 1 de octubre de 2026»: the year too, for an agent that reads dates literally."""
    return f"{_weekday(day)} {day.day} de {month_name(day.year, day.month)}"
