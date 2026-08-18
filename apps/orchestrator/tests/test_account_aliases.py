"""User-chosen account names are grounded, persisted and reused safely."""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, AccountAlias, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.services import account_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")
CURRENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
SAVINGS = uuid.UUID("33333333-3333-3333-3333-333333333333")
TODAY = date(2026, 8, 9)
HOLDINGS = (
    Holding(CURRENT, "CURRENT", "Cuenta corriente", "EUR", 100_000),
    Holding(SAVINGS, "SAVINGS", "Hucha", "EUR", 50_000),
)


class Planner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.THIS_MONTH),
            )
        )


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(currency="EUR", total_minor=2_500, movement_count=2)


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            "EUR",
            100_000,
            100_000,
            datetime(2026, 8, 9, 10),
            holdings=HOLDINGS,
        )


class Directory:
    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return HOLDINGS


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


def handler(planner: Planner, ledger: Ledger) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        account_directory=Directory(),
        contacts=Contacts(),
        cards=Cards(),
    )


async def ask(
    service: AskFinancialQuestionHandler,
    sentence: str,
    conversation: Conversation = IDLE,
):  # type: ignore[no-untyped-def]
    return await service.handle(
        AskFinancialQuestionQuery(sentence, OWNER, TODAY, conversation=conversation)
    )


def test_only_explicit_alias_definitions_are_read() -> None:
    assert account_words.alias_definition_in(
        "Cuando diga viajes, me refiero a la cuenta Hucha"
    ) == ("viajes", "cuenta hucha")
    assert account_words.alias_definition_in("Llama recibos a mi cuenta corriente") == (
        "recibos",
        "cuenta corriente",
    )
    assert account_words.alias_definition_in("mi cuenta de viajes") is None


async def test_an_alias_is_learned_without_a_model_and_scopes_the_next_query() -> None:
    planner = Planner()
    ledger = Ledger()
    service = handler(planner, ledger)

    learned = await ask(service, "Cuando diga viajes, me refiero a la cuenta Hucha")
    result = await ask(
        service,
        "¿Cuánto gasté en viajes este mes?",
        learned.conversation,
    )

    assert planner.calls == 1, "learning the grounded alias itself does not need a model"
    assert learned.conversation.account_aliases == (AccountAlias("viajes", SAVINGS, "Hucha"),)
    assert ledger.plans[0].only_account_id == SAVINGS
    assert result.conversation.account_aliases == learned.conversation.account_aliases


async def test_a_learned_alias_also_selects_a_balance_account() -> None:
    service = handler(Planner(), Ledger())
    learned = await ask(service, "Llama viajes a la cuenta Hucha")

    result = await ask(service, "¿Cuánto tengo en viajes?", learned.conversation)

    assert "Hucha" in result.text
    assert "500,00 €" in result.text
    assert "Cuenta corriente" not in result.text


def test_account_aliases_survive_the_untrusted_wire_round_trip() -> None:
    conversation = Conversation(account_aliases=(AccountAlias("viajes", SAVINGS, "Hucha"),))

    payload = conversation_mapper.to_payload(conversation)
    restored = conversation_mapper.to_domain(payload)

    assert restored.account_aliases == conversation.account_aliases
