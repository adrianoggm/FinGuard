"""Whether a sentence is a question for an external agent, and which agent (spec §14 A1).

A predicate the controller consults before the visual and financial paths, the way it consults
`_is_an_order_to_pay`: never an `Intent`, so no model interpreter can open this path and nothing
about the financial readers moves. The line it draws is Review Focus 1: a timetable question about
the gym goes to the gyms, a money question about the gym stays with the bank.
"""

import pytest
from support import episodes

from finguard_orchestrator.domain.models.expert_agents import ExpertAgent
from finguard_orchestrator.domain.services.expert_agent_words import (
    ACTIVITIES,
    activity_in,
    asks_an_expert_agent,
    claims_an_agent_name,
    named_agents,
)


def agent(agent_id: str, name: str) -> ExpertAgent:
    return ExpertAgent(agent_id, name, True, None, f"sha256:{agent_id}", (), ())


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Qué clases de spinning hay el jueves por la tarde?",
        "¿hay yoga mañana?",
        "¿hay yoga pasado mañana?",
        "horario de pilates del viernes",
        "¿qué clases hay en el gimnasio este fin de semana?",
        "busca clases de pádel el sábado en el Norte",
    ],
)
def test_an_activity_timetable_question_is_an_agent_request(sentence: str) -> None:
    assert asks_an_expert_agent(sentence)


@pytest.mark.parametrize(
    "sentence",
    [  # Review Focus 1
        "¿cuánto gasto en el gimnasio?",
        "¿cuánto pagué al gimnasio el mes pasado?",
        "¿cuánto me cuesta el gimnasio al mes?",
        "gastos de gimnasio este año",
        "¿qué pagos recurrentes tengo?",
        "¿cuánto gasté en clases de inglés?",
        "envía 25 euros a Laura para la clase de yoga",
        "ponlo en barras",
    ],
)
def test_money_about_the_gym_is_not_an_agent_request(sentence: str) -> None:
    assert not asks_an_expert_agent(sentence)


def test_the_activity_is_folded_and_the_agent_named_by_a_distinctive_word() -> None:
    assert activity_in("¿Hay SPÍNNING el jueves?") == "spinning"
    agents = (agent("gym-norte", "Gimnasio Norte"), agent("gym-sur", "Gimnasio Sur"))
    assert named_agents("busca en el Norte", agents) == ("gym-norte",)
    assert named_agents("en el gimnasio", agents) == ()  # «gimnasio» names no one


def test_the_lexicon_is_folded_and_read_whole() -> None:
    """Every entry is already in the form `spanish.folded` produces, a two-word activity is read
    across any spacing, and an activity is a whole word: «yogaterapia» is not yoga."""
    assert ACTIVITIES == (
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
    assert activity_in("¿hay natación el lunes?") == "natacion"
    assert activity_in("clases de Body   Pump mañana") == "body pump"
    assert activity_in("¿hay yogaterapia mañana?") is None
    assert activity_in("¿qué clases hay mañana?") is None


@pytest.mark.parametrize(
    "sentence",
    [
        # A stem at a word start and the verbs only as whole words (ruling R4): «pasado mañana» is a
        # day, «pasa» is an order to move money.
        "pasa 20 a Laura para el yoga del jueves",
        "mándale el horario de pilates y 10 euros",
        "¿hay algún recibo del gimnasio este mes?",
        "¿qué cuota de yoga hay?",
        "¿hay un bizum de la clase de spinning?",
        "¿hay 5 € de la clase de zumba?",
        "¿hay 12e de la sesión de pilates?",
        "¿tengo saldo para la clase de yoga del lunes?",
    ],
)
def test_a_money_cue_anywhere_keeps_the_sentence_financial(sentence: str) -> None:
    assert not asks_an_expert_agent(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "yoga",  # an activity and nothing that asks about a timetable
        "me gusta el pilates",
        "el gimnasio",
    ],
)
def test_an_activity_without_a_timetable_cue_is_not_an_agent_request(sentence: str) -> None:
    assert not asks_an_expert_agent(sentence)


def test_a_distinctive_word_is_neither_generic_nor_shared_by_every_agent() -> None:
    """The words every listed agent shares name no one alone, and neither does an activity or the
    vocabulary every gym's name is made of: «Yoga Studio Norte» is not named by a question about
    yoga."""
    agents = (agent("studio-norte", "Yoga Studio Norte"), agent("studio-sur", "Yoga Studio Sur"))
    assert named_agents("¿hay yoga mañana en el studio?", agents) == ()
    assert named_agents("¿hay yoga mañana en el sur?", agents) == ("studio-sur",)
    lonely = (agent("gym-norte", "Gimnasio Norte"),)
    assert named_agents("horario del gimnasio", lonely) == ()
    assert named_agents("horario del gimnasio norte", lonely) == ("gym-norte",)


def test_a_capitalised_name_after_a_gym_word_is_a_claim_to_name_one() -> None:
    """How a sentence names a gym nobody registered: «el Gimnasio Oeste». Only a capitalised word
    right after the gym noun counts, so «el gimnasio este fin de semana» claims nothing."""
    assert claims_an_agent_name("¿hay spinning el jueves en el Gimnasio Oeste?")
    assert claims_an_agent_name("horario del club Atlántico")
    assert not claims_an_agent_name("¿qué clases hay en el gimnasio este fin de semana?")
    assert not claims_an_agent_name("¿Qué clases hay en el Gimnasio El jueves?")
    assert not claims_an_agent_name("¿hay yoga mañana?")


def test_no_existing_corpus_sentence_is_claimed() -> None:
    """The corpus must not move (Global Constraints): no sentence of any existing episode reaches
    an agent. Task 5 adds `is_a_booking_request(s, has_offers=False)` to the same sweep."""
    claimed = [
        (episode.name, turn.say)
        for episode in episodes.load()
        for turn in episode.turns
        if asks_an_expert_agent(turn.say)
    ]
    assert claimed == []
