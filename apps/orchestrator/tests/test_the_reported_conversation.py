"""One real conversation, turn by turn, with the answers it should have given.

Somebody sat down with the demo and asked twelve questions. Seven of them came back wrong, and not
wrong in seven ways — wrong in six, because two of the failures were the same one twice. This is
that conversation, transcribed, with what each turn is supposed to answer.

    ¿qué movimientos he tenido entre mis cuentas?  → "¿de qué periodo?"          ✓
    en este año                                    → 14.019,05 €, 194 movimientos ✗ everything
    ¿qué cuentas tengo?                            → "eso no puedo calcularlo"    ✗ it can
    ¿cuánto tengo en mi cuenta de ahorros?         → all three, with a breakdown  ✗ it named one
    ¿en mi hucha?                                  → "¿de qué periodo?"           ✗
    ¿cuánto dinero tengo en mi Hucha?              → 5.485,50 € en Hucha          ✓
    ¿y en el fondo?                                → "sigo sin tener claro…"      ✗
    …traspasar el total de fondo indexado
      a la cuenta de ahorros                       → "¿a cuál lo muevo?"          ✗ it said which
    a Hucha                                        → "¿de qué periodo?"           ✗

The figures are the demo dataset's, so the amounts here are the amounts on screen. They are what
makes this a transcript rather than an illustration: 7.755,92 € is what the holder has where the
answer says "entre tus cuentas", and 2.700,00 € is a cost basis and says so.

**One turn is answered by refusing, and that is the fix rather than a gap left open.** "Movimientos
entre mis cuentas" means the internal transfers, and no plan can express only those: banking books
an internal movement with no category, and `include_internal` widens a query rather than narrowing
one. Saying so is the answer until the contract changes. What it may not do is what it did — answer
14.019,05 € over 194 movements, every movement the holder made, to a question about the ones between
two of their own accounts.
"""

import uuid
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
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome, Spoken
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("08aba04f-2b30-4639-88a6-eac741153ce0")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
FUND = uuid.UUID("33333333-3333-3333-3333-333333333333")
TODAY = date(2026, 8, 5)

# Laura's three accounts, with the figures the demo dataset actually holds.
CURRENT_ACCOUNT = Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042)
SAVINGS = Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550)
INVESTMENT = Holding(FUND, "ASSET", "Fondo indexado", "EUR", 270_000)

# What the two spendable accounts add up to. The fund is not in it, and the answer says why.
SPENDABLE = "7.755,92 €"

THIS_YEAR = RelativePeriod(period=Period.THIS_YEAR)
EVERYTHING_THIS_YEAR = FinancialPlan(direction=Direction.ALL, period=THIS_YEAR)

# 14.019,05 € over 194 movements: the answer the second turn gave, and the figure this file exists
# to keep off a question about transfers between two of somebody's own accounts.
ALL_OF_IT = QueryAnswer(
    currency="EUR",
    total_minor=1_401_905,
    movement_count=194,
    groups=(Group("total", 1_401_905, 194),),
)


class Planner:
    """Scripted per sentence, and it raises on anything it was not given.

    Half of this conversation is answered without a model — three balances, an account list, a
    transfer — and the raise is what says so. A turn that quietly started asking one would pass
    here and behave differently on a machine with a different model installed.
    """

    def __init__(self, script: dict[str, PlanningResult]) -> None:
        self._script = script
        self.asked: list[str] = []

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        self.asked.append(sentence)
        for fragment, result in self._script.items():
            if fragment in sentence.lower():
                return result
        raise AssertionError(f"the planner was asked about {sentence!r}, which nothing scripted")


class Ledger:
    def __init__(self, answer: QueryAnswer = ALL_OF_IT) -> None:
        self.answer = answer
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return self.answer

    async def ranked(self, *_: object, **__: object) -> None:
        raise AssertionError("no ranking in this conversation")


class Accounts:
    def __init__(self, *holdings: Holding) -> None:
        self._holdings = holdings or (CURRENT_ACCOUNT, SAVINGS, INVESTMENT)

    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=self._holdings,
        )


class NoContacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


@pytest.fixture(autouse=True)
def _restore():  # type: ignore[no-untyped-def]
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def wired(script: dict[str, PlanningResult] | None = None, *holdings: Holding):  # type: ignore[no-untyped-def]
    planner = Planner(script or {})
    ledger = Ledger()
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(*holdings),  # type: ignore[arg-type]
        contacts=NoContacts(),  # type: ignore[arg-type]
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
    return TestClient(app), planner, ledger


class Chat:
    """The browser: store the conversation, send it back."""

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


# --- "¿Qué cuentas tengo?" ------------------------------------------------------------------


def test_which_accounts_do_i_have_is_a_question_with_an_answer() -> None:
    """Turn three, and the flattest failure in the transcript: "eso no puedo calcularlo todavía",
    about a list this service holds and had printed one turn later.

    It was excluded from the balance router on purpose — the exclusion that keeps "¿cuántos
    movimientos tengo?" on the path that can answer it — and nothing else picked it up. A balance
    answer *is* the account list: a total with every account and its figure under it.
    """
    client, planner, _ = wired()

    answer = Chat(client).say("Que cuentas tengo ?")

    assert answer["outcome"] == Spoken.BALANCE.value
    assert planner.asked == [], "a question about the account list reached the model"
    for account in ("Cuenta corriente", "Hucha", "Fondo indexado"):
        assert account in answer["text"]


def test_counting_movements_is_still_not_the_balance() -> None:
    """The exclusion that "cuentas" left. It has to keep working, or the fix traded one real figure
    about the wrong thing for another."""
    counting = FinancialPlan(direction=Direction.ALL, period=THIS_YEAR)
    client, _, ledger = wired({"movimientos": PlanningResult.planned(counting)})

    answer = Chat(client).say("¿cuántos movimientos tengo este año?")

    assert answer["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans, "a question about movements was answered with a balance"


# --- "Cuánto dinero tengo en mi cuenta de ahorros" -------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Cuanto dinero tengo en mi cuenta de ahorros ?",
        "¿cuánto dinero tengo en mis ahorros?",
        "¿cuánto tengo en mi Hucha?",
    ],
)
def test_an_account_named_with_a_possessive_is_an_account_named(said: str) -> None:
    """Turn four. The vocabulary carried its article — "la cuenta de ahorros" was a phrase and "mi
    cuenta de ahorros" was not — so a sentence naming one account got the total of all three:

        "Tienes 7.755,92 € entre tus cuentas: 2.270,42 € en Cuenta corriente y 5.485,50 € en Hucha…"

    Every figure in it is correct, and none of them is the answer. Possessives are how people talk
    about their own money, and this is a vocabulary about somebody's own accounts.
    """
    client, _, _ = wired()

    answer = Chat(client).say(said)

    assert answer["text"] == "Tienes 5.485,50 € en Hucha."
    assert answer["total_minor"] == 548_550


def test_naming_none_still_answers_with_all_of_them() -> None:
    """The other half, and the reason the vocabulary is narrow. A question that names no account is
    answered with the total and a breakdown, because a breakdown is a better answer than a question
    — and the investment is left out of the figure and named after it."""
    client, _, _ = wired()

    answer = Chat(client).say("¿cuánto dinero tengo?")

    assert answer["text"].startswith(f"Tienes {SPENDABLE} entre tus cuentas")
    assert answer["total_minor"] == 775_592
    assert "2.700,00 €" in answer["text"]
    assert "no lo que vale hoy" in answer["text"], "a cost basis was printed as a valuation"


# --- "¿En mi Hucha?" ------------------------------------------------------------------------


def test_a_bare_account_after_a_balance_is_a_balance_about_that_account() -> None:
    """Turns five and seven, the same failure twice. Told a figure and asked which account it was
    in, the assistant asked which *period* — a question about a window, to somebody who had asked
    about a state.

    The sentence names an account and nothing else. What decides is the turn before it.
    """
    client, planner, _ = wired()
    chat = Chat(client)

    chat.say("¿cuánto dinero tengo?")
    narrowed = chat.say("En mi Hucha ?")

    assert narrowed["outcome"] == Spoken.BALANCE.value
    assert narrowed["text"] == "Tienes 5.485,50 € en Hucha."
    assert planner.asked == [], "a balance follow-up reached the model"


def test_the_fund_answers_with_what_it_cost_and_says_that_is_what_it_is() -> None:
    """Turn seven. An ASSET balance is a cost basis and never a valuation — there is no price
    source — so the figure travels with the sentence that makes it honest."""
    client, _, _ = wired()
    chat = Chat(client)

    chat.say("¿cuánto dinero tengo?")
    fund = chat.say("Y en EL FONDO")

    assert fund["outcome"] == Spoken.BALANCE.value
    assert "2.700,00 €" in fund["text"]
    assert "no lo que vale hoy" in fund["text"]


def test_the_same_words_after_a_spending_question_still_narrow_the_spending() -> None:
    """The reason this reads the previous turn instead of the sentence. "¿Y en la hucha?" is a
    balance after a balance and a spending question after a spending question, and the sentence is
    identical. Answering the balance here would be the same class of mistake pointing the other
    way."""
    spending = FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR)
    client, _, ledger = wired(
        {"gasté": PlanningResult.planned(spending), "hucha": PlanningResult.planned(spending)}
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté este año?")
    narrowed = chat.say("¿y en la hucha?")

    assert narrowed["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[1].only_account_id == str(PIGGY) or ledger.plans[1].only_account_id == PIGGY


def test_a_window_in_the_sentence_makes_it_a_question_about_movements_again() -> None:
    """The follow-up rule is narrow on purpose: an account *and nothing else*. "¿Y en la hucha el
    mes pasado?" has moved on to a question the ledger answers, and reading it as a balance would
    ignore the window the user just wrote down."""
    spending = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
    )
    client, _, ledger = wired({"hucha": PlanningResult.planned(spending)})
    chat = Chat(client)

    chat.say("¿cuánto dinero tengo?")
    chat.say("¿y en la hucha el mes pasado?")

    assert ledger.plans, "a question with a window in it was answered with a balance"


# --- "Traspasar el total de fondo indexado a la cuenta de ahorros" ---------------------------


def test_a_transfer_that_says_where_the_money_goes_is_not_ambiguous() -> None:
    """Turn eleven. The sentence names two accounts — one to take from, one to put into — and the
    reader counted both as candidate destinations:

        "Tienes más de una cuenta de ese tipo: Hucha y Fondo indexado. ¿A cuál lo muevo?"

    Asking the user to resolve an ambiguity the reader had invented, about a sentence that says
    exactly where the money goes. What decides is the preposition.
    """
    client, _, _ = wired()

    answer = Chat(client).say(
        "me gustaria traspasar el total de fondo indexado a la cuenta de ahorros"
    )

    assert "más de una cuenta" not in answer["text"]
    assert "Hucha" in answer["text"]


def test_the_account_it_comes_from_is_not_offered_as_somewhere_to_put_it() -> None:
    """Stated as the property, because that is the defect: the source and the destination were the
    same list."""
    client, _, _ = wired()

    answer = Chat(client).say("pasa el dinero de la hucha al fondo")

    assert "Fondo indexado" in answer["text"]
    assert "más de una cuenta" not in answer["text"]


def test_a_genuine_ambiguity_asks_for_the_instruction_rather_than_the_missing_word() -> None:
    """Two piggy banks, one word for both — the ambiguity that is real. It may be asked about, and
    it may not leave a slot open: nothing on this path holds state, so a reply naming only the
    account arrives with the amount already gone. "¿A cuál lo muevo?" answered with "a Hucha" was
    turn twelve, and it came back "¿de qué periodo?".
    """
    travels = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)
    client, _, _ = wired({}, CURRENT_ACCOUNT, SAVINGS, travels)

    answer = Chat(client).say("mueve 200 € a la hucha")

    assert "Hucha" in answer["text"] and "Hucha viajes" in answer["text"]
    assert "otra vez" in answer["text"], "it asks a question and does not say what to answer"
    assert answer["conversation"]["awaiting"] is None
    assert answer["conversation"]["workflow"] is None


def test_the_repeated_instruction_is_understood() -> None:
    """The turn after. Naming both accounts is what makes repeating it cheap, so repeating it has
    to work — otherwise the advice is as much of a dead end as the question was."""
    travels = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)
    client, planner, _ = wired({}, CURRENT_ACCOUNT, SAVINGS, travels)
    chat = Chat(client)

    chat.say("mueve 200 € a la hucha")
    again = chat.say("pasa 200 € a Hucha viajes")

    assert planner.asked == [], "a transfer instruction reached the model"
    assert again["outcome"] == Spoken.TRANSFER_READY.value


# --- what the whole thing does end to end ----------------------------------------------------


def test_the_transcript_from_the_third_turn_on() -> None:
    """The conversation as it was typed, in one test, because the failures compounded: a balance
    that answered about everything made the next question look like a new subject, and the loop
    detector then refused a question nobody had asked twice.

    The first two turns are left out and have their own test below — they are the one thing here
    that still cannot be answered.
    """
    client, planner, _ = wired()
    chat = Chat(client)

    accounts = chat.say("Que cuentas tengo ?")
    savings = chat.say("Cuanto dinero tengo en mi cuenta de ahorros ?")
    narrowed = chat.say("En mi Hucha ?")
    again = chat.say("Cuanto dinero tengo en mi Hucha?")
    fund = chat.say("Y en EL FONDO")
    moved = chat.say("me gustaria traspasar el total de fondo indexado a la cuenta de ahorros")

    assert accounts["outcome"] == Spoken.BALANCE.value
    assert savings["text"] == "Tienes 5.485,50 € en Hucha."
    assert narrowed["text"] == "Tienes 5.485,50 € en Hucha."
    assert again["text"] == "Tienes 5.485,50 € en Hucha."
    assert "2.700,00 €" in fund["text"]
    assert "más de una cuenta" not in moved["text"]
    # Six turns and the model was never needed once. Every one of them is a closed vocabulary — the
    # accounts, the balance, the destination — and a model in any of them is a different answer on
    # a machine with a different model installed.
    assert planner.asked == []


def test_movements_between_my_own_accounts_is_not_answered_with_all_of_them() -> None:
    """Turns one and two, and the worst answer in the transcript.

    "Entre mis cuentas" means the internal transfers, and no plan can express only those: banking
    books an internal movement with no category to filter on, and `include_internal` widens a query
    rather than narrowing one. Answering it needs a contract change and belongs in its own branch.

    Until then the answer is to say so. What it did instead was ask for a period and then produce
    14.019,05 € over 194 movements — every movement the holder made that year, presented as the
    answer to a question about transfers between two of their own accounts. A real figure about a
    different question is the one failure this service is built to make impossible.
    """
    client, planner, ledger = wired()

    refused = Chat(client).say("Perdona que movimientos he tenido entre mis cuentas ?")

    assert refused["outcome"] == PlanningOutcome.UNSUPPORTED.value
    assert ledger.plans == [], "it ran a query it could not narrow to what was asked"
    assert planner.asked == [], "the guard is a control and runs before the model"
    assert "traspasos entre tus cuentas" in refused["text"], "it does not say what it cannot do"


def test_the_same_words_about_a_balance_are_still_a_balance() -> None:
    """The refusal has to be narrow. "¿Cuánto tengo entre mis cuentas?" carries the same three words
    and asks about a state, which has a real answer — and the balance is routed before the guard so
    that it keeps getting one."""
    client, _, _ = wired()

    answer = Chat(client).say("¿cuánto tengo entre mis cuentas?")

    assert answer["outcome"] == Spoken.BALANCE.value
    assert answer["total_minor"] == 775_592
