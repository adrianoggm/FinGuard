"""The sentences the payment conversation says, in Spanish.

Templates, not a model. The figures come from banking and the words come from here, so the number in
front of the user is the number the ledger holds — a model asked to phrase a receipt is a model that
can round one.

Spanish because it reaches the user, which is the one exception the English-everywhere rule makes.
"""

from finguard_orchestrator.domain.enums import Rail
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
    verb = "mover" if preview.rail is Rail.INTERNAL else "enviar"
    return (
        f"Vas a {verb} {money(preview.amount_minor, preview.currency)} a "
        f"{preview.recipient}{_where(preview)}{for_what}. "
        f"Te quedarían {money(preview.balance_after_minor, preview.currency)}. "
        f"¿Confirmas? Responde sí o no."
    )


def _where(preview: PaymentPreview) -> str:
    """The destination beside the name, spelled per rail.

    A Bizum repeats the phone, so a name that matched the wrong person can be caught by whoever
    knows the number. A transfer repeats the account, whole, because that is the thing being
    recognised. An internal transfer says nothing extra: the name *is* the account, and the holder
    named it themselves.

    Per rail rather than "the identifier that is not None". That version printed "(None)" on every
    transfer — a real defect this rail exposed rather than caused — because the phone was read
    unconditionally and a transfer has no phone.
    """
    if preview.rail is Rail.BIZUM:
        return f" ({preview.recipient_phone})"
    if preview.rail is Rail.TRANSFER and preview.recipient_iban:
        return f", a la cuenta {preview.recipient_iban}"
    return ""


def receipt(paid: PaymentReceipt) -> str:
    moved = "movidos" if paid.rail is Rail.INTERNAL else "enviados"
    return (
        f"Hecho: {money(paid.amount_minor, paid.currency)} {moved} a {paid.recipient}. "
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


def unclear_pending(recipient: str, amount_minor: int, currency: str) -> str:
    """Repeat the material facts when chat state has the intent but not banking's full preview.

    The user may have answered with a question because they no longer remember what was on the
    table. A generic "¿confirmas el pago?" makes the next yes unsafe and the conversation feel
    stuck.
    """
    return (
        "No estoy seguro de haberte entendido. "
        f"El pago pendiente es de {money(amount_minor, currency)} a {recipient}. "
        "¿Quieres enviarlo? Responde sí o no."
    )


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


def unsure(draft: PaymentDraft) -> str:
    """A useful answer when the user cannot fill the field the draft is waiting for."""
    if PaymentField.RECIPIENT in draft.missing:
        return f"No pasa nada. Puedo enseñarte tus contactos si quieres. {still_missing(draft)}"
    return f"No pasa nada. {still_missing(draft)} También puedes cancelar el bizum."


def paused(draft: PaymentDraft) -> str:
    """A slot value arrived after the payment had stepped aside for another conversation.

    Repeat what is settled, but do not treat the new value as consent to resume. The explicit
    phrase is deliberately stable so a button or a person can send exactly the same instruction.
    """
    if draft.recipient_name:
        payment = f"El Bizum a {draft.recipient_name}"
    elif draft.amount_minor is not None:
        payment = f"El Bizum de {money(draft.amount_minor, draft.currency)}"
    else:
        payment = "El Bizum"
    return (
        f"{payment} está pausado. Para retomarlo, escribe «continuar con el Bizum»; "
        "para descartarlo, responde «no»."
    )


def ambiguous(name: str, candidates: tuple[str, ...]) -> str:
    """Two people answer to that name, so the user picks. Choosing for them would be the assistant
    deciding who gets the money.

    Used where the name is known to be a name — banking's own refusal carries the fragment it
    matched on. When the only thing to hand is the sentence, `several_candidates` is the one to use.
    """
    return f"Tienes varios contactos que se llaman {name}: {', '.join(candidates)}. ¿A quién?"


def several_candidates(candidates: tuple[str, ...]) -> str:
    """The same question when the matched fragment is not separable from the sentence.

    The first version passed the whole sentence in as the name, and the user read "tienes
    varios contactos que se llaman Vale que te parece si le hacemos un bizum a Marta". Beyond
    reading badly, echoing a sentence back into an answer is a habit worth not having.
    """
    return f"Tienes varios contactos con ese nombre: {', '.join(candidates)}. ¿A quién se lo envío?"


def not_a_contact(name: str) -> str:
    return f"No tengo a {name} en tus contactos, así que no puedo enviarle nada."


def no_account_for(name: str) -> str:
    """A transfer to somebody whose account this bank does not have saved.

    It names what is missing and what to do about it, because both halves are what the user can
    act on. Offering a Bizum is not a consolation: it is the rail that works for this person right
    now, and the holder came here to move money rather than to choose a rail.
    """
    return (
        f"Tengo a {name} en tus contactos, pero no su número de cuenta, así que no puedo hacerle "
        f"una transferencia. Puedes darme la cuenta, o hacerle un bizum."
    )


def not_a_contact_chosen() -> str:
    """A picked contact that is not in the account's list.

    No name in it, because there is nothing the user typed to quote back — they tapped something,
    and either the list has changed underneath them or the identifier did not come from us. Asking
    them to say who, rather than reporting on an identifier they never saw.
    """
    return "Ese contacto ya no está en tu lista. Dime a quién quieres enviarle el dinero."


def insufficient_funds() -> str:
    return "No he enviado nada: tu saldo no cubre ese pago."


def nothing_was_sent() -> str:
    """The bank refused the request itself, so no intent was ever pending.

    Its own sentence because the one below is a claim: "ya no está pendiente" says a payment
    existed and has been dealt with, and sending somebody to check their movements for a payment
    that was never made is a wrong answer about money in the direction that alarms people. This
    covers a reused idempotency key and any refusal this service does not have a name for, and both
    mean the same two things to whoever is reading — nothing moved, and trying again works.
    """
    return "No he enviado nada. Vuelve a intentarlo."


def no_longer_live() -> str:
    """Expired, cancelled, or already paid — one sentence, because they mean the same thing to
    whoever is looking at the preview. Notably it does not say which: "ya lo has pagado" invites
    checking, and the honest instruction is the same either way."""
    return "Ese pago ya no está pendiente. Comprueba tus movimientos antes de volver a intentarlo."


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


def confirmation_unavailable() -> str:
    """The bank failed after confirmation started, when execution may already have happened."""
    return (
        "No he podido comprobar si el banco completó el pago. No crees otro bizum: "
        "puedes reintentar esta confirmación sin que se ejecute dos veces."
    )


def cancellation_unavailable() -> str:
    """The bank failed while cancelling, so whether the intent is still live is unknown."""
    return (
        "No he podido comprobar si el banco canceló el pago. No crees otro bizum: "
        "puedes volver a responder no para reintentar la cancelación."
    )


def cannot_pay() -> str:
    """For a credential without the payment scope, or a deployment that has not turned payments on.

    The same sentence for both, because the difference is ours: the user did not choose their
    credential and cannot act on which of the two it was.
    """
    return "No puedo hacer pagos con esta cuenta. Puedo consultar tus movimientos y tus gastos."
