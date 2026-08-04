"""Paying the person the user picked, rather than the name they typed.

Two contacts called Marta is a question — `test_contacts.py` is about never answering it ourselves —
and once the user has answered it, sending the *name* back means resolving it again. In the reported
conversation that round trip was three turns: "Marta Vega" matched both Martas on the first name, so
the reply that resolved the ambiguity reported it again.

An identifier ends that, and it moves the trust boundary rather than removing it. The docstring on
`PreparePaymentCommand` said an identifier "would let a caller pay somebody who is not in their
list", and that is true of an identifier taken on faith. This one is resolved through the same
account-scoped view as a name: row-level security means a contact of another account does not
exist, so a forged id is `ContactNotFoundError` — the same answer as a typo, which is all a caller
probing for other people's contacts should be able to learn.
"""

import uuid

import asyncpg
import pytest
from support import as_pool, create_account, create_contact

from finguard_banking.application.commands.payments import PreparePaymentCommand
from finguard_banking.domain.errors import ContactNotFoundError, InvalidValueError
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.contact_repository import (
    PostgresContactRepository,
)

EUR = "EUR"


# --- the command's own invariant ------------------------------------------------------


def test_a_payment_names_the_recipient_exactly_one_way() -> None:
    """Both would be two answers to "who gets paid" and no rule about which wins. Neither is a
    payment to nobody. Refused here because a command that can be built wrong is a decision
    deferred to whichever handler reads the fields in which order."""
    with pytest.raises(InvalidValueError):
        PreparePaymentCommand(
            account_id=uuid.uuid4(),
            amount=Money(15_00, EUR),
            idempotency_key="k",
            recipient_name="Marta",
            recipient_contact_id=uuid.uuid4(),
        )

    with pytest.raises(InvalidValueError):
        PreparePaymentCommand(
            account_id=uuid.uuid4(), amount=Money(15_00, EUR), idempotency_key="k"
        )


# --- the directory, against the database ---------------------------------------------


@pytest.mark.integration
async def test_an_identifier_resolves_to_that_contact(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")
    chosen = await create_contact(connection, account, "Marta Solis Vega", "+34600333444")
    directory = PostgresContactRepository(as_pool(connection))

    found = await directory.one(account, chosen)

    assert found.display_name == "Marta Solis Vega"
    assert found.phone == "+34600333444"


@pytest.mark.integration
async def test_two_contacts_with_the_same_name_are_told_apart(
    connection: asyncpg.Connection,
) -> None:
    """The case a name cannot answer at all. `resolve` is right to refuse it forever; an identifier
    is the only thing that ever could, and this is why the choice is sent as one."""
    account = await create_account(connection)
    first = await create_contact(connection, account, "Marta Solis Vega", "+34600111222")
    second = await create_contact(connection, account, "Marta Solis Vega", "+34600333444")
    directory = PostgresContactRepository(as_pool(connection))

    assert (await directory.one(account, first)).phone == "+34600111222"
    assert (await directory.one(account, second)).phone == "+34600333444"


@pytest.mark.integration
async def test_somebody_elses_contact_does_not_exist(connection: asyncpg.Connection) -> None:
    """The security property, and the reason the id is resolved rather than trusted. Not
    "forbidden": a caller who can tell "not yours" from "not real" can enumerate other people's
    address books."""
    mine = await create_account(connection)
    theirs = await create_account(connection)
    hidden = await create_contact(connection, theirs, "Marta Solis Vega", "+34600333444")
    directory = PostgresContactRepository(as_pool(connection))

    with pytest.raises(ContactNotFoundError):
        await directory.one(mine, hidden)


@pytest.mark.integration
async def test_an_invented_identifier_does_not_exist(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    directory = PostgresContactRepository(as_pool(connection))

    with pytest.raises(ContactNotFoundError):
        await directory.one(account, uuid.uuid4())
