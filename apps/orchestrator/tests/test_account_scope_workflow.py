import uuid
from datetime import date, datetime

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    AccountSelection,
    AccountSelectionMode,
    Conversation,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 21)
CURRENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
SAVINGS = uuid.UUID("33333333-3333-3333-3333-333333333333")
HOLDINGS = (
    Holding(CURRENT, "CURRENT", "Cuenta corriente", "EUR", 100_000),
    Holding(SAVINGS, "SAVINGS", "Hucha", "EUR", 50_000),
)


class Planner:
    async def plan(self, *_: object, **__: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
        )


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(
            currency="EUR",
            total_minor=1_000,
            movement_count=1,
            groups=(Group("Gastos", 1_000, 1),),
        )

    async def ranked(self, ranking: TopMovements, **_: object) -> object:
        self.plans.append(ranking)  # type: ignore[arg-type]
        return None


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 100_000, 100_000, datetime(2026, 8, 21), holdings=HOLDINGS)


class CountingAccounts(Accounts):
    def __init__(self) -> None:
        self.calls = 0

    async def of(self, *_: object, **__: object) -> AccountSummary:
        self.calls += 1
        return await super().of()


class Directory:
    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return HOLDINGS


class MutableDirectory(Directory):
    def __init__(self, holdings: tuple[Holding, ...] = HOLDINGS) -> None:
        self.holdings = holdings

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return self.holdings


class UnavailableDirectory:
    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        from finguard_orchestrator.domain.errors import BankingUnavailableError

        raise BankingUnavailableError("directory unavailable")


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


def service(
    ledger: Ledger, directory: object = Directory(), accounts: object = Accounts()
) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        Planner(),
        ledger,
        accounts=accounts,  # type: ignore[arg-type]
        account_directory=directory,  # type: ignore[arg-type]
        contacts=Contacts(),
        cards=Cards(),
    )


async def ask(
    handler: AskFinancialQuestionHandler, sentence: str, conversation: Conversation = IDLE
) -> object:
    return await handler.handle(
        AskFinancialQuestionQuery(sentence, OWNER, TODAY, conversation=conversation)
    )


@pytest.mark.asyncio
async def test_generic_query_asks_for_account_without_touching_ledger_then_hucha_resumes() -> None:
    ledger = Ledger()
    handler = service(ledger)

    clarification = await ask(handler, "¿Cuánto he gastado este mes?")
    assert clarification.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans == []
    assert clarification.suggestions == ["Cuenta corriente", "Hucha", "Todas mis cuentas"]

    answered = await ask(handler, "Hucha", clarification.conversation)
    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].only_account_id == SAVINGS
    assert answered.conversation.account_selection is not None
    assert answered.conversation.account_selection.mode is AccountSelectionMode.SELECTED
    assert "Hucha" in answered.text


@pytest.mark.asyncio
async def test_all_accounts_choice_executes_pending_plan_across_accounts() -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "¿Cuánto he gastado este mes?")
    answered = await ask(handler, "Todas mis cuentas", clarification.conversation)

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].across_accounts
    assert answered.conversation.account_selection is not None
    assert answered.conversation.account_selection.mode is AccountSelectionMode.ALL
    assert "todas tus cuentas" in answered.text


@pytest.mark.asyncio
async def test_new_complete_question_does_not_consume_account_choice() -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "¿Cuánto he gastado este mes?")
    answered = await ask(
        handler, "¿Cuánto he gastado en Hucha este mes?", clarification.conversation
    )

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert clarification.conversation.active is not None
    assert ledger.plans[-1].only_account_id == SAVINGS


@pytest.mark.asyncio
async def test_one_live_account_is_selected_without_clarification() -> None:
    ledger = Ledger()
    handler = service(ledger, MutableDirectory((HOLDINGS[0],)))

    answered = await ask(handler, "Cuánto he gastado este mes?")

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].only_account_id == CURRENT
    assert not ledger.plans[-1].across_accounts


@pytest.mark.asyncio
async def test_persisted_selection_is_reused_by_aggregate_and_ranked_many() -> None:
    ledger = Ledger()
    handler = service(ledger)
    first = await ask(handler, "Cuánto he gastado este mes?")
    selected = await ask(handler, "Hucha", first.conversation)
    follow_up = await ask(handler, "Cuánto he gastado el mes pasado?", selected.conversation)
    ranking = await ask(handler, "Lista mis gastos de mayor a menor", follow_up.conversation)

    assert ledger.plans[0].only_account_id == SAVINGS
    assert ledger.plans[1].only_account_id == SAVINGS
    assert getattr(ledger.plans[-1], "only_account_id", None) == SAVINGS
    assert ranking.conversation.account_selection is not None


@pytest.mark.asyncio
async def test_deleted_persisted_selection_reopens_account_clarification() -> None:
    ledger = Ledger()
    directory = MutableDirectory()
    handler = service(ledger, directory)
    first = await ask(handler, "Cuánto he gastado este mes?")
    selected = await ask(handler, "Hucha", first.conversation)
    directory.holdings = (HOLDINGS[0],)

    clarification = await ask(handler, "Cuánto he gastado el mes pasado?", selected.conversation)

    assert clarification.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans[-1].only_account_id == SAVINGS
    assert clarification.suggestions == ["Cuenta corriente", "Todas mis cuentas"]


@pytest.mark.asyncio
async def test_directory_unavailable_returns_honest_error_without_ledger_read() -> None:
    ledger = Ledger()
    handler = service(ledger, UnavailableDirectory())

    result = await ask(handler, "Cuánto he gastado este mes?")

    assert result.outcome == "BANKING_UNAVAILABLE"
    assert ledger.plans == []


@pytest.mark.asyncio
async def test_directory_unavailable_for_explicit_account_never_widens() -> None:
    ledger = Ledger()
    handler = service(ledger, UnavailableDirectory())

    result = await ask(handler, "Cuánto he gastado en la cuenta Viajes este mes?")

    assert result.outcome == "BANKING_UNAVAILABLE"
    assert ledger.plans == []


@pytest.mark.asyncio
async def test_unknown_custom_account_reoffers_live_candidates_without_ledger_read() -> None:
    ledger = Ledger()
    handler = service(ledger)

    result = await ask(handler, "Cuánto he gastado en la cuenta Viajes este mes?")

    assert result.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert result.suggestions == ["Cuenta corriente", "Hucha", "Todas mis cuentas"]
    assert ledger.plans == []


@pytest.mark.asyncio
async def test_explicit_all_accounts_is_remembered_as_the_last_plan_scope() -> None:
    ledger = Ledger()
    handler = service(ledger)

    clarification = await ask(handler, "Cuánto he gastado este mes?")
    result = await ask(handler, "Todas mis cuentas", clarification.conversation)

    assert result.conversation.last_plan is not None
    assert result.conversation.last_plan.across_accounts is True


@pytest.mark.asyncio
async def test_balance_does_not_consume_pending_account_scope() -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "Cuánto he gastado este mes?")

    result = await ask(handler, "Cuánto dinero tengo?", clarification.conversation)

    assert result.outcome == "BALANCE"
    assert ledger.plans == []
    assert result.conversation.active == clarification.conversation.active


@pytest.mark.asyncio
async def test_scope_change_followup_reuses_previous_period_and_switches_to_all() -> None:
    ledger = Ledger()
    handler = service(ledger)

    clarification = await ask(handler, "Cuánto he gastado en los últimos 12 meses?")
    hucha = await ask(handler, "Hucha", clarification.conversation)
    result = await ask(handler, "Ahora en todas mis cuentas", hucha.conversation)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].across_accounts is True
    assert ledger.plans[-1].period == ledger.plans[0].period
    assert result.conversation.account_selection is not None
    assert result.conversation.account_selection.mode is AccountSelectionMode.ALL
    assert result.conversation.last_plan is not None
    assert result.conversation.last_plan.across_accounts is True


@pytest.mark.asyncio
async def test_scope_change_followup_also_retargets_a_previous_ranking() -> None:
    ledger = Ledger()
    handler = service(ledger)
    ranking = TopMovements(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        only_account_id=SAVINGS,
    )
    conversation = Conversation(
        last_plan=ranking,
        account_selection=AccountSelection(AccountSelectionMode.SELECTED, (SAVINGS,)),
    )

    result = await ask(handler, "Ahora en todas mis cuentas", conversation)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].across_accounts is True
    assert ledger.plans[-1].period == ranking.period


def special_focus_conversation(kind: str, mirrored: FinancialPlan) -> Conversation:
    fields: dict[str, object] = {"last_plan": mirrored}
    if kind == "ratio":
        fields["last_ratio"] = RatioPlan(
            part=mirrored,
            whole=FinancialPlan(
                direction=Direction.INCOME,
                period=mirrored.period,
            ),
            kind=RatioKind.SHARE,
        )
    elif kind == "pending_ratio":
        fields["pending_ratio"] = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.SHARE,
        )
    else:
        fields["last_comparison"] = ComparisonPlan(
            earlier=FinancialPlan(
                direction=Direction.EXPENSE,
                date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
            ),
            latest=FinancialPlan(
                direction=Direction.EXPENSE,
                date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
            ),
        )
    return Conversation(**fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ratio", "pending_ratio", "comparison"])
async def test_scope_change_does_not_retarget_a_special_focus_query_mirror(kind: str) -> None:
    ledger = Ledger()
    handler = service(ledger)
    mirrored = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        categories=("FOOD",),
        only_account_id=SAVINGS,
    )

    result = await ask(
        handler,
        "Ahora en todas mis cuentas",
        special_focus_conversation(kind, mirrored),
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert len(ledger.plans) == 1
    assert ledger.plans[0].period == RelativePeriod(Period.THIS_MONTH)
    assert ledger.plans[0].categories == ()


@pytest.mark.asyncio
async def test_scope_change_after_a_balance_stays_on_the_balance_path() -> None:
    ledger = Ledger()
    accounts = CountingAccounts()
    handler = service(ledger, accounts=accounts)
    suspended = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=12),
        only_account_id=SAVINGS,
    )

    result = await ask(
        handler,
        "Ahora en todas mis cuentas",
        Conversation(last_plan=suspended, answered_the_balance=True),
    )

    assert result.outcome == "BALANCE"
    assert accounts.calls == 1
    assert ledger.plans == []


@pytest.mark.asyncio
async def test_graph_intent_survives_account_scope_choice() -> None:
    ledger = Ledger()
    handler = service(ledger)

    clarification = await ask(handler, "Representa en un gráfico mis gastos este mes")
    result = await ask(handler, "Todas mis cuentas", clarification.conversation)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert result.explicit_visualization is True
    assert result.visual_dataset is not None


@pytest.mark.asyncio
async def test_preclassified_balance_intent_still_consumes_exact_account_chip() -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "Representa en un gráfico mis gastos este mes")
    result = await handler.handle(
        AskFinancialQuestionQuery(
            "Todas mis cuentas",
            OWNER,
            TODAY,
            conversation=clarification.conversation,
            intent=Intent.ACCOUNT_BALANCE,
        )
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert result.conversation.account_selection is not None
    assert result.conversation.account_selection.mode is AccountSelectionMode.ALL
    assert result.explicit_visualization is True


@pytest.mark.asyncio
async def test_complete_balance_question_does_not_consume_pending_account_scope() -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "Representa en un gráfico mis gastos este mes")
    result = await handler.handle(
        AskFinancialQuestionQuery(
            "¿Cuánto dinero tengo en todas mis cuentas?",
            OWNER,
            TODAY,
            conversation=clarification.conversation,
            intent=Intent.ACCOUNT_BALANCE,
        )
    )

    assert result.outcome == "BALANCE"
    assert result.conversation.active == clarification.conversation.active
    assert ledger.plans == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sentence",
    [
        # The comparison pair: two plans, two `_scoped` calls, no catch (`_compare_periods`).
        "compara mis gastos de julio con junio",
        # A ratio's two operands (`_answer_ratio`).
        "qué porcentaje de mi nómina se fue en alquiler este año",
        # Net cash flow, which reaches the scope through the ratio draft (`_scoped_ratio_draft`).
        "cuál es mi flujo de caja neto",
    ],
)
async def test_no_read_path_turns_a_scope_requirement_into_a_crash(sentence: str) -> None:
    """A holder with several accounts and a sentence that names none is a question, never an error.

    `_scoped` raises `AccountScopeRequiredError` and exactly two of its eleven call sites translated
    it. The comparison pair, the ratio operands and net cash flow did not, so each of these was an
    HTTP 500 as a first question -- and invisible to every test, because the same sentence works
    once a previous turn has settled the scope.
    """
    ledger = Ledger()
    handler = service(ledger)

    answer = await ask(handler, sentence)

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "cuenta" in answer.text.lower()
    assert "Todas mis cuentas" in answer.suggestions
    assert ledger.plans == [], "no figure is read before the holder has said whose money it is"
