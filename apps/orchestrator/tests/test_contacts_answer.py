"""Asking who can be paid.

    "Perdona, ¿podrías decirme qué contactos tengo para mandar bizum?"
    → "¿Para qué contacto te gustaría enviar un bizum?"

The assistant asked for the recipient of a payment nobody had asked to make, because "bizum" is a
payment word and the payment router ran first. The user had asked *who is available* — a list this
service has served on /v1/contacts since it was written, with no model in the path.
"""

import uuid

from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import answer_writer

MARTA_RUIZ = Contact(
    contact_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
    display_name="Marta Ruiz Gomez",
    phone="+34600111222",
)
MARTA_SOLIS = Contact(
    contact_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
    display_name="Marta Solis Vega",
    phone="+34600333444",
)


def test_the_contacts_are_listed_by_name() -> None:
    said = answer_writer.contacts((MARTA_RUIZ, MARTA_SOLIS))

    assert "Marta Ruiz Gomez" in said
    assert "Marta Solis Vega" in said


def test_an_empty_directory_says_so_rather_than_listing_nothing() -> None:
    assert "no tienes" in answer_writer.contacts(()).lower()
