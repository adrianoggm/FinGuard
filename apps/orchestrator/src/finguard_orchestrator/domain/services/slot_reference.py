"""«La de las 19:00 en el Norte»: which offered class a booking sentence means (spec §5.2).

Resolved against the sealed offers the way `visual_reference` resolves a bar, and only against
them: what the holder was shown is the whole vocabulary, so a class that was never offered cannot be
named into existence. Every clue the sentence gives narrows the offers, in this order, on the
folded sentence:

    gym         a distinctive word of a name the offers carry («en el Norte», `named_in`); a
                capitalised name after «gimnasio» that none of them has is a gym never offered
    activity    one of the lexicon's (`activity_in`), matched as the verifier matches it
    day         a weekday («del sábado»), for offers that hold one time on two days
    time        every clock time stated (`activity_when.clock_times_in`)
    ordinal     «la primera / segunda / tercera / última», of what is left

One class left is chosen. Several with no ordinal to pick one are asked about -- never picked
(Review Focus 2) -- and none is a class that was not offered. Nothing here reads the clock: whether
a chosen class has already started is the caller's to say.
"""

import re
from dataclasses import dataclass
from typing import Final

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import ActivityOffers
from finguard_orchestrator.domain.models.expert_agents import ActivitySlot
from finguard_orchestrator.domain.services.activity_when import (
    _WEEKDAY,
    _WEEKDAYS,
    clock_times_in,
)
from finguard_orchestrator.domain.services.agent_reply_verifier import same_activity
from finguard_orchestrator.domain.services.expert_agent_words import (
    activity_in,
    claims_an_agent_name,
    named_in,
)
from finguard_orchestrator.domain.services.spanish import folded

# With its article: «a primera hora» is a time of day, not the first class offered.
_ORDINAL: Final = re.compile(r"\bla\s+(?P<which>primera|segunda|tercera|ultima)\b")
_POSITION: Final = {"primera": 0, "segunda": 1, "tercera": 2, "ultima": -1}


@dataclass(frozen=True)
class SlotChosen:
    slot: ActivitySlot


@dataclass(frozen=True)
class SlotAmbiguous:
    candidates: tuple[ActivitySlot, ...]  # at least two, in the offers' order

    def __post_init__(self) -> None:
        if len(self.candidates) < 2:
            raise InvalidValueError("an ambiguity is between at least two classes")


@dataclass(frozen=True)
class SlotNotOffered:
    pass


def resolve_slot(
    sentence: str, offers: ActivityOffers
) -> SlotChosen | SlotAmbiguous | SlotNotOffered:
    """The offered class the sentence points at, the ones it could mean, or none (module doc)."""
    text = " ".join(folded(sentence).split())
    candidates = list(offers.slots)

    named = named_in(sentence, offers.agent_names)
    if named:
        candidates = [slot for slot in candidates if slot.agent_id in named]
    elif claims_an_agent_name(sentence):
        return SlotNotOffered()
    activity = activity_in(sentence)
    if activity is not None:
        candidates = [slot for slot in candidates if same_activity(activity, slot.activity)]
    weekdays = {_WEEKDAYS.index(match["day"]) for match in _WEEKDAY.finditer(text)}
    if weekdays:
        candidates = [slot for slot in candidates if slot.start.weekday() in weekdays]
    times = set(clock_times_in(sentence))
    if times:
        candidates = [slot for slot in candidates if slot.start.time() in times]

    ordinal = _ORDINAL.search(text)
    if ordinal is not None:
        position = _POSITION[ordinal["which"]]
        if not -len(candidates) <= position < len(candidates):
            return SlotNotOffered()
        return SlotChosen(candidates[position])
    if not candidates:
        return SlotNotOffered()
    if len(candidates) == 1:
        return SlotChosen(candidates[0])
    return SlotAmbiguous(tuple(candidates))
