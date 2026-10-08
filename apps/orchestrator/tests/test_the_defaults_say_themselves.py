"""What a question means when it does not say, and the answer saying it (spec §3.5).

Measured live on 2026-10-07: «¿Cuánto llevo gastado en Spotify?» was asked which account and then
which period, two questions about things the person had a perfectly ordinary meaning for. The
defaults are the product's now — every account unless the person chose one, the year so far for
«llevo gastado», the last 30 days for «últimamente» — and each answer names the window it used, so a
default is never a silent guess. The old account question is one setting away
(`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask`).

Driven over HTTP on the episodes stack: three accounts, nothing settled.
"""

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

from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import app, create_app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import answer_writer, category_words
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
    assert asked["conversation"]["account_selection_mode"] == "SELECTED"
    answered = ask(stack, "este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
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
