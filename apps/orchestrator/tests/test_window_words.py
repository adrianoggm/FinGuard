"""Reading a window out of a sentence, without a model.

The clarification the planner asks for is almost always the window — "¿de qué periodo?" — and
a window is a closed vocabulary exactly like a category. So when somebody answers "este año",
there is nothing to infer and nothing to ask a model about.

This is what makes a clarification finishable. Before it, answering the question restarted the
whole conversation: the reply "este año" arrived as a two-word sentence with no context, and
the planner had to derive a complete plan from it.

Precision over coverage, and the exclusions are deliberate. "Últimamente" and "hace un mes"
are windows a person understands and this does not, so they fall through to the model rather
than being guessed at — the cost of a miss is one more turn, and the cost of a wrong guess is
a figure about the wrong month.
"""

import pytest

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.services import window_words


@pytest.mark.parametrize(
    ("sentence", "period", "count"),
    [
        ("hoy", Period.TODAY, None),
        ("ayer", Period.YESTERDAY, None),
        ("esta semana", Period.THIS_WEEK, None),
        ("la semana pasada", Period.LAST_WEEK, None),
        ("este mes", Period.THIS_MONTH, None),
        ("el mes pasado", Period.LAST_MONTH, None),
        ("este año", Period.THIS_YEAR, None),
        ("en lo que va de año", Period.THIS_YEAR, None),
        ("los últimos 30 días", Period.LAST_N_DAYS, 30),
        ("últimos 7 dias", Period.LAST_N_DAYS, 7),
        ("los últimos 3 meses", Period.LAST_N_MONTHS, 3),
        ("en los últimos 6 meses", Period.LAST_N_MONTHS, 6),
        # Inside a longer sentence, because a reply to "¿de qué periodo?" is often a small sentence
        # rather than a bare phrase.
        ("pues el mes pasado", Period.LAST_MONTH, None),
        ("me refería a este año", Period.THIS_YEAR, None),
    ],
)
def test_a_window_this_vocabulary_knows_is_read(
    sentence: str, period: Period, count: int | None
) -> None:
    window = window_words.window_in(sentence)

    assert window is not None
    assert window.period is period
    assert window.n == count


@pytest.mark.parametrize(
    "sentence",
    [
        "",
        "no sé",
        "de gasolina",
        # Windows a person understands and this does not. They fall through to the model rather than
        # being guessed: a miss costs one turn, a wrong guess costs a figure about the wrong month.
        "últimamente",
        "hace un mes",
        "desde que empecé a trabajar",
        "en verano",
    ],
)
def test_anything_else_is_not_a_window(sentence: str) -> None:
    assert window_words.window_in(sentence) is None


def test_last_year_is_not_a_window_because_the_contract_has_none() -> None:
    """A harder exclusion than the ambiguous ones: "el año pasado" is perfectly clear, and banking's
    period vocabulary simply has no LAST_YEAR to translate it into. Reading it as THIS_YEAR would
    produce a figure about a different year, which is what this whole file exists to avoid."""
    assert window_words.window_in("el año pasado") is None


def test_the_longest_reading_wins() -> None:
    """Both "el mes pasado" and "este mes" contain the word "mes", so whichever of the two
    phrases is longer is the one the person actually said."""
    assert window_words.window_in("el mes pasado").period is Period.LAST_MONTH
    assert window_words.window_in("este mes").period is Period.THIS_MONTH


def test_a_count_out_of_range_is_not_a_window() -> None:
    """The domain bounds `n`, so a sentence asking for 5000 days is not a window this can build.
    Returning None sends it to the model, which is better than raising from a reader."""
    assert window_words.window_in("los últimos 5000 días") is None


def test_the_singular_forms_are_read_too() -> None:
    """Somebody answering "el último mes" means one month, and "en el último día" means one day.
    Both are counted periods with n=1 rather than a separate name."""
    assert window_words.window_in("el último mes") == window_words.window_in("los últimos 1 meses")
