"""Typed counterparty aliases are grounded, persisted and never demoted to merchants."""

from datetime import date
from uuid import UUID

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import CounterpartyType, Direction, Period
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, CounterpartyAlias
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.services import entity_alias_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 9)


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
        return QueryAnswer(currency="EUR", total_minor=1_000, movement_count=1)


class Unused:
    async def of(self, *_: object, **__: object) -> object:
        raise AssertionError("account summary is not needed for a typed entity alias")

    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


def service(planner: Planner, ledger: Ledger) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Unused(),  # type: ignore[arg-type]
        contacts=Unused(),  # type: ignore[arg-type]
        cards=Unused(),  # type: ignore[arg-type]
    )


async def ask(
    handler: AskFinancialQuestionHandler,
    sentence: str,
    conversation: Conversation = IDLE,
):  # type: ignore[no-untyped-def]
    return await handler.handle(
        AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=conversation)
    )


def test_definitions_require_an_explicit_entity_type() -> None:
    assert entity_alias_words.alias_definition_in(
        "Cuando diga el súper, me refiero al comercio Mercadona"
    ) == ("super", "mercadona", CounterpartyType.MERCHANT)
    assert entity_alias_words.alias_definition_in("Llama mi hermana a la persona Elena") == (
        "hermana",
        "elena",
        CounterpartyType.PERSON,
    )
    assert entity_alias_words.alias_definition_in("Llama favorito a Elena") is None


async def test_a_person_alias_is_grounded_and_applied_as_a_person() -> None:
    planner = Planner()
    ledger = Ledger()
    handler = service(planner, ledger)

    learned = await ask(handler, "Cuando diga mi hermana, me refiero a la persona Elena")
    result = await ask(handler, "¿Cuánto le envié a mi hermana este mes?", learned.conversation)

    assert planner.calls == 1
    assert ledger.plans[0].counterparties == ("elena",)
    assert ledger.plans[0].counterparty_types == (CounterpartyType.PERSON,)
    assert ledger.plans[1].counterparties == ("elena",)
    assert ledger.plans[1].counterparty_types == (CounterpartyType.PERSON,)
    assert ledger.plans[1].merchants == ()
    assert result.conversation.counterparty_aliases == learned.conversation.counterparty_aliases


def test_counterparty_aliases_survive_the_untrusted_wire_round_trip() -> None:
    conversation = Conversation(
        counterparty_aliases=(CounterpartyAlias("super", "Mercadona", CounterpartyType.MERCHANT),)
    )

    restored = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))

    assert restored.counterparty_aliases == conversation.counterparty_aliases


async def test_an_entity_alias_cannot_later_be_reused_for_an_account() -> None:
    handler = service(Planner(), Ledger())
    learned = await ask(handler, "Llama hermana a la persona Elena")

    refused = await ask(handler, "Llama hermana a la cuenta Hucha", learned.conversation)

    assert "ya identifica una contraparte" in refused.text
    assert refused.conversation.account_aliases == ()
    assert refused.conversation.counterparty_aliases == learned.conversation.counterparty_aliases
