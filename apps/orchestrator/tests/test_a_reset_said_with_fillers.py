"""A reset said the way people say it, with the words they put around it.

Validation fix 1, F5. On 2026-10-08 none of these dropped anything: «bah olvidalo» ran the last
query again (suscripciones-olvidalo 4), and «que no, dejalo» (suscripciones-olvidalo 5), «dejalo,
da igual» (web-suscripciones 3) and «que no, olvídalo» (web-suscripciones 4) got «Eso no puedo
calcularlo todavía.». The reset lexicon took courtesy and a leading «no» beside its words, and
nothing else, so any filler made the turn a request that happened to contain one.

A whole turn made of reset words and fillers — «bah», «que no», «no», «venga», «pues», «da igual»,
«nada», «gracias», commas — is a reset now. What must not change is pinned beside it: a «?» is
never one (R16), «da igual» alone answers «¿En qué cuenta?» as any account (R51), and a reset word
followed by a request is the request.

Driven over HTTP on the episodes stack.
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.services.turn_arbiter import is_a_reset

RESET_TEXT = "Vale, lo dejamos. ¿Qué quieres consultar?"

SAID_WITH_FILLERS = (
    "bah olvidalo",
    "que no, dejalo",
    "dejalo, da igual",
    "que no, olvídalo",
    "venga, olvídalo",
    "pues nada, déjalo",
    "no, nada, gracias",
    "bah, da igual, olvídalo",
)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("sentence", SAID_WITH_FILLERS)
def test_a_reset_with_fillers_after_an_answer_drops_it(sentence: str) -> None:
    """suscripciones-olvidalo 4 and 5, web-suscripciones 3 and 4."""
    stack = episodes.a_stack()
    first = ask(stack, "cuanto pago en suscripciones este mes", SETTLED)
    stack.planner.asked.clear()

    forgotten = ask(stack, sentence, first["conversation"])

    assert forgotten["text"] == RESET_TEXT, forgotten["text"]
    assert len(stack.ledger.ran) == 1, "no second query"
    assert stack.planner.asked == []
    assert forgotten["conversation"]["last_plan"] is None


@pytest.mark.parametrize("sentence", SAID_WITH_FILLERS)
def test_a_reset_with_fillers_drops_the_question_left_open(sentence: str) -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["conversation"]["awaiting"] == "WINDOW"

    forgotten = ask(stack, sentence, asked["conversation"])

    assert forgotten["text"] == RESET_TEXT, forgotten["text"]
    assert forgotten["conversation"]["last_asked"] is None
    assert forgotten["conversation"].get("workflow") in (None, "IDLE")


@pytest.mark.parametrize(
    "sentence",
    [
        # R51: alone, it answers «¿En qué cuenta…?» as any account.
        "da igual",
        # Fillers alone say nothing to drop.
        "bah",
        "que no",
        "venga",
        "pues",
        "pues da igual",
        # R16: a question is never a reset.
        "¿que no, déjalo?",
        "bah, ¿olvídalo?",
        # A reset word followed by a request is the request.
        "no, déjalo en restaurantes",
        "olvídate del alquiler y dime el resto",
        "bah, olvídalo y dime lo de gasolina",
    ],
)
def test_what_is_not_only_reset_words_and_fillers_is_no_reset(sentence: str) -> None:
    assert not is_a_reset(sentence)


@pytest.mark.parametrize("sentence", SAID_WITH_FILLERS)
def test_the_shapes_are_resets_to_the_lexicon_itself(sentence: str) -> None:
    assert is_a_reset(sentence)


# --- round 2, M-2: what a filler reset does to money on its way ---------------------------------


@pytest.mark.parametrize("sentence", SAID_WITH_FILLERS)
def test_a_reset_with_fillers_over_a_preview_asks_the_confirmation_again(sentence: str) -> None:
    """A preview waits for a yes or a no, and its gates answer first: the payment is asked about
    again, nothing new is prepared, and nothing is confirmed or cancelled at the bank."""
    stack = episodes.a_stack()
    preview = ask(stack, "mándale 20 euros a Marta Solis Vega", SETTLED)
    assert preview["outcome"] == "AWAITING_CONFIRMATION", preview["text"]

    answer = ask(stack, sentence, preview["conversation"])

    assert answer["outcome"] == "AWAITING_CONFIRMATION", answer["text"]
    assert answer["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    assert len(stack.bank.prepared) == 1, "only the preview itself"
    assert stack.bank.confirmed == [] and stack.bank.cancelled == []


@pytest.mark.parametrize("sentence", SAID_WITH_FILLERS)
def test_a_reset_with_fillers_drops_a_half_built_bizum(sentence: str) -> None:
    """Nothing is staged until the amount is known, so the reset drops it and says so."""
    stack = episodes.a_stack()
    asked = ask(stack, "hazle un bizum a Marta Solis Vega", SETTLED)
    assert asked["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS", asked["text"]

    answer = ask(stack, sentence, asked["conversation"])

    assert answer["text"] == RESET_TEXT, answer["text"]
    assert answer["conversation"]["workflow"] is None
    assert stack.bank.prepared == []
