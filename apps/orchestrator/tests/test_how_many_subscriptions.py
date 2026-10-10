"""«¿Cuántas suscripciones tengo?» — four, not thirty-six.

Reported. The answer was "36 movimientos en suscripciones en los últimos 12 meses" and the holder
said what anybody would: "yo pensaba que tenía solo netflix altafir spoti y steam". Both numbers are
true and only one is the answer — thirty-six is how many times they were charged, four is how many
things they are paying for.

Two separate things were wrong. The sentence they actually typed said "subscripciones" with a b, so
it named no category at all and the whole deterministic path was missed; and even spelled correctly,
nothing counted distinct payees, so the question reached the planner and came back as a count of
rows.

No new metric and nothing new asked of banking: a breakdown by merchant already returns one group
per payee, so the count is the number of groups and the names are their keys.
"""

from finguard_orchestrator.domain.services import explicit_facts


def test_the_misspelling_everybody_writes_is_read() -> None:
    """The sentence from the report, verbatim. "Subscripciones" with the b named no category, so the
    turn missed every deterministic reading and the model counted rows."""
    assert explicit_facts.asks_how_many_payees(
        "Perdona sabrias decirme que subscripciones tengo activas ahora mismo ?"
    )


def test_a_question_about_money_is_still_about_money() -> None:
    """The three sentences this must not steal. Each names the same category and asks something the
    service already answered — a sum, a count of rows, an average — and reading any of them as a
    list of payees replaces a right answer with a right answer to another question."""
    for sentence in (
        "¿cuánto gasté en suscripciones?",
        "¿cuántos movimientos tengo en suscripciones?",
        "¿cuál es mi gasto medio en suscripciones?",
    ):
        assert explicit_facts.asks_how_many_payees(sentence) is False, sentence


def test_a_rail_is_a_charge_and_not_a_payee() -> None:
    """The fourth sentence, and it was being stolen. "¿Cuántos bizums tengo?" is how many times —
    the same question as "¿cuántos pagos tengo?" — and it came back grouped by merchant over twelve
    months, which is how many *people* this holder has ever sent one to. It happened inside the
    episode whose whole subject is that "tengo" counts.

    The words naming the charge were a list and the rails were not in it."""
    for sentence in (
        "¿cuántos bizums tengo?",
        "¿cuántas transferencias tengo?",
        "cuantos traspasos tengo",
    ):
        assert explicit_facts.asks_how_many_payees(sentence) is False, sentence


def test_a_thing_you_have_is_still_counted_by_payee() -> None:
    """What separates the two, and why only the rails were added. A subscription and a direct debit
    name the arrangement at least as often as the charge for it, so counting *them* is counting
    payees — which is the question this whole path was written for. A bizum is never an
    arrangement."""
    for sentence in (
        "¿cuántas suscripciones tengo?",
        "¿cuáles son mis suscripciones?",
        "¿cuántas domiciliaciones tengo?",
    ):
        assert explicit_facts.asks_how_many_payees(sentence) is True, sentence
