"""What kind of thing the user just said.

Before this there were two answers — payment or financial query — so everything that was neither
went to the query planner and came back as a refusal about averages and forecasts. "¿Quién eres?"
was answered with *"solo consulto lo que ya ha pasado, sin medias, comparaciones ni previsiones"*:
not wrong about the product, and not an answer to the question.

Deterministic, like the payment router and for the same reason: which conversation this is cannot
depend on the disposition of whichever model is configured.

The order is the design. Payment first, because money is the one that must not be misread. Then the
three that are about the assistant or about its last answer, and only what is left is a question for
the ledger — a sentence that is genuinely about spending should never be captured by a phrase that
merely resembles a complaint.
"""

import pytest

from finguard_orchestrator.domain.services import intent_router
from finguard_orchestrator.domain.services.intent_router import Intent


def route(sentence: str, *, answered: bool = True) -> Intent:
    return intent_router.route(sentence, has_previous=answered)


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum de 20 euros a Marta",
        "mándale 15 euros a Aitor",
    ],
)
def test_a_payment_is_still_a_payment(sentence: str) -> None:
    """First, and not because it is the most common. It is the one where being wrong costs money."""
    assert route(sentence) is Intent.PAYMENT


@pytest.mark.parametrize(
    "sentence",
    [
        "¿quién eres?",
        "quien eres",
        "¿qué puedes hacer?",
        "¿qué sabes hacer?",
        "ayuda",
        "¿cómo funcionas?",
        "¿eres una IA?",
        "¿eres un bot?",
        "¿para qué sirves?",
    ],
)
def test_asking_about_the_assistant_is_not_a_question_about_money(sentence: str) -> None:
    assert route(sentence) is Intent.IDENTITY_OR_HELP


@pytest.mark.parametrize(
    "sentence",
    [
        # Three turns of a real conversation, all answered with "¿de qué periodo?". The pattern
        # wanted the literal "qué puedes hacer", so a user asking what the product does was sent to
        # the financial planner, whose commonest complaint is a missing window.
        "Oye con que cosas me puedes asistir / ayudar ?",
        "¿en qué me puedes ayudar?",
        "Vale puedes hacer transferencias o solo bizums ?",
        "¿sabes hacer transferencias?",
        "¿puedes hacer pagos?",
    ],
)
def test_asking_what_the_assistant_can_do_is_answered_and_not_planned(sentence: str) -> None:
    """The answer already existed and was good. The whole defect was the recogniser, and the whole
    cost of it was a user concluding the product is broken."""
    assert route(sentence) is Intent.IDENTITY_OR_HELP


@pytest.mark.parametrize(
    "sentence",
    [
        # The costly direction. A capability question routed to payments asks "¿a quién?" and
        # wastes a turn; a payment order routed to the identity answer loses a payment.
        "hagamos un bizum",
        "hazle un bizum de 20 euros a Marta",
        "¿puedes hacerme un bizum?",
        "¿puedes hacer un bizum a Marta?",
    ],
)
def test_an_order_to_pay_is_still_an_order_however_politely_it_asks(sentence: str) -> None:
    assert route(sentence) is Intent.PAYMENT


def test_a_polite_request_for_a_figure_is_still_a_question_about_money() -> None:
    """`puedes hacer` had to stay narrow. "¿Puedes decirme…" is a question phrased politely, and a
    bare `puedes` would have swallowed half the dataset."""
    assert route("¿puedes decirme cuánto gasté el mes pasado?") is Intent.FINANCIAL_QUERY


@pytest.mark.parametrize(
    "sentence",
    [
        "¿qué categorías entiendes?",
        "¿qué categorías manejas?",
        "dime las categorías disponibles",
        "lista de categorías",
        "¿qué opciones de categoría hay?",
    ],
)
def test_asking_for_the_category_catalogue_is_not_a_financial_query(sentence: str) -> None:
    assert route(sentence) is Intent.LIST_CATEGORIES


def test_asking_how_the_previous_answer_was_classified_is_not_the_catalogue() -> None:
    assert route("¿en qué categoría has clasificado el cine?") is Intent.EXPLAIN_PREVIOUS


def test_asking_where_the_user_spent_most_is_a_new_query_not_an_explanation() -> None:
    assert (
        route("Vale en que categoria es la que he gastado mas a lo largo de este año?")
        is Intent.FINANCIAL_QUERY
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "¿por qué me has mostrado restaurantes?",
        "¿en qué categoría has clasificado el cine?",
        "¿de dónde sale ese número?",
        "¿qué consulta has hecho?",
        "¿cómo has calculado eso?",
        "¿De dónde has sacado esa cifra?",
        "¿Qué has consultado para llegar a ese dato?",
        "¿En qué te basas para decirme eso?",
        "¿Qué registros has mirado para darme esa cantidad?",
        "¿Cómo has llegado a esa cifra en concreto?",
        "¿En qeu te basas para dar esa cifra?",
        "¿Dond sale esa cantidad exactamnte?",
        "no entiendo el desglose",
        # The sentence from the real conversation, verbatim. The first version of this router missed
        # it, so it reached the planner and came back asking which period — a question about the
        # catalogue does not have a period.
        "¿es que los restaurantes pertenecen a la misma categoría que el cine?",
        "¿el cine cuenta como ocio?",
        "¿es lo mismo que restaurantes?",
        "¿están en la misma categoría?",
    ],
)
def test_asking_about_the_last_answer_reads_the_plan_and_not_the_ledger(sentence: str) -> None:
    assert route(sentence) is Intent.EXPLAIN_PREVIOUS


def test_there_is_nothing_to_explain_before_the_first_answer() -> None:
    """Without a previous plan the same sentence is not about anything, so it goes where every other
    unrecognised sentence goes. Answering "the query was…" with no query would be inventing one."""
    assert route("¿por qué me has mostrado eso?", answered=False) is Intent.FINANCIAL_QUERY


@pytest.mark.parametrize(
    "sentence",
    [
        "no me has entendido",
        "perdona, no me has entendido",
        "no, me refería a los restaurantes",
        "eso no es lo que te he preguntado",
        "me refiero al alquiler",
        "no era eso",
    ],
)
def test_a_correction_is_marked_as_one(sentence: str) -> None:
    """It matters because of what happens next: a correction patches only the fields the user
    replaces instead of deriving a fresh plan or silently repeating the wrong answer."""
    assert route(sentence) is Intent.CORRECTION


def test_a_correction_before_any_answer_is_just_a_question() -> None:
    assert route("no me has entendido", answered=False) is Intent.FINANCIAL_QUERY


@pytest.mark.parametrize(
    "sentence",
    [
        "no me has entendido",
        "Perdona, no me has entendido.",
        "No era eso.",
        "te has equivocado",
        "eso no es lo que te pregunté",
    ],
)
def test_a_bare_correction_contains_no_replacement(sentence: str) -> None:
    assert intent_router.is_bare_correction(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "no me has entendido, me refería al alquiler",
        "no era este mes, era el anterior",
        "me refiero a los restaurantes",
    ],
)
def test_a_correction_with_new_information_is_not_bare(sentence: str) -> None:
    assert intent_router.is_bare_correction(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "La respuesta que me has dado no es correcta",
        "Creo que hay un error en la respuesta, ¿puedes revisarla?",
        "La respuesa no me convence, explícamelo de otra manera",
    ],
)
def test_rejecting_the_answer_without_a_replacement_opens_a_correction_slot(
    sentence: str,
) -> None:
    assert route(sentence) is Intent.CORRECTION
    assert intent_router.is_bare_correction(sentence) is True


def test_an_answer_rejection_that_supplies_the_replacement_is_not_bare() -> None:
    sentence = "La respuesta no es correcta, me refería al mes pasado"

    assert route(sentence) is Intent.CORRECTION
    assert intent_router.is_bare_correction(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto gasté en gasolina el mes pasado?",
        "¿en qué me he gastado más este mes?",
        "¿cuántas veces fui al súper?",
        "gastos de julio",
        "¿y este mes?",
        # The ones that resemble another intent and are not it. A question about what a *shop*
        # charges is not a question about what the assistant does, and asking for a breakdown is not
        # complaining about one.
        "¿qué me cobra Netflix?",
        "dame el desglose por categorías",
        "¿por qué gasté tanto en junio?",
    ],
)
def test_everything_else_is_a_question_for_the_ledger(sentence: str) -> None:
    assert route(sentence) is Intent.FINANCIAL_QUERY


def test_a_payment_wins_over_a_complaint() -> None:
    """A sentence that both complains and asks for money is a payment. Reading it as a correction
    would drop it into the query path, where it would be refused as an action — annoying, but the
    reverse would be worse in the other direction, so the order is chosen and not accidental."""
    assert route("no me has entendido, hazle un bizum de 20 euros a Marta") is Intent.PAYMENT


# --- the balance, which is not a question about movements ----------------------------
#
# The first thing the user asked in the reported conversation:
#
#     "Buenos días, ¿podrías decirme cuánto dinero tengo en mis cuentas?"
#     → "No he encontrado movimientos del 2026-08-04 al 2026-08-04."
#
# Everything about that is wrong, and the router is where it starts. There were five intents and
# none of them was the balance, so it fell through to FINANCIAL_QUERY — where `FinancialPlan` can
# express a window, a direction, categories, merchants, a grouping and SUM or COUNT, and cannot
# express a balance at all. The model built the nearest thing it could: today's movements.
#
# The figure exists and needs no model: /v1/account/summary has served it to the home screen since
# it was written. The conversation simply could not reach it.


@pytest.mark.parametrize(
    "sentence",
    [
        "Buenos días, ¿podrías decirme cuánto dinero tengo en mis cuentas?",
        "¿cuánto dinero tengo en la cuenta?",
        "cuanto dinero tengo",
        "¿qué saldo tengo?",
        "¿cuál es mi saldo?",
        "mi saldo actual",
        "¿cuánto me queda en la cuenta?",
        "¿de cuánto dispongo?",
        "¿cuánto tengo disponible?",
        "saldo",
        "Cuanto tiengo en mi huch",
        "Cuanto tngo",
    ],
)
def test_a_question_about_the_balance_is_its_own_intent(sentence: str) -> None:
    assert route(sentence) is Intent.ACCOUNT_BALANCE


@pytest.mark.parametrize(
    "sentence",
    [
        # About movements, which is what the financial planner is for. The discriminator is that a
        # balance is a state and these are all sums over a window — the words that decide it are
        # "gasté", "ingresé" and a period, never "tengo".
        "¿cuánto gasté el mes pasado?",
        "¿cuánto me he gastado en el supermercado?",
        "¿cuánto ingresé este mes?",
        "¿cuántos movimientos tengo este mes?",
        "¿cuántas compras tengo en Mercadona?",
        # A card's limit is not the account's balance, and neither is a contact list.
        "¿qué contactos tengo?",
        "¿qué tarjetas tengo?",
    ],
)
def test_a_question_about_movements_is_not_a_balance(sentence: str) -> None:
    """A false positive here is expensive: it would answer "¿cuánto gasté?" with the balance, which
    is a real figure about something else — the same class of error the balance bug itself is."""
    assert route(sentence) is not Intent.ACCOUNT_BALANCE


@pytest.mark.parametrize(
    "sentence",
    [
        "muéstrame los movimientos de mis cuentas",
        "¿cuánto gasté entre mis cuentas este mes?",
        "¿cuánto ingresé en mis cuentas?",
    ],
)
def test_account_words_inside_a_ledger_question_do_not_turn_it_into_a_balance(
    sentence: str,
) -> None:
    assert route(sentence) is Intent.FINANCIAL_QUERY


def test_asking_which_accounts_spent_most_is_not_an_account_catalogue() -> None:
    assert route("¿Cuáles son las cuentas con más gastos?") is Intent.FINANCIAL_QUERY


def test_a_ledger_origin_is_not_an_explanation_of_the_previous_answer() -> None:
    assert route("¿De dónde viene el ingreso de Marta?") is Intent.FINANCIAL_QUERY


def test_an_error_in_a_charge_is_not_a_correction_of_the_assistant() -> None:
    assert route("Creo que hay un error en el cargo de Netflix") is Intent.FINANCIAL_QUERY


def test_a_category_filter_is_not_the_category_catalogue() -> None:
    assert route("Dame el gasto por categorías del mes pasado") is Intent.FINANCIAL_QUERY


def test_a_payment_to_somebody_in_the_agenda_is_not_a_contact_list() -> None:
    assert route("Envía 20 euros a una persona de mi agenda") is Intent.PAYMENT


# --- who can I pay -------------------------------------------------------------------
#
# From the reported conversation:
#
#     "Perdona, ¿podrías decirme qué contactos tengo para mandar bizum?"
#     → "¿Para qué contacto te gustaría enviar un bizum?"
#
# It was read as a payment — "bizum" is a payment word — so the assistant asked for the recipient
# of a payment nobody had asked to make. The user had asked *who is available*, which is a list the
# service has served on /v1/contacts since it was written.
#
# **Checked before PAYMENT, which reverses the rule that payments come first.** That rule exists
# because being wrong about a payment costs money; listing contacts cannot move any, so the risk
# runs the other way here. What keeps it safe is the narrowness of the pattern: it needs the word
# "contactos" or an explicit "a quién puedo", and no real payment order contains either.


@pytest.mark.parametrize(
    "sentence",
    [
        "Perdona, ¿podrías decirme qué contactos tengo para mandar bizum?",
        "¿qué contactos tengo?",
        "mis contactos",
        "¿a quién puedo hacer un bizum?",
        "¿a quién puedo enviar dinero?",
        "lista de contactos",
        "enséñame los contactos",
    ],
)
def test_asking_who_can_be_paid_is_a_question(sentence: str) -> None:
    assert route(sentence) is Intent.LIST_CONTACTS


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum de 20 euros a Marta",
        "mándale 15 euros a Marta Solis",
        "quiero hacerle 15 euros de bizum a Marta con el concepto tapas",
        "transfiere 50 euros a Aitor",
    ],
)
def test_an_order_to_pay_is_still_a_payment(sentence: str) -> None:
    """The reversal above must not cost a single real payment. None of these names a contact list,
    and being wrong here is the expensive direction."""
    assert route(sentence) is Intent.PAYMENT
