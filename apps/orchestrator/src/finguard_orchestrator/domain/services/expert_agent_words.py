"""Whether a sentence asks an external expert agent, and which one (spec §14 A1).

A predicate, not an `Intent`: the controller consults it before the visual and financial paths, the
way it consults `_is_an_order_to_pay`, so no model interpreter can open this path and the intent
lexicon's ratchets do not move. Everything is read on the folded sentence (`spanish.folded`), so
accents and case are optional.

    subject     an activity of `ACTIVITIES`, or «clase(s)», «sesion(es)», «gimnasio». Never
                «horario» alone: «¿qué horario tiene el banco?» is about the bank.
    cue         «hay», «que clases», «horario», «busca…», «plaza(s)», or a day or a band that
                `activity_when` reads («el jueves», «mañana», «por la tarde», «este fin de semana»)
                -- except that when «gimnasio» is the only subject, a day or a band is not enough
                and one of the timetable words must be there: «¿y el gimnasio hoy?» is a follow-up
                to a question about spending, as often as not (ruling R14).
    never       any money or ledger cue (`_MONEY_CUES`): «¿cuánto gasto en el gimnasio?», «¿qué
                movimientos hay del gimnasio?» stay with the bank (Review Focus 1). Stems match at a
                word start; the verbs that move money only as whole words (ruling R4), so «pasado
                mañana» is a day and «pasa 20 a Laura» is not.
    never       a chart (`_VISUAL_CUES`): «muéstrame el gimnasio de este fin de semana en barras» is
                drawn from the ledger (ruling R14).

`_MONEY_CUES` is shared with `is_a_booking_request` (Task 5), for the same reason: a sentence that
mentions money is the bank's, whatever else it mentions.
"""

import re
from collections.abc import Sequence
from typing import Final

from finguard_orchestrator.domain.models.expert_agents import ExpertAgent

# The day and band vocabulary of the future-day reader, read rather than repeated: a cue here that
# the window reader did not also understand would claim a sentence it then could not date.
from finguard_orchestrator.domain.services.activity_when import (
    _BAND,
    _DATE,
    _NEXT_WEEK,
    _THIS_WEEK,
    _TODAY,
    _TOMORROW,
    _WEEKDAY,
    _WEEKEND,
)
from finguard_orchestrator.domain.services.spanish import folded

# Folded, as `spanish.folded` leaves them. What the search sends is this form, never the sentence.
ACTIVITIES: Final[tuple[str, ...]] = (
    "spinning",
    "yoga",
    "pilates",
    "padel",
    "natacion",
    "crossfit",
    "zumba",
    "boxeo",
    "body pump",
    "aquagym",
)

_ACTIVITY: Final = re.compile(
    r"\b(" + "|".join(r"\s+".join(map(re.escape, name.split())) for name in ACTIVITIES) + r")\b"
)
# A class or a session names a timetable on its own; the gym is also a merchant, so it needs more.
_CLASS: Final = re.compile(r"\b(?:clases?|sesion(?:es)?)\b")
_GYM: Final = re.compile(r"\bgimnasios?\b")
_CUE: Final = re.compile(r"\bhay\b|\bque\s+clases\b|\bhorarios?\b|\bbusc|\bplazas?\b")
_DAY_OR_BAND: Final = (_TODAY, _TOMORROW, _WEEKDAY, _DATE, _WEEKEND, _NEXT_WEEK, _THIS_WEEK, _BAND)

_MONEY_CUES: Final = re.compile(
    r"\b(?:cuanto|gast|pag|cuesta|cobr|traspas|bizum|transfer|recurrent|recibo|cuota|saldo)"
    # What the ledger holds, named: a charge, a movement, a purchase, a direct debit, an invoice.
    r"|\b(?:cargo|movimiento|compra|domicili|factura|importe|dinero)"
    r"|\beuros?\b|€|\d+ ?e\b"
    r"|\benvia(?:le|me)?\b|\bmanda(?:le|me)?\b|\bpasa\b|\bmueve\b"
)
# What a chart is asked with. No gym draws one; a sentence asking for one is about the ledger.
_VISUAL_CUES: Final = re.compile(
    r"\bbarras?\b|\bgrafic|\bcompar|\bmuestra(?:me)?\b|\bensena(?:me)?\b|\bdesglos|\btop\b"
    r"|\bponlo\b"
)

# What every gym's name is made of, so it names no gym on its own: «en el gimnasio» asks them all.
_GENERIC: Final = frozenset(
    {
        "gimnasio",
        "gimnasios",
        "gym",
        "club",
        "centro",
        "center",
        "sport",
        "sports",
        "fitness",
        "deportivo",
        "deportiva",
        "polideportivo",
        "studio",
        "estudio",
        "box",
        "the",
        "and",
        "los",
        "las",
        "del",
    }
    | {word for name in ACTIVITIES for word in name.split()}
)
# A capitalised word after one of these is a gym's name being given (`claims_an_agent_name`).
_CALLED: Final = re.compile(r"\b(?:gimnasio|gym|club|centro)\s+(?P<name>[^\W\d_][\w-]*)", re.I)
# Words a sentence may capitalise after a gym noun that are not a name: «el Gimnasio El jueves».
_NOT_A_NAME: Final = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "y",
        "en",
        "a",
        "al",
        "este",
        "esta",
        "ese",
        "esa",
        "para",
        "por",
        "con",
        "que",
        "hoy",
        "manana",
        "pasado",
        "lunes",
        "martes",
        "miercoles",
        "jueves",
        "viernes",
        "sabado",
        "domingo",
    }
)


def _text(sentence: str) -> str:
    return " ".join(folded(sentence).split())


def asks_an_expert_agent(sentence: str) -> bool:
    """A timetable question about an activity or the gym, and nothing about money (module doc)."""
    text = _text(sentence)
    if _MONEY_CUES.search(text) or _VISUAL_CUES.search(text):
        return False
    if _ACTIVITY.search(text) or _CLASS.search(text):
        return bool(_CUE.search(text) or any(pattern.search(text) for pattern in _DAY_OR_BAND))
    # The gym alone: only a word that asks for a timetable, never a bare day or band.
    return bool(_GYM.search(text) and _CUE.search(text))


def activity_in(sentence: str) -> str | None:
    """The first activity of `ACTIVITIES` the sentence names, as the lexicon writes it."""
    found = _ACTIVITY.search(_text(sentence))
    return " ".join(found.group(1).split()) if found else None


def named_agents(sentence: str, agents: Sequence[ExpertAgent]) -> tuple[str, ...]:
    """The ids of the agents the sentence names by a distinctive word of their card name.

    A word is distinctive when it is not generic (`_GENERIC`, which holds the activities too), is
    at least three letters long, and is not shared by every listed agent: with «Gimnasio Norte» and
    «Gimnasio Sur», «norte» names one and «gimnasio» names none.
    """
    said = set(re.findall(r"\w+", folded(sentence)))
    words = {agent.id: _name_words(agent.name) for agent in agents}
    shared = set.intersection(*words.values()) if len(words) > 1 else set()
    return tuple(agent.id for agent in agents if (words[agent.id] - shared) & said)


def claims_an_agent_name(sentence: str) -> bool:
    """Whether the sentence gives a gym's name: a capitalised word right after «gimnasio», «gym»,
    «club» or «centro» that is not a day or a function word. Read on the sentence as written, since
    capitals are the only sign of a name, and consulted only when `named_agents` found none -- then
    the name is one no registered agent has."""
    for match in _CALLED.finditer(sentence):
        name = match["name"]
        if name[0].isupper() and folded(name) not in _NOT_A_NAME:
            return True
    return False


def _name_words(name: str) -> set[str]:
    return {word for word in re.findall(r"\w+", folded(name)) if len(word) >= 3} - _GENERIC
