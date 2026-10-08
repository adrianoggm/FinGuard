"""What a question means when it does not say, and the answer saying it (spec §3.5).

Measured live on 2026-10-07: «¿Cuánto llevo gastado en Spotify?» was asked which account and then
which period, two questions about things the person had a perfectly ordinary meaning for. The
defaults are the product's now — every account unless the person chose one, the year so far for
«llevo gastado», the last 30 days for «últimamente» — and each answer names the window it used, so a
default is never a silent guess. The old account question is one setting away
(`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask`).

Driven over HTTP on the episodes stack: three accounts, nothing settled.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from support import episodes
from support.pending import (
    SETTLED,
    AsksForTheWindow,
    KeepsTheBase,
    ask,
    asking_for_the_window_stack,
)

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import app, create_app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import answer_writer, category_words, explicit_facts
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.planning.llm_query_planner import WINDOW_QUESTION

# A window for a plan whose window the test does not read.
_ANY = RelativePeriod(Period.THIS_MONTH)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# --- the account -------------------------------------------------------------------------------


def test_an_unqualified_spending_question_does_not_ask_for_the_account() -> None:
    stack = episodes.a_stack()  # directory with three accounts, nothing settled
    answered = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert answered["outcome"] == "PLANNED"
    assert stack.ledger.plans[-1].across_accounts
    # A default is said (ruling R44, which reverses this line of the brief: «not in»).
    assert "Consultado en todas tus cuentas." in answered["text"]
    # And it is not a choice: nothing is stored, so the next question reads the default again.
    assert answered["conversation"].get("account_selection_mode") is None


def test_the_old_question_is_one_setting_away() -> None:
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"


def test_a_balance_still_lists_every_account() -> None:
    answered = ask(episodes.a_stack(), "¿cuánto tengo?")
    assert "Hucha" in answered["text"] and "Cuenta corriente" in answered["text"]


def test_a_payment_still_names_its_source_account() -> None:
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    previewed = ask(stack, "15 euros con concepto de comida", started["conversation"])
    assert previewed["outcome"] == "AWAITING_CONFIRMATION"
    assert stack.bank.prepared[-1].amount_minor == 1500  # unchanged by the analytics default


def test_a_payment_leaves_from_the_credentials_account_under_either_setting() -> None:
    """The analytics default is about which figures answer a question. A payment's source is the
    account the credential names, and neither value of the setting reaches it."""
    for setting in ("all", "ask"):
        stack = episodes.a_stack(account_scope_default=setting)
        started = ask(stack, "hagamos un bizum a Marta Solis Vega")
        ask(stack, "15 euros con concepto de comida", started["conversation"])
        assert stack.bank.sources == [episodes.ACCOUNT], setting


def test_an_account_named_in_the_sentence_still_wins() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto gasté en la hucha en julio?")
    ran = stack.ledger.plans[-1]
    assert not ran.across_accounts and ran.only_account_id == episodes.PIGGY
    assert "Consultado en Hucha." in answered["text"]


def test_an_account_chosen_earlier_in_the_conversation_still_wins() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "¿Cuánto gasté en la hucha en julio?")
    ask(stack, "¿Cuánto gasté en restaurantes en junio?", first["conversation"])
    ran = stack.ledger.plans[-1]
    assert not ran.across_accounts and ran.only_account_id == episodes.PIGGY


def test_an_account_chosen_earlier_is_said_by_its_name() -> None:
    """Final review, M-2: «Consultado en la cuenta seleccionada.» named nothing — the question did
    not name the hucha, and the line under its figure should."""
    stack = episodes.a_stack()
    first = ask(stack, "¿Cuánto gasté en la hucha en julio?")
    answered = ask(stack, "¿Cuánto gasté en restaurantes en junio?", first["conversation"])
    assert answered["text"].endswith("\nConsultado en Hucha."), answered["text"]


def test_a_ranking_over_an_account_chosen_earlier_says_its_name() -> None:
    stack = episodes.a_stack()
    chosen = {"account_selection_mode": "SELECTED", "account_selection_ids": [str(episodes.FUND)]}
    answered = ask(stack, "¿cuál fue mi mayor gasto el mes pasado?", chosen)
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert answered["text"].endswith("\nConsultado en Fondo indexado."), answered["text"]


def test_every_account_asked_for_in_words_is_not_said_back() -> None:
    """Ruling R44: the turn said it, so the answer does not repeat it."""
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto gasté en julio en todas mis cuentas?")
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas" not in answered["text"]


def test_every_account_chosen_earlier_is_said() -> None:
    """A persisted choice of every account is not this turn's words (ruling R44)."""
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto gasté en julio?", SETTLED)
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas." in answered["text"]


def test_every_account_chosen_at_the_question_is_said() -> None:
    """Under `ask`, the chip that answers the question is the scope chosen this turn, and the answer
    confirms it — the line a later turn that never mentions accounts goes on saying there, because
    every account was the person's choice rather than the default."""
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    answered = ask(stack, "Todas mis cuentas", asked["conversation"])
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas." in answered["text"]


# --- the setting -------------------------------------------------------------------------------


@pytest.mark.parametrize("chosen", ["all", "ask"])
def test_the_setting_reaches_the_handler(monkeypatch: pytest.MonkeyPatch, chosen: str) -> None:
    """Through `create_app`, because what could break is the wiring: a handler built without the
    setting would run on its own default whatever the deployment said."""
    monkeypatch.setenv("ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT", chosen)
    get_settings.cache_clear()
    try:
        built = create_app()
    finally:
        get_settings.cache_clear()
    assert built.state.ask_handler._account_scope_default == chosen  # noqa: SLF001


def test_every_account_is_what_ships() -> None:
    """Read from the class and from `.env.defaults`, not from an instance, so a developer whose own
    `.env` asks the question does not fail a test about what the code ships with."""
    assert Settings.model_fields["account_scope_default"].default == "all"
    shipped = (Path(__file__).resolve().parents[1] / ".env.defaults").read_text(encoding="utf-8")
    assert "\nORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=all\n" in shipped


# --- the window --------------------------------------------------------------------------------


def test_llevo_gastado_is_the_year_so_far_and_says_so() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR
    assert "en lo que va de año" in answered["text"]


def test_ultimamente_is_the_last_thirty_days_and_says_so() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto he gastado últimamente en restaurantes?")
    assert stack.ledger.plans[-1].period == RelativePeriod(Period.LAST_N_DAYS, n=30)
    assert "últimos 30 días" in answered["text"]


def test_llevo_gastado_with_a_window_keeps_that_window() -> None:
    stack = episodes.a_stack()
    ask(stack, "¿cuánto llevo gastado este mes?")
    assert stack.ledger.plans[-1].period.period is Period.THIS_MONTH
    ask(stack, "¿cuánto llevo gastado desde el lunes?")
    assert stack.ledger.plans[-1].period.period is Period.THIS_WEEK


class AsksLikeItsPrompt(AsksForTheWindow):
    """The planner as its pinned prompt teaches it: «¿Cuánto he gastado últimamente?» is a question
    to ask about, and so is any sentence it finds no window in — whatever `window_words` reads."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            WINDOW_QUESTION,
            FinancialPlanDraft(
                direction=Direction.EXPENSE,
                categories=tuple(category_words.precisely_named_in(sentence)),
            ),
            Slot.WINDOW,
        )


@pytest.mark.parametrize(
    ("sentence", "window"),
    [
        ("¿Cuánto he gastado últimamente?", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("¿Cuánto llevo gastado?", RelativePeriod(Period.THIS_YEAR)),
    ],
)
def test_the_model_does_not_ask_for_a_window_the_sentence_names(
    sentence: str, window: RelativePeriod
) -> None:
    """The live path. The planner asks — its prompt says to — and the draft it stopped on is
    completed with the window the sentence names, as a reply would have completed it a turn later.
    No category is named, so the rescue that builds a plan from the sentence alone cannot."""
    stack = asking_for_the_window_stack(planner=AsksLikeItsPrompt())
    answered = ask(stack, sentence)
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert stack.ledger.plans[-1].period == window


def test_a_window_nothing_here_reads_is_still_asked_about() -> None:
    """«Desde Navidad» is a window, and not the year so far: the model's question stands."""
    stack = asking_for_the_window_stack(planner=AsksLikeItsPrompt())
    asked = ask(stack, "¿Cuánto llevo gastado desde Navidad?")
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    assert stack.ledger.plans == []


def test_a_window_the_sentence_states_is_still_the_models_to_ask_about() -> None:
    """Only the two defaults answer the model's window question. One the sentence states and the
    model still asked about is a reading the vocabulary and the model disagree on, and the question
    stands, as it always did."""
    stack = asking_for_the_window_stack(planner=AsksLikeItsPrompt())
    asked = ask(stack, "¿cuánto gasté este mes?")
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    assert stack.ledger.plans == []


class AsksAboutThisDraft(AsksForTheWindow):
    """A model that asks for the window over the draft it was built with, whatever it is told."""

    def __init__(self, draft: FinancialPlanDraft) -> None:
        super().__init__()
        self._draft = draft

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(WINDOW_QUESTION, self._draft, Slot.WINDOW)


@pytest.mark.parametrize(
    ("sentence", "draft"),
    [
        (
            "¿he gastado más en Mercadona o en Lidl últimamente?",
            FinancialPlanDraft(direction=Direction.EXPENSE, merchants=("Mercadona", "Lidl")),
        ),
        (
            "¿llevo gastado más en Mercadona o en Lidl?",
            FinancialPlanDraft(direction=Direction.EXPENSE, merchants=("Mercadona", "Lidl")),
        ),
        (
            "¿he gastado más en Marta o en Javi últimamente?",
            FinancialPlanDraft(direction=Direction.EXPENSE, counterparties=("Marta", "Javi")),
        ),
    ],
)
def test_a_comparison_is_not_answered_with_one_window(
    sentence: str, draft: FinancialPlanDraft
) -> None:
    """Rulings R42 and R46 (I-3). Two shops or two people, compared over a default window the
    model asked about: the draft completed with that window would be one figure — the two summed —
    for a question about which is more. The model's question stands instead. (Two categories named
    precisely never reach this: `_from_what_the_sentence_said` builds them a breakdown.)"""
    stack = asking_for_the_window_stack(planner=AsksAboutThisDraft(draft))
    asked = ask(stack, sentence)
    assert asked["outcome"] == "NEEDS_CLARIFICATION", asked["text"]
    summed = [
        plan
        for plan in stack.ledger.plans
        if len(plan.merchants) > 1 or len(plan.counterparties) > 1 or len(plan.categories) > 1
    ]
    assert summed == []


# --- the period question ------------------------------------------------------------------------


def test_the_period_question_names_what_it_is_about() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["question"] == "¿De qué periodo quieres ver tus gastos en restaurantes?"
    assert asked["conversation"]["last_asked"] == "WINDOW"
    # The examples stay: each is a phrase the reply is read back with.
    assert asked["text"] == (
        "¿De qué periodo quieres ver tus gastos en restaurantes? "
        "Por ejemplo este mes, el mes pasado o los últimos 30 días."
    )


def test_the_period_question_asked_again_names_the_person() -> None:
    """The reply named an account and no period: the question comes back over the draft that kept
    the account, and says whose money it is about."""
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "¿cuánto le he mandado a Marta Solis Vega?")
    told = ask(stack, "en la hucha", asked["conversation"])
    assert told["question"] == "¿De qué periodo quieres ver lo que le mandaste a Marta Solis Vega?"


def test_plain_spending_keeps_the_plain_question() -> None:
    """Spending is what a bare «¿cuánto…?» is about; there is nothing to name."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté?")
    assert asked["question"] == WINDOW_QUESTION
    assert WINDOW_QUESTION == (
        "¿De qué periodo? Por ejemplo este mes, el mes pasado o los últimos 30 días."
    ), "the planner's own text is pinned"


class AsksInItsOwnWords(AsksForTheWindow):
    """A model that wrote its own window question."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            "¿Qué periodo te interesa?",
            FinancialPlanDraft(direction=Direction.EXPENSE, categories=("RESTAURANTS",)),
            Slot.WINDOW,
        )


def test_a_question_the_model_wrote_is_asked_as_written() -> None:
    """A sentence that names no category, so the question reaching the person is the model's (one
    that names a category is rebuilt from the sentence, `_from_what_the_sentence_said`)."""
    stack = asking_for_the_window_stack(planner=AsksInItsOwnWords())
    asked = ask(stack, "¿cuánto fue aquello?")
    assert asked["text"] == asked["question"] == "¿Qué periodo te interesa?"


@pytest.mark.parametrize(
    ("question", "asked_back"),
    [
        ("¿cuánto ingresé en la hucha?", "¿De qué periodo quieres ver tus ingresos?"),
        ("¿cuánto gasté en gasolina?", "¿De qué periodo quieres ver tus gastos en gasolina?"),
        ("¿y en suscripciones?", "¿De qué periodo quieres ver tus gastos en suscripciones?"),
        ("¿cuánto he gastado en la hucha?", WINDOW_QUESTION),
        ("dame los gastos de la hucha por categoría", WINDOW_QUESTION),
    ],
)
def test_a_question_of_its_own_gets_its_own_period_question(question: str, asked_back: str) -> None:
    """Carried from Task 5: a question of its own asked while the period question waited was
    answered «Sigo sin tener claro el periodo» — the reply to a turn that answered nothing — about a
    question the person had only just asked. It is asked its own period, about its own subject."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, question, asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION"
    assert told["question"] == asked_back
    assert "Sigo sin tener claro" not in told["text"]
    # Recorded as asked, so a reply to this one that answers nothing is the repeat.
    assert told["conversation"]["last_asked"] == "WINDOW"


def test_a_reply_that_answers_nothing_still_gets_the_choices() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, "pues no sé", asked["conversation"])
    assert told["text"].startswith("Sigo sin tener claro el periodo")
    assert told["suggestions"]


@pytest.mark.parametrize(
    ("plan", "asked"),
    [
        (
            FinancialPlan(direction=Direction.EXPENSE, period=_ANY, categories=("RESTAURANTS",)),
            "¿De qué periodo quieres ver tus gastos en restaurantes?",
        ),
        (
            FinancialPlan(direction=Direction.EXPENSE, period=_ANY, counterparties=("Marta",)),
            "¿De qué periodo quieres ver lo que le mandaste a Marta?",
        ),
        (
            FinancialPlan(direction=Direction.INCOME, period=_ANY, counterparties=("Marta",)),
            "¿De qué periodo quieres ver lo que recibiste de Marta?",
        ),
        # Several people are «les» (ruling R46).
        (
            FinancialPlan(
                direction=Direction.EXPENSE, period=_ANY, counterparties=("Marta", "Javi")
            ),
            "¿De qué periodo quieres ver lo que les mandaste a Marta y Javi?",
        ),
        # The payroll is yours, not «tus ingresos en nómina» (ruling R46).
        (
            FinancialPlan(direction=Direction.INCOME, period=_ANY, categories=("SALARY",)),
            "¿De qué periodo quieres ver tu nómina?",
        ),
        (
            FinancialPlan(direction=Direction.EXPENSE, period=_ANY, merchants=("Spotify",)),
            "¿De qué periodo quieres ver tus gastos en Spotify?",
        ),
        (
            FinancialPlan(
                direction=Direction.EXPENSE, period=_ANY, excluded_categories=("TRANSPORT_FUEL",)
            ),
            "¿De qué periodo quieres ver tus gastos sin gasolina?",
        ),
        (
            FinancialPlan(direction=Direction.INCOME, period=_ANY),
            "¿De qué periodo quieres ver tus ingresos?",
        ),
        (FinancialPlan(direction=Direction.EXPENSE, period=_ANY), None),
    ],
)
def test_the_question_says_the_subject_in_the_answers_words(
    plan: FinancialPlan, asked: str | None
) -> None:
    assert answer_writer.window_question(plan) == asked


# --- an account named beside the period question (ruling R46, C-1) -----------------------------
#
# The account a question names is read when the question runs, from its own sentence. A question
# the planner answers with «¿De qué periodo?» runs on the reply's turn, and «este año» names no
# account: under `all` the figure came from every account, with no line to say so.


@pytest.mark.parametrize("setting", ["all", "ask"])
@pytest.mark.parametrize(
    ("question", "account"),
    [
        ("¿cuánto he gastado en la hucha?", episodes.PIGGY),
        ("¿cuánto he gastado en restaurantes en la hucha?", episodes.PIGGY),
        ("¿cuánto he gastado en Fondo indexado?", episodes.FUND),
    ],
)
def test_an_account_named_beside_the_period_question_is_the_one_that_runs(
    setting: str, question: str, account: object
) -> None:
    stack = asking_for_the_window_stack(settled=False, account_scope_default=setting)
    asked = ask(stack, question)
    assert asked["outcome"] == "NEEDS_CLARIFICATION", asked["text"]
    # Kept on the draft, and the conversation's only once the question is answered (final review,
    # I-2): stored on the asking turn, it outlived a question the holder walked away from.
    assert asked["conversation"]["draft"]["only_account_id"] == str(account)
    assert asked["conversation"].get("account_selection_mode") is None
    answered = ask(stack, "este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert answered["conversation"]["account_selection_mode"] == "SELECTED"
    ran = stack.ledger.plans[-1]
    assert ran.only_account_id == account and not ran.across_accounts
    assert ran.period.period is Period.THIS_YEAR


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_a_new_question_naming_an_account_keeps_it_for_its_own_period(setting: str) -> None:
    """While «¿cuánto gasté en restaurantes?» waited for its period: the new question is asked its
    own period (Task 8), and the account it named is still its account when the period comes."""
    stack = asking_for_the_window_stack(account_scope_default=setting)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, "¿cuánto ingresé en la hucha?", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION", told["text"]
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    ran = stack.ledger.plans[-1]
    assert ran.only_account_id == episodes.PIGGY and ran.direction is Direction.INCOME
    assert ran.categories == ()


def test_every_account_named_beside_the_period_question_is_kept_under_ask() -> None:
    """«En todas mis cuentas» in the question settles the scope there and then: the period that
    follows is not asked which account as well."""
    stack = asking_for_the_window_stack(settled=False, account_scope_default="ask")
    asked = ask(stack, "¿cuánto he gastado en restaurantes en todas mis cuentas?")
    answered = ask(stack, "este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert stack.ledger.plans[-1].across_accounts


def test_the_payroll_is_not_the_current_account_beside_the_period_question() -> None:
    """«Nómina» alone is the payroll, as `_scoped` reads it (ruling R25): it narrows nothing."""
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "¿cuánto cobré de nómina?")
    assert asked["conversation"].get("account_selection_mode") is None


# --- the account of a question nobody answered (final review, I-2) ------------------------------
#
# The account a question waiting for its period names is that question's, and becomes the
# conversation's only when the question is answered. Stored as the holder's choice on the turn that
# asked, it outlived the question: «¿cuánto he gastado en la hucha?» → «¿De qué periodo?» →
# «olvídalo» → «¿cuánto gasté en julio?» ran the hucha alone.


@pytest.mark.parametrize(
    "between",
    [
        ("olvídalo",),
        ("¿cuánto tengo?",),
        ("mándale 20 € a Marta Solis Vega", "no"),
        ("¿cuánto gasté en restaurantes en julio?",),
    ],
)
def test_the_account_of_a_question_left_unanswered_is_not_the_conversations(
    between: tuple[str, ...],
) -> None:
    stack = asking_for_the_window_stack(settled=False, account_scope_default="all")
    asked = ask(stack, "¿cuánto he gastado en la hucha?")
    assert asked["outcome"] == "NEEDS_CLARIFICATION", asked["text"]
    conversation = asked["conversation"]
    for sentence in between:
        conversation = ask(stack, sentence, conversation)["conversation"]
    answered = ask(stack, "¿cuánto gasté en julio?", conversation)
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert all(
        plan.only_account_id != episodes.PIGGY and plan.across_accounts
        for plan in stack.ledger.plans
    ), [(plan.only_account_id, plan.across_accounts) for plan in stack.ledger.plans]


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_the_account_of_the_question_is_remembered_once_it_is_answered(setting: str) -> None:
    """The other side of I-2: answered, the hucha is the conversation's, and the next question
    naming no account reads it, as a question that named it and ran at once does."""
    stack = asking_for_the_window_stack(settled=False, account_scope_default=setting)
    asked = ask(stack, "¿cuánto he gastado en la hucha?")
    answered = ask(stack, "este año", asked["conversation"])
    assert answered["conversation"]["account_selection_mode"] == "SELECTED"
    ask(stack, "¿cuánto gasté en julio?", answered["conversation"])
    assert stack.ledger.plans[-1].only_account_id == episodes.PIGGY


# --- rankings (ruling R46, I-1) ----------------------------------------------------------------
#
# A ranking scopes itself (`_scope_ranking`), and it asked the account question of its own.

_RANKINGS = (
    "¿cuál fue mi mayor gasto el mes pasado?",
    "¿cuáles fueron mis 3 mayores gastos el mes pasado?",
    "¿cuál fue mi mayor ingreso este año?",
)


@pytest.mark.parametrize("question", _RANKINGS)
def test_a_ranking_reads_every_account_by_default(question: str) -> None:
    stack = episodes.a_stack()
    answered = ask(stack, question)
    assert answered["outcome"] == "PLANNED", answered["text"]
    ran = stack.ledger.ran[-1]
    assert isinstance(ran, TopMovements) and ran.across_accounts


@pytest.mark.parametrize("question", _RANKINGS)
def test_a_ranking_asks_the_account_when_the_deployment_asks(question: str) -> None:
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, question)
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"
    assert stack.ledger.ran == []


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_a_ranking_in_a_named_account_stays_there(setting: str) -> None:
    stack = episodes.a_stack(account_scope_default=setting)
    ask(stack, "¿cuál fue mi mayor gasto en la hucha el mes pasado?")
    ran = stack.ledger.ran[-1]
    assert ran.only_account_id == episodes.PIGGY and not ran.across_accounts


# --- «llevo gastado» up to now, and a default as the reply (ruling R46) -------------------------


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto llevo gastado hasta ahora?",
        "¿cuánto llevo gastado hasta la fecha?",
        "¿cuánto llevo gastado hasta hoy?",
    ],
)
def test_llevo_gastado_up_to_now_is_the_year_so_far_and_says_so(question: str) -> None:
    stack = episodes.a_stack()
    answered = ask(stack, question)
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR
    assert "en lo que va de año" in answered["text"]


@pytest.mark.parametrize(
    ("reply", "window"),
    [
        ("últimamente", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("¿y últimamente?", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("lo que he gastado últimamente", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("lo que llevo gastado", RelativePeriod(Period.THIS_YEAR)),
        ("lo que llevo gastado hasta ahora", RelativePeriod(Period.THIS_YEAR)),
    ],
)
def test_a_default_given_as_the_reply_fills_the_period_of_the_open_question(
    reply: str, window: RelativePeriod
) -> None:
    """Ruling R46. A reply to «¿De qué periodo…?» is about the money that question is about, so
    «últimamente» needs no word for money there, and «lo que llevo gastado» is not a new question
    about all spending: both complete the restaurants question, without the model."""
    planner = KeepsTheBase()
    stack = asking_for_the_window_stack(planner=planner)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    answered = ask(stack, reply, asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    ran = stack.ledger.plans[-1]
    assert ran.categories == ("RESTAURANTS",) and ran.period == window
    assert reply not in planner.asked, "the period was filled in code"


# --- an account left out (ruling R46) ------------------------------------------------------------


@pytest.mark.parametrize("setting", ["all", "ask"])
@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto gasté el mes pasado en todas menos la hucha?",
        "¿cuánto gasté el mes pasado sin contar la hucha?",
        "¿cuánto gasté el mes pasado sin la hucha?",
    ],
)
def test_an_account_left_out_is_never_the_one_read(setting: str, question: str) -> None:
    """«Sin contar la hucha» ran only the hucha — the one account the person left out, a wrong
    figure under both settings."""
    stack = episodes.a_stack(account_scope_default=setting)
    ask(stack, question)
    assert all(
        plan.only_account_id != episodes.PIGGY and episodes.PIGGY not in plan.only_account_ids
        for plan in stack.ledger.plans
    )


# Every account but the hucha, in the directory's order.
_ALL_BUT_THE_HUCHA = (episodes.ACCOUNT, episodes.FUND)


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto gasté el mes pasado en todas menos la hucha?",
        "¿cuánto gasté el mes pasado sin contar la hucha?",
        "¿cuánto gasté el mes pasado sin la hucha?",
    ],
)
def test_an_account_left_out_leaves_every_other_one_under_the_default(question: str) -> None:
    """Ruling R48. An account after «sin (contar)» or «menos» is an account left out, never a shop
    left out: under `all` the figure is every other account, and nothing says «sin la hucha» over a
    figure that holds it."""
    stack = episodes.a_stack()
    answered = ask(stack, question)
    assert answered["outcome"] == "PLANNED", answered["text"]
    ran = stack.ledger.plans[-1]
    assert ran.only_account_ids == _ALL_BUT_THE_HUCHA and not ran.across_accounts
    assert ran.excluded_merchants == ()
    assert "sin la hucha" not in answered["text"]
    # A subset, said by naming each account in it (final review, M-2) — never as the account left
    # out, and no longer as «la cuenta seleccionada», singular, over two accounts that said none.
    assert "Consultado en Cuenta corriente y Fondo indexado." in answered["text"]
    assert "Consultado en Hucha" not in answered["text"]


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto gasté el mes pasado sin contar la hucha?",
        "¿cuánto gasté el mes pasado sin la hucha?",
    ],
)
def test_an_account_left_out_is_asked_about_on_a_deployment_that_asks(question: str) -> None:
    """Under `ask`, as before: which accounts. And «Todas mis cuentas» to that is every account, so
    the answer does not say «sin la hucha» over a figure that holds it (ruling R48)."""
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, question)
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"
    answered = ask(stack, "Todas mis cuentas", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert stack.ledger.plans[-1].excluded_merchants == ()
    assert "sin la hucha" not in answered["text"]


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_a_shop_left_out_is_still_a_shop_left_out(setting: str) -> None:
    stack = episodes.a_stack(account_scope_default=setting)
    ask(stack, "¿cuánto gasté el mes pasado sin Mercadona?", SETTLED)
    assert [shop.casefold() for shop in stack.ledger.plans[-1].excluded_merchants] == ["mercadona"]


@pytest.mark.parametrize(
    ("sentence", "shops"),
    [
        ("¿cuánto gasté el mes pasado sin contar la hucha?", ()),
        ("¿cuánto gasté el mes pasado sin la hucha?", ()),
        ("¿cuánto gasté el mes pasado sin la cuenta Viajes?", ()),
        ("¿cuánto gasté el mes pasado sin Mercadona?", ("mercadona",)),
    ],
)
def test_explicit_facts_never_reads_an_account_as_a_shop_left_out(
    sentence: str, shops: tuple[str, ...]
) -> None:
    assert explicit_facts.excluded_merchants_in(sentence) == shops


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_a_comparison_with_an_account_left_out_leaves_every_other_one(setting: str) -> None:
    """Final review: under `ask` a comparison is scoped without the account question, and it ran
    every account — the hucha too — for «junio vs julio en restaurantes sin la hucha». Asked
    nothing, it reads what the sentence asked for, as `all` does."""
    stack = episodes.a_stack(account_scope_default=setting)
    answered = ask(stack, "junio vs julio en restaurantes sin la hucha")
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert len(stack.ledger.plans) == 2
    assert all(plan.only_account_ids == _ALL_BUT_THE_HUCHA for plan in stack.ledger.plans), [
        (plan.across_accounts, plan.only_account_ids) for plan in stack.ledger.plans
    ]


def test_a_ranking_with_an_account_left_out_leaves_every_other_one() -> None:
    stack = episodes.a_stack()
    ask(stack, "¿cuál fue mi mayor gasto el mes pasado sin la hucha?")
    ran = stack.ledger.ran[-1]
    assert isinstance(ran, TopMovements)
    assert ran.only_account_ids == _ALL_BUT_THE_HUCHA and ran.excluded_merchants == ()


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto he ingresado este año sin contar la nómina?",
        "¿cuánto he ingresado este año sin la nómina?",
        "¿cuánto he ingresado este año menos la nómina?",
    ],
)
def test_the_payroll_left_out_is_the_payroll_and_not_the_current_account(question: str) -> None:
    """Final review, I-3: ruling R25 for what is left out. «Nómina» without «cuenta» is the
    payroll, so «sin contar la nómina» is income without the payroll from every account — it ran
    income from the hucha and the fund only, leaving out the account the payroll is paid into."""
    stack = episodes.a_stack()
    answered = ask(stack, question)
    assert answered["outcome"] == "PLANNED", answered["text"]
    ran = stack.ledger.plans[-1]
    assert ran.direction is Direction.INCOME and ran.period.period is Period.THIS_YEAR
    assert ran.excluded_categories == ("SALARY",)
    assert ran.across_accounts and ran.only_account_ids == () and ran.only_account_id is None
    assert "Consultado en todas tus cuentas." in answered["text"]


@pytest.mark.parametrize("setting", ["all", "ask"])
@pytest.mark.parametrize(
    ("question", "figure", "kept", "left_out"),
    [
        ("¿cuánto tengo sin la hucha?", 215_648, "2.156,48 € en Cuenta corriente", "Hucha"),
        (
            "¿cuánto tengo sin contar el fondo?",
            215_648 + 641_550,
            "6.415,50 € en Hucha",
            "Fondo indexado",
        ),
    ],
)
def test_the_balance_honours_an_account_left_out(
    setting: str, question: str, figure: int, kept: str, left_out: str
) -> None:
    """Final review, I-4: R48 for the balance. «¿cuánto tengo sin la hucha?» answered «Tienes
    6.415,50 € en Hucha.», the one account left out, and «sin contar el fondo» the fund alone."""
    stack = episodes.a_stack(account_scope_default=setting)
    answered = ask(stack, question)
    assert answered["outcome"] == "BALANCE", answered["text"]
    assert answered["total_minor"] == figure, answered["text"]
    assert kept in answered["text"] and left_out not in answered["text"]


def test_the_payroll_account_left_out_is_still_the_account() -> None:
    """The other side of I-3: «la cuenta nómina» is the account (R25), and leaving it out leaves
    every other one."""
    stack = episodes.a_stack()
    ask(stack, "¿cuánto he ingresado este año sin contar la cuenta nómina?")
    assert stack.ledger.plans[-1].only_account_ids == (episodes.PIGGY, episodes.FUND)


def test_an_account_left_out_beside_the_period_question_is_left_out_of_its_answer() -> None:
    """C-1's path: the period question's draft keeps every account but the one left out."""
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "¿cuánto he gastado sin la hucha?")
    answered = ask(stack, "este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    assert stack.ledger.plans[-1].only_account_ids == _ALL_BUT_THE_HUCHA


# --- what the default leaves alone, under `all` (the pins the first report claimed) ---------------

_TRAVEL = uuid.UUID("77777777-7777-7777-7777-777777777777")


class _Holdings:
    """An account directory of the holdings given."""

    def __init__(self, *holdings: Holding) -> None:
        self._holdings = holdings

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return self._holdings


def _handler_over(directory: _Holdings, ledger: episodes.Ledger) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        episodes.AnyPlan(),  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=episodes.Accounts(),  # type: ignore[arg-type]
        account_directory=directory,  # type: ignore[arg-type]
        contacts=episodes.Contacts(),
        cards=episodes.NoCards(),  # type: ignore[arg-type]
    )


async def test_an_account_named_by_its_own_name_still_wins() -> None:
    ledger = episodes.Ledger()
    directory = _Holdings(*episodes.HOLDINGS, Holding(_TRAVEL, "SAVINGS", "Viajes", "EUR", 10_000))
    await _handler_over(directory, ledger).handle(
        AskFinancialQuestionQuery(
            "¿cuánto gasté en la cuenta Viajes en julio?", episodes.ACCOUNT, episodes.TODAY
        )
    )
    ran = ledger.plans[-1]
    assert ran.only_account_id == _TRAVEL and not ran.across_accounts


def test_an_account_named_by_an_alias_still_wins() -> None:
    stack = episodes.a_stack()
    learned = ask(stack, "Cuando diga viajes, me refiero a la cuenta Hucha")
    ask(stack, "¿Cuánto gasté en viajes en julio?", learned["conversation"])
    ran = stack.ledger.plans[-1]
    assert ran.only_account_id == episodes.PIGGY and not ran.across_accounts


def test_an_account_chosen_and_gone_is_asked_about_again() -> None:
    """A stale choice is still the person's: under `all` it is asked again, not read as every
    account."""
    stack = episodes.a_stack()
    gone = {"account_selection_mode": "SELECTED", "account_selection_ids": [str(_TRAVEL)]}
    asked = ask(stack, "¿Cuánto gasté en julio?", gone)
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"
    assert stack.ledger.plans == []


async def test_a_lone_account_is_read_as_itself() -> None:
    ledger = episodes.Ledger()
    lone = _Holdings(episodes.HOLDINGS[0])
    answered = await _handler_over(lone, ledger).handle(
        AskFinancialQuestionQuery("¿Cuánto gasté en julio?", episodes.ACCOUNT, episodes.TODAY)
    )
    assert answered.outcome == "PLANNED"
    assert ledger.plans[-1].only_account_id == episodes.ACCOUNT


async def test_an_account_left_out_by_its_own_name_leaves_every_other_one() -> None:
    ledger = episodes.Ledger()
    directory = _Holdings(*episodes.HOLDINGS, Holding(_TRAVEL, "SAVINGS", "Viajes", "EUR", 10_000))
    answered = await _handler_over(directory, ledger).handle(
        AskFinancialQuestionQuery(
            "¿cuánto gasté en julio sin la cuenta Viajes?", episodes.ACCOUNT, episodes.TODAY
        )
    )
    ran = ledger.plans[-1]
    assert ran.only_account_ids == tuple(h.account_id for h in episodes.HOLDINGS)
    assert ran.excluded_merchants == () and "Viajes" not in answered.text


@pytest.mark.parametrize("setting", ["all", "ask"])
def test_an_account_left_out_by_an_alias_is_never_the_one_read(setting: str) -> None:
    stack = episodes.a_stack(account_scope_default=setting)
    learned = ask(stack, "Cuando diga viajes, me refiero a la cuenta Hucha")
    ask(stack, "¿Cuánto gasté en julio sin viajes?", learned["conversation"])
    assert all(
        plan.only_account_id != episodes.PIGGY and episodes.PIGGY not in plan.only_account_ids
        for plan in stack.ledger.plans
    )
    assert all(plan.excluded_merchants == () for plan in stack.ledger.plans)
    if setting == "all":
        assert stack.ledger.plans[-1].only_account_ids == _ALL_BUT_THE_HUCHA
