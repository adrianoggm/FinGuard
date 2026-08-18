"""Which sentences are an instruction to pay somebody.

The first step of §4.1 — "Intent" — and it is deterministic on purpose. Asking the model "is this a
payment?" would put the decision to move money inside the thing that is not a source of truth, and
it would answer differently between two models we have measured 60 points apart on disposition.

What each mistake costs is not symmetric, which is what the rules are shaped around:

    a question read as a payment      the extraction finds no grounded amount and it is refused
    a payment read as a question      the capability guard refuses it as an action
    another action read as a payment  refused the same way as a question read as one

None of the three moves money. What would is a payment that skips the confirmation, and no routing
decision can cause that: the confirmation lives on the other side of a second request.
"""

import pytest

from finguard_orchestrator.domain.services import payment_router


@pytest.mark.parametrize(
    "sentence",
    [
        # The demo's sentence, and the shapes of it people actually type.
        "hazle un bizum de 20 euros a Marta",
        "haz un bizum de 20€ a Marta",
        "mándale 20 euros a Marta",
        "manda 20 euros a Marta",
        "envía 20 euros a Marta",
        "envíale 20 euros a Marta",
        "transfiere 20 euros a Marta",
        "hazle una transferencia de 20 euros a Marta",
        "págale 20 euros a Marta",
        "devuélvele los 20 euros a Marta",
        # No amount, but unmistakably a payment. It becomes a question about the amount, not a
        # refusal about capabilities — which is the answer somebody who said this wants.
        "hazle un bizum a Marta",
        "quiero hacerle un bizum a Marta",
        "Tengo que enviar 20 euros a Marta, puedo hacerlo por bizum, ¿verdad?",
        "Quiero mandrle 20 euros a Marta por bizum, ¿cómo lo hago?",
    ],
)
def test_an_instruction_to_pay_is_recognised(sentence: str) -> None:
    assert payment_router.is_payment(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        # Questions, including the ones that name money and a person.
        "¿cuánto gasté en gasolina en junio?",
        "¿cuánto le he mandado a Marta este año?",
        "¿cuántos bizums le he hecho a Marta?",
        "¿me quedan 20 euros?",
        "dime mis gastos de 20 euros",
        "¿qué transferencias hice en julio?",
        # Actions that are not payments. Still unsupported, and refused by the capability guard
        # rather than sent down a path that ends in a payment prompt.
        "cancela mi suscripción a Netflix",
        "cámbiame el límite de la tarjeta",
        "dame de baja del gimnasio",
        # Information about how to pay is not an instruction to pay now.
        "Quiero saber cómo enviar 20 euros a Marta",
        "Necesito saber cuánto envié a Marta",
        # The recipient-less transfer vocabulary belongs to the own-account workflow.
        "Quiero transferir dinero de una de mis cuentas a otra",
        "Estoy intentando hacer una transferencia entre mis cuentas, ¿cómo lo hago?",
        "Eso fue nomina o un bizum?",
    ],
)
def test_everything_else_is_not_a_payment(sentence: str) -> None:
    assert not payment_router.is_payment(sentence)


def test_a_past_payment_is_a_question_not_an_order() -> None:
    """The tense is the whole difference, and getting it wrong is the one costly direction: an
    account holder asking what they sent last month must not be shown a prompt to send it again."""
    assert not payment_router.is_payment("le mandé 20 euros a Marta la semana pasada")
    assert not payment_router.is_payment("le he mandado 20 euros a Marta")
    assert not payment_router.is_payment("le envié 20 euros a Marta el martes")


def test_a_past_payment_is_also_read_as_one_rather_than_only_refused() -> None:
    """The half of the same sentence that used to be thrown away.

    `is_payment` says what must not happen — no offer to send it again — and for a long time that
    was all this module said about the past. So "le he mandado 20 € a Marta" left here as a
    negative and reached the financial planner with nothing recognised in it, and a model was asked
    to build a query out of a sentence whose tense and direction this vocabulary already knew.
    """
    assert payment_router.describes_a_past_payment("le he mandado 20 € a Marta")
    assert payment_router.describes_a_past_payment("¿cuánto le mandé a Marta el mes pasado?")


@pytest.mark.parametrize(
    "sentence",
    [
        # Equally past, and somebody else's payment. Reading it as money the holder sent would
        # answer "¿he recibido algún bizum de Marta?" with what they spent.
        "me ha llegado un bizum de Marta",
        "Marta me hizo un bizum de 20 euros",
        # Not the past at all. An order stays an order, and `is_payment` is what handles it.
        "hazle un bizum de 20 euros a Marta",
    ],
)
def test_what_is_not_a_payment_already_made(sentence: str) -> None:
    assert payment_router.describes_a_past_payment(sentence) is False


@pytest.mark.parametrize(
    "reply", ["no sé", "No lo se.", "ni idea", "no estoy segura", "todavía no lo sé"]
)
def test_a_bare_uncertain_reply_is_recognised(reply: str) -> None:
    assert payment_router.is_uncertain(reply)


@pytest.mark.parametrize(
    "reply",
    ["no sé a quién tengo en contactos", "no sé, quizá Marta", "¿cuánto gasté?"],
)
def test_a_sentence_with_a_subject_is_not_reduced_to_uncertainty(reply: str) -> None:
    assert not payment_router.is_uncertain(reply)


@pytest.mark.parametrize(
    "sentence",
    [
        "continuar con el bizum",
        "sigamos con el pago",
        "retoma la transferencia",
        "quiero seguir con el bizum",
    ],
)
def test_resuming_a_paused_payment_has_to_be_explicit(sentence: str) -> None:
    assert payment_router.resumes(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    ["15 euros", "Marta Solis Vega", "sigamos", "¿cuánto gasté este mes?"],
)
def test_a_slot_value_or_an_unrelated_sentence_does_not_resume_a_payment(sentence: str) -> None:
    assert payment_router.resumes(sentence) is False


# --- the three ways Spanish gives an order -------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # Reported, seven turns of it. Every one read as a question, because they start with "que"
        # and the interrogative test cannot tell that "que" from "¿qué?". The holder ended up
        # shouting at the assistant.
        "de hoy imbecil que le hagas el puto bizum",
        "QUe quiero queh agas un bizum",
        "quiero que hagas un bizum",
        "quiero que le hagas un bizum de 20 euros a Marta",
        "que le mandes 20 euros a Marta",
        # The first of that chain, which is the volitional form in the first person plural.
        "Oye y aitor vamos a darle el bixum que le debo del desayuno que son 25 euros",
    ],
)
def test_an_order_in_the_subjunctive_is_an_order(sentence: str) -> None:
    """The second person is what makes reading these safe. "Hagas" is addressed to the assistant,
    and nobody asks a capability question with it — "¿cómo hago un bizum?" is first person and
    "¿puedes hacer un bizum?" is an infinitive."""
    assert payment_router.is_payment(sentence) is True
    assert payment_router.is_explicit_order(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cómo hago un bizum?",
        "¿puedes hacer transferencias?",
        "¿cuánto le he mandado a Marta este año?",
        "¿qué bizums he recibido este mes?",
        "quiero saber cuánto he pagado en bizums",
        "que hagas el desglose por categorías",
        "quiero traspasar dinero entre mis cuentas",
    ],
)
def test_a_question_about_payments_is_still_a_question(sentence: str) -> None:
    """The wider order vocabulary must not widen what counts as an instruction. "Que hagas el
    desglose" is the subjunctive too and names no payment; the rest ask about the past, about a
    capability, or about the holder's own accounts."""
    assert payment_router.is_payment(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum a Carmen",
        "mándale 20 euros a Marta",
        "envíale 20 euros a Marta",
        "págale 20 euros a Marta",
        "transfiérele 20 euros a Marta",
        "ingrésale 20 euros a Marta",
        # The one the imperative list did not have. It was a payment and not an order, so it could
        # not interrupt an open clarification — which is the whole reason this predicate exists.
        "devuélvele los 20 euros a Marta",
    ],
)
def test_an_order_in_the_imperative_is_an_order(sentence: str) -> None:
    """Every verb that moves money, in the form that commands it. A verb present in the payment
    vocabulary and absent from this one is a payment nothing lets through a clarification."""
    assert payment_router.is_explicit_order(sentence) is True


@pytest.mark.parametrize("sentence", ["pues bizum", "bizum", "un bizum"])
def test_a_bare_payment_word_is_not_an_instruction(sentence: str) -> None:
    """The distinction the precedence rule rests on. A bare noun offered as the answer to "¿de qué
    comercio o categoría?" belongs to that question; an instruction does not, and only the verb
    tells them apart. `is_payment` is true for both — it is the narrower question — so the route
    asks this
    one before letting a payment interrupt an open clarification."""
    assert payment_router.is_explicit_order(sentence) is False


# --- the spellings and the morphology, closed as classes -------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Hagamos un bisum",
        "hagamos un bizum",
        "haz un vizum a Marta",
        "hazle un bixum a carmen",
        "mandale un bisum de 20 euros",
        "hazme un bizu a Javi",
    ],
)
def test_a_rail_is_read_however_it_is_spelled(sentence: str) -> None:
    """ "Bizum" arrived as bixum, bisum and vizum in three separate reported conversations, and each
    miss made the whole payment path silently unreachable — the worst shape a defect can have.

    Enumerating the misspellings was the previous answer and it was wrong twice. One edit against a
    two-word lexicon closes the class instead of the instance."""
    assert payment_router.is_payment(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        # A word one edit from a rail is a rail; a word two edits away is a word.
        "¿cuánto gasté en bizcochos?",
        "¿cuál es mi preferencia de pago?",
        "¿cuánto he gastado en el súper?",
    ],
)
def test_a_word_that_is_not_a_rail_stays_a_word(sentence: str) -> None:
    assert payment_router.is_payment(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    ["¿puedes hacer transferencias?", "¿sabes hacer bizums?", "¿podrías hacer pagos?"],
)
def test_asking_what_the_assistant_can_do_is_not_an_order(sentence: str) -> None:
    """The plural is what made this matter. "Transferencia" did not match "transferencias" while the
    lexicon was an alternation, so a capability question was refused for the wrong reason — by a
    typo, not by a rule. One edit apart matches the plural, so the rule now has to exist."""
    assert payment_router.is_payment(sentence) is False


# --- a destination that is your own account is nobody to pay ------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # Reported. None of these matched the phrase list, so they reached the payment path and came
        # back "no tengo a Fondo indexado en tus contactos": an account answered as a person who
        # does not exist.
        "pasa 100 del fondo a la hucha",
        "pasame 100 a la hucha",
        "quiero pasar dinero de mi cuenta corriente a mi fondo",
        "mueve 200 euros a la hucha",
        # The explicit framing, which names no account at all and so has no destination to read.
        # Both tests are needed and this is the one the phrase list still earns its place for.
        "quiero traspasar dinero entre mis cuentas",
    ],
)
def test_moving_money_between_your_own_accounts_is_never_a_payment(sentence: str) -> None:
    """Grounded in the account vocabulary rather than in more phrasings of "between my accounts".

    The question was never lexical: it is whether the destination is one of the holder's own account
    kinds or a person. The kind vocabulary already knew, and this router was not asking it."""
    assert payment_router.is_payment(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum a Marta",
        "hazle un bixum a carmen",
        "mandale 20 euros a Javi",
        "envia 50 euros a Marta Solis Vega",
    ],
)
def test_a_person_is_still_somebody_to_pay(sentence: str) -> None:
    """The half that must not break: a name is not an account kind, so nothing here reads as a
    transfer between the holder's own accounts."""
    assert payment_router.is_payment(sentence) is True


# --- the past, conjugated rather than remembered ------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        # The persons the hand-written alternation never had: it stopped at "mandé" and "mandaste".
        "le mandamos un bizum de 20 euros a Marta",
        "le mandasteis el bizum a Marta",
        "le enviamos una transferencia de 50 euros",
        # The pluperfect, which had "había" and not "habíamos".
        "le habíamos mandado un bizum a Marta",
        "habíamos hecho una transferencia a Marta",
        # The imperfect, which was absent entirely.
        "el mes pasado le mandaba un bizum a mi hermano",
        # The forms a regular table gets wrong, and would have dropped silently.
        "le transfirió 30 euros por bizum",
        "le pagué el bizum a Javi",
        # Money arriving, in the persons and tenses that list was missing too.
        "me llegaron 50 euros por bizum",
        "el año pasado recibía bizums de Marta",
    ],
)
def test_a_past_form_is_a_question_however_it_is_conjugated(sentence: str) -> None:
    """The one vocabulary where a gap fails towards the dangerous side. A past form nobody
    enumerated is read as an order, and somebody asking what they sent is shown a prompt to send it
    again — so the forms come from the conjugation and not from what a list remembered."""
    assert payment_router.is_payment(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        # The other half: an imperative shares its stem with the past and is still an order.
        "mandale un bizum de 20 euros a Marta",
        "devuelvele los 20 euros a Marta",
        "pagale el bizum a Javi",
        "ingresale 50 euros a Marta",
    ],
)
def test_a_wider_past_does_not_swallow_an_order(sentence: str) -> None:
    """What a stem (``mand\\w*``) would have cost. Completeness has to come from conjugating the
    verb, not from widening the match until it reaches the imperative too."""
    assert payment_router.is_payment(sentence) is True
