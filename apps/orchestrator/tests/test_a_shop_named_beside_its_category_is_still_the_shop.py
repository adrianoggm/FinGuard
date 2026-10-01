"""«¿Cuánto he gastado en suscripciones de Netflix este mes?» — Netflix, not every subscription.

Reported by review, 2026-09-14. The repair that catches a category answered instead of a shop
(`ask_financial_question._answer`, the `plan.categories and not plan.merchants` branch) read the
movements it had just been given, looked for a shop the sentence named, and switched itself off the
moment the sentence also named a category:

    said = _tokens(sentence) - _NOT_A_NAME
    if not said or category_words.named_in(sentence):   # ← any category at all
        return None

The reason for that gate is real and stays: "Gimnasio Altafit" is a shop, «gimnasio» is how
somebody says SUBSCRIPTIONS, and a question about the gym must not come back about one gym. But the
question it asks is the wrong one. «Suscripciones de Netflix» names a category *and* a shop, in
different words, and there the gate threw away the only reader left that could tell:

  · the planner returns `categories=("SUBSCRIPTIONS",)`, `merchants=()`;
  · the verifier passes it -- the category the sentence named really is in the plan;
  · banking answers the whole category, truthfully, and lists Netflix among its evidence;
  · this repair was the last reader able to notice, and it had given up before looking.

The answer is a genuine figure, correctly computed, for a wider question than the one asked. That
is the exact shape of failure this service is built around, and it is worse than a refusal because
it reads like a confirmation.

The fix is one word wide: the category's own words are dropped from what the sentence is taken to
have said, instead of the sentence being dropped. The gym keeps working -- «gimnasio» is a word the
category vocabulary claims, so nothing is left to match "Gimnasio Altafit" with.
"""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import category_words, merchant_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)

# The four shops behind a month of subscriptions, as the ledger spells them.
SHOPS = ("Netflix", "Gimnasio Altafit", "Spotify", "Steam")


def movement(merchant: str, amount_minor: int) -> Movement:
    return Movement(
        occurred_at=datetime(2026, 8, 3, 9, 0),
        description=f"Cargo {merchant}",
        amount_minor=amount_minor,
        direction="EXPENSE",
        merchant_name=merchant,
        category="SUBSCRIPTIONS",
    )


EVERY_SUBSCRIPTION = QueryAnswer(
    currency="EUR",
    total_minor=6_447,
    movement_count=4,
    movements=tuple(
        movement(shop, minor) for shop, minor in zip(SHOPS, (1_399, 2_990, 1_099, 959), strict=True)
    ),
)

ONLY_NETFLIX = QueryAnswer(
    currency="EUR",
    total_minor=1_399,
    movement_count=1,
    movements=(movement("Netflix", 1_399),),
)


class Ledger:
    """The whole category first, and whatever the repair narrows to second."""

    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return ONLY_NETFLIX if plan.merchants else EVERY_SUBSCRIPTION


class PlansTheCategory:
    """The mistake this repair is for: the shop in the sentence, the category in the plan."""

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.THIS_MONTH),
                categories=("SUBSCRIPTIONS",),
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


async def ask(sentence: str) -> tuple[object, Ledger]:
    ledger = Ledger()
    result = await AskFinancialQuestionHandler(
        PlansTheCategory(),
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=IDLE
        )
    )
    return result, ledger


# --- the reader, on its own -----------------------------------------------------------------


def test_a_shop_named_beside_its_category_is_read() -> None:
    assert merchant_words.named_in("¿cuánto he gastado en suscripciones de Netflix?", SHOPS) == (
        "Netflix"
    )


def test_the_category_alone_still_names_no_shop() -> None:
    assert merchant_words.named_in("¿cuánto he gastado en suscripciones este mes?", SHOPS) is None


def test_the_gym_is_still_the_category_and_not_one_gym() -> None:
    """The rule the old gate existed for, and the one line that must never regress: «gimnasio» is
    a word SUBSCRIPTIONS owns, so it cannot be what names "Gimnasio Altafit"."""
    assert merchant_words.named_in("¿cuánto gasto en el gimnasio?", SHOPS) is None


def test_a_restaurant_named_beside_restaurants_is_read() -> None:
    shops = ("La Tagliatella", "Bar Manolo")
    sentence = "¿cuánto gasté en restaurantes en La Tagliatella este mes?"
    assert merchant_words.named_in(sentence, shops) == "La Tagliatella"


def test_a_near_miss_beside_its_category_is_offered() -> None:
    """The fuzzy half of the same question. "Espotifai" is nobody's category word, so the category
    in the sentence is no reason to stop looking for the shop it nearly names."""
    nearly = merchant_words.almost_named_in("¿cuánto gasté en suscripciones de espotifai?", SHOPS)
    assert "Spotify" in nearly


def test_a_category_word_is_never_a_candidate_for_a_shop() -> None:
    """And the fuzzy half's own risk, which is why the words are dropped rather than the sentence:
    a shop whose name is one letter from «suscripciones» would otherwise be guessed at."""
    assert merchant_words.almost_named_in("¿cuánto gasté en suscripciones?", SHOPS) == ()


def test_the_vocabulary_says_which_words_named_the_category() -> None:
    said = category_words.words_named_in("¿cuánto he gastado en suscripciones de Netflix?")
    assert "suscripciones" in said
    assert "netflix" not in said


def test_a_sentence_that_names_no_category_claims_no_words() -> None:
    assert category_words.words_named_in("¿cuánto gasté en Netflix?") == frozenset()


def test_a_misspelled_category_claims_the_word_the_holder_typed() -> None:
    """`_one_wrong_letter` reads "suscripcionse" as SUBSCRIPTIONS; the word dropped has to be the
    one in the sentence, because that is the one a caller will be subtracting."""
    said = category_words.words_named_in("¿cuánto he gastado en suscripcionse?")
    assert "suscripcionse" in said


# --- and end to end -------------------------------------------------------------------------


async def test_the_answer_is_narrowed_to_the_shop_the_question_named() -> None:
    result, ledger = await ask("¿Cuánto he gastado en suscripciones de Netflix este mes?")

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.merchants for plan in ledger.plans] == [(), ("Netflix",)]
    assert ledger.plans[1].categories == ()
    assert "Solo Netflix" in result.text
    assert "13,99" in result.text


async def test_the_note_says_what_the_first_query_had_covered() -> None:
    result, _ = await ask("¿Cuánto he gastado en suscripciones de Netflix este mes?")

    assert "suscripciones" in result.text.lower()


async def test_a_question_about_the_category_itself_is_answered_whole() -> None:
    """The other half of the same branch: no shop named, so nothing is narrowed and the category's
    own figure is the answer -- one query, not two."""
    result, ledger = await ask("¿Cuánto he gastado en suscripciones este mes?")

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.merchants for plan in ledger.plans] == [()]
    assert "64,47" in result.text
