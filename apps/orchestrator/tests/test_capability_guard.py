"""The guard that runs before the model.

Refusing what the domain cannot express is currently a disposition of whichever model is
configured: it moved from 0% to 60% by changing model, and a disposition is not a
control. This is the control.

It does not read the sentence. It recognises a closed list of capabilities we know we do
not have, which is a much smaller job and the only one that can be done with certainty.
"""

import pathlib

import pytest
import yaml

from finguard_orchestrator.domain.services import capability_guard
from finguard_orchestrator.domain.services.capability_guard import unsupported_reason

PLANNING = pathlib.Path(__file__).resolve().parents[1] / "evaluation" / "planning"


def cases(*expects: str) -> list[tuple[str, str]]:
    """Dataset cases with the given expectation, as (id, question).

    The guard is measured against the same 235 cases the models are, because a guard
    that disagrees with the dataset is either wrong or the dataset is — and either way
    somebody has to look.
    """
    found = []
    for name in ("dev", "validation", "adversarial"):
        data = yaml.safe_load((PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))
        found += [(c["id"], c["question"]) for c in data["cases"] if c["expect"] in expects]
    return found


def test_an_average_is_refused_before_any_model_sees_it() -> None:
    assert unsupported_reason("¿Cuál es mi gasto medio mensual?") is not None


def test_an_average_is_refused_however_it_is_worded() -> None:
    assert unsupported_reason("¿Cuánto gasto de media a la semana?") is not None


def test_a_comparison_between_periods_is_refused() -> None:
    assert unsupported_reason("¿He gastado más este mes que el anterior?") is not None


def test_a_ranking_is_not_a_comparison() -> None:
    """The pair that decides the whole design. Both say "más"; one asks to compare two
    windows and the other asks which group is largest inside one, which `groupBy` plus
    an ordering expresses perfectly well.

    A guard that refuses this is worse than no guard: it turns a question the system can
    answer into a refusal, and refusals are the failure the model was already making.
    """
    assert unsupported_reason("¿Cuál es el comercio donde más gasto este mes?") is None


# --- against the dataset the models are scored on -----------------------------------


# Three dataset cases are `unsupported` for reasons that are not a missing capability,
# and stating that here is more honest than widening the guard until it swallows them.
NOT_OURS = {
    # A date in the future. The planner already refuses a window that ends after today,
    # and it has the reference date — which this does not, and should not need.
    "adv_future_single_day",
    # Not about money at all. See the module docstring: recognising this needs an
    # allow-list of financial words, and "¿y este mes?" contains none of them.
    "adv_weather",
    "adv_general_knowledge",
}


@pytest.mark.parametrize(("case_id", "question"), cases("unsupported"), ids=lambda v: v)
def test_the_guard_catches_what_the_dataset_calls_unsupported(case_id: str, question: str) -> None:
    if case_id in NOT_OURS:
        pytest.skip("not a missing capability — see NOT_OURS")
    assert unsupported_reason(question) is not None, case_id


@pytest.mark.parametrize(("case_id", "question"), cases("plan", "clarification"), ids=lambda v: v)
def test_the_guard_refuses_nothing_the_system_can_answer(case_id: str, question: str) -> None:
    """The property that matters more than the one above.

    A miss costs what we already have: the model decides, sometimes wrongly. A false
    refusal costs something we do not have yet — it takes a question the pipeline answers
    correctly today and turns it into a dead end, before any model sees it. This runs
    over every answerable case in all three sets so that widening a pattern cannot buy
    recall with silence.
    """
    assert unsupported_reason(question) is None, f"{case_id}: {question}"


# --- what used to be refused here ----------------------------------------------------
#
# LARGEST_MOVEMENT lived in this file until the capability existed. "¿Cuál ha sido el mayor gasto?"
# was refused because `Metric` is SUM or COUNT, so a total in place of a maximum was the only thing
# the domain could express — and a total presented as a maximum is a real number about a different
# question.
#
# It is answered now, by RANK_MOVEMENTS. What is left here is the discrimination that family had to
# respect, which still matters: a *ranking of groups* is neither a comparison nor a refusal, and the
# guard must keep its hands off it.


@pytest.mark.parametrize(
    "question",
    [
        "En los últimos 3 meses, ¿cuál ha sido el mayor gasto que he realizado?",
        "¿cuál fue mi mayor gasto del mes pasado?",
        "¿cuál fue la compra más cara?",
        "el movimiento de mayor importe de julio",
        "¿cuál es el pago más grande que he hecho este año?",
    ],
)
def test_asking_for_one_extreme_movement_is_no_longer_refused(question: str) -> None:
    """The guard is a list of capabilities the domain does not have. This one it has, so a rule here
    would be a dead end in front of a working answer — the failure this file's own docstring calls
    the expensive one."""
    assert unsupported_reason(question) is None


@pytest.mark.parametrize(
    "question",
    [
        # A ranking of groups, which `groupBy` expresses, and a plain aggregation. Neither was ever
        # refusable and both stay that way.
        "¿en qué categoría he gastado más este mes?",
        "¿qué categoría es la que más he gastado?",
        "¿dónde gasto más?",
        "¿cuánto gasté en el supermercado el mes pasado?",
        "¿cuántas compras hice en Mercadona?",
    ],
)
def test_a_ranking_of_groups_or_a_total_is_not_refused(question: str) -> None:
    assert unsupported_reason(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "¿Cuál es el Bizum más alto que he realizado?",
        "¿cuál ha sido la transferencia más alta?",
        "¿cuál fue mi mayor ingreso este año?",
        "el bizum de mayor importe",
        "¿cuál fue el recibo más caro?",
    ],
)
def test_a_superlative_about_any_kind_of_movement_is_not_a_comparison(question: str) -> None:
    """The exception list of nouns was only the generic words for money going out — gasto, compra,
    pago, movimiento, cargo, importe, factura. So "el Bizum más alto que he realizado" carried
    "más … que", matched nothing, and came back as "todavía no comparo dos periodos" to a question
    containing exactly one period. A ranking subject is whatever the holder calls the movement."""
    assert unsupported_reason(question) is None


# --- which row a continuation asks for ------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "rank"),
    [
        ("y el segundo", 2),
        ("¿y el tercero?", 3),
        ("y la cuarta", 4),
        ("y el quinto", 5),
        ("y el décimo", 10),
    ],
)
def test_a_stated_ordinal_carries_its_position(sentence: str, rank: int) -> None:
    """It used to be a boolean, so the caller advanced by one whichever word it was and "¿y el
    tercero?" after the largest expense answered with the second."""
    found = capability_guard.ordinal_in(sentence)

    assert found is not None
    assert found.mode == "ABSOLUTE"
    assert found.rank == rank


@pytest.mark.parametrize("sentence", ["y el siguiente", "y otro", "y los demás", "y el resto"])
def test_a_pronoun_names_no_position(sentence: str) -> None:
    """ "El siguiente" is genuinely relative — it is one more than wherever we are — and that is the
    only case where the caller may advance by one."""
    found = capability_guard.ordinal_in(sentence)

    assert found is not None
    assert found.mode == "NEXT"
    assert found.rank is None


@pytest.mark.parametrize(
    "sentence",
    ["¿cuánto gasté?", "y en gasolina", "el tercero de la lista de contactos", ""],
)
def test_a_sentence_with_content_of_its_own_is_not_a_continuation(sentence: str) -> None:
    """Anchored at both ends. Reading a whole question as "the next one" would answer it with the
    second largest expense, which is worse than the loop the ordinal reader replaced."""
    assert capability_guard.ordinal_in(sentence) is None


# --- operating on the account, rather than reading it ------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # Selling and buying.
        "vende mis acciones del fondo indexado",
        "quiero vender participaciones del fondo",
        "compra 10 acciones de Inditex",
        "cómprame acciones de Telefónica",
        "quiero invertir 500 euros en bolsa",
        "invierte 1000 en el fondo",
        # Contracting a product, and ending one.
        "contrata un plan de pensiones",
        "quiero contratar una hipoteca",
        "domicilia el recibo de la luz",
        "dame de baja de la cuenta nómina",
        "abre una cuenta nueva",
        "cierra mi hucha",
        # The card, and cash.
        "bloquea mi tarjeta",
        "anula la tarjeta de crédito",
        "saca 100 euros del cajero",
        "retira efectivo de la cuenta",
    ],
)
def test_a_request_to_operate_is_refused_before_any_model_sees_it(question: str) -> None:
    """All sixteen used to reach the planner, and whatever it planned came back as a figure.

    Measured, not supposed: eight of these were run through the service and every one was answered
    with a total over a period. A real figure answering a different question is the failure nothing
    downstream can contradict, and here it answers a *request* with one.

    It also fixes what the user reads. Falling through produced "eso no puedo calcularlo todavía"
    for a request to sell shares — true of the wrong thing, because nobody asked for a calculation.
    """
    assert unsupported_reason(question) == "ACTION"


@pytest.mark.parametrize(
    "question",
    [
        # «Compra» is a noun before it is an imperative, which is why the pattern needs the thing
        # bought. Refusing these would be the expensive direction: a question this service answers
        # well, turned into a dead end.
        "¿cuánto gasté en la compra?",
        "¿cuántas compras hice en Mercadona?",
        "¿cuál fue mi compra más cara?",
        # «Abre» and «cierra» are about periods at least as often as about accounts.
        "¿cuánto llevo desde que abre el mes?",
        # Past tense is a question about what already happened, never an instruction.
        "¿cuánto he sacado del cajero este mes?",
        "¿cuánto he retirado en efectivo?",
        "¿cuánto he pagado de recibos domiciliados?",
        "¿cuánto vendí el año pasado?",
    ],
)
def test_asking_about_an_operation_that_happened_is_not_a_request_to_perform_one(
    question: str,
) -> None:
    assert unsupported_reason(question) is None
