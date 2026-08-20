"""A rule written for one member of a class, and not for the class.

Four defects of one batch were the same mould, and every one of them was found by a person reading:

    the year read "anterior" and the month did not
    the year was read without its article and the month only with one
    the verb of the balance carried three misspellings by hand and the question word carried none
    the account table carried "la nomnia" as an entry and had no rule behind it

None of those is a hard bug. Each is a vocabulary where somebody wrote a rule for the case in front
of them, the sister cases kept working by coincidence, and the gap sat there until a real
conversation hit it. The fifth one would have been found by a person reading too.

**So this file asks the question instead of waiting.** For a class whose members are interchangeable
— the calendar units, the words for a balance — it builds the same sentence for every member and
fails when one of them knows a form its sisters do not. What it reports is not "this phrase is
broken"; it is "six units read this and the seventh does not", which is the shape of every one of
the four above.

**Exceptions are declared, with a reason.** "Este fin de semana" is not a window on a Wednesday and
that is deliberate — this service reads what has happened, and the weekend has not. A parity rule
with no way to say that would force a wrong fix, so an exception is a line here with the reason
beside it rather than a hole in the matrix nobody explains.
"""

from datetime import date

import pytest

from finguard_orchestrator.domain.services import account_words, intent_router, window_words
from finguard_orchestrator.domain.services.intent_router import Intent

TODAY = date(2026, 8, 5)  # a Wednesday, which one of the exceptions below turns on

# The calendar units this service can express, with the article and the adjective ending Spanish
# gives each. Members of one class: whatever can be said about a month can be said about a quarter.
UNITS: dict[str, tuple[str, str, str]] = {
    #  unit            article  "this"  adjective
    "mes": ("el", "este", "pasado"),
    "trimestre": ("el", "este", "pasado"),
    "semestre": ("el", "este", "pasado"),
    "ano": ("el", "este", "pasado"),
    "fin de semana": ("el", "este", "pasado"),
    "semana": ("la", "esta", "pasada"),
    "quincena": ("la", "esta", "pasada"),
}

# The ways a person says each of them. `{art}` `{this}` `{adj}` are filled per unit.
FORMS: dict[str, str] = {
    "the one before, as everybody writes it": "{art} {unit} {adj}",
    "the one before, said the other way": "{art} {unit} anterior",
    "with the preposition glued to the article": "de{lla} {unit} {adj}",
    "inside a sentence": "cuanto gaste en {art} {unit} {adj}",
    "the current one": "{this} {unit}",
}

# Forms a unit is allowed not to know, and why. Anything not listed here has to hold for every unit.
EXCUSED: dict[tuple[str, str], str] = {
    ("fin de semana", "the current one"): (
        "this service reads what has happened, and on a Wednesday the weekend has not"
    ),
}


def _said(unit: str, form: str) -> str:
    article, this, adjective = UNITS[unit]
    return FORMS[form].format(
        art=article,
        this=this,
        adj=adjective,
        unit=unit,
        lla="l" if article == "el" else " la",
    )


@pytest.mark.parametrize("form", list(FORMS))
def test_every_calendar_unit_knows_the_same_forms(form: str) -> None:
    """One form at a time, across every unit — because the failure is always a minority of one.

    "El mes anterior" was the whole of it: six units read the word "anterior" and the month did not,
    so a question that named its period outright went to a model to be asked which period it meant.
    Nothing in the suite could see that, because every test about the month passed and every test
    about the quarter passed.
    """
    read = {unit: window_words.window_in(_said(unit, form), today=TODAY) for unit in UNITS}
    missing = sorted(unit for unit, window in read.items() if window is None)
    unexcused = [unit for unit in missing if (unit, form) not in EXCUSED]

    assert not unexcused, (
        f"{len(read) - len(missing)} of {len(read)} units read {form!r} and these do not: "
        + ", ".join(f"{unit!r} → {_said(unit, form)!r}" for unit in unexcused)
    )


def test_an_excused_unit_is_still_excused_for_the_reason_given() -> None:
    """An exception nobody rechecks becomes a hole. This pins the one we have to the day it is
    about: on a Saturday "este fin de semana" is a window, so the excuse is about the clock and not
    about the vocabulary."""
    saturday = date(2026, 8, 8)

    assert window_words.window_in("este fin de semana", today=TODAY) is None
    assert window_words.window_in("este fin de semana", today=saturday) is not None


# The words for how much money there is. One class again: what can be asked with "tengo" can be
# asked with "queda", and the balance intent has to hear all of them.
STATES = ("cuanto tengo", "cuanto me queda", "cuanto hay", "de cuanto dispongo", "que saldo tengo")


@pytest.mark.parametrize("said", STATES)
def test_every_way_of_asking_for_the_balance_reaches_the_balance(said: str) -> None:
    assert intent_router.route(said, has_previous=False) is Intent.ACCOUNT_BALANCE


@pytest.mark.parametrize("said", STATES)
def test_a_slip_in_the_question_word_costs_none_of_them_the_balance(said: str) -> None:
    """The third defect, asked of the class rather than of the sentence that reported it.

    "Cunato tengo" was a balance answered by the financial planner while the verb beside it already
    tolerated three misspellings written out by hand. A rule that only fixes the reported spelling
    leaves the same hole under the next one.
    """
    slipped = said.replace("cuanto", "cunato", 1)

    assert intent_router.route(slipped, has_previous=False) is Intent.ACCOUNT_BALANCE


# Every kind of account this service knows, by a word somebody would use for it.
ACCOUNTS = {
    "CURRENT": "la cuenta corriente",
    "SAVINGS": "la hucha",
    "ASSET": "el fondo",
}


@pytest.mark.parametrize(("kind", "said"), sorted(ACCOUNTS.items()))
def test_every_account_kind_survives_a_transposition(kind: str, said: str) -> None:
    """The fourth defect. The table carried "la nomnia" as an entry — one spelling of one account,
    written down because somebody typed it — while "hcuha" was nobody's."""
    words = said.split()
    noun = words[-1]
    slipped = " ".join([*words[:-1], noun[0] + noun[2] + noun[1] + noun[3:]])

    assert account_words.named_in(said) == (kind,)
    assert account_words.named_in(slipped) == (kind,), f"{slipped!r} is {said!r} mistyped"
