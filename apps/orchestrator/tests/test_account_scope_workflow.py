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
    Slot,
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
FUND = uuid.UUID("44444444-4444-4444-4444-444444444444")
HOLDINGS = (
    Holding(CURRENT, "CURRENT", "Cuenta corriente", "EUR", 100_000),
    Holding(SAVINGS, "SAVINGS", "Hucha", "EUR", 50_000),
)
# The holder Laura actually has: the third one is what "¿cuánto he invertido?" resolves to without
# the sentence ever naming it.
THREE = (*HOLDINGS, Holding(FUND, "ASSET", "Fondo indexado", "EUR", 165_000))


class Planner:
    async def plan(self, *_: object, **__: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
        )


class LastMonthPlanner:
    async def plan(self, *_: object, **__: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
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


class ThreeAccounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 315_000, 315_000, datetime(2026, 8, 21), holdings=THREE)


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
    ledger: Ledger,
    directory: object = Directory(),
    accounts: object = Accounts(),
    planner: object | None = None,
) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        planner or Planner(),  # type: ignore[arg-type]
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
@pytest.mark.parametrize("reply", ["Todas mis cuentas", "en todas", "todas", "en todas ellas"])
async def test_all_accounts_choice_executes_pending_plan_across_accounts(reply: str) -> None:
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "¿Cuánto he gastado este mes?")
    answered = await ask(handler, reply, clarification.conversation)

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


@pytest.mark.asyncio
async def test_a_later_unqualified_turn_still_says_the_figures_covered_every_account() -> None:
    """The note describes the figures, and on both turns those came from every account.

    The remembered plan is what was *asked*, so a turn whose sentence never named the scope
    carries `across_accounts=False` -- and that is correct. Reading the note off it made
    "Consultado en todas tus cuentas" vanish from the second such turn onward while the ledger
    was still being read across every account the holder has.
    """
    ledger = Ledger()
    handler = service(ledger)
    clarification = await ask(handler, "Cuánto he gastado este mes?")
    across = await ask(handler, "Todas mis cuentas", clarification.conversation)
    assert "Consultado en todas tus cuentas." in across.text

    later = await ask(handler, "Cuánto he gastado el mes pasado?", across.conversation)

    assert later.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].across_accounts is True
    assert "Consultado en todas tus cuentas." in later.text
    # The plan stays what was asked. This sentence did not name the scope, so it does not claim it.
    assert later.conversation.last_plan is not None
    assert later.conversation.last_plan.across_accounts is False


@pytest.mark.asyncio
async def test_two_answered_periods_compare_when_only_the_first_named_the_scope() -> None:
    """«Compáralos» compares the two questions the conversation has already answered.

    A three-turn conversation rather than a call on the recogniser, because the defect lives in
    what the previous turn stored: the July plan remembers the scope its own sentence named and
    the June plan does not, so the pair disagreed on a field neither question mentioned and the
    comparison was refused. Building the two plans by hand would prove nothing.
    """
    ledger = Ledger()
    handler = service(ledger)

    july = await ask(handler, "cuánto gasté en julio en todas mis cuentas")
    june = await ask(handler, "cuánto gasté en junio", july.conversation)
    assert july.outcome == PlanningOutcome.PLANNED.value
    assert june.outcome == PlanningOutcome.PLANNED.value
    # The precondition: the second question never named the scope, so its plan does not carry it.
    assert june.conversation.last_plan is not None
    assert june.conversation.last_plan.across_accounts is False

    compared = await ask(handler, "compáralos", june.conversation)

    assert compared.outcome == PlanningOutcome.PLANNED.value
    assert "julio de 2026" in compared.text
    assert "junio de 2026" in compared.text
    # One comparison, one scope: both operands read the accounts both figures came from.
    assert [plan.across_accounts for plan in ledger.plans[-2:]] == [True, True]


@pytest.mark.asyncio
async def test_a_scope_the_service_derived_is_not_stored_as_the_holders_choice() -> None:
    """Reproduced twice against the live service from an empty thread, `demo-token-laura-pays`:

        «¿cuánto he invertido en el último año?»
            → «Moviste 1.650,00 € hacia la cuenta consultada …»
              account_selection = SELECTED(Fondo indexado)   ← the holder never chose this
        «¿cuánto gasté el mes pasado?»
            → «No he encontrado movimientos el mes pasado. Puedes ampliar el periodo …»

    Laura spent 1.895,97 € last month. "Cuánto he invertido" names a *purpose*, and `_scoped`
    resolves it to the holder's asset account precisely because the sentence does not name one --
    so that scope is this service's execution policy, and storing it made the next question about
    ordinary spending mean "inside the index fund". The advice to widen the period then sent her
    to fix the window, which was the one part of the query that was right.

    An unqualified spending question with nothing settled is answered by asking whose money it is,
    which is what a first turn gets anyway. The assertion that matters is that nothing about the
    fund was inherited.
    """
    ledger = Ledger()
    handler = service(
        ledger, MutableDirectory(THREE), accounts=ThreeAccounts(), planner=LastMonthPlanner()
    )

    invested = await ask(handler, "¿cuánto he invertido en el último año?")

    assert invested.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].internal_only is True
    assert ledger.plans[-1].only_account_id == FUND, "the derived scope still runs on the fund"
    assert invested.conversation.account_selection is None, (
        "the sentence named no account, so there is no choice to remember"
    )

    spent = await ask(handler, "¿cuánto gasté el mes pasado?", invested.conversation)

    assert spent.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "Fondo indexado" in spent.suggestions
    assert "Todas mis cuentas" in spent.suggestions
    assert len(ledger.plans) == 1, "no figure about last month is read from inside the fund"


@pytest.mark.asyncio
async def test_a_derived_scope_does_not_overwrite_the_all_accounts_choice() -> None:
    """The same defect from the state a real thread is in from its second turn onward.

    This is the shape the `scope: legacy` corpus episodes replay: the holder has already said
    "todas mis cuentas", one turn asks about contributions to the fund, and the turns after it are
    about ordinary spending. Overwriting ALL with the fund is how a question about supermarkets was
    answered from inside an index fund.
    """
    ledger = Ledger()
    handler = service(
        ledger, MutableDirectory(THREE), accounts=ThreeAccounts(), planner=LastMonthPlanner()
    )
    settled = Conversation(account_selection=AccountSelection(AccountSelectionMode.ALL))

    invested = await ask(handler, "¿cuánto he invertido en el último año?", settled)

    assert invested.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].only_account_id == FUND
    assert invested.conversation.account_selection is not None
    assert invested.conversation.account_selection.mode is AccountSelectionMode.ALL

    spent = await ask(handler, "¿cuánto gasté el mes pasado?", invested.conversation)

    assert spent.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].across_accounts is True
    assert ledger.plans[-1].only_account_id is None


@pytest.mark.asyncio
async def test_the_account_the_holder_named_is_still_remembered() -> None:
    """The other half of the rule, so the fix above cannot be "never remember anything".

    A sentence that names an account states a scope, and losing it makes the very next ellipsis
    widen back to every account.
    """
    ledger = Ledger()
    handler = service(
        ledger, MutableDirectory(THREE), accounts=ThreeAccounts(), planner=LastMonthPlanner()
    )
    settled = Conversation(account_selection=AccountSelection(AccountSelectionMode.ALL))

    named = await ask(handler, "¿cuánto gasté en la hucha este mes?", settled)

    assert named.outcome == PlanningOutcome.PLANNED.value
    assert named.conversation.account_selection is not None
    assert named.conversation.account_selection.mode is AccountSelectionMode.SELECTED
    assert named.conversation.account_selection.account_ids == (SAVINGS,)

    later = await ask(handler, "¿y el mes pasado?", named.conversation)

    assert later.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].only_account_id == SAVINGS


def slot_asked(answer: object) -> Slot | None:
    """Which slot a turn asked for, read the way both counters read it.

    The harness asks the wire for `conversation.awaiting`; this service's own
    `_asks_the_same_thing_again` asks the state for `awaiting or last_asked`. The two agree by
    construction, and a turn that asked for something and reports `None` here is invisible to
    both.
    """
    if answer.outcome != PlanningOutcome.NEEDS_CLARIFICATION.value:
        return None
    return answer.conversation.awaiting or answer.conversation.last_asked


@pytest.mark.asyncio
async def test_the_same_account_question_twice_running_is_detectable() -> None:
    """Two identical questions, two identical clarifications, and a counter that can see it.

    This is the loop from `evaluation/conversations/RESULTS.md` with the account slot in place of
    the period one: the reply does not settle the scope, so the same question comes back. Nothing
    here breaks the loop — that is a judgement about wording, not about state — but the run has to
    be *visible*, and it was not: both turns reported no slot at all, so a run-length counter over
    the slot saw a run of zero on the path most likely to loop.

    The comparison sentence is deliberate. It reaches the scope requirement through the backstop,
    which leaves no resumable workflow behind, so `awaiting` has nothing to hang on and
    `last_asked` is the only place the question can be recorded — the same hole `last_asked` was
    added for, in a second path that never used it.
    """
    ledger = Ledger()
    handler = service(ledger)

    first = await ask(handler, "compara mis gastos de julio con junio")
    again = await ask(handler, "compara mis gastos de julio con junio", first.conversation)

    assert first.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert again.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert [slot_asked(first), slot_asked(again)] == [Slot.ACCOUNT, Slot.ACCOUNT]
    assert ledger.plans == [], "no figure is read before the holder has said whose money it is"
