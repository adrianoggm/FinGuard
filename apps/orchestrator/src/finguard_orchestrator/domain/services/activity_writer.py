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
    BookingReceipt,
    RejectionRule,
)
from finguard_orchestrator.domain.services.answer_writer import (
    _MONTHS,
    _WEEKDAYS,
    listed,
    month_name,
)
from finguard_orchestrator.domain.services.expert_agent_words import ACTIVITIES
from finguard_orchestrator.domain.services.payment_writer import paused
from finguard_orchestrator.domain.services.spanish import folded
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft

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

    Over several days, the exact interval the data part's `from` and `to` carry (ruling R23): «Busco
    clases de yoga desde el jueves 1 de octubre de 2026 a las 10:00 hasta el domingo 4 de octubre
    de 2026 a las 23:00.», then «, de 15:00 a 21:00» before the stop only when a band was asked for
    -- the same hours the data part's `band` carries (ruling R15).
    """
    first, last = query.start.date(), query.end.date()
    activity = shown(query.activity)
    if first == last:
        return (
            f"Busco clases de {activity} el {_dated(first)} "
            f"entre las {query.start:%H:%M} y las {query.end:%H:%M}."
        )
    hours = f", de {query.band[0]:%H:%M} a {query.band[1]:%H:%M}" if query.band is not None else ""
    return (
        f"Busco clases de {activity} desde el {_dated(first)} a las {query.start:%H:%M} "
        f"hasta el {_dated(last)} a las {query.end:%H:%M}{hours}."
    )


def booking_request(slot: ActivitySlot, member_ref: str) -> str:
    """«Quiero reservar la clase de Spinning del jueves 1 de octubre de 2026 a las 19:00.
    Referencia: N-1001-1900. Socio: <memberRef>.» -- the reference only when the agent gave one.

    Filled from the verified slot alone, in the slot's own day and time: the activity as the agent
    wrote it, the reference it gave, and the pairwise pseudonym (§5.4). Never the holder's sentence.
    """
    reference = f" Referencia: {slot.slot_ref}." if slot.slot_ref is not None else ""
    return (
        f"Quiero reservar la clase de {slot.activity} del {_dated(slot.start.date())} "
        f"a las {slot.start:%H:%M}.{reference} Socio: {member_ref}."
    )


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


# --- the booking (spec §5.2, §7) -------------------------------------------------------------
#
# A slot's activity and a gym's name are the agent's words, verified and bounded; a reference or a
# booking code is one ASCII token and is written as plain text, never as an address to follow.


def booking_preview(slot: ActivitySlot, agent_name: str) -> str:
    """«Vas a reservar Spinning, jueves 1 de octubre, 19:00–19:45, en Gimnasio Norte.
    ¿Confirmas?» -- the hours as the class's, its end only when the agent gave one."""
    return (
        f"Vas a reservar {slot.activity}, {_day(slot.start.date())}, {_hours(slot)}, "
        f"en {agent_name}. ¿Confirmas?"
    )


def booking_unclear(slot: ActivitySlot, agent_name: str) -> str:
    """The preview again, and what it is waiting for: a payment's unclear reply, for a class."""
    return f"{booking_preview(slot, agent_name)} Responde sí o no."


def which_booking(candidates: Sequence[ActivitySlot], names: Mapping[str, str]) -> str:
    """«¿Cuál de estas: 19:00 en Gimnasio Norte o 19:00 en Gimnasio Sur?» -- each class told apart
    by what differs: the day when they are on several, the activity when there are several."""
    days = len({slot.start.date() for slot in candidates}) > 1
    activities = len({_folded_activity(slot) for slot in candidates}) > 1
    choices = tuple(_choice(slot, names, days=days, activities=activities) for slot in candidates)
    return f"¿Cuál de estas: {_either(choices)}?"


def booking_suggestions(candidates: Sequence[ActivitySlot], names: Mapping[str, str]) -> list[str]:
    """One chip per class of an ambiguity, each naming exactly what tells it apart -- so sending
    one back resolves to that class and no other (`slot_reference`)."""
    days = len({slot.start.date() for slot in candidates}) > 1
    activities = len({_folded_activity(slot) for slot in candidates}) > 1
    agents = len({slot.agent_id for slot in candidates}) > 1
    chips = []
    for slot in candidates:
        chip = f"Apúntame a la de las {slot.start:%H:%M}"
        if days:
            chip += f" del {_weekday(slot.start.date())}"
        if activities:
            chip += f" de {slot.activity}"
        if agents:
            chip += f" en {names.get(slot.agent_id, slot.agent_id)}"
        chips.append(chip)
    return chips


def not_offered() -> str:
    return "Esa clase no está entre las que te he ofrecido."


def started() -> str:
    return "Esa clase ya ha empezado."


def search_again() -> str:
    """The offers are absent, or their seal did not check out (§5.3): never read, never repaired."""
    return "No puedo usar esa búsqueda; vuelve a pedirla."


def booking_after_payment(draft: PaymentDraft) -> str:
    """A preview would replace a payment under way (ruling R18): name it, and say how to finish
    or drop it, in the payment conversation's own words."""
    return f"Antes de reservar, termina o descarta el pago que tienes a medias. {paused(draft)}"


def on_screen() -> str:
    """A yes by voice (§14 A10): a booking is confirmed where it can be seen."""
    return "Confirma la reserva en la pantalla."


def booking_cancelled() -> str:
    return "De acuerdo, no reservo nada."


def booking_forbidden() -> str:
    return "Tu acceso no permite reservar con agentes externos."


def booked(receipt: BookingReceipt, agent_name: str) -> str:
    """«Reserva hecha en Gimnasio Sur: Spinning, jueves 1 de octubre, 18:30. Código SUR-7731.»"""
    slot = receipt.slot
    return (
        f"Reserva hecha en {agent_name}: {slot.activity}, {_day(slot.start.date())}, "
        f"{_hours(slot)}. Código {receipt.booking_code}."
    )


def booking_alternatives(
    slot: ActivitySlot, agent_name: str, alternatives: Sequence[ActivitySlot]
) -> str:
    """«La de las 19:00 no está disponible; Gimnasio Norte ofrece las 21:00 y el viernes 2 a las
    18:30.» -- the day named for any alternative not on the booked class's (ruling R22). Not
    «completa»: an agent pausing the booking with other sessions has not said why, and a full
    class is only the likeliest reason."""
    times = tuple(
        f"las {each.start:%H:%M}"
        if each.start.date() == slot.start.date()
        else f"el {_weekday(each.start.date())} {each.start.day} a las {each.start:%H:%M}"
        for each in alternatives
    )
    return f"La de las {slot.start:%H:%M} no está disponible; {agent_name} ofrece {listed(times)}."


def booking_unavailable(agent_name: str) -> str:
    """A paused booking with no alternative that verifies (ruling R22): the gym asked for nothing,
    the class is just not available as asked."""
    return f"Esa clase no está disponible en {agent_name}; vuelve a buscar."


def booking_link_required(agent_name: str) -> str:
    """The booking is not retried (§7): once linked, the holder asks for it again."""
    return (
        f"{agent_name} necesita que vincules tu cuenta de socio; cuando lo hayas hecho, "
        "vuelve a pedir la reserva."
    )


def booking_unsupported(agent_name: str) -> str:
    return f"{agent_name} pide un dato que FinGuard no proporciona; la reserva no se ha hecho."


def booking_failed(agent_name: str) -> str:
    """The gym's own no (`FAILED`, `REJECTED`, `CANCELED`). A failure before anything was sent is
    `booking_not_sent`, and a completed task FinGuard could not verify `booking_unknown` (R17)."""
    return f"{agent_name} no ha confirmado la reserva."


def booking_not_sent(agent_name: str) -> str:
    """A failure before anything reached the gym (ruling R30): the listing, the card, the
    gateway's own refusal. Said as what it is, so the holder knows asking again is safe."""
    return f"No he podido contactar con {agent_name}; no se ha enviado nada."


def booking_unknown(agent_name: str) -> str:
    """A reply lost once the booking was sent (§5.2): it may have happened, so it is never sent
    again and the holder is sent to the one party who knows."""
    return f"No sé si la reserva se ha completado; consulta en {agent_name}."


def _hours(slot: ActivitySlot) -> str:
    """«19:00–19:45», or «18:30» when the agent gave no end."""
    if slot.end is None:
        return f"{slot.start:%H:%M}"
    return f"{slot.start:%H:%M}–{slot.end:%H:%M}"


def _folded_activity(slot: ActivitySlot) -> str:
    return " ".join(re.findall(r"\w+", folded(slot.activity)))


def _choice(slot: ActivitySlot, names: Mapping[str, str], *, days: bool, activities: bool) -> str:
    """«19:00 en Gimnasio Norte», «19:00 Yoga en Gimnasio Sur», «el sábado 3 de octubre a las
    18:30 en Gimnasio Norte»."""
    at = f"{slot.start:%H:%M}"
    named = f" {slot.activity}" if activities else ""
    gym = names.get(slot.agent_id, slot.agent_id)
    if days:
        return f"el {_day(slot.start.date())} a las {at}{named} en {gym}"
    return f"{at}{named} en {gym}"


def _either(choices: Sequence[str]) -> str:
    """«A o B», «A, B o C»: a question between classes is a choice of one."""
    if len(choices) == 1:
        return choices[0]
    return f"{', '.join(choices[:-1])} o {choices[-1]}"


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
