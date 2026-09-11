"""A-8 + B-8 + A-10 (Wave 3 hardening): the design's own restyle sentences are restyles.

Design §3.1 lists «Prefiero líneas» and «Enséñame solo los cinco primeros» as the *definition*
of presentation-only, and both used to fall through `interpret_visual` as `None` -- a plural
`_VISUAL` never learned, and a limit phrase `EncodingPatch` had no reading for. Both then reached
the financial planner: a banking call and an unrelated answer for a sentence that asked to
change nothing about the data.

A-10's pair is the opposite failure: `_FINANCIAL` matched a bare axis noun ("categorías",
"gastos") beside a style word and refused the turn `VISUAL_AMBIGUOUS` -- a question about a chart
that names its own axis, misread as "you asked to change two things at once".

Probed directly against `interpret_visual`, with `CHARTED.visual_contexts` standing in for a
chart already on screen (§3.1's own contexts) -- no route, no ledger, no model.
"""

from test_visual_follow_up import CHARTED

from finguard_orchestrator.domain.services.visual_interpreter import interpret_visual

CONTEXTS = CHARTED.visual_contexts


def test_a_plural_mark_word_is_a_restyle() -> None:
    for sentence in ("Prefiero líneas", "Ponlo en lineas", "Muéstralo en áreas", "Ponlo en puntos"):
        intent = interpret_visual(sentence, CONTEXTS)
        assert intent is not None, sentence
        assert intent.patch is not None, sentence
        assert intent.patch.mark is not None, sentence


def test_puntos_names_the_point_mark() -> None:
    intent = interpret_visual("Ponlo en puntos", CONTEXTS)
    assert intent is not None and intent.patch is not None
    assert intent.patch.mark == "point"


def test_a_verb_of_preference_beside_a_mark_word_is_still_read_by_the_mark() -> None:
    for sentence in ("Prefiero líneas", "Mejor en barras", "Cámbialo a circular", "Pásalo a áreas"):
        intent = interpret_visual(sentence, CONTEXTS)
        assert intent is not None, sentence
        assert intent.patch is not None, sentence
        assert intent.patch.mark is not None, sentence


def test_a_row_limit_in_words_or_digits_is_a_limit_patch() -> None:
    cases = {
        "Enséñame solo los cinco primeros": 5,
        "los tres primeros": 3,
        "los 3 primeros": 3,
        "solo los 10 últimos": 10,
        "los diez últimos": 10,
    }
    for sentence, expected in cases.items():
        intent = interpret_visual(sentence, CONTEXTS)
        assert intent is not None, sentence
        assert intent.patch is not None, sentence
        assert intent.patch.limit == expected, sentence


def test_a_limit_outside_one_to_twenty_is_refused_with_a_reason() -> None:
    intent = interpret_visual("los 25 primeros", CONTEXTS)
    assert intent is not None
    assert intent.patch is None
    assert intent.explanation is not None


def test_a_bare_axis_noun_beside_a_sort_word_is_a_restyle_not_a_mixed_refusal() -> None:
    """A-10: the axis noun names the axis, not a query. Neither sentence names a window or a
    comparison verb, so neither is mixed any more -- both are sort restyles."""
    for sentence in (
        "Ordena las categorías de mayor a menor",
        "Ponme los gastos en barras",
    ):
        intent = interpret_visual(sentence, CONTEXTS)
        assert intent is not None, sentence
        assert intent.mixed is False, sentence
        assert intent.patch is not None, sentence


def test_a_real_financial_signal_beside_a_style_word_is_still_mixed() -> None:
    """The other half of A-10's rule: a window or a comparison verb beside a style word is a
    genuine two-things-at-once turn, and the ambiguity still has to be asked about."""
    for sentence in (
        "Compara esto en barras",
        "Ponlo en barras por meses",
    ):
        intent = interpret_visual(sentence, CONTEXTS)
        assert intent is not None, sentence
        assert intent.mixed is True, sentence
