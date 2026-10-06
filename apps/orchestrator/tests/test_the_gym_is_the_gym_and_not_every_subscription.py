"""«¿Cuánto gasté en el gimnasio este año?» — the gym, not every subscription.

Reported from a live session:

    "¿Cuánto gasté en el gimnasio este año?"
    "421,79 € en suscripciones en lo que va de año, en 23 movimientos."
      · Netflix · Gimnasio Altafit · Spotify · Steam · …

The gym alone is 29,90 € a month. «Gimnasio» is a word of the SUBSCRIPTIONS vocabulary, so the
planner, the gate and the shop reader all agreed the sentence was about the category — and every
one of them was right about the category and wrong about the question. The holder named one kind
of thing inside it.

The rule this pins (option «B1», chosen by the user) runs after the bank answers, so nothing a
model reads changed: when the category was named by a *member* word — a word of its vocabulary
that is not the category's own name — and that word is, as a whole word, in the name of the
holder's shops inside the category in the window answered, the answer is narrowed to **every** one
of those shops and says so. None called that: the category's figure stays, with one line saying
why. «Suscripciones» and «supermercado» are the categories' own names and never narrow anything.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import category_words, merchant_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)


@dataclass(frozen=True)
class Charge:
    merchant: str
    category: str
    amount_minor: int


GYM = Charge("Gimnasio Altafit", "SUBSCRIPTIONS", 2_990)
SECOND_GYM = Charge("Gimnasio Municipal", "SUBSCRIPTIONS", 1_800)
NETFLIX = Charge("Netflix", "SUBSCRIPTIONS", 1_399)
SPOTIFY = Charge("Spotify", "SUBSCRIPTIONS", 1_099)


class Ledger:
    """The window's charges, answered the way banking answers a plan over them.

    `evidence` is how many movements come back beside the figure. The deployment sends ten, and a
    year of subscriptions is dozens, so a shop the rule must find may not be among them — the
    complete list is a breakdown by merchant, which is what this answers when asked for one.
    """

    def __init__(self, *charges: Charge, evidence: int = 10) -> None:
        self.charges = charges
        self.evidence = evidence
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        matched = [
            charge
            for charge in self.charges
            if (not plan.categories or charge.category in plan.categories)
            and (not plan.merchants or charge.merchant in plan.merchants)
        ]
        groups = (
            tuple(Group(charge.merchant, charge.amount_minor, 1) for charge in matched)
            if plan.group_by is GroupBy.MERCHANT
            else ()
        )
        return QueryAnswer(
            currency="EUR",
            total_minor=sum(charge.amount_minor for charge in matched),
            movement_count=len(matched),
            groups=groups,
            movements=tuple(
                Movement(
                    occurred_at=datetime(2026, 7, 3, 9, 0),
                    description=f"Cargo {charge.merchant}",
                    amount_minor=charge.amount_minor,
                    direction="EXPENSE",
                    merchant_name=charge.merchant,
                    category=charge.category,
                )
                for charge in matched
            )[: self.evidence],
        )


class PlansTheCategory:
    """What the planner does with these sentences: the category the vocabulary names, this year.

    Read from the vocabulary rather than written per test, so the plan each sentence gets is the
    one the gate would accept for it -- the case this rule exists for is a plan nobody can fault.
    """

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.THIS_YEAR),
                categories=category_words.precisely_named_in(sentence),
            )
        )


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary("EUR", 100_000, 100_000, datetime(2026, 8, 5, 10))


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


class TwoAccounts:
    """A holder the account question is asked of, as the one in the live session was."""

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return (
            Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 100_000),
            Holding(uuid.UUID(int=2), "SAVINGS", "Hucha", "EUR", 50_000),
        )


async def ask(  # type: ignore[no-untyped-def]
    sentence: str,
    ledger: Ledger,
    conversation: Conversation = IDLE,
    directory: TwoAccounts | None = None,
):
    return await AskFinancialQuestionHandler(
        PlansTheCategory(),
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        account_directory=directory,
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
    )


# --- the vocabulary: a category's own name against the kinds inside it ------------------------


def test_the_gym_is_a_kind_inside_subscriptions() -> None:
    assert category_words.kinds_named_in(
        "¿Cuánto gasté en el gimnasio este año?", "SUBSCRIPTIONS"
    ) == ("gimnasio",)


def test_a_category_called_by_its_own_name_names_no_kind() -> None:
    for sentence in ("¿Cuánto gasté en suscripciones?", "¿y en la suscripción?"):
        assert category_words.kinds_named_in(sentence, "SUBSCRIPTIONS") == (), sentence
    assert category_words.kinds_named_in("¿Cuánto gasté en el supermercado?", "GROCERIES") == ()


def test_the_own_name_beside_the_kind_is_the_category_still() -> None:
    """«Suscripciones, gimnasio incluido» and «suscripciones aparte del gimnasio» both say the
    category's own name, and a rule that narrowed either would answer the opposite of the second.
    The category's own name wins: the answer is the one it always was."""
    for sentence in (
        "¿Cuánto gasté en suscripciones, gimnasio incluido?",
        "¿Cuánto gasté en suscripciones aparte del gimnasio?",
    ):
        assert category_words.kinds_named_in(sentence, "SUBSCRIPTIONS") == (), sentence


def test_a_kind_left_out_is_not_the_kind_asked_about() -> None:
    assert category_words.kinds_named_in(
        "¿Cuánto gasté en taxi sin el metro?", "TRANSPORT_PUBLIC"
    ) == ("taxi",)


@pytest.mark.parametrize(
    ("sentence", "category"),
    [
        ("¿Cuánto gasté en streaming?", "SUBSCRIPTIONS"),
        ("¿Cuánto gasté en el dentista?", "HEALTH"),
        ("¿Cuánto gasté en autobús?", "TRANSPORT_PUBLIC"),
        ("¿Cuánto gasté en videojuegos?", "LEISURE"),
        ("¿Cuánto gasté en casas de apuestas?", "LEISURE"),
    ],
)
def test_a_kind_no_shop_is_named_after_is_the_category(sentence: str, category: str) -> None:
    """Kinds a shop rarely carries in its name -- a dentist is a «Clínica Dental», a bus line is
    «EMT Madrid» -- would only ever reach the note, and the note would read as wrong beside the
    shop that plainly is one. They answer the category, as they did before the rule."""
    assert category_words.kinds_named_in(sentence, category) == ()


def test_every_kind_is_a_word_of_its_own_category() -> None:
    """One source of truth: a kind is a word the category's precise vocabulary already holds,
    marked, never a second spelling of it kept somewhere else -- and never the category's name."""
    for category, kinds in category_words._KINDS.items():
        for kind in kinds:
            assert kind in category_words._PRECISE[category], (category, kind)
            assert kind != category_words.name_of(category), (category, kind)


def test_a_shop_is_called_by_a_kind_as_a_whole_word_folded() -> None:
    shops = ("Gimnasio Altafit", "Gimnasiolandia", "Netflix", "Óptica Roca")
    assert merchant_words.called(("gimnasio",), shops) == ("Gimnasio Altafit",)
    assert merchant_words.called(("optica",), shops) == ("Óptica Roca",)
    assert merchant_words.called(("streaming",), shops) == ()


def test_a_shop_is_called_by_a_kind_in_its_plural() -> None:
    """Shops name themselves in the plural as often as not -- «Cines Yelmo», «Farmacias Trébol» --
    and a whole-word match alone told the holder no shop was called «cine» beside Cines Yelmo. The
    Spanish plural and nothing looser: +s, +es."""
    shops = ("Cines Yelmo", "Farmacias Trébol", "Gimnasios Rivas", "Cinesa", "Fnac")
    assert merchant_words.called(("cine",), shops) == ("Cines Yelmo",)
    assert merchant_words.called(("farmacia",), shops) == ("Farmacias Trébol",)
    assert merchant_words.called(("gimnasio",), shops) == ("Gimnasios Rivas",)
    assert merchant_words.called(("taxi",), ("Taxis Madrid", "Taxistas Unidos")) == (
        "Taxis Madrid",
    )


# --- and end to end ---------------------------------------------------------------------------


async def test_the_gym_is_answered_as_the_gym() -> None:
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchants == ("Gimnasio Altafit",)
    assert ledger.plans[-1].categories == ()
    assert "29,90 € en Gimnasio Altafit" in result.text
    assert "54,88" not in result.text, "the whole category is the reported defect"
    # «Ver la consulta» is the query that produced the figure, not the one the plan started as.
    assert "Gimnasio Altafit" in result.plan_in_words
    assert "suscripciones" not in result.plan_in_words


async def test_the_cinema_is_the_cinema_the_ledger_calls_cines() -> None:
    ledger = Ledger(
        Charge("Cines Yelmo", "LEISURE", 1_850),
        Charge("Fnac", "LEISURE", 3_499),
        Charge("Decathlon", "LEISURE", 4_200),
    )

    result = await ask("¿Cuánto gasté en el cine este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchants == ("Cines Yelmo",)
    assert ledger.plans[-1].categories == ()
    assert "18,50 € en Cines Yelmo" in result.text
    assert "ningún comercio" not in result.text


async def test_two_gyms_are_both_gyms() -> None:
    """Every shop the word names, never one of them -- and found in the window, not only in the
    handful of movements that came back as evidence: here that handful holds no gym at all."""
    ledger = Ledger(NETFLIX, GYM, SECOND_GYM, evidence=1)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert set(ledger.plans[-1].merchants) == {"Gimnasio Altafit", "Gimnasio Municipal"}
    assert "47,90" in result.text
    assert "Gimnasio Altafit" in result.text
    assert "Gimnasio Municipal" in result.text


async def test_no_gym_is_the_category_and_says_so() -> None:
    ledger = Ledger(NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.merchants for plan in ledger.plans] == [()]
    assert "24,98" in result.text
    note = result.text.splitlines()[-1]
    assert "toda la categoría suscripciones" in note
    assert "«gimnasio»" in note


async def test_the_dentist_is_the_health_category_as_before() -> None:
    ledger = Ledger(
        Charge("Clinica Dental Sur", "HEALTH", 6_000), Charge("Farmacia Gran Via", "HEALTH", 1_250)
    )

    result = await ask("¿cuánto gasté en el dentista?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [(plan.categories, plan.merchants) for plan in ledger.plans] == [(("HEALTH",), ())]
    assert result.text == (
        "72,50 € en salud en lo que va de año, en 2 movimientos.\nConsultado en todas tus cuentas."
    )


async def test_the_category_by_its_own_name_is_answered_whole() -> None:
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en suscripciones este año?", ledger)

    assert [plan.merchants for plan in ledger.plans] == [()]
    assert "54,88" in result.text
    assert "comercio" not in result.text


async def test_an_own_name_inside_a_shop_name_narrows_nothing() -> None:
    """«Supermercado» is GROCERIES' own name, so «Supermercado Dia» is one supermarket among
    several and the question about the supermarket is about all of them."""
    ledger = Ledger(
        Charge("Supermercado Dia", "GROCERIES", 4_210), Charge("Mercadona", "GROCERIES", 6_000)
    )

    result = await ask("¿Cuánto gasté en el supermercado este año?", ledger)

    assert [plan.merchants for plan in ledger.plans] == [()]
    assert ledger.plans[0].categories == ("GROCERIES",)
    assert "102,10" in result.text
    assert "comercio" not in result.text


async def test_a_follow_up_keeps_the_gym() -> None:
    """The narrowed plan is the one remembered, as for any question about a shop, so «¿y el año
    pasado?» is still about the gym rather than about every subscription last year."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    first = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    await ask("¿y el año pasado?", ledger, first.conversation)

    follow_up = ledger.plans[-1]
    assert follow_up.merchants == ("Gimnasio Altafit",)
    assert follow_up.categories == ()
    assert follow_up.date_range is not None
    assert follow_up.date_range.start.year == 2025


async def test_the_gym_survives_the_account_question() -> None:
    """The live session's own route: «¿En qué cuenta…?» in between, and «Todas mis cuentas» as the
    reply. The pending plan runs on the reply's turn, whose sentence names no gym at all -- the
    question that did is the turn before it, in the log -- and the answer came back as every
    subscription the holder pays."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿Cuánto gasté en el gimnasio este año?", ledger, directory=TwoAccounts())
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans == []

    answered = await ask("Todas mis cuentas", ledger, asked.conversation, TwoAccounts())

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchants == ("Gimnasio Altafit",)
    assert ledger.plans[-1].across_accounts
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_a_shop_named_beside_its_category_survives_the_account_question() -> None:
    """The same readers, the same route: the shop reader read the reply too, so «suscripciones de
    Netflix» answered every subscription once an account had been chosen."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask(
        "¿Cuánto gasté en suscripciones de Netflix este año?", ledger, directory=TwoAccounts()
    )

    answered = await ask("Todas mis cuentas", ledger, asked.conversation, TwoAccounts())

    assert ledger.plans[-1].merchants == ("Netflix",)
    assert "Solo Netflix" in answered.text
