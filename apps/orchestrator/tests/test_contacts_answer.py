"""Asking who can be paid.

    "Perdona, ¿podrías decirme qué contactos tengo para mandar bizum?"
    → "¿Para qué contacto te gustaría enviar un bizum?"

The assistant asked for the recipient of a payment nobody had asked to make, because "bizum" is a
payment word and the payment router ran first. The user had asked *who is available* — a list this
service has served on /v1/contacts since it was written, with no model in the path.

The names travel as data as well as in the sentence. A client that has to parse "Marta Ruiz Gomez,
Marta Solis Vega" out of a paragraph to draw two buttons is a client that will get it wrong, and the
identifiers are what let a later payment name a contact instead of a string.
"""

import uuid
from datetime import date

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, Spoken
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

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


class FakeContacts:
    def __init__(self, *found: Contact, failure: Exception | None = None) -> None:
        self._found = found
        self._failure = failure
        self.asked_for: uuid.UUID | None = None

    async def all_of(self, account_id: uuid.UUID) -> tuple[Contact, ...]:
        self.asked_for = account_id
        if self._failure is not None:
            raise self._failure
        return self._found


class FakePlanner:
    def __init__(self) -> None:
        self.asked: str | None = None

    async def plan(
        self, sentence: str, *, today: date, previous: FinancialPlan | None = None
    ) -> PlanningResult:
        self.asked = sentence
        return PlanningResult.planned(
            FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(period=Period.TODAY))
        )


class FakeLedger:
    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid"):
        raise AssertionError("a question about contacts reached the ledger")


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("a question about contacts reached the balance")


def handler_with(contacts: FakeContacts) -> tuple[FakePlanner, AskFinancialQuestionHandler]:
    planner = FakePlanner()
    return planner, AskFinancialQuestionHandler(
        planner, FakeLedger(), accounts=FakeAccounts(), contacts=contacts
    )


async def ask(sentence: str, contacts: FakeContacts, **extra: object):
    planner, handler = handler_with(contacts)
    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            **extra,  # type: ignore[arg-type]
        )
    )
    return planner, result


async def test_the_contacts_are_listed_without_a_model() -> None:
    contacts = FakeContacts(MARTA_RUIZ, MARTA_SOLIS)

    planner, result = await ask(
        "Perdona, ¿podrías decirme qué contactos tengo para mandar bizum?", contacts
    )

    assert planner.asked is None
    assert contacts.asked_for == ACCOUNT
    assert result.outcome == Spoken.CONTACTS.value
    assert "Marta Ruiz Gomez" in result.text
    assert "Marta Solis Vega" in result.text


async def test_the_names_travel_as_data_too() -> None:
    """So a client can draw a button per contact and send the identifier back, instead of parsing
    names out of a paragraph and re-interpreting "Marta Vega" as free text."""
    _, result = await ask("¿qué contactos tengo?", FakeContacts(MARTA_RUIZ, MARTA_SOLIS))

    assert [c.display_name for c in result.contacts] == ["Marta Ruiz Gomez", "Marta Solis Vega"]
    assert result.contacts[0].contact_id == str(MARTA_RUIZ.contact_id)


async def test_an_empty_directory_says_so_rather_than_listing_nothing() -> None:
    _, result = await ask("¿qué contactos tengo?", FakeContacts())

    assert result.contacts == []
    assert "no tienes" in result.text.lower()


async def test_a_bank_that_will_not_answer_does_not_invent_a_list() -> None:
    _, result = await ask(
        "¿qué contactos tengo?", FakeContacts(failure=BankingUnavailableError("x"))
    )

    assert result.contacts == []
    assert result.outcome != Spoken.CONTACTS.value


async def test_asking_for_contacts_does_not_erase_the_previous_plan() -> None:
    """Same rule as the balance: a turn, not a reset."""
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("GROCERIES",),
    )

    _, result = await ask(
        "¿qué contactos tengo?",
        FakeContacts(MARTA_RUIZ),
        conversation=Conversation(last_plan=previous),
    )

    assert result.conversation.last_plan is not None
    assert result.conversation.last_plan.period.period is Period.LAST_MONTH


async def test_a_real_payment_order_is_untouched() -> None:
    """The routing reversal that makes this work — contacts before payments — must not cost a
    payment. This is the assertion that keeps it honest at the pipeline level."""
    contacts = FakeContacts(MARTA_SOLIS)
    _, handler = handler_with(contacts)

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="hazle un bizum de 20 euros a Marta Solis",
            account_id=ACCOUNT,
            today=TODAY,
        )
    )

    assert contacts.asked_for is None
    assert result.outcome != Spoken.CONTACTS.value
