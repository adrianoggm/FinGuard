"""Somebody who does not ask the same kind of question twice in a row.

The other conversation files walk one shape each: a ranking, a Bizum, a clarification, a recovery.
Nobody talks like that. A real turn is an aside, a correction, a different account, a question about
the assistant, and then — four turns later — "¿y en gasolina?", which only means anything if
everything in between kept its hands off the plan.

So this file is about **what a turn is allowed to touch**. There are nine kinds of turn and they
divide into three groups:

    answered from here      identity, the category catalogue, the explanation of the last answer
    answered from banking   a balance, a contact list, a transfer read out of a sentence
    answered from a plan    an aggregate, a ranking, and the corrections and clarifications of one

Only the third group may change what a follow-up inherits. The first two are asides — the user did
not stop asking about last month by asking who they can pay — and the whole class of defect here is
an aside that resets the conversation, or a plan that survives something that should have ended it.

Each test is a conversation rather than a call, and asserts on the plan that reached the ledger
rather than on prose: prose is written by a template and a template does not answer the question
"which figures did this produce".

The planner is scripted per sentence and **raises on anything it was not given**, which is half the
value. A deterministic turn that quietly reached the model shows up as a failure here rather than as
a slightly different answer on a machine with a different model installed.
"""

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
    Spoken,
)
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import query_recovery
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
FUND = uuid.UUID("33333333-3333-3333-3333-333333333333")
TODAY = date(2026, 8, 4)

LAST_MONTH = RelativePeriod(period=Period.LAST_MONTH)
THIS_YEAR = RelativePeriod(period=Period.THIS_YEAR)
JULY = AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31))

CURRENT_ACCOUNT = Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042)
SAVINGS = Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550)
INVESTMENT = Holding(FUND, "ASSET", "Fondo indexado", "EUR", 270_000)

MARTA = Contact(uuid.UUID(int=9), "Marta Solis Vega", "+34600333444")


def spending(**over: object) -> FinancialPlan:
    """An ordinary expense question, with whichever field the test is about replaced."""
    return replace(
        FinancialPlan(direction=Direction.EXPENSE, period=LAST_MONTH, categories=("GROCERIES",)),
        **over,  # type: ignore[arg-type]
    )


FUEL_RANKING = TopMovements(
    direction=Direction.EXPENSE, period=THIS_YEAR, categories=("TRANSPORT_FUEL",)
)

SOMETHING = QueryAnswer(
    currency="EUR",
    total_minor=46_556,
    movement_count=3,
    groups=(Group("total", 46_556, 3),),
)
NOTHING = QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
PEOPLE = QueryAnswer(
    currency="EUR",
    total_minor=7_822,
    movement_count=3,
    groups=(Group("Elena", 4_656, 2), Group("Sergio", 3_166, 1)),
)

A_REFUEL = Movement(
    occurred_at=datetime(2026, 3, 14, 10, 0),
    description="Repostaje",
    amount_minor=8_240,
    direction="EXPENSE",
    merchant_name="Cepsa",
    category="TRANSPORT_FUEL",
    currency="EUR",
)


class Planner:
    """One plan per sentence fragment, and it raises on anything it was not given.

    Raising is the assertion. Half of these turns are answered without a model — a balance, an
    aside, an ordinal, a relaxation — and a turn that quietly started reaching one would otherwise
    pass here and behave differently on a machine with a different model installed.
    """

    def __init__(self, script: dict[str, PlanningResult]) -> None:
        self._script = script
        self.asked: list[str] = []
        self.inherited: list[FinancialPlan | TopMovements | None] = []

    async def plan(self, sentence: str, *, previous=None, **_: object) -> PlanningResult:  # type: ignore[no-untyped-def]
        self.asked.append(sentence)
        self.inherited.append(previous)
        for fragment, result in self._script.items():
            if fragment in sentence.lower():
                return result
        raise AssertionError(f"the planner was asked about {sentence!r}, which nothing scripted")


class Ledger:
    def __init__(self, answer: QueryAnswer = SOMETHING, *, down: bool = False) -> None:
        self.answer = answer
        self.down = down
        self.plans: list[FinancialPlan] = []
        self.rankings: list[TopMovements] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        if self.down:
            raise BankingUnavailableError("the bank did not answer")
        return self.answer

    async def ranked(self, ranking: TopMovements, **_: object) -> Movement | None:
        self.rankings.append(ranking)
        return A_REFUEL


class Accounts:
    def __init__(self, *holdings: Holding, down: bool = False) -> None:
        self._holdings = holdings or (CURRENT_ACCOUNT, SAVINGS, INVESTMENT)
        self.down = down
        self.asked = 0

    async def of(self, *_: object, **__: object) -> AccountSummary:
        self.asked += 1
        if self.down:
            raise BankingUnavailableError("no accounts")
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 4, 9, 30, tzinfo=UTC),
            holdings=self._holdings,
        )


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[Contact, ...]:
        return (MARTA,)


@pytest.fixture(autouse=True)
def _restore():  # type: ignore[no-untyped-def]
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def wired(
    script: dict[str, PlanningResult],
    answer: QueryAnswer = SOMETHING,
    *,
    accounts: Accounts | None = None,
    down: bool = False,
):  # type: ignore[no-untyped-def]
    planner = Planner(script)
    ledger = Ledger(answer, down=down)
    bank = accounts or Accounts()
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=bank,  # type: ignore[arg-type]
        contacts=Contacts(),
        cards=NoCards(),  # type: ignore[arg-type]
    )
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[get_command_bus] = lambda: CommandBus()
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = False
    return TestClient(app), planner, ledger, bank


class Chat:
    """A client that holds the conversation the way the browser does: store it, send it back."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.conversation: dict | None = None

    def say(self, question: str) -> dict:
        response = self._client.post(
            "/v1/questions", json={"question": question, "conversation": self.conversation}
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        self.conversation = answer["conversation"]
        return answer


def test_two_safe_reads_in_one_turn_are_both_answered_and_auditable() -> None:
    client, planner, ledger, accounts = wired(
        {
            "gaste": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            )
        }
    )

    # Keep this boundary test ASCII: the currently pinned TestClient transport corrupts Unicode
    # JSON on Windows. Unicode clause recognition is covered directly in test_turn_analyzer.py.
    answer = Chat(client).say("Cuanto tengo y cuanto gaste este mes?")

    assert answer["outcome"] == "COMPOUND"
    assert [part["outcome"] for part in answer["parts"]] == [
        Spoken.BALANCE.value,
        PlanningOutcome.PLANNED.value,
    ]
    assert all(part["conversation"] is None for part in answer["parts"])
    assert answer["parts"][1]["plan"] is not None
    assert answer["conversation"]["last_plan"]["period"] == Period.THIS_MONTH.value
    assert planner.asked == ["cuanto gaste este mes"]
    assert accounts.asked == 1
    assert len(ledger.plans) == 1


def test_a_shared_trailing_window_seeds_the_other_financial_clause() -> None:
    client, planner, ledger, _ = wired(
        {
            "gaste": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            ),
            "ingrese": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.INCOME,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            ),
        }
    )

    answer = Chat(client).say("Cuanto gaste y cuanto ingrese este mes?")

    assert answer["outcome"] == "COMPOUND"
    assert len(answer["parts"]) == 2
    assert planner.asked[0] == "cuanto ingrese este mes"
    assert [plan.direction for plan in ledger.plans] == [Direction.INCOME, Direction.EXPENSE]


def test_compound_clauses_do_not_leak_filters_into_each_other() -> None:
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                    categories=("GROCERIES",),
                )
            ),
            "ingrese": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.INCOME,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            ),
        }
    )

    answer = Chat(client).say("Cuanto gaste en supermercado este mes y cuanto ingrese este mes?")

    assert answer["outcome"] == "COMPOUND"
    assert [plan.categories for plan in ledger.plans] == [("GROCERIES",), ()]


def test_a_trailing_window_is_shared_without_sharing_its_category() -> None:
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                    categories=("GROCERIES",),
                )
            ),
            "ingrese": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.INCOME,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            ),
        }
    )

    answer = Chat(client).say("Cuanto ingrese y cuanto gaste en supermercado este mes?")

    assert answer["outcome"] == "COMPOUND"
    assert [plan.categories for plan in ledger.plans] == [("GROCERIES",), ()]
    assert answer["conversation"]["last_plan"]["direction"] == Direction.EXPENSE.value


def test_safe_compound_reads_have_the_same_shape_when_streamed() -> None:
    client, _, _, _ = wired(
        {
            "gaste": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            )
        }
    )

    response = client.post(
        "/v1/questions/stream", json={"question": "Cuanto tengo y cuanto gaste este mes?"}
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert len(events) == 1
    assert events[0]["type"] == "answer"
    assert events[0]["outcome"] == "COMPOUND"
    assert len(events[0]["parts"]) == 2


def test_a_compound_containing_a_payment_still_asks_the_user_to_choose() -> None:
    client, _, ledger, _ = wired({})

    answer = Chat(client).say("Haz un bizum a Marta y cuanto gaste este mes")

    assert answer["outcome"] == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert answer["parts"] == []
    assert len(answer["suggestions"]) == 2
    assert ledger.plans == []


def test_a_typed_net_cash_flow_can_share_a_turn_with_the_balance() -> None:
    client, planner, ledger, accounts = wired({})

    answer = Chat(client).say("Cual fue mi flujo neto este mes y cuanto tengo?")

    assert answer["outcome"] == "COMPOUND"
    assert [part["outcome"] for part in answer["parts"]] == [
        PlanningOutcome.PLANNED.value,
        Spoken.BALANCE.value,
    ]
    assert [plan.direction for plan in ledger.plans] == [Direction.EXPENSE, Direction.INCOME]
    assert answer["conversation"]["last_ratio_kind"] == "NET_CASH_FLOW"
    assert answer["conversation"]["answered_the_balance"] is True
    assert planner.asked == []
    assert accounts.asked == 1


def test_a_comparison_with_and_inside_it_remains_one_compound_part() -> None:
    client, planner, ledger, _ = wired({})

    answer = Chat(client).say("Compara mis gastos de junio y julio y dime cuanto tengo")

    assert answer["outcome"] == "COMPOUND"
    assert len(answer["parts"]) == 2
    assert len(ledger.plans) == 2
    assert answer["conversation"]["last_comparison_earlier"] is not None
    assert answer["conversation"]["answered_the_balance"] is True
    assert planner.asked == []


def test_a_comparison_does_not_lend_one_of_its_two_windows_to_an_abbreviation() -> None:
    client, planner, ledger, _ = wired({})

    answer = Chat(client).say("Compara mis gastos de junio y julio y cuanto gaste en supermercados")

    assert answer["outcome"] == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert len(answer["suggestions"]) == 2
    assert ledger.plans == []
    assert planner.asked == []


def test_a_savings_rate_shares_only_its_single_window_with_the_next_read() -> None:
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                    categories=("GROCERIES",),
                )
            )
        }
    )

    answer = Chat(client).say(
        "Cual fue mi tasa de ahorro este mes y cuanto gaste en supermercados?"
    )

    assert answer["outcome"] == "COMPOUND"
    assert len(answer["parts"]) == 2
    assert [plan.direction for plan in ledger.plans] == [
        Direction.EXPENSE,
        Direction.INCOME,
        Direction.EXPENSE,
    ]
    assert ledger.plans[-1].period == RelativePeriod(Period.THIS_MONTH)
    assert ledger.plans[-1].categories == ("GROCERIES",)


# =========================================================================================
# An aside is an aside. Three kinds of turn are answered from here and none of them may
# change what the next question inherits.
# =========================================================================================


def test_asking_who_you_are_costs_nothing_of_the_question_before_it() -> None:
    """ "¿Quién eres?" used to reach the planner and come back as a refusal about averages. It is
    answered from a constant now — and the point here is the *third* turn: the follow-up still
    inherits the window of the first, because nothing in between was allowed to touch it."""
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    aside = chat.say("¿quién eres?")
    chat.say("¿y en gasolina?")

    assert aside["outcome"] == Spoken.IDENTITY.value
    assert len(ledger.plans) == 2, "the aside asked the ledger something"
    assert ledger.plans[1].period == LAST_MONTH


def test_the_category_catalogue_is_product_data_and_leaves_the_conversation_alone() -> None:
    client, planner, ledger, _ = wired({"supermercado": PlanningResult.planned(spending())})
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    asked = len(planner.asked)
    listed = chat.say("dime las categorías disponibles")

    assert listed["outcome"] == Spoken.CATEGORIES.value
    assert len(planner.asked) == asked, "the catalogue reached the model"
    assert listed["conversation"]["last_plan"]["categories"] == ["GROCERIES"]


def test_the_catalogue_can_be_consulted_in_the_middle_of_a_clarification() -> None:
    """The realistic reason to ask for it: the assistant asked which category and the user does not
    know the words. Reading the list must not close the question that prompted it."""
    client, _, _, _ = wired({"llevo gastado": HALF_READ})
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    listed = chat.say("dime las categorías disponibles")

    assert listed["outcome"] == Spoken.CATEGORIES.value
    assert listed["conversation"]["awaiting"] == "WINDOW"


def test_the_explanation_reads_the_last_plan_and_never_the_ledger() -> None:
    """Asking why we answered something is a fact we hold. Sending it to a model would be asking it
    to guess at our own reasoning, and sending it to the ledger would answer a different question
    with a real figure."""
    client, planner, ledger, _ = wired({"supermercado": PlanningResult.planned(spending())})
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    asked, read = len(planner.asked), len(ledger.plans)
    why = chat.say("¿de dónde sale ese número?")

    assert why["outcome"] == Spoken.EXPLANATION.value
    assert (len(planner.asked), len(ledger.plans)) == (asked, read)
    assert why["plan"], "the explanation did not say which query it is about"


def test_the_same_words_are_not_an_explanation_before_there_is_anything_to_explain() -> None:
    """The intent is gated on there being a previous answer, and that gate is what "conversation"
    means: the same sentence is about the last turn on turn two and about nothing on turn one."""
    client, _, ledger, _ = wired({"de dónde sale": PlanningResult.planned(spending(categories=()))})

    first = Chat(client).say("¿de dónde sale ese número?")

    assert first["outcome"] != Spoken.EXPLANATION.value
    assert ledger.plans, "with nothing behind it, the sentence is an ordinary question"


def test_the_contact_list_is_an_aside_too_and_keeps_the_plan_behind_it() -> None:
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    listed = chat.say("¿a quién puedo hacerle un bizum?")
    chat.say("¿y en gasolina?")

    assert listed["outcome"] == Spoken.CONTACTS.value
    assert [c["display_name"] for c in listed["contacts"]] == ["Marta Solis Vega"]
    assert ledger.plans[1].period == LAST_MONTH


def test_a_balance_in_the_middle_of_an_analysis_does_not_end_it() -> None:
    """A balance is a state and a spending question is a sum over a window, and the review's first
    reported conversation was the two being confused. This is the conversational half: asking one
    must not cost the other."""
    client, _, ledger, bank = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    balance = chat.say("¿cuánto dinero tengo?")
    chat.say("¿y en gasolina?")

    assert balance["outcome"] == Spoken.BALANCE.value
    assert bank.asked >= 1
    assert len(ledger.plans) == 2, "the balance question ran a query"
    assert ledger.plans[1].period == LAST_MONTH


def test_counting_movements_is_not_a_balance_however_much_it_says_tengo() -> None:
    """The exclusion that keeps the balance router narrow. "¿Cuántos movimientos tengo?" is a
    question the planner answers well, and a balance would be a real figure about something else."""
    counting = spending(metric=Metric.COUNT, categories=())
    client, _, ledger, _ = wired({"movimientos": PlanningResult.planned(counting)})

    answer = Chat(client).say("¿cuántos movimientos tengo este mes?")

    assert answer["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[0].metric is Metric.COUNT


def test_a_balance_question_answers_even_when_the_ledger_is_down() -> None:
    """Different ports, different failures. The balance comes from the account summary, so a query
    path that cannot reach banking says nothing about whether this one can."""
    client, _, _, _ = wired({}, down=True)

    answer = Chat(client).say("¿cuánto dinero tengo?")

    assert answer["outcome"] == Spoken.BALANCE.value
    assert answer["total_minor"] is not None


def test_a_balance_nobody_could_read_is_never_a_zero() -> None:
    """The one figure that must not be invented. "Tienes 0,00 €" for "we could not ask" is the
    worst answer a bank can give, so nothing numeric goes out at all."""
    client, _, _, _ = wired({}, accounts=Accounts(down=True))

    answer = Chat(client).say("¿cuánto dinero tengo?")

    assert answer["outcome"] == PlanningOutcome.PROVIDER_ERROR.value
    assert answer["total_minor"] is None


# =========================================================================================
# Changing the subject of the question itself: which field moves, and which stays.
# =========================================================================================


def test_changing_the_category_keeps_the_window_that_was_never_mentioned_again() -> None:
    client, planner, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    chat.say("¿y en gasolina?")

    assert len(planner.inherited) == 1, "a closed-vocabulary filter should not need the model"
    assert ledger.plans[1].categories == ("TRANSPORT_FUEL",)
    assert ledger.plans[1].period == LAST_MONTH


def test_changing_the_window_keeps_the_category_that_was_never_mentioned_again() -> None:
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "este año": PlanningResult.planned(spending(period=THIS_YEAR)),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    chat.say("¿y este año?")

    assert ledger.plans[1].categories == ("GROCERIES",)
    assert ledger.plans[1].period == THIS_YEAR


def test_the_direction_can_be_flipped_without_losing_the_window() -> None:
    """Income and expenses are the same question about opposite halves of the ledger, and a
    follow-up that flips it is ordinary — "¿y cuánto ingresé?" after a month of spending."""
    client, _, ledger, _ = wired(
        {
            "gasté": PlanningResult.planned(spending(categories=())),
            "ingresé": PlanningResult.planned(spending(categories=(), direction=Direction.INCOME)),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado?")
    chat.say("¿y cuánto ingresé?")

    assert ledger.plans[1].direction is Direction.INCOME
    assert ledger.plans[1].period == LAST_MONTH


def test_asking_how_many_instead_of_how_much_changes_the_metric_and_nothing_else() -> None:
    client, _, ledger, _ = wired(
        {
            "cuánto gasté": PlanningResult.planned(spending()),
            "cuántas veces": PlanningResult.planned(spending(metric=Metric.COUNT)),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    chat.say("¿y cuántas veces?")

    assert ledger.plans[1].metric is Metric.COUNT
    assert ledger.plans[1].categories == ("GROCERIES",)


def test_asking_who_regroups_the_same_window_rather_than_starting_over() -> None:
    """ "¿A qué personas?" asks who, which is a grouping and not a total. A correct number in the
    wrong shape is what it used to come back as."""
    bizums = spending(categories=("TRANSFER_BIZUM",), group_by=GroupBy.MERCHANT)
    client, _, ledger, _ = wired(
        {
            "bizum": PlanningResult.planned(spending(categories=("TRANSFER_BIZUM",))),
            "personas": PlanningResult.planned(bizums),
        },
        PEOPLE,
    )
    chat = Chat(client)

    chat.say("¿cuánto he mandado por bizum el mes pasado?")
    who = chat.say("¿y a qué personas?")

    assert ledger.plans[1].group_by is GroupBy.COUNTERPARTY
    assert [group["key"] for group in who["groups"]] == ["Elena", "Sergio"]


def test_two_bare_months_in_a_row_are_two_different_months() -> None:
    """Both read here rather than by the model, which is why this is worth walking: the second one
    has to replace the first, and an explicit window is the one thing a merge may never inherit."""
    client, _, ledger, _ = wired(
        {
            "marzo": PlanningResult.planned(spending()),
            "abril": PlanningResult.planned(spending()),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté en marzo en el súper?")
    chat.say("¿y en abril?")

    assert ledger.plans[0].date_range == AbsoluteRange(date(2026, 3, 1), date(2026, 3, 31))
    assert ledger.plans[1].date_range == AbsoluteRange(date(2026, 4, 1), date(2026, 4, 30))


def test_a_month_then_a_year_widens_to_the_year_rather_than_keeping_the_month() -> None:
    """ "El año pasado" after a question about March is the whole of 2025 — not March of 2025, which
    is what "¿y el año anterior?" means. Two phrases, two meanings, and reading them alike would
    answer twelve months as one or one as twelve."""
    client, _, ledger, _ = wired(
        {
            "marzo": PlanningResult.planned(spending()),
            "año pasado": PlanningResult.planned(spending()),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté en marzo en el súper?")
    chat.say("¿y cuánto gasté el año pasado?")

    assert ledger.plans[1].date_range == AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31))


def test_the_year_before_after_a_bare_month_is_that_month_a_year_back() -> None:
    """The other phrase, and it is a shift rather than a window: the same days one year earlier,
    with the category and the direction untouched because nothing was re-read."""
    client, planner, ledger, _ = wired({"junio": PlanningResult.planned(spending())})
    chat = Chat(client)

    chat.say("¿cuánto gasté en junio en el súper?")
    asked = len(planner.asked)
    chat.say("¿y el año anterior?")

    assert len(planner.asked) == asked, "the shift reached the model"
    assert ledger.plans[1].date_range == AbsoluteRange(date(2025, 6, 1), date(2025, 6, 30))
    assert ledger.plans[1].categories == ("GROCERIES",)


# =========================================================================================
# A ranking is a different shape of answer, and moving in and out of one is where the
# context is easiest to lose.
# =========================================================================================


def test_an_ordinal_walks_the_ranking_and_an_aggregate_question_leaves_it() -> None:
    """Two shapes in one conversation. A ranking is a row and an aggregate is a total, and the
    third turn must not be answered as the fourth-largest anything."""
    client, _, ledger, _ = wired(
        {
            "gasolina": PlanningResult.planned(FUEL_RANKING),
            "supermercado": PlanningResult.planned(spending()),
        }
    )
    chat = Chat(client)

    chat.say("¿cuál fue el gasto más caro del año en gasolina?")
    chat.say("¿y el segundo?")
    chat.say("¿cuánto gasté el mes pasado en el supermercado?")

    assert [r.rank for r in ledger.rankings] == [1, 2]
    assert len(ledger.plans) == 1
    assert ledger.plans[0].categories == ("GROCERIES",)


def test_el_siguiente_advances_by_one_and_an_ordinal_names_its_own_position() -> None:
    """They are genuinely different. "El siguiente" has no number in it — it is one more than
    wherever the conversation is — and reading "el tercero" the same way answered it with the
    second."""
    client, _, ledger, _ = wired({"gasolina": PlanningResult.planned(FUEL_RANKING)})
    chat = Chat(client)

    chat.say("¿cuál fue el gasto más caro del año en gasolina?")
    chat.say("¿y el siguiente?")
    chat.say("¿y el cuarto?")
    chat.say("¿y el siguiente?")

    assert [r.rank for r in ledger.rankings] == [1, 2, 4, 5]


def test_an_aside_in_the_middle_of_a_ranking_does_not_lose_the_position() -> None:
    """The composition worth checking: an aside preserves `last_plan`, and `last_plan` is where the
    rank lives. Either half working alone would still lose the conversation."""
    client, _, ledger, _ = wired({"gasolina": PlanningResult.planned(FUEL_RANKING)})
    chat = Chat(client)

    chat.say("¿cuál fue el gasto más caro del año en gasolina?")
    chat.say("¿y el segundo?")
    chat.say("¿quién eres?")
    chat.say("¿y el tercero?")

    assert [r.rank for r in ledger.rankings] == [1, 2, 3]


def test_a_ranking_is_explained_without_inventing_a_grouping_it_has_not_got() -> None:
    """The explainer speaks about aggregates — which metric ran, how the groups were formed — and a
    ranking has neither. So the *sentence* it writes is the general one, while the plan line still
    says which ranking the question is about: describing fields that do not exist would be the
    assistant reporting on itself incorrectly, which is the one subject it has no excuse for.
    """
    client, planner, ledger, _ = wired({"gasolina": PlanningResult.planned(FUEL_RANKING)})
    chat = Chat(client)

    chat.say("¿cuál fue el gasto más caro del año en gasolina?")
    asked, ranked = len(planner.asked), len(ledger.rankings)
    why = chat.say("¿cómo lo has calculado?")

    assert why["outcome"] == Spoken.EXPLANATION.value
    assert (len(planner.asked), len(ledger.rankings)) == (asked, ranked)
    described = why["plan"].lower()
    assert "transport_fuel" in described, "the explanation lost which question it is about"
    assert "1" in described, "it lost which position of the ranking"
    for invented in ("agrupad", "media", "suma de"):
        assert invented not in why["text"].lower()


# =========================================================================================
# Which of the holder's accounts a question is about, turn by turn.
# =========================================================================================


def test_naming_an_account_narrows_and_saying_nothing_widens_again() -> None:
    """Three turns, three scopes. The rule is read from a closed vocabulary and the model never
    sees the decision — which is why the middle turn must not leak into the third."""
    client, _, ledger, _ = wired(
        {
            "gasté": PlanningResult.planned(spending(categories=())),
            "hucha": PlanningResult.planned(spending(categories=())),
            "en total": PlanningResult.planned(spending(categories=())),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado?")
    chat.say("¿y en la hucha?")
    chat.say("¿y en total?")

    assert ledger.plans[0].across_accounts is True
    assert ledger.plans[1].only_account_id == PIGGY
    assert ledger.plans[2].across_accounts is True
    assert ledger.plans[2].only_account_id is None


def test_an_account_the_holder_does_not_have_widens_rather_than_guessing() -> None:
    """None of that kind is not one of that kind. Narrowing to whatever is nearest would answer
    about an account the sentence did not choose, and the honest scope is the wider one."""
    client, _, ledger, _ = wired(
        {"fondo": PlanningResult.planned(spending(categories=()))},
        accounts=Accounts(CURRENT_ACCOUNT, SAVINGS),
    )

    Chat(client).say("¿cuánto gasté el mes pasado en el fondo?")

    assert ledger.plans[0].across_accounts is True
    assert ledger.plans[0].only_account_id is None


def test_a_word_matching_two_accounts_selects_both_without_other_kinds() -> None:
    """Two piggy banks and one kind word selects both without adding the current account."""
    other = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)
    client, _, ledger, _ = wired(
        {"hucha": PlanningResult.planned(spending(categories=()))},
        accounts=Accounts(CURRENT_ACCOUNT, SAVINGS, other),
    )

    Chat(client).say("¿cuánto gasté el mes pasado en la hucha?")

    assert ledger.plans[0].across_accounts is False
    assert ledger.plans[0].only_account_ids == (SAVINGS.account_id, other.account_id)


def test_an_account_list_that_cannot_be_read_widens_rather_than_narrowing_wrongly() -> None:
    """Narrowing needs the list. Without it the fallback is the wider answer and not the declared
    account: one account out of three, labelled as though the word had been understood, is exactly
    the failure the scoping rule exists to stop."""
    client, _, ledger, _ = wired(
        {"hucha": PlanningResult.planned(spending(categories=()))},
        accounts=Accounts(down=True),
    )

    Chat(client).say("¿cuánto gasté el mes pasado en la hucha?")

    assert ledger.plans[0].across_accounts is True


def test_a_balance_about_one_account_does_not_scope_the_question_after_it() -> None:
    """The scope is read from each sentence, never inherited. Carrying it would answer a question
    about everything with the figures of whichever account was mentioned two turns ago."""
    client, _, ledger, _ = wired({"gasté": PlanningResult.planned(spending(categories=()))})
    chat = Chat(client)

    named = chat.say("¿cuánto tengo en la hucha?")
    chat.say("¿cuánto gasté el mes pasado?")

    assert named["outcome"] == Spoken.BALANCE.value
    assert ledger.plans[0].across_accounts is True


# =========================================================================================
# Refusals, mixed into a conversation that keeps going.
# =========================================================================================


def test_an_average_never_reaches_the_model_even_mid_conversation() -> None:
    """The guard is a control and runs before the planner. Refusing afterwards would be whatever
    disposition the configured model happens to have, which moved by sixty points between two
    models that were measured."""
    client, planner, _, _ = wired({"supermercado": PlanningResult.planned(spending())})
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    asked = len(planner.asked)
    answered = chat.say("¿cuál es mi gasto medio en gasolina?")

    assert answered["outcome"] == PlanningOutcome.PLANNED.value
    assert len(planner.asked) == asked


@pytest.mark.parametrize(
    ("said", "family"),
    [
        ("¿qué porcentaje de mi gasto es el alquiler?", "RATIO"),
        (
            "¿gasté más del 1 al 15 de julio que del 16 al 31 de julio?",
            "COMPARISON",
        ),
        ("a este ritmo, ¿cuánto voy a gastar este mes?", "FORECAST"),
        ("¿debería recortar en restaurantes?", "ADVICE"),
    ],
)
def test_each_family_is_refused_in_its_own_words_without_ending_the_conversation(
    said: str, family: str
) -> None:
    """Five different things to tell somebody, and one sentence for all of them would say nothing
    about whether to rephrase, wait or give up. What matters conversationally is the second half:
    the plan behind the refusal is still there afterwards."""
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina el mes pasado": PlanningResult.planned(
                spending(categories=("TRANSPORT_FUEL",))
            ),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    refused = chat.say(said)

    assert refused["outcome"] == PlanningOutcome.UNSUPPORTED.value
    assert refused["conversation"]["last_plan"]["categories"] == ["GROCERIES"]
    assert refused["text"], f"{family} was refused without saying anything"


def test_a_superlative_that_looks_like_a_comparison_is_answered_rather_than_refused() -> None:
    """ "El bizum más alto que he hecho" carries "más … que", where the "que" opens a relative
    clause and not a second term. It was refused as a comparison — "todavía no comparo dos
    periodos" — to a question containing one period and a real answer."""
    bizums = TopMovements(
        direction=Direction.EXPENSE, period=THIS_YEAR, categories=("TRANSFER_BIZUM",)
    )
    client, _, ledger, _ = wired({"bizum más alto": PlanningResult.planned(bizums)})

    answer = Chat(client).say("¿cuál es el bizum más alto que he hecho este año?")

    assert answer["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.rankings, "a ranking question was not answered as a ranking"


def test_the_same_question_without_the_word_that_was_refused_is_answered() -> None:
    """What a refusal is for. It names the family so the user can drop it, and the very next turn
    has to work — a refusal that poisoned the conversation would teach somebody to start over."""
    client, _, ledger, _ = wired(
        {"gasolina el mes pasado": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",)))}
    )
    chat = Chat(client)

    chat.say("¿cuál es mi gasto medio en gasolina?")
    answered = chat.say("¿cuánto gasté en gasolina el mes pasado?")

    assert answered["outcome"] == PlanningOutcome.PLANNED.value
    assert answered["conversation"]["workflow"] is None
    assert ledger.plans[0].categories == ("TRANSPORT_FUEL",)


# =========================================================================================
# Corrections: the user says the last answer was wrong.
# =========================================================================================


def test_a_bare_rejection_asks_what_to_change_and_keeps_every_good_field() -> None:
    """ "No es eso" contains no financial information at all. Sending it to the planner would ask a
    model to derive a query from a rejection; instead the validated plan is held so the next turn
    names one replacement rather than restating everything."""
    client, planner, _, _ = wired({"supermercado": PlanningResult.planned(spending())})
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    asked = len(planner.asked)
    rejected = chat.say("no es eso")

    assert rejected["outcome"] == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert len(planner.asked) == asked, "a bare rejection reached the model"
    assert rejected["conversation"]["awaiting"] == "CORRECTION"
    assert rejected["conversation"]["draft"]["categories"] == ["GROCERIES"]


def test_the_reply_to_a_rejection_patches_the_plan_it_was_holding() -> None:
    """Three turns: an answer, a rejection, a replacement. The third is planned as a *follow-up* of
    the first, so the fields nobody corrected survive."""
    client, planner, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    chat.say("no es eso")
    chat.say("en gasolina")

    assert len(planner.inherited) == 1, "the replacement is a deterministic patch of the draft"
    assert ledger.plans[1].categories == ("TRANSPORT_FUEL",)
    assert ledger.plans[1].period == LAST_MONTH


def test_a_correction_that_carries_its_replacement_is_one_turn_and_not_two() -> None:
    """ "Me refería a gasolina" rejects and replaces at once. Asking "¿qué debería cambiar?" back
    would be asking about something the sentence just said."""
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    corrected = chat.say("me refería a gasolina")

    assert corrected["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[1].categories == ("TRANSPORT_FUEL",)


def test_a_rejection_before_there_is_anything_to_reject_is_not_a_correction() -> None:
    """Gated on a previous answer, like the explanation. With nothing behind it there is no plan to
    patch, and pretending there is would hold an empty draft the next turn tries to complete."""
    client, _, _, _ = wired({"no es eso": PlanningResult.needs_clarification("¿De qué periodo?")})

    answer = Chat(client).say("no es eso")

    assert answer["conversation"]["awaiting"] != "CORRECTION"


# =========================================================================================
# Clarifications, and what a change of subject does to one.
# =========================================================================================


HALF_READ = PlanningResult.needs_clarification(
    "¿De qué periodo?",
    # What the model had already read when it stopped to ask. The ordinary case: it emits one
    # whenever it read anything at all, and it is what the next turn completes.
    draft=FinancialPlanDraft(direction=Direction.EXPENSE, categories=("GROCERIES",)),
)


def test_a_clarification_is_finished_by_a_bare_window_without_a_model() -> None:
    """Both halves are closed vocabularies — the slot and the window — so there is nothing here to
    infer. Before this, answering the question restarted the conversation."""
    client, planner, ledger, _ = wired({"llevo gastado": HALF_READ})
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    asked = len(planner.asked)
    finished = chat.say("el mes pasado")

    assert finished["outcome"] == PlanningOutcome.PLANNED.value
    assert len(planner.asked) == asked, "the reply to a clarification reached the model"
    assert ledger.plans[0].period == LAST_MONTH


def test_a_clarification_does_not_survive_a_change_of_subject() -> None:
    """Only payments suspend. A question that was asked and abandoned is abandoned: keeping it
    alive is how "este año", said about something else entirely, completed a plan the user had
    moved on from."""
    client, _, ledger, _ = wired(
        {
            "llevo gastado": PlanningResult.needs_clarification("¿De qué periodo?"),
            "supermercado": PlanningResult.planned(spending()),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    after = chat.say("¿cuánto dinero tengo?")

    assert after["conversation"]["workflow"] is None
    assert after["conversation"]["awaiting"] is None
    assert len(ledger.plans) == 1


def test_the_slot_survives_an_aside_so_the_reply_after_it_still_lands() -> None:
    """The realistic sequence: the assistant asks which period, the user looks up the categories,
    then answers. The aside is not a change of subject and must not close the question."""
    client, _, ledger, _ = wired({"llevo gastado": HALF_READ})
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    chat.say("dime las categorías disponibles")
    finished = chat.say("el mes pasado")

    assert finished["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[0].period == LAST_MONTH


def test_a_reply_that_answers_nothing_goes_to_the_model_rather_than_being_guessed_at() -> None:
    """Precision over coverage, in a conversation. "Pues no sé" fills no slot, and inventing a
    window for it would answer a question the user never finished asking."""
    client, planner, _, _ = wired(
        {
            "llevo gastado": PlanningResult.needs_clarification("¿De qué periodo?"),
            "pues no sé": PlanningResult.needs_clarification("¿De qué periodo?"),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    asked = len(planner.asked)
    chat.say("pues no sé")

    assert len(planner.asked) == asked + 1


# =========================================================================================
# Recovery, in the middle of a conversation rather than at the end of one.
# =========================================================================================


def test_a_relaxed_query_becomes_the_context_the_next_follow_up_inherits() -> None:
    """The half a round-trip test cannot see. A relaxation patches the plan in code, and if the
    patched plan is not what gets remembered, the next "¿y en gasolina?" re-narrows to a merchant
    the user just removed."""
    client, planner, ledger, _ = wired(
        {"mercadonna": PlanningResult.planned(spending(merchants=("Mercadonna",)))}, NOTHING
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté en Mercadonna el mes pasado?")
    relaxed = chat.say(query_recovery.WITHOUT_MERCHANT)

    assert relaxed["conversation"]["last_plan"]["merchants"] == []
    assert relaxed["conversation"]["last_plan"]["categories"] == ["GROCERIES"]


def test_a_conversation_survives_the_bank_being_down_for_one_turn() -> None:
    """Four turns, and the bank is only down for the second. What is under test is that the failed
    turn left the conversation usable: the fourth still inherits from the first."""
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
            "restaurantes": PlanningResult.planned(spending(categories=("RESTAURANTS",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    ledger.down = True
    failed = chat.say("¿y en gasolina?")
    ledger.down = False
    chat.say(query_recovery.RETRY)
    chat.say("¿y en restaurantes?")

    assert failed["outcome"] == "BANKING_UNAVAILABLE"
    assert ledger.plans[-1].categories == ("RESTAURANTS",)
    assert ledger.plans[-1].period == LAST_MONTH


def test_an_aside_between_a_failure_and_its_retry_does_not_lose_the_saved_query() -> None:
    """A retry lives beside the active workflow rather than inside one, which is what makes this
    work: the user can ask who they are talking to and still press "Reintentar"."""
    client, _, ledger, _ = wired({"supermercado": PlanningResult.planned(spending())}, down=True)
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    aside = chat.say("¿quién eres?")
    assert aside["conversation"]["retry_plan"] is not None

    ledger.down = False
    retried = chat.say(query_recovery.RETRY)

    assert retried["outcome"] == PlanningOutcome.PLANNED.value


# =========================================================================================
# A transfer read out of a sentence: an act, not a question, and not a memory either.
# =========================================================================================


def test_a_transfer_sentence_is_understood_without_becoming_the_context() -> None:
    """The chat reads it and stops. Preparing is a `payments:prepare` scope on another route, and
    the plan behind the question before it is not a casualty of somebody moving money."""
    client, _, ledger, _ = wired(
        {
            "supermercado": PlanningResult.planned(spending()),
            "gasolina": PlanningResult.planned(spending(categories=("TRANSPORT_FUEL",))),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el supermercado?")
    moved = chat.say("mueve 200 € a la hucha")
    chat.say("¿y en gasolina?")

    assert moved["outcome"] == Spoken.TRANSFER_READY.value
    assert moved["conversation"]["active"] is None if "active" in moved["conversation"] else True
    assert ledger.plans[1].period == LAST_MONTH, "the transfer became the context"


def test_the_same_verbs_in_a_question_are_a_question_and_it_is_answered_as_one() -> None:
    """ "¿Cuánto paso a la hucha cada mes?" carries a moving verb and an account, and reading it as
    an order would reply to a question with an offer to move money nobody asked to move."""
    client, _, ledger, _ = wired(
        {"paso a la hucha": PlanningResult.planned(spending(categories=("INTERNAL",)))}
    )

    answer = Chat(client).say("¿cuánto paso a la hucha cada mes?")

    assert answer["outcome"] != Spoken.TRANSFER_READY.value
    assert ledger.plans, "a question about a habit was not answered"


def test_a_transfer_to_an_account_the_holder_does_not_have_says_so_plainly() -> None:
    """It names what does exist rather than refusing. A holder told "no puedo" about their own
    accounts learns the wrong thing about the product."""
    client, _, _, _ = wired({}, accounts=Accounts(CURRENT_ACCOUNT))

    answer = Chat(client).say("mueve 200 € a la hucha")

    assert "No tienes ninguna cuenta de ese tipo" in answer["text"]


# --- what the conversation remembers happening -------------------------------------------------


def test_the_thread_accumulates_across_turns_and_rides_the_wire() -> None:
    """The log, end to end, through the surface a browser actually uses.

    Every field beside it describes the turn immediately before, taken by whichever branch ran — so
    a chain of them is lossy at each hop and an ellipsis three deep stops resolving. This is the one
    piece of the conversation nobody decides the contents of: one turn in, one entry out, in order,
    and the client round-trips it like everything else because there are no sessions here.

    Deliberately a mixture: an aside, a question answered from a plan, and a pleasantry. All three
    are turns that happened, and the two the state fields treat as "nothing to remember" are exactly
    the ones a later "¿y eso?" needs to see.
    """
    client, _, _, _ = wired(
        {
            "gaste": PlanningResult.planned(
                FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
            )
        }
    )
    chat = Chat(client)

    chat.say("Quien eres?")
    chat.say("Cuanto gaste este mes?")
    answer = chat.say("Gracias")

    assert [turn["sentence"] for turn in answer["conversation"]["turns"]] == [
        "Quien eres?",
        "Cuanto gaste este mes?",
        "Gracias",
    ]
    assert [turn["outcome"] for turn in answer["conversation"]["turns"]] == [
        Spoken.IDENTITY.value,
        PlanningOutcome.PLANNED.value,
        Spoken.COURTESY.value,
    ]
    assert answer["conversation"]["turns"][1]["plan"] is not None, (
        "the query behind an answer is what a later ellipsis leans on"
    )


def test_one_turn_records_one_entry_however_many_answers_it_produced() -> None:
    """A compound turn answers twice and is still one thing the user said. Recording per result
    would double it, and a log that counts turns differently from the person having them is worse
    than no log."""
    client, _, _, _ = wired(
        {
            "gaste": PlanningResult.planned(
                FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
            )
        }
    )

    answer = Chat(client).say("Cuanto tengo y cuanto gaste este mes?")

    assert len(answer["conversation"]["turns"]) == 1
