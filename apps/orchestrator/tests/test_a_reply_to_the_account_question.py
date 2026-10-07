"""A reply to «¿En qué cuenta quieres consultarlo?» answers the question that asked it.

Reported from the running demo, by somebody asking ordinary questions:

    ¿Gasté más en gasolina en junio o en julio?  → "¿En qué cuenta quieres consultarlo?"
    Todas mis cuentas                            → "Tienes 8.571,98 € entre tus cuentas…"

The second answer is a true figure about the wrong question. The comparison was asked by a reader
(`_direct_period_comparison`), and a reader that meets several accounts reaches the scope question
through `_scope_question`, the backstop, which leaves no resumable workflow behind — only
`last_asked`. So the chip was read as a fresh sentence, and in `hybrid` mode the word «cuentas» is
exactly what makes the model call it a balance. «¿Qué suscripciones tengo?» failed the same way.

Driven over HTTP on the deployment's stack (an account directory, three accounts), with a turn
interpreter whose model reads every short reply as a balance — which is what the real one does with
these two — so each test here fails on the code that produced the report. The oracle for "answers
the pending question" is the same question asked in a conversation whose scope was already settled
that way: same text, same reads.
"""

import uuid
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from support import episodes

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.ask_financial_question import (
    _asked_which_account_about,
)
from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.constants import MAX_TURN_SENTENCE
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot, Turn
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic, Hybrid
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

COMPARISON = "¿Gasté más en gasolina en junio o en julio?"
SUBSCRIPTIONS = "¿Qué suscripciones tengo?"
WHICH_ACCOUNT = "¿En qué cuenta quieres consultarlo?"
# What the backstop offers on this stack: every account with a name to show.
OFFERED = tuple(holding.display_name for holding in episodes.HOLDINGS)


class CallsItABalance:
    """The model behind the port, reading a short reply about accounts the way the real one does.

    Its prompt lists «¿qué cuentas tengo?» as a balance example, so «Todas mis cuentas» and
    «Cuenta corriente» come back ACCOUNT_BALANCE whenever `Hybrid` thinks they are worth asking
    about — which the stem «cuent» guarantees.
    """

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        return Intent.ACCOUNT_BALANCE


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def hybrid_stack() -> episodes.Stack:
    stack = episodes.a_stack()
    app.dependency_overrides[turn_interpreter] = lambda: Hybrid(CallsItABalance(), Deterministic())
    return stack


def ask(
    stack: episodes.Stack, question: str, conversation: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = stack.client.post(
        "/v1/questions", json={"question": question, "conversation": conversation}
    )
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


def asked_which_account(stack: episodes.Stack, question: str) -> dict[str, Any]:
    """The first turn of every conversation here: the backstop asking, with nothing to resume."""
    asked = ask(stack, question)
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    assert asked["question"] == WHICH_ACCOUNT
    assert asked["conversation"]["workflow"] is None, "the backstop leaves no workflow behind"
    assert asked["conversation"]["last_asked"] == "ACCOUNT"
    assert stack.ledger.ran == [], "no figure is read before the holder has said whose money"
    conversation: dict[str, Any] = asked["conversation"]
    return conversation


def settled(question: str, scope: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """The same question in a conversation that had already chosen this scope."""
    stack = hybrid_stack()
    answer = ask(stack, question, scope)
    return answer, list(stack.ledger.ran)


@pytest.mark.parametrize("question", [COMPARISON, SUBSCRIPTIONS])
def test_all_my_accounts_answers_the_question_that_asked(question: str) -> None:
    stack = hybrid_stack()
    conversation = asked_which_account(stack, question)

    answered = ask(stack, "Todas mis cuentas", conversation)

    expected, expected_reads = settled(question, {"account_selection_mode": "ALL"})
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert "entre tus cuentas" not in answered["text"], "the balance, not the question asked"
    assert answered["text"] == expected["text"]
    assert stack.ledger.ran == expected_reads
    assert stack.ledger.ran and all(
        isinstance(plan, FinancialPlan) and plan.across_accounts for plan in stack.ledger.ran
    )
    # Chosen once, and remembered like a choice made on the resumable path.
    assert answered["conversation"]["account_selection_mode"] == "ALL"
    assert answered["conversation"]["last_asked"] is None


def test_one_named_account_answers_the_question_scoped_to_it() -> None:
    stack = hybrid_stack()
    conversation = asked_which_account(stack, COMPARISON)

    answered = ask(stack, "Cuenta corriente", conversation)

    expected, expected_reads = settled(
        COMPARISON,
        {"account_selection_mode": "SELECTED", "account_selection_ids": [str(episodes.ACCOUNT)]},
    )
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert answered["text"] == expected["text"]
    assert stack.ledger.ran == expected_reads
    # Both halves of the comparison, each read from the current account and nothing else.
    assert len(stack.ledger.ran) == len(("junio", "julio"))
    for plan in stack.ledger.ran:
        assert isinstance(plan, FinancialPlan)
        assert plan.categories == ("TRANSPORT_FUEL",)
        assert plan.only_account_id == episodes.ACCOUNT
        assert not plan.across_accounts
    assert answered["conversation"]["account_selection_mode"] == "SELECTED"
    assert answered["conversation"]["account_selection_ids"] == [str(episodes.ACCOUNT)]


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        # A period is not an account: the planner reads it, and asks for the account itself.
        ("el mes pasado", "NEEDS_CLARIFICATION"),
        # A balance question names no account to choose, so it is the balance it always was.
        ("¿cuánto tengo?", "BALANCE"),
    ],
)
def test_a_reply_that_names_no_account_choice_is_read_as_before(reply: str, outcome: str) -> None:
    stack = hybrid_stack()
    conversation = asked_which_account(stack, COMPARISON)

    answered = ask(stack, reply, conversation)

    assert answered["outcome"] == outcome, answered["text"]
    assert stack.ledger.ran == [], "the pending comparison was not run behind the reply"
    assert answered["conversation"]["account_selection_mode"] is None


def test_an_order_naming_an_account_is_still_that_order() -> None:
    """«Traspasa 100 a hucha» is four words naming an account, so by its words it is a chip — and
    it is an order to move money, which owns its turn whatever is pending. Only the intent guard in
    `_answering_the_scope_question` tells the two apart: without it the comparison ran in Hucha."""
    stack = hybrid_stack()
    conversation = asked_which_account(stack, COMPARISON)

    answered = ask(stack, "traspasa 100 a hucha", conversation)

    assert answered["outcome"] == "AWAITING_CONFIRMATION", answered["text"]
    assert stack.ledger.ran == [], "the pending comparison was not run behind the order"
    assert answered["conversation"]["account_selection_mode"] is None


async def test_a_payment_reading_is_never_taken_for_the_answer() -> None:
    """The payment half of the same guard, at the handler, because that is the only place it can
    be reached: the edge sends every payment reading down the payment path before this handler,
    except a bare rail word («un bizum») while a question is open — and a bare rail word names no
    account. A caller handing the handler its own reading still gets the rule: «todas mis cuentas
    por bizum» is a chip by its words, and read as a payment it is not the answer."""
    ledger = episodes.Ledger()
    handler = AskFinancialQuestionHandler(
        episodes.AnyPlan(),  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=episodes.Directory(),  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
    )
    question = await handler.handle(
        AskFinancialQuestionQuery(COMPARISON, episodes.ACCOUNT, episodes.TODAY)
    )
    assert question.question == WHICH_ACCOUNT

    await handler.handle(
        AskFinancialQuestionQuery(
            "todas mis cuentas por bizum",
            episodes.ACCOUNT,
            episodes.TODAY,
            conversation=question.conversation,
            intent=Intent.PAYMENT,
        )
    )

    assert not [
        plan
        for plan in ledger.ran
        if isinstance(plan, FinancialPlan) and plan.categories == ("TRANSPORT_FUEL",)
    ], "the pending comparison was run behind a payment"


def test_the_question_survives_an_aside_like_the_resumable_one_does() -> None:
    """«Gracias» asks nothing and leaves `last_asked` alone, so the chip after it still answers
    the comparison — the way `31-account-scope.yaml` lets an aside sit inside the resumable one."""
    stack = hybrid_stack()
    conversation = asked_which_account(stack, COMPARISON)
    aside = ask(stack, "gracias", conversation)
    assert aside["outcome"] == "COURTESY"

    answered = ask(stack, "Todas mis cuentas", aside["conversation"])

    expected, _ = settled(COMPARISON, {"account_selection_mode": "ALL"})
    assert answered["text"] == expected["text"]


def test_a_transfer_the_question_ended_stays_ended() -> None:
    """The comparison is a change of subject, so `_steps` ends the transfer that was waiting for an
    amount — and the backstop, built from the conversation the turn *arrived* with, put it back.
    With a transfer awaiting AMOUNT the account question is not the open one, so the chip was read
    as a sentence of its own and answered the balance: the reported defect, one turn later."""
    stack = hybrid_stack()
    opened = ask(stack, "quiero traspasar dinero a la hucha")
    assert opened["conversation"]["workflow"] == "TRANSFER_IN_PROGRESS"

    asked = ask(stack, COMPARISON, opened["conversation"])
    assert asked["question"] == WHICH_ACCOUNT
    assert asked["conversation"]["workflow"] is None, "the transfer the comparison ended"
    assert asked["conversation"]["last_asked"] == "ACCOUNT"

    answered = ask(stack, "Todas mis cuentas", asked["conversation"])

    expected, expected_reads = settled(COMPARISON, {"account_selection_mode": "ALL"})
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert answered["text"] == expected["text"]
    assert stack.ledger.ran == expected_reads


# --- what the reply is read against ---------------------------------------------------------


def asked(sentence: str) -> Conversation:
    """The state the backstop leaves, as `stream` records it: the slot, and the turn that asked."""
    return IDLE.asking(Slot.ACCOUNT).with_turn(
        Turn(sentence=sentence, outcome="NEEDS_CLARIFICATION", question=WHICH_ACCOUNT)
    )


def test_what_the_reply_is_read_against_survives_the_wire() -> None:
    """No field was added for this: the slot and the turn log already travel. This is the round
    trip the reply depends on, through JSON, so a mapper that dropped either is caught here rather
    than as a balance answered in the demo."""
    out = asked(COMPARISON)

    payload = conversation_mapper.to_payload(out)
    echoed = ConversationPayload.model_validate(payload.model_dump(mode="json"))
    back = replace(conversation_mapper.to_domain(echoed), conversation_id=out.conversation_id)

    assert back == out
    assert _asked_which_account_about(back, OFFERED) == COMPARISON


def test_only_the_backstops_open_question_is_answered_this_way() -> None:
    assert _asked_which_account_about(asked(COMPARISON), OFFERED) == COMPARISON
    # Answered already: the turn after it cleared the slot.
    assert _asked_which_account_about(replace(asked(COMPARISON), last_asked=None), OFFERED) is None
    # A different question asked last, even about the account, is not this one.
    other = IDLE.asking(Slot.ACCOUNT).with_turn(
        Turn(sentence=COMPARISON, outcome="NEEDS_CLARIFICATION", question="¿Cuál?")
    )
    assert _asked_which_account_about(other, OFFERED) is None


def test_a_sentence_the_log_may_have_cut_is_not_asked_again() -> None:
    """`Turn` keeps `MAX_TURN_SENTENCE` characters. A sentence at the limit may be the start of a
    longer one, and answering the start would answer something the holder did not ask."""
    cut = "¿" + "a" * MAX_TURN_SENTENCE

    assert _asked_which_account_about(asked(cut), OFFERED) is None
    assert _asked_which_account_about(asked(cut[: MAX_TURN_SENTENCE - 1]), OFFERED) is not None


def test_an_account_choice_is_never_the_question_pending() -> None:
    """A reply's own turn carries the account question when the question it resumed raised again,
    and its sentence is then the chip. Asked again as a question, «Cuenta corriente» or «Todas mis
    cuentas» is a balance — the defect this module exists for, one turn later."""
    for chip in ("Cuenta corriente", "Todas mis cuentas", "Fondo indexado"):
        assert _asked_which_account_about(asked(chip), OFFERED) is None, chip
    assert _asked_which_account_about(asked(COMPARISON), OFFERED) == COMPARISON


class ChangesAfterOneRead:
    """The holder's directory, and an account that is closed and reopened under a new id between
    two reads of one turn — the only way a resumed question can meet the account question again."""

    def __init__(self) -> None:
        self.reads_before_the_change: int | None = None

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        if self.reads_before_the_change is None:
            return episodes.HOLDINGS
        if self.reads_before_the_change > 0:
            self.reads_before_the_change -= 1
            return episodes.HOLDINGS
        reopened = Holding(uuid.uuid4(), "CURRENT", "Cuenta corriente", "EUR", 215_648)
        return (reopened, *episodes.HOLDINGS[1:])


async def test_a_second_question_on_the_reply_does_not_make_the_chip_the_question() -> None:
    planner, ledger, directory = episodes.AnyPlan(), episodes.Ledger(), ChangesAfterOneRead()
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=directory,  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
    )

    def turn(sentence: str, conversation: Conversation = IDLE) -> AskFinancialQuestionQuery:
        return AskFinancialQuestionQuery(
            sentence, episodes.ACCOUNT, episodes.TODAY, conversation=conversation
        )

    first = await handler.handle(turn(COMPARISON))
    # The reply's own read resolves «Cuenta corriente»; the resumed comparison's read finds that
    # account gone, so the comparison asks again, on the reply's turn.
    directory.reads_before_the_change = 1
    second = await handler.handle(turn("Cuenta corriente", first.conversation))
    assert second.question == WHICH_ACCOUNT
    asked_before = len(planner.asked)

    await handler.handle(turn("Todas mis cuentas", second.conversation))

    assert "Cuenta corriente" not in planner.asked[asked_before:], (
        "the chip of the turn before was asked as though it were the question"
    )


class HearsTheQuestion:
    """A visual planner behind the port that abstains, keeping the question it was handed."""

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def plan(self, question: str, *_: object, **__: object) -> None:
        self.questions.append(question)


async def test_the_chart_is_planned_for_the_question_not_the_chip() -> None:
    """The visual planner reads the question to choose a chart. On a resumed turn the question is
    the comparison; «Todas mis cuentas» is what the holder typed to settle its scope."""
    hears = HearsTheQuestion()
    handler = AskFinancialQuestionHandler(
        episodes.AnyPlan(),  # type: ignore[arg-type]
        episodes.Ledger(),  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=episodes.Directory(),  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
        visual_policy=VisualPlanningPolicy(
            model=hears,  # type: ignore[arg-type]
            fallback=DeterministicVisualPlanner(),
            verifier=verify,
            mode="shadow",
            budget_seconds=1.0,
        ),
    )
    question = await handler.handle(
        AskFinancialQuestionQuery(COMPARISON, episodes.ACCOUNT, episodes.TODAY)
    )

    answered = await handler.handle(
        AskFinancialQuestionQuery(
            "Todas mis cuentas",
            episodes.ACCOUNT,
            episodes.TODAY,
            conversation=question.conversation,
        )
    )

    assert answered.visual_dataset is not None, "the comparison draws a chart"
    assert hears.questions == [COMPARISON]
