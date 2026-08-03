"""Resolving "hazle un Bizum a Marta" to a person.

Step 10 of the demo shows the recipient before anything is confirmed, so a name has to become a
contact. The property that matters is not the lookup — it is what happens when the name is not
enough: **two Martas must be a question, never a choice.**

Picking one would be the same failure as every other in this project wearing different clothes:
a plausible answer to a question nobody asked, except this one moves money.
"""

import asyncpg
import pytest
from support import as_pool, create_account, create_contact

from finguard_banking.domain.errors import AmbiguousContactError, ContactNotFoundError
from finguard_banking.infrastructure.persistence.contact_repository import (
    PostgresContactRepository,
)

pytestmark = pytest.mark.integration


async def repository(connection: asyncpg.Connection) -> PostgresContactRepository:
    return PostgresContactRepository(as_pool(connection))


async def test_a_name_resolves_to_one_person(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")

    found = await (await repository(connection)).resolve(account, "Marta")

    assert found.display_name == "Marta Ruiz Gomez"
    assert found.phone == "+34600111222"


async def test_capitalisation_does_not_decide_who_gets_paid(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")

    found = await (await repository(connection)).resolve(account, "marta ruiz")

    assert found.phone == "+34600111222"


async def test_two_matches_are_a_question_not_a_choice(connection: asyncpg.Connection) -> None:
    """The one that matters. Choosing between two Martas is a plausible answer to a question
    nobody asked, and this one moves money."""
    account = await create_account(connection)
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")
    await create_contact(connection, account, "Marta Solis Vega", "+34600333444")

    with pytest.raises(AmbiguousContactError) as caught:
        await (await repository(connection)).resolve(account, "Marta")

    assert "Marta Ruiz Gomez" in str(caught.value)
    assert "Marta Solis Vega" in str(caught.value), "the user cannot choose without the names"


async def test_a_name_nobody_has_is_not_found(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")

    with pytest.raises(ContactNotFoundError):
        await (await repository(connection)).resolve(account, "Aitor")


async def test_a_contact_of_another_account_is_not_found(
    connection: asyncpg.Connection,
) -> None:
    """Not "not authorised" — not found, because the caller has no way to learn that the
    contact exists at all. Enforced by row-level security rather than by this query."""
    mine, theirs = await create_account(connection), await create_account(connection)
    await create_contact(connection, theirs, "Marta Ruiz Gomez", "+34600111222")

    with pytest.raises(ContactNotFoundError):
        await (await repository(connection)).resolve(mine, "Marta")


async def test_the_whole_list_comes_back_in_reading_order(connection: asyncpg.Connection) -> None:
    """A payment screen shows who you can pay. Asking somebody to type a name they can only get
    right from a list they have not seen is how money reaches the wrong Marta."""
    account = await create_account(connection)
    for name, phone in (
        ("Marta Solis Vega", "+34600333444"),
        ("Aitor Beltran Diaz", "+34611222333"),
        ("Marta Ruiz Gomez", "+34600111222"),
    ):
        await create_contact(connection, account, name, phone)
    repository = PostgresContactRepository(as_pool(connection))
    await connection.execute("SELECT set_config('app.account_id', $1::text, true)", str(account))

    listed = await repository.all_of(account)

    assert [contact.display_name for contact in listed] == [
        "Aitor Beltran Diaz",
        "Marta Ruiz Gomez",
        "Marta Solis Vega",
    ]


async def test_an_account_with_nobody_to_pay_gets_an_empty_list(
    connection: asyncpg.Connection,
) -> None:
    """Empty is an answer, not a failure. An account with no contacts is a new account."""
    account = await create_account(connection)

    assert await PostgresContactRepository(as_pool(connection)).all_of(account) == ()


async def test_the_list_is_only_this_account_s(connection: asyncpg.Connection) -> None:
    """The same containment every other read has, and worth pinning here because this list is what
    a payment screen offers to send money to."""
    mine = await create_account(connection)
    theirs = await create_account(connection)
    await create_contact(connection, mine, "Marta Solis Vega", "+34600333444")
    await create_contact(connection, theirs, "Elena Prat Mas", "+34666777888")
    repository = PostgresContactRepository(as_pool(connection))

    listed = await repository.all_of(mine)

    assert [contact.display_name for contact in listed] == ["Marta Solis Vega"]
