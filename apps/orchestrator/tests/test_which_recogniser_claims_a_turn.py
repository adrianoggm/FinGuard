"""Where the order of the cascade is doing invisible work.

`_steps` reads a sentence by asking nineteen deterministic recognisers in a fixed order and taking
the first that answers. That is first-match, and for a turn only one of them claims it is also
best-match — the two only differ where more than one fires. Which is exactly what the code cannot
observe: the first one to answer returns, so a second reading is never computed, never logged and
never measured.

That is what makes the cascade expensive to change. A branch moved to fix one turn can silently
change another, and nothing says which turns are sitting on a tie. So this file asks all nineteen
on every turn of the corpus and counts the disagreements.

**It is a measurement, not a rule.** The recognisers are pure — a query in, a reading or None out,
no I/O — so asking all of them costs nothing but time, and none of it runs in production. What comes
out decides whether a scoring resolver is worth building at all: with no contested turn there is
nothing for one to resolve, and building it would be a mechanism in search of a tie.
"""

import pytest
from support import episodes

CORPUS = episodes.load()

# A conflict is two recognisers wanting to run *different* queries on the same turn. Two agreeing is
# not one: whichever wins, the same question is asked.
#
# Twelve of 1106 turns, and the order resolves every one of them correctly. That is the answer to
# "is a scoring resolver worth building": not yet. A resolver's only job would be to reproduce an
# ordering that already works, and twelve ties is a list somebody can read rather than a
# distribution needing a mechanism. What the measurement is *for* is that the list can no longer
# grow in silence — and that each entry has to be looked at once, which is how the thirteenth
# stopped being one.
#
# The four classes, because a count is not a work item:
#
#   ratio vs contextual_follow_up (5)
#       "¿Y el mes pasado?", "Este año", "¿cuál fue mi tasa de ahorro?" after a proportion, a
#       savings rate or a net cash flow. `ratio` re-runs both operands with the new window; the
#       loser would run one plain expense sum. The most load-bearing ordering in the cascade: swap
#       it and a follow-up to "¿qué porcentaje del sueldo me va en alquiler?" quietly becomes a
#       total, with a real figure attached.
#
#   direct_period_comparison vs average / deterministic_breakdown / natural_period (4)
#       "Compara mis gastos de este semestre con el anterior" and friends. Every loser drops half
#       the comparison and answers about one of the two windows.
#
#   internal_movement vs contextual_follow_up (2)
#       "¿Cuánto he movido a la hucha este año?" — EXPENSE against INCOME, so the two readings
#       differ by the sign of the answer.
#
#   account_follow_up vs contextual_follow_up (1)
#       "En la que ingreso la nomina vaya —", naming the account where the salary lands. The thread
#       is about spending, so EXPENSE is right and the INCOME reading is the wrong one: the sentence
#       describes *which account*, not which direction.
#
# Every losing reading here was checked against what its episode asks for, which is the work each
# entry is meant to cause. Two that looked like misreadings are not:
#
#   internal_movement claiming "¿cuánto he gastado en inversiones?" as internal INCOME is correct —
#   money put *into* the fund is the incoming internal leg, which is the episode's whole point, and
#   the EXPENSE reading beside it is the mistaken one.
#
#   account_follow_up reading "en la que ingreso la nómina" as EXPENSE is correct, for the reason
#   above.
#
# One was: `average` read "top 2 categorías por ticket medio" as groupBy MERCHANT, inheriting the
# dimension of the two turns before instead of the one the sentence names. Fixed at the source, and
# it is why this number is twelve rather than thirteen.
#
# A ratchet, not a target. Lower it by removing a tie; raise it only with the new pair written down.
#
# Thirteen now, and the move is one out and two in.
#
# Out: `contextual_follow_up` no longer claims "¿cuánto he gastado en inversiones?", because a
# follow-up that names something no vocabulary here knows now declines instead of patching the
# query in view. One reading left, so no tie.
#
# In, and both were always there: `executed()` could not say which accounts a query was restricted
# to or which counterparty it named, so two pairs whose readings differ by exactly that looked
# identical and were counted as agreement. "¿Qué aportaciones hice al fondo este año?" — one
# reading restricted to the fund and one not. "Desglósalo por cuenta" — one keeping the shop the
# previous answer singled out and one dropping it, which is the difference between breaking down
# that shop and breaking down the whole category.
#
# Neither is a wrong answer today: the order picks the right reading in both. What changed is that
# the measurement can see them, which is the point of a measurement.
CONTESTED = 13


def test_the_corpus_reports_which_turns_are_contested(capsys: pytest.CaptureFixture[str]) -> None:
    """Printed rather than only asserted, because the pairs are the actionable part. "These two
    recognisers disagree on this turn" is a work item; a percentage is not."""
    contested = [
        (episode.name, number, said, claims)
        for episode in CORPUS
        for number, said, claims in episodes.contested(episode)
    ]

    with capsys.disabled():
        print(f"\n  turns where two recognisers disagree: {len(contested)}")
        for name, number, said, claims in contested[:20]:
            print(f"    {name} · turn {number} · {said!r}")
            for who, reading in sorted(claims.items()):
                print(f"        {who}: {reading}")

    assert len(contested) <= CONTESTED, (
        "the cascade's order decides these turns, and nothing else records that it does"
    )


def test_a_turn_the_order_decides_says_which_query_it_ran() -> None:
    """The tie is not resolved. What is fixed is that it can no longer change an answer quietly.

    On a contested turn the two readings are not variants of one answer: a follow-up would return
    the spending side of a net cash flow and drop the income one; `internal_movement` and
    `contextual_follow_up` differ by the sign of the figure; a comparison and a single-period query
    answer about two months and about one. Reordering the cascade to fix one turn would have moved
    those, and the corpus would have agreed — it asserted which *tool* answered, and both readings
    are the same tool.

    Every one of the thirteen now states the plan it ran, so the losing reading fails the episode
    rather than passing it with a different figure: a different window, direction or category is a
    complaint, and a comparison collapsing to one query is a count mismatch. That is what a
    resolver would have been built to protect, without building one — and this test is what keeps
    it true, because a contested turn added tomorrow could arrive without an expectation.
    """
    silent = [
        f"{episode.name} · turn {number} · {said!r}"
        for episode in CORPUS
        for number, said, _ in episodes.contested(episode)
        if not episode.turns[number - 1].ran
    ]

    assert silent == [], "\n".join(
        ["turns the cascade's order decides and no expectation pins:", *silent]
    )


def test_no_recogniser_raises_on_a_turn_it_does_not_own() -> None:
    """The reason production still short-circuits, checked instead of assumed.

    A recogniser only ever runs on turns the ones above it declined, so it is free to assume what
    those already excluded. If that assumption is wrong the exception is invisible — the branch is
    unreachable — right up until somebody reorders the cascade. Here every one of them is asked
    about every turn, which is the only way to find out.
    """
    raised = [
        (episode.name, number, said, who, reading)
        for episode in CORPUS
        for number, said, claims in episodes.contested(episode)
        for who, reading in claims.items()
        if reading.startswith("raised ")
    ]

    assert raised == []
