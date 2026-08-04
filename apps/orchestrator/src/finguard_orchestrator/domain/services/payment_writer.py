"""The sentences the payment conversation says, in Spanish.

Templates, not a model. The figures come from banking and the words come from here, so the number in
front of the user is the number the ledger holds — a model asked to phrase a receipt is a model that
can round one.

Spanish because it reaches the user, which is the one exception the English-everywhere rule makes.
"""

from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.services.answer_writer import money
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField


def confirm_question(preview: PaymentPreview, concept: str | None = None) -> str:
    """The confirmation prompt: who, how much, what it leaves, and what it is for.

    The first three because a prompt without the resulting balance asks somebody to
    approve a payment without telling them what it costs, and the phone number so a name
    that matched the wrong person can be caught by whoever knows the number.

    The concept is here because this is the moment the user says yes. A note they wrote and cannot
    see at that point is a field they cannot check — and it is going into the ledger with their name
    on it.
    """
    for_what = f' con el concepto "{concept}"' if concept else ""
    return (
        f"Vas a enviar {money(preview.amount_minor, preview.currency)} a "
        f"{preview.recipient} ({preview.recipient_phone}){for_what}. "
        f"Te quedarían {money(preview.balance_after_minor, preview.currency)}. "
        f"¿Confirmas? Responde sí o no."
    )


def receipt(paid: PaymentReceipt) -> str:
    return (
        f"Hecho: {money(paid.amount_minor, paid.currency)} enviados a {paid.recipient}. "
        f"Te quedan {money(paid.balance_after_minor, paid.currency)}."
    )


def cancelled() -> str:
    return "Vale, no he enviado nada."


def unclear(preview: PaymentPreview | None = None) -> str:
    """Neither a yes nor a no. The payment stays where it is and the question is asked again.

    With the preview to hand it is repeated, because a reply that changed the amount means the user
    is looking at a different payment from the one on the table.
    """
    if preview is not None:
        return f"No estoy seguro de haberte entendido. {confirm_question(preview)}"
    return "No estoy seguro de haberte entendido. ¿Confirmas el pago? Responde sí o no."


def still_missing(draft: PaymentDraft) -> str:
    """The question a half-built payment needs answered.

    Names the slot and, when it has one, the part already settled: "¿de cuánto quieres hacerle el
    bizum a Marta?" tells the user both what to type and that the assistant did not lose Marta.
    """
    if PaymentField.RECIPIENT in draft.missing:
        if draft.amount_minor is not None:
            amount = money(draft.amount_minor, draft.currency)
            return f"¿A quién le quieres enviar {amount}?"
        return "¿A quién le quieres enviar el dinero?"
    to = f" a {draft.recipient_name}" if draft.recipient_name else ""
    return f"¿De cuánto quieres hacerle el bizum{to}?"


def ambiguous(name: str, candidates: tuple[str, ...]) -> str:
    """Two people answer to that name, so the user picks. Choosing for them would be the assistant
    deciding who gets the money."""
    return f"Tienes varios contactos que se llaman {name}: {', '.join(candidates)}. ¿A quién?"


def not_a_contact(name: str) -> str:
    return f"No tengo a {name} en tus contactos, así que no puedo enviarle nada."


def insufficient_funds() -> str:
    return "No he enviado nada: tu saldo no cubre ese pago."


def no_longer_live() -> str:
    """Expired, cancelled, or already paid — one sentence, because they mean the same thing to
    whoever is looking at the preview. Notably it does not say which: "ya lo has pagado" invites
    checking, and the honest instruction is the same either way."""
    return "Ese pago ya no está pendiente. Si quieres hacerlo, dímelo otra vez."


def unsupported() -> str:
    return (
        "Solo puedo enviar dinero a personas de tus contactos. "
        "Dime a quién y cuánto, por ejemplo: hazle un bizum de 20 euros a Marta."
    )


def not_understood() -> str:
    """A malformed reading and an ungrounded one get the same sentence. The difference matters to us
    and not to the user, and naming it would be reporting on ourselves rather than helping."""
    return "No he entendido el pago. ¿Puedes decírmelo otra vez, con la cantidad en cifras?"


def provider_error() -> str:
    return "No he podido procesar el pago ahora mismo. Vuelve a intentarlo."


def bank_unavailable() -> str:
    return "No puedo hablar con el banco ahora mismo, así que no he enviado nada."


def cannot_pay() -> str:
    """For a credential without the payment scope, or a deployment that has not turned payments on.

    The same sentence for both, because the difference is ours: the user did not choose their
    credential and cannot act on which of the two it was.
    """
    return "No puedo hacer pagos con esta cuenta. Puedo consultar tus movimientos y tus gastos."
