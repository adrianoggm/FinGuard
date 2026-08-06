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


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


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
        contacts=NoContacts(),
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


def test_a_genuine_ambiguity_is_asked_about_and_the_answer_is_heard() -> None:
    """Two piggy banks, one word for both — the ambiguity that is real, and turn twelve.

    "¿A cuál lo muevo?" answered "a Hucha" came back "¿de qué periodo?", because the path held no
    state: the amount was gone by the time the reply arrived, and the sentence was classified from
    scratch. Now the turn keeps what it read, so the reply only has to name the account — and it is
    read by the holder's own name for it, since "Hucha" and "Hucha viajes" are the same kind.
    """
    travels = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)
    client, planner, _ = wired({}, CURRENT_ACCOUNT, SAVINGS, travels)
    chat = Chat(client)

    asked = chat.say("mueve 200 € a la hucha")
    assert "Hucha" in asked["text"] and "Hucha viajes" in asked["text"]
    assert asked["conversation"]["awaiting"] == "ACCOUNT"
    assert asked["conversation"]["transfer_amount_minor"] == 200_00, "the amount was dropped"

    finished = chat.say("a Hucha viajes")

    assert finished["outcome"] == Spoken.TRANSFER_READY.value
    assert finished["total_minor"] == 200_00
    assert "Hucha viajes" in finished["text"]
    assert finished["conversation"]["workflow"] is None
    assert planner.asked == [], "answering a transfer question reached the model"


def test_the_amount_asked_for_is_the_amount_it_hears() -> None:
    """The turn that reported it: "¿Cuánto quieres mover a Hucha?" answered "1000 pavos" came back
    "¿de qué periodo?". The question and the answer are one exchange, and the state is what makes
    them one."""
    client, planner, _ = wired()
    chat = Chat(client)

    asked = chat.say("quiero traspasar del fondo a hucha")
    assert asked["conversation"]["awaiting"] == "AMOUNT"

    finished = chat.say("1000 pavos")

    assert finished["outcome"] == Spoken.TRANSFER_READY.value
    assert finished["total_minor"] == 100_000
    assert planner.asked == []


def test_a_transfer_that_names_no_account_asks_which_rather_than_refusing() -> None:
    """ "Me gustaría transferir dinero entre cuentas" is an instruction with the destination left
    out. It reached the capability guard and was refused with a sentence about *querying* the
    transfers between accounts — a refusal about reading, to somebody asking to write."""
    client, _, _ = wired()

    answer = Chat(client).say("Me gustaria tranferir dinero entre cuentas")

    assert answer["conversation"]["awaiting"] == "ACCOUNT"
    assert "Hucha" in answer["text"] and "Cuenta corriente" in answer["text"]


@pytest.mark.parametrize(
    "said",
    [
        "Me gustaria enviarme dinero a la cuenta corriente",
        "Me gustaria tranferir dinero a mi cuenta corriente desde mi hucha",
    ],
)
def test_an_instruction_to_move_money_is_not_answered_with_a_balance(said: str) -> None:
    """Both were answered with a figure — "Tienes 2.270,42 € en Cuenta corriente" — because neither
    verb was in the moving vocabulary. `enviar` and `mandar` are deliberately absent, since they are
    what a person says about paying somebody else; the *reflexive* is the opposite and has exactly
    one possible destination, which is the holder's own account.
    """
    client, _, _ = wired()

    answer = Chat(client).say(said)

    assert answer["outcome"] != Spoken.BALANCE.value
    assert answer["conversation"]["awaiting"] == "AMOUNT"
    assert "Cuenta corriente" in answer["text"]


def test_the_state_of_my_savings_is_a_balance_question() -> None:
    """ "¿Cuál es el estado actual de mi hucha de ahorros?" came back "¿de qué periodo te gustaría
    saber el estado actual de tu hucha de ahorros?" — a question about a window, to somebody asking
    about a state, quoting their own sentence back at them."""
    client, planner, _ = wired()

    answer = Chat(client).say("Oye cual es el estado actual de mi hucha de ahorros")

    assert answer["outcome"] == Spoken.BALANCE.value
    assert answer["text"] == "Tienes 5.485,50 € en Hucha."
    assert planner.asked == []


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


# =========================================================================================
# The same demo, further down. Four more turns, and the first of them wrote off the rest:
# a question about money *arriving* was read as an order to send some, so every turn after
# it was inside a payment nobody had asked for.
# =========================================================================================


def test_a_question_about_money_arriving_is_not_an_order_to_send_any() -> None:
    """ "¿He recibido algún bizum de María este mes?" answered "¿de cuánto fue el bizum que
    recibiste de María?" and then, a turn later, "no tengo a María en tus contactos, así que no
    puedo enviarle nada" — to somebody asking whether she had sent *them* anything.

    "Recibido" was not one of the payment participles and the sentence carries no question word, so
    "bizum" as a verb plus "bizum" as money was enough to read it as an instruction. The cost is not
    one wrong answer: every turn afterwards belonged to a payment that was never ordered.
    """
    received = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(period=Period.THIS_MONTH),
        categories=("TRANSFER_BIZUM",),
        merchants=("María",),
    )
    client, _, ledger = wired({"recibido": PlanningResult.planned(received)})

    answer = Chat(client).say("He recibido algún bizum de María este mes ?")

    assert answer["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans, "a question about received money never reached the ledger"
    assert ledger.plans[0].direction is Direction.INCOME


@pytest.mark.parametrize(
    "said",
    [
        "He recibido algún bizum de María este mes ?",
        "¿me ha llegado el bizum de Marta?",
        "¿me hizo Javi la transferencia?",
        "me han hecho un bizum de 20 €",
    ],
)
def test_every_way_of_saying_the_money_came_in(said: str) -> None:
    """Read as one signal rather than as tense, because it is not about tense: these are about the
    other direction, and no amount of payment vocabulary makes them an instruction."""
    from finguard_orchestrator.domain.services import payment_router

    assert not payment_router.is_payment(said)


def test_ordering_a_payment_is_still_ordering_one() -> None:
    """The rule leans towards reading a sentence as a question, and it still has to let a real
    instruction through — a payment dropped into the read path is a payment that cannot be made."""
    from finguard_orchestrator.domain.services import payment_router

    assert payment_router.is_payment("hazle un bizum de 20 € a Marta")
    assert payment_router.is_payment("mándale 15 euros a Javi")


def test_a_word_answering_an_open_question_is_not_a_payment_order() -> None:
    """A word offered as the answer to a question, read as an order.

    "Pues bizum", answering "¿de qué comercio o categoría?", started a payment and asked who to send
    it to. A one-word reply while a question is open belongs to the question.
    """
    client, _, ledger = wired(
        {
            "de algun otro": PlanningResult.needs_clarification("¿De qué comercio o categoría?"),
            "algún otro": PlanningResult.needs_clarification("¿De qué comercio o categoría?"),
            "bizum": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.INCOME,
                    period=RelativePeriod(period=Period.LAST_N_MONTHS, n=2),
                    categories=("TRANSFER_BIZUM",),
                )
            ),
        }
    )
    chat = Chat(client)

    chat.say("Y de algún otro en los 2 ultimos meses")
    answered = chat.say("pues bizum")

    assert answered["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].categories == ("TRANSFER_BIZUM",)


def test_a_real_instruction_still_interrupts_an_open_question() -> None:
    """The other half, and what keeps the rule narrow: a figure is what tells a change of subject
    from a one-word reply. Losing a payment to a clarification nobody remembers is the failure in
    the opposite direction."""
    client, _, _ = wired({"llevo gastado": PlanningResult.needs_clarification("¿De qué periodo?")})
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    paid = chat.say("hazle un bizum de 20 € a Marta")

    # Payments are off in this harness, so the honest answer is that it cannot make one — which is
    # the payment path answering, and that is what this asserts.
    assert "pagos" in paid["text"]


def test_how_much_have_i_invested_is_not_answered_with_everything_that_came_in() -> None:
    """The worst answer of the second conversation. "¿Cuánto he invertido en el último año?" came
    back 30.279,48 € over 35 movements, and the movements listed were the salary — every euro that
    arrived, labelled as what the holder had put away.

    Money reaching the fund arrives as an internal transfer, so this is the same contract gap as the
    first turn of the first conversation, wearing a different word. The refusal says where the
    figure actually is, and that question works.
    """
    client, planner, ledger = wired()

    refused = Chat(client).say("Caunto he invertido en el ultimo año ?")

    assert refused["outcome"] == PlanningOutcome.UNSUPPORTED.value
    assert ledger.plans == [] and planner.asked == []
    assert "cuánto tengo en el fondo" in refused["text"]


def test_and_the_question_it_points_at_answers() -> None:
    """A refusal that names a question has to name one that works, or it is a second dead end."""
    client, _, _ = wired()

    answer = Chat(client).say("¿cuánto tengo en el fondo?")

    assert "2.700,00 €" in answer["text"]
    assert "no lo que vale hoy" in answer["text"]


# =========================================================================================
# The third conversation, in one walk. Its failures compounded like the first one's: a
# balance question answered as a spending query left the next turn looking like a new
# subject, and the loop detector then refused a question nobody had asked twice.
# =========================================================================================


def test_the_third_transcript_end_to_end() -> None:
    """Eight turns as they were typed, and the model is needed for none of them.

    Everything here is a closed vocabulary — the accounts, the balance, the destination, the amount
    — and a model in any of it is a different answer on a machine with a different one installed.
    """
    client, planner, ledger = wired()
    chat = Chat(client)

    state = chat.say("Oye cual es el estado actual de mi hucha de ahorros")
    saldo = chat.say("Cual es el saldo de mi hucha")
    sending = chat.say("Me gustaria enviarme dinero a la cuenta corriente")
    amount = chat.say("300 euros")
    between = chat.say("Me gustaria tranferir dinero entre cuentas")
    which = chat.say("a la hucha")
    fund = chat.say("quiero traspasar del fondo a hucha")
    done = chat.say("1000 pavos")

    assert state["text"] == "Tienes 5.485,50 € en Hucha."
    assert saldo["text"] == "Tienes 5.485,50 € en Hucha."
    assert sending["conversation"]["awaiting"] == "AMOUNT"
    assert amount["outcome"] == Spoken.TRANSFER_READY.value
    assert amount["total_minor"] == 300_00
    assert between["conversation"]["awaiting"] == "ACCOUNT"
    assert which["conversation"]["awaiting"] == "AMOUNT"
    assert fund["conversation"]["awaiting"] == "AMOUNT"
    assert done["total_minor"] == 100_000
    assert planner.asked == [] and ledger.plans == []


def test_the_first_two_turns_no_longer_cascade() -> None:
    """Turns one to three of the third transcript, and the reason a wrong answer is expensive.

        "¿el estado actual de mi hucha?"  →  "¿de qué periodo?"
        "el total"                        →  "sigo sin tener claro el periodo"
        "este año"                        →  14.019,05 €, every movement of the year

    Three turns to reach a figure about everything, from a question about one account's balance.
    None of the three questions was about a window and all three were answered about one.
    """
    client, _, ledger = wired()
    chat = Chat(client)

    first = chat.say("Oye cual es el estado actual de mi hucha de ahorros")
    second = chat.say("El total")

    assert first["outcome"] == Spoken.BALANCE.value
    assert first["conversation"]["awaiting"] is None, "it asked a question it did not need to"
    assert (
        second["text"]
        != "Sigo sin tener claro el periodo. Dime uno de estos, o unas fechas concretas."
    )
    assert ledger.plans == []


# --- what the new state may and may not survive -------------------------------------------


def test_a_transfer_waiting_for_an_amount_survives_an_aside() -> None:
    """The same rule every other state follows. Asking who you are talking to, in the middle of
    moving money, must not lose the account that was already settled."""
    client, _, _ = wired()
    chat = Chat(client)

    chat.say("quiero traspasar del fondo a hucha")
    aside = chat.say("¿quién eres?")
    finished = chat.say("1000 pavos")

    assert aside["outcome"] == Spoken.IDENTITY.value
    assert finished["outcome"] == Spoken.TRANSFER_READY.value
    assert finished["total_minor"] == 100_000


def test_a_question_in_the_middle_of_a_transfer_is_answered_as_a_question() -> None:
    """The other direction, and the one that costs more. A transfer under way must not swallow a
    change of subject — "¿cuánto gasté el mes pasado?" is a question however open the transfer is,
    and answering it with "¿cuánto quieres mover?" would be the payment defect all over again.
    """
    spending = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
    )
    client, _, ledger = wired({"gasté": PlanningResult.planned(spending)})
    chat = Chat(client)

    chat.say("quiero traspasar del fondo a hucha")
    asked = chat.say("¿cuánto gasté el mes pasado?")

    assert asked["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans, "a question during a transfer never reached the ledger"


def test_a_finished_transfer_leaves_nothing_open() -> None:
    """Read in full, so there is nothing to answer. What is left is the confirmation, and that is a
    screen with a `payments:prepare` scope behind it rather than a turn of this chat — a read path
    that quietly gained the ability to stage a payment would erase the seam."""
    client, _, _ = wired()

    answer = Chat(client).say("mueve 200 € a la hucha")

    assert answer["outcome"] == Spoken.TRANSFER_READY.value
    assert answer["conversation"]["workflow"] is None
    assert answer["conversation"]["transfer_amount_minor"] is None


def test_a_transfer_state_survives_the_round_trip_through_the_client() -> None:
    """It crosses the wire twice per turn, through a Pydantic model on each side, and either
    crossing can drop a field. A dropped amount here is the question asked a second time."""
    client, _, _ = wired()
    chat = Chat(client)

    asked = chat.say("quiero traspasar del fondo a hucha")

    assert asked["conversation"]["workflow"] == "TRANSFER_IN_PROGRESS"
    assert asked["conversation"]["transfer_to"] is not None
    assert asked["conversation"]["awaiting"] == "AMOUNT"
