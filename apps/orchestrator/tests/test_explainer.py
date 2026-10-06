"""Answers about the assistant and about its own last answer, with no model and no ledger.

The question that made this necessary came from a real conversation: *"¿es que los restaurantes
pertenecen a la misma categoría que el cine?"* — asked after an answer about the cinema came back
filtered by restaurants. It is not a question about money. The catalogue answers it, and the
previous plan says whether the suspicion was right.
"""

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.services import category_words, explainer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


def plan(*categories: str, metric: Metric = Metric.SUM) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=categories,
        group_by=GroupBy.NONE,
        metric=metric,
    )


def test_the_identity_says_what_it_cannot_do_too() -> None:
    """An assistant that lists only its talents is one whose limits somebody discovers by being
    surprised.

    Transfers are named now because a user asked for them by name — "¿puedes hacer transferencias
    o solo bizums?" — and got "¿de qué periodo?" three turns running. The recogniser was the bug;
    an answer that did not mention transfers would have been the next one.
    """
    said = explainer.IDENTITY.replace("\n", " ")

    assert "FinGuard" in said
    assert "bizum" in said
    assert "medias" in said
    assert "transferencias" in said
    assert "Comparar dos semanas, meses o años" in said
    assert "rangos de fechas explícitos" in said
    assert "se acumulan" in said
    assert "ticket medio" in said


def test_explaining_restates_the_query_that_ran() -> None:
    """In the words the answer used: the restated query said "expense, transport_fuel" until the
    query line learnt to speak Spanish, about a question the holder had asked about «gasolina»."""
    said = explainer.explain("¿qué consulta has hecho?", plan("TRANSPORT_FUEL"))

    assert "el mes pasado" in said
    assert "gasolina" in said
    assert "transport_fuel" not in said.lower()


def test_the_misfiled_question_is_answered_plainly() -> None:
    """The one that matters. Somebody asks about the cinema, the plan says restaurants, and this is
    the sentence that lets them correct it instead of believing the figure."""
    said = explainer.explain("¿en qué categoría has clasificado el cine?", plan("RESTAURANTS"))

    assert "LEISURE" in said
    assert "filtraba por restaurantes" in said


def test_two_categories_named_gets_the_answer_the_question_was_really_asking() -> None:
    said = explainer.explain(
        "¿es que los restaurantes pertenecen a la misma categoría que el cine?",
        plan("RESTAURANTS"),
    )

    assert "RESTAURANTS" in said
    assert "LEISURE" in said
    assert "categorías distintas" in said


def test_a_category_that_was_used_is_confirmed_as_used() -> None:
    said = explainer.explain("¿en qué categoría has puesto el alquiler?", plan("HOUSING_RENT"))

    assert "es la que usé" in said


def test_a_query_with_no_filter_says_so() -> None:
    said = explainer.explain("¿en qué categoría has clasificado el cine?", plan())

    assert "no filtré por ninguna categoría" in said


def test_there_is_nothing_to_explain_without_a_plan() -> None:
    said = explainer.explain("¿de dónde sale ese número?", None)

    assert "no hay ninguna consulta" in said.lower()


# --- the catalogue -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("el cine", ("LEISURE",)),
        ("CINE", ("LEISURE",)),
        ("la gasolina", ("TRANSPORT_FUEL",)),
        ("el alquiler y la gasolina", ("HOUSING_RENT", "TRANSPORT_FUEL")),
        # Accents folded both ways: somebody typing "cafe" means "café".
        ("un cafe", ("RESTAURANTS",)),
        ("el súper", ("GROCERIES",)),
        ("el super", ("GROCERIES",)),
    ],
)
def test_the_words_people_use_are_found(sentence: str, expected: tuple[str, ...]) -> None:
    assert category_words.named_in(sentence) == expected


def test_the_longest_gloss_wins() -> None:
    """ "compras de capricho" is LEISURE and contains no word GROCERIES should claim. Without the
    longest-first rule, "la compra" inside it would file a question about treats under groceries."""
    assert category_words.named_in("mis compras de capricho") == ("LEISURE",)


def test_a_word_inside_another_word_is_not_a_match() -> None:
    """Boundaries, not substrings: "metro" is a category and "kilometros" is not."""
    assert category_words.named_in("he hecho 300 kilometros") == ()


def test_a_sentence_about_nothing_in_the_catalogue_finds_nothing() -> None:
    assert category_words.named_in("¿cuánto me queda?") == ()


def test_the_order_follows_the_question() -> None:
    """The answer reads in the order the question asked, which is what makes it sound like an
    answer rather than a lookup."""
    assert category_words.named_in("¿el cine o el alquiler?") == ("LEISURE", "HOUSING_RENT")
