"""Whether a sentence is a question for an external agent, and which agent (spec §14 A1).

A predicate the controller consults before the visual and financial paths, the way it consults
`_is_an_order_to_pay`: never an `Intent`, so no model interpreter can open this path and nothing
about the financial readers moves. The line it draws is Review Focus 1: a timetable question about
the gym goes to the gyms, a money question about the gym stays with the bank.
"""

from pathlib import Path

import pytest
import yaml
from support import episodes

from finguard_orchestrator.domain.models.expert_agents import ExpertAgent
from finguard_orchestrator.domain.services import payment_router
from finguard_orchestrator.domain.services.expert_agent_words import (
    ACTIVITIES,
    activity_in,
    asks_an_expert_agent,
    claims_an_agent_name,
    is_a_booking_request,
    named_agents,
    named_in,
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


@pytest.mark.parametrize(
    "sentence",
    [
        # Ruling R14: a question about the ledger names what the ledger holds.
        "¿Hay algún cargo del gimnasio este mes?",
        "¿qué movimientos hay del gimnasio?",
        "¿hay domiciliaciones del gimnasio?",
        "¿hay facturas del gimnasio?",
        "¿hay algún importe del gimnasio hoy?",
        "¿hay compras en el gimnasio esta semana?",
        "¿Hay un cargo específico para el uso del gimnasio?",
        # A chart is drawn from the ledger, never from a gym.
        "gimnasio esta semana en barras",
        "muéstrame el gimnasio de este fin de semana",
        "compara el gimnasio de esta semana con la semana que viene",
        # A follow-up about the gym, read against the financial question before it.
        "¿y el gimnasio hoy?",
        "¿y mañana en el gimnasio?",
        # «horario» alone is not about a class.
        "¿Qué horario tiene el banco?",
        "gracias por el horario",
    ],
)
def test_the_ledger_a_chart_and_a_bare_horario_are_not_agent_requests(sentence: str) -> None:
    assert not asks_an_expert_agent(sentence)


def test_the_gym_alone_needs_a_timetable_word_not_just_a_day() -> None:
    """With no activity and no «clase»/«sesión», «gimnasio» is a merchant as often as a place:
    only a word that asks for a timetable makes it one."""
    assert not asks_an_expert_agent("el gimnasio el jueves por la tarde")
    assert asks_an_expert_agent("¿hay plazas en el gimnasio el jueves?")
    assert asks_an_expert_agent("horario del gimnasio mañana")
    assert asks_an_expert_agent("busca el gimnasio del sábado")
    assert asks_an_expert_agent("¿qué clases hay mañana?")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


EVALUATION = Path(__file__).resolve().parents[1] / "evaluation"
# The files under `evaluation/` written to reach an agent, by name, and nothing else: the corpus
# episodes that drive a search and a booking through both routes, and the agents' own replies the
# normaliser evaluation reads. Exact paths rather than a pattern, so a new file is swept until
# somebody names it here -- and `test_only_the_agent_episodes_are_exempt` holds the corpus entry to
# the episodes that declare agents.
MEANT_FOR_AGENTS = frozenset({"conversations/35-expert-agents.yaml", "a2a/cases.yaml"})


def _swept() -> list[Path]:
    """Every evaluation file R14 protects: all of them but the ones meant for agents."""
    return [
        path
        for path in sorted(EVALUATION.rglob("*.yaml"))
        if path.relative_to(EVALUATION).as_posix() not in MEANT_FOR_AGENTS
    ]


def _corpus_files() -> list[Path]:
    return sorted((EVALUATION / "conversations").glob("*.yaml"))


def test_only_the_agent_episodes_are_exempt() -> None:
    """An exempt corpus file holds only episodes that declare a gateway, and an episode that
    declares one lives in an exempt file: the exemption cannot hide a financial episode."""
    corpus = {path.stem: path.relative_to(EVALUATION).as_posix() for path in _corpus_files()}
    for episode in episodes.load():
        exempt = corpus[episode.name.split("/")[0]] in MEANT_FOR_AGENTS
        assert exempt == bool(episode.agents), episode.name
    assert all((EVALUATION / name).is_file() for name in MEANT_FOR_AGENTS)


def test_no_string_of_any_evaluation_file_is_claimed() -> None:
    """Every question, paraphrase, note and rejected case under `evaluation/`: nothing the planner
    and the visual evaluations measure may reach an agent (Ruling R14)."""
    files = _swept()
    assert len(files) > 30, "the sweep found the evaluation files"
    claimed = [
        (path.relative_to(EVALUATION).as_posix(), text)
        for path in files
        for text in _strings(yaml.safe_load(path.read_text(encoding="utf-8")))
        if asks_an_expert_agent(text)
    ]
    assert claimed == []


def test_no_existing_corpus_sentence_is_claimed() -> None:
    """The corpus must not move (Global Constraints): no sentence of any existing episode reaches
    an agent, as a question or as a booking -- not even if a search's offers were in the
    conversation (fix round 1), which claims a superset of what it claims without them. The
    episodes declaring a gateway are the ones written to reach one."""
    claimed = [
        (episode.name, turn.say)
        for episode in episodes.load()
        if not episode.agents
        for turn in episode.turns
        if asks_an_expert_agent(turn.say) or is_a_booking_request(turn.say, has_offers=True)
    ]
    assert claimed == []


@pytest.mark.parametrize("has_offers", [False, True], ids=["no-offers", "offers"])
def test_no_string_of_any_evaluation_file_is_a_booking(has_offers: bool) -> None:
    """The evaluation sweep above, for the booking predicate -- with offers in the conversation
    too (fix round 1): offers outlive unrelated turns, and a booking is checked before anything
    else, so a period or a search that merely begins «la primera…» must stay what it was."""
    claimed = [
        (path.relative_to(EVALUATION).as_posix(), text)
        for path in _swept()
        for text in _strings(yaml.safe_load(path.read_text(encoding="utf-8")))
        if is_a_booking_request(text, has_offers=has_offers)
    ]
    assert claimed == []


# --- a booking (Task 5, spec §5.2) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Apúntame a la de las 19:00 en el Norte",
        "apuntame a spinning",
        "Resérvame la primera",
        "reservame la de las 18:30",
        "me apunto a la de las 20:00",
        "quiero reservar la clase de yoga",
        "reserva la clase de las 19:00",
        "reserva una plaza en la de las 18:30",
        "reserva una sesión de pilates",
    ],
)
def test_a_booking_verb_is_a_booking_request_with_or_without_offers(sentence: str) -> None:
    """Said with a verb, a booking is one whether or not anything was offered: with nothing to
    book, the answer is to search first, not the bank."""
    assert is_a_booking_request(sentence, has_offers=False)
    assert is_a_booking_request(sentence, has_offers=True)


@pytest.mark.parametrize(
    "sentence",
    [
        "la de las 19:00",
        "la de las 7",
        "quiero la de las 18:30",
        "quiero ir a la de Norte",
        "la primera",
        "¿y la segunda?",
        "la tercera",
        "la última del Norte",
    ],
)
def test_a_bare_pointer_is_a_booking_request_only_when_there_is_something_to_point_at(
    sentence: str,
) -> None:
    assert is_a_booking_request(sentence, has_offers=True)
    assert not is_a_booking_request(sentence, has_offers=False)


@pytest.mark.parametrize(
    "sentence",
    [
        # An ordinal before a period noun is a period, whatever else was offered (fix round 1).
        "Ahora en la segunda quincena de julio",
        "¿y la última semana?",
        "¿y la primera quincena de febrero?",
        "la primera semana de agosto",
        "la última del mes pasado",
        "la segunda de este año",
        # A day the future-day reader reads, or a financial period, is a question about when.
        "la de las 19:00 del jueves",
        "la primera de mañana por la tarde",
        "la de las 18:30 de hoy",
        # A search cue is a search.
        "quiero la de clases de yoga",
        "¿qué clases hay en la primera?",
    ],
)
def test_a_pointer_that_names_a_period_or_a_search_is_not_a_booking(sentence: str) -> None:
    assert not is_a_booking_request(sentence, has_offers=True)


def test_a_booking_verb_still_books_whatever_period_it_names() -> None:
    """The period rule is for pointers alone: with a verb, «del jueves» picks the class."""
    assert is_a_booking_request("apúntame a la de las 19:00 del jueves", has_offers=True)
    assert is_a_booking_request("resérvame la primera", has_offers=True)
    # A clock phrase with its part of the day is a time, not a band.
    assert is_a_booking_request("la de las 7 de la tarde", has_offers=True)


@pytest.mark.parametrize(
    "sentence",
    [
        "pasa 100 a la reserva",
        "¿cuánto tengo en la reserva?",
        "apúntame 20 euros a la reserva",
        "apúntame el gasto de la de las 19:00",
        "resérvame 50 € para el gimnasio",
        "quiero reservar dinero para la clase",
        "¿cuánto cuesta la primera?",
        # A chart's bars are pointed at with the same words, and a chart is the ledger's.
        "la primera barra",
        "muéstrame la última",
    ],
)
def test_money_or_a_chart_keeps_a_pointer_away_from_the_gyms(sentence: str) -> None:
    """The shared cues (`_MONEY_CUES`, `_VISUAL_CUES`) veto a booking the way they veto a search.
    That includes «compra», so «¿puedo comprar esa clase?» is the bank's: a booking moves no money
    in this design (D1), and a sentence that says it does is not one."""
    assert not is_a_booking_request(sentence, has_offers=True)
    assert not is_a_booking_request("¿puedo comprar la de las 19:00?", has_offers=True)


@pytest.mark.parametrize(
    "sentence",
    [
        "reserva",
        "la reserva de emergencia",
        "¿cómo va mi reserva?",
        "primera hora",
        "la de siempre",
        "hola",
    ],
)
def test_the_word_reserva_and_other_near_misses_are_not_a_booking(sentence: str) -> None:
    assert not is_a_booking_request(sentence, has_offers=True)


def test_an_agent_is_named_by_its_shown_name_as_well_as_by_its_card() -> None:
    """The offers keep the name the holder was shown, not the card: the same rule reads both."""
    names = (("gym-norte", "Gimnasio Norte"), ("gym-sur", "Gimnasio Sur"))
    assert named_in("la de las 19:00 en el Norte", names) == ("gym-norte",)
    assert named_in("la de las 19:00 en el gimnasio", names) == ()
    agents = (agent("gym-norte", "Gimnasio Norte"), agent("gym-sur", "Gimnasio Sur"))
    assert named_agents("la del Sur", agents) == named_in("la del Sur", names) == ("gym-sur",)


@pytest.mark.parametrize(
    "sentence",
    [
        "ingrésale 20 a Marta por el yoga de mañana",
        "devuélvele 12 a Diego por el pádel del sábado",
        "ingresa 30 a Laura por las clases de natación de esta semana",
    ],
)
def test_an_order_to_pay_is_the_payments_whatever_class_it_names(sentence: str) -> None:
    """Ruling R24: the expert branch runs before the order to pay, so the payment path's own
    reading of «a payment» vetoes both predicates -- one definition, not a second cue list."""
    assert payment_router.is_payment(sentence)
    assert not asks_an_expert_agent(sentence)
    assert not is_a_booking_request(sentence, has_offers=False)
    assert not is_a_booking_request(sentence, has_offers=True)
