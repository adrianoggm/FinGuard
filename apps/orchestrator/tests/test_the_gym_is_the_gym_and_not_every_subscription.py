"""«¿Cuánto gasté en el gimnasio este año?» — the gym, not every subscription.

Reported from a live session:

    "¿Cuánto gasté en el gimnasio este año?"
    "421,79 € en suscripciones en lo que va de año, en 23 movimientos."
      · Netflix · Gimnasio Altafit · Spotify · Steam · …

The gym alone is 29,90 € a month. «Gimnasio» is a word of the SUBSCRIPTIONS vocabulary, so the
planner, the gate and the shop reader all agreed the sentence was about the category — and every
one of them was right about the category and wrong about the question. The holder named one kind
of thing inside it.

The rule this pins (option «B1», chosen by the user) is decided once, from the sentence that asks,
when its plan is built (`explicit_facts.of_its_kind`), and nothing a model reads changed: when the
category was named by a *member* word and by nothing else — a word of its vocabulary that is not
the category's own name, and that a shop of that kind carries in its name — the plan keeps the
category and carries the word as a merchant term, which the bank matches as a substring of the
shop's name, case folded («cine» is "Cines Yelmo" and "Cinesa"). So the answer is **every** shop of
the holder's in the category that carries the word, and the plan carries that through every path a
plan travels: the window question, the account question, a retry, a follow-up. When no shop carries
it, the category's figure is given, with one line saying which figure it is and why. «Suscripciones»
and «supermercado» are the categories' own names and never narrow anything.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot, Turn
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import category_words, explicit_facts
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

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
    year of subscriptions is dozens, so a shop the answer names may not be among them — the
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
            # As the bank matches a term (`query_compiler`): a substring of the lowered name.
            and (
                not plan.merchant_terms
                or any(term.lower() in charge.merchant.lower() for term in plan.merchant_terms)
            )
            and charge.merchant not in plan.excluded_merchants
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
    `without` is a shop the plan leaves out, for a sentence that says so, and `shops` the shops it
    names, copied as written -- what the planning prompt asks of the model for «el gimnasio McFit».
    """

    def __init__(self, without: tuple[str, ...] = (), shops: tuple[str, ...] = ()) -> None:
        self.without = without
        self.shops = shops

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.THIS_YEAR),
                categories=category_words.precisely_named_in(sentence),
                merchants=self.shops,
                excluded_merchants=self.without,
            )
        )


class AsksForTheWindow(PlansTheCategory):
    """The planner when the sentence names no window: it read the category and stopped to ask."""

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        return PlanningResult.needs_clarification(
            "¿De qué periodo?",
            draft=FinancialPlanDraft(
                direction=Direction.EXPENSE,
                categories=category_words.precisely_named_in(sentence),
            ),
        )


class AsksForTheCategory(PlansTheCategory):
    """The planner when the sentence names a window and no category: it asks which one."""

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        return PlanningResult.needs_clarification(
            "¿En qué categoría?",
            draft=FinancialPlanDraft(
                direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR)
            ),
            awaiting=Slot.CATEGORY,
        )


class Unreachable(Ledger):
    """Down for the first `failures` reads, which is what leaves a query kept for a retry."""

    def __init__(self, *charges: Charge, failures: int) -> None:
        super().__init__(*charges)
        self.failures = failures

    async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
        if self.failures:
            self.failures -= 1
            raise BankingUnavailableError("down")
        return await super().run(plan, **kwargs)


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
    planner: PlansTheCategory | None = None,
):
    return await AskFinancialQuestionHandler(
        planner or PlansTheCategory(),
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        account_directory=directory,
        contacts=Contacts(),
        cards=Cards(),
        # A directory here is `TwoAccounts`, the holder the account question is asked of — which a
        # deployment does only when configured to ask. Without one the shipped default stands.
        account_scope_default="ask" if directory is not None else "all",
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


# Each names the category with a kind *and* another of its words, and each was answered with the
# kind's shops alone: «metro y tren» as Metro de Madrid without Renfe, «taxi o VTC» without Cabify.
# The other word is in the glossary and not among the precise words, which is all the check read.
BESIDE_ANOTHER_WORD = [
    ("¿Cuánto gasté en metro y tren este año?", "TRANSPORT_PUBLIC"),
    ("¿Cuánto gasté en cine y libros este año?", "LEISURE"),
    ("¿Cuánto gasté en taxi o VTC este año?", "TRANSPORT_PUBLIC"),
    ("¿Cuánto gasté en la farmacia y el médico este año?", "HEALTH"),
]


@pytest.mark.parametrize(("sentence", "category"), BESIDE_ANOTHER_WORD)
def test_a_kind_beside_another_word_for_its_category_is_not_all_it_said(
    sentence: str, category: str
) -> None:
    assert category_words.kinds_named_in(sentence, category) == ()


async def test_the_metro_and_the_train_are_both_answered() -> None:
    ledger = Ledger(
        Charge("Metro de Madrid", "TRANSPORT_PUBLIC", 4_000),
        Charge("Renfe", "TRANSPORT_PUBLIC", 2_500),
    )

    result = await ask("¿Cuánto gasté en metro y tren este año?", ledger)

    assert [(plan.categories, plan.merchants) for plan in ledger.plans] == [
        (("TRANSPORT_PUBLIC",), ())
    ]
    assert "65,00 € en transporte público" in result.text
    assert "comercio" not in result.text


def test_a_kind_left_out_is_not_the_kind_asked_about() -> None:
    assert category_words.kinds_named_in(
        "¿Cuánto gasté en taxi sin el metro?", "TRANSPORT_PUBLIC"
    ) == ("taxi",)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿y aparte del gimnasio?",
        "¿Cuánto gasté este año a parte del gimnasio?",
        "¿Cuánto gasté además del gimnasio?",
        "¿Cuánto pago al mes que no sea el gimnasio?",
    ],
)
def test_a_kind_set_apart_is_not_the_kind_asked_about(sentence: str) -> None:
    """«Aparte de», «además de» and «que no sea» put the kind aside as surely as «sin»: the
    question is about everything else in the category, the opposite of its shops."""
    assert category_words.kinds_named_in(sentence, "SUBSCRIPTIONS") == ()


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


@pytest.mark.parametrize(
    ("category", "kind"),
    [(category, kind) for category, kinds in category_words._KINDS.items() for kind in kinds],
)
def test_every_kind_is_a_word_of_its_own_category_alone(category: str, kind: str) -> None:
    """Three things a kind has to be, each of which a plausible edit to `_KINDS` would break.

    A word the category's precise vocabulary already holds -- one source of truth, never a second
    spelling kept here. A word of no other category, in either vocabulary: a kind narrows the plan's
    one category to its shops, and a word two categories share would narrow whichever the plan
    happened to hold. And a kind the rule can reach: said alone, it names the category by itself,
    so no other word of the category's vocabulary hides inside it and switches it off for good."""
    assert kind in category_words._PRECISE[category]
    for other in category_words.keys():
        if other != category:
            assert kind not in category_words._PRECISE.get(other, ()), other
            assert kind not in category_words._GLOSS.get(other, ()), other
    assert category_words.kinds_named_in(f"¿Cuánto gasté en {kind}?", category) == (kind,)


# --- the plan: a kind is a merchant term beside its category -----------------------------------

THIS_YEAR = RelativePeriod(Period.THIS_YEAR)


def of_its_kind(sentence: str, **fields: object) -> FinancialPlan:
    """A plan with these fields, as `explicit_facts.of_its_kind` leaves it after `sentence`."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR, **fields)  # type: ignore[arg-type]
    return explicit_facts.of_its_kind(plan, explicit_facts.read(sentence, today=TODAY))


def test_the_gym_is_a_term_beside_the_category() -> None:
    plan = of_its_kind("¿Cuánto gasté en el gimnasio?", categories=("SUBSCRIPTIONS",))

    assert (plan.categories, plan.merchants, plan.merchant_terms) == (
        ("SUBSCRIPTIONS",),
        (),
        ("gimnasio",),
    )


def test_a_half_read_plan_carries_it_too() -> None:
    """The draft a window question keeps is the plan the reply completes, so the kind has to be on
    it before the question is asked -- the reply «este año» names none."""
    draft = explicit_facts.enforce(
        FinancialPlanDraft(direction=Direction.EXPENSE, categories=("SUBSCRIPTIONS",)),
        explicit_facts.read("¿Cuánto gasté en el gimnasio?", today=TODAY),
    )

    assert draft.merchant_terms == ("gimnasio",)
    assert draft.with_window(THIS_YEAR).merchant_terms == ("gimnasio",)


@pytest.mark.parametrize(
    "fields",
    [
        # A shop named exactly wins, and a shop left out is the holder's to account for.
        {"merchants": ("McFit",)},
        {"excluded_merchants": ("Gimnasio Altafit",)},
        # A plan cannot hold "these gyms, or else that pharmacy".
        {"categories": ("SUBSCRIPTIONS", "HEALTH")},
        # A family of the reader's own is not this rule's to replace.
        {"merchant_terms": ("apuest", "bet")},
        # Never a payment to a person, nor a movement picked by pronoun.
        {"counterparties": ("Marta",)},
        {"transaction_ids": (uuid.UUID(int=7),)},
    ],
)
def test_a_plan_the_kind_may_not_narrow_is_left_alone(fields: dict[str, object]) -> None:
    """The sentence names the gym every time; what refuses the kind is the plan."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=THIS_YEAR,
        **{"categories": ("SUBSCRIPTIONS",), **fields},  # type: ignore[arg-type]
    )
    said = explicit_facts.read("¿Cuánto gasté en el gimnasio?", today=TODAY)

    assert said.kinds_of("SUBSCRIPTIONS") == ("gimnasio",)
    assert explicit_facts.of_its_kind(plan, said) == plan


def test_a_kind_leaves_with_its_category() -> None:
    """«¿Y en salud?» after the gym: a merge that changes the category keeps the terms, because to
    the model they are one field it did not say it changed. Health shops called «gimnasio» are no
    question anybody asked, and the note under an empty answer would say so about health."""
    plan = of_its_kind("¿y en salud?", categories=("HEALTH",), merchant_terms=("gimnasio",))

    assert plan.merchant_terms == ()


def test_a_kind_of_the_new_category_replaces_the_old_one() -> None:
    assert of_its_kind(
        "¿y en la farmacia?", categories=("HEALTH",), merchant_terms=("gimnasio",)
    ).merchant_terms == ("farmacia",)
    assert of_its_kind(
        "¿y en la óptica?", categories=("HEALTH",), merchant_terms=("farmacia",)
    ).merchant_terms == ("óptica", "optica")


async def test_a_kind_is_carried_with_and_without_its_accent() -> None:
    """Probed by review: the bank lowers a shop's name and does not fold it, so «óptica» alone is
    not "Optica Roca" -- and the note under the category's figure then said no shop carried the
    word, beside the one that does. Both spellings go as alternatives; the person reads one."""
    ledger = Ledger(
        Charge("Optica Roca", "HEALTH", 9_000), Charge("Farmacia Gran Via", "HEALTH", 1_250)
    )

    result = await ask("¿Cuánto gasté en la óptica este año?", ledger)

    assert ledger.plans[-1].merchant_terms == ("óptica", "optica")
    assert "90,00 € en Optica Roca" in result.text
    assert "ningún comercio" not in result.text
    assert result.plan_in_words.endswith("salud, comercios con «óptica» en el nombre")


def test_a_kind_stays_while_its_category_does() -> None:
    """The follow-up that names no kind keeps the one the plan carries, and a plan the holder
    widened past every category («buscar sin categoría») keeps it as well -- that is the gyms in
    any category, which is what the button says."""
    assert of_its_kind(
        "¿y el año pasado?", categories=("SUBSCRIPTIONS",), merchant_terms=("gimnasio",)
    ).merchant_terms == ("gimnasio",)
    assert of_its_kind("¿y el año pasado?", merchant_terms=("gimnasio",)).merchant_terms == (
        "gimnasio",
    )


# --- and end to end ---------------------------------------------------------------------------


async def test_the_gym_is_answered_as_the_gym() -> None:
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    # One query, and it is the gym's: the category stays and the kind narrows it.
    assert [(plan.categories, plan.merchant_terms) for plan in ledger.plans] == [
        (("SUBSCRIPTIONS",), ("gimnasio",))
    ]
    assert "29,90 € en Gimnasio Altafit" in result.text
    assert "54,88" not in result.text, "the whole category is the reported defect"
    # «Ver la consulta» is the query that produced the figure, said as a person says it; the
    # identifier line the planner reads keeps its own words.
    assert result.plan_in_words.endswith("suscripciones, comercios con «gimnasio» en el nombre")
    assert result.plan is not None
    assert result.plan.endswith("subscriptions, comercio contiene gimnasio")


async def test_the_cinema_is_the_cinema_the_ledger_calls_cines() -> None:
    ledger = Ledger(
        Charge("Cines Yelmo", "LEISURE", 1_850),
        Charge("Fnac", "LEISURE", 3_499),
        Charge("Decathlon", "LEISURE", 4_200),
    )

    result = await ask("¿Cuánto gasté en el cine este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchant_terms == ("cine",)
    assert ledger.plans[-1].categories == ("LEISURE",)
    assert "18,50 € en Cines Yelmo" in result.text
    assert "ningún comercio" not in result.text


async def test_a_shop_carries_the_kind_as_the_bank_reads_a_term() -> None:
    """A term is a substring of the shop's name, case folded, and that is the bank's rule rather
    than a plural rule of ours: «cine» is "Cinesa" as well as "Cines Yelmo". Both are cinemas, and
    the category beside the term keeps a shop of any other kind out."""
    ledger = Ledger(
        Charge("Cines Yelmo", "LEISURE", 1_850),
        Charge("Cinesa", "LEISURE", 900),
        Charge("Fnac", "LEISURE", 3_499),
    )

    result = await ask("¿Cuánto gasté en el cine este año?", ledger)

    assert "27,50 € en Cines Yelmo y Cinesa" in result.text
    assert "Fnac" not in result.text


async def test_two_gyms_are_both_gyms() -> None:
    """Every shop the word names, never one of them: the bank adds up every gym of the window, so
    there is no list of them to get right first."""
    ledger = Ledger(NETFLIX, GYM, SECOND_GYM)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [plan.merchant_terms for plan in ledger.plans] == [("gimnasio",)]
    assert "47,90 € en Gimnasio Altafit y Gimnasio Municipal" in result.text


async def test_gyms_the_evidence_cannot_all_show_are_said_by_their_kind() -> None:
    """The answer names the shops only when the movements beside the figure are all of them. Here
    one of two came back, and naming it would read as the whole list."""
    ledger = Ledger(NETFLIX, GYM, SECOND_GYM, evidence=1)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert "47,90 € en comercios con «gimnasio» en el nombre" in result.text
    assert "Gimnasio Altafit" not in result.text.splitlines()[0]


async def test_no_gym_is_the_category_and_says_so() -> None:
    ledger = Ledger(NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    # The kind, which found nothing, and then the category -- the one extra query this costs.
    assert [plan.merchant_terms for plan in ledger.plans] == [("gimnasio",), ()]
    assert "24,98 € en suscripciones" in result.text
    note = result.text.splitlines()[-1]
    assert note == (
        "(Incluye toda la categoría suscripciones: en este periodo ningún comercio lleva"
        " «gimnasio» en el nombre.)"
    )
    # «Ver la consulta» is the query that produced the figure; the turn remembers the question.
    assert result.plan_in_words.endswith("gastos, suscripciones")
    remembered = result.conversation.last_plan
    assert isinstance(remembered, FinancialPlan)
    assert (remembered.categories, remembered.merchant_terms) == (("SUBSCRIPTIONS",), ("gimnasio",))


async def test_the_turn_after_the_note_is_still_about_the_gym() -> None:
    """Probed by review: after «ningún comercio lleva «gimnasio»», «¿y el año pasado?» was every
    subscription with no note -- the turn had remembered the category's figure as the question.
    The question was the gym, and last year is asked about the gym."""
    ledger = Ledger(NETFLIX, SPOTIFY)
    first = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)
    ran = len(ledger.plans)

    result = await ask("¿y el año pasado?", ledger, first.conversation)

    assert ledger.plans[ran].merchant_terms == ("gimnasio",)
    assert ledger.plans[ran].date_range is not None
    assert result.text.splitlines()[-1] == (
        "(Incluye toda la categoría suscripciones: en este periodo ningún comercio lleva"
        " «gimnasio» en el nombre.)"
    )


async def test_no_gym_in_an_empty_category_is_nothing_found() -> None:
    """With nothing in the category either, there is no figure for the note to explain: the answer
    is the kind's own «no he encontrado», which is true."""
    ledger = Ledger(Charge("Farmacia Gran Via", "HEALTH", 1_250))

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert result.text.startswith(
        "No he encontrado movimientos en comercios con «gimnasio» en el nombre"
    )
    assert "Incluye" not in result.text


async def test_the_dentist_is_the_health_category_as_before() -> None:
    ledger = Ledger(
        Charge("Clinica Dental Sur", "HEALTH", 6_000), Charge("Farmacia Gran Via", "HEALTH", 1_250)
    )

    result = await ask("¿cuánto gasté en el dentista?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert [(plan.categories, plan.merchants) for plan in ledger.plans] == [(("HEALTH",), ())]
    # Every account, unsaid: the sentence asked nothing about accounts (spec §3.5).
    assert result.text == "72,50 € en salud en lo que va de año, en 2 movimientos."


async def test_a_follow_up_setting_the_gym_apart_is_not_the_gym() -> None:
    """Probed by review: «¿y aparte del gimnasio?» after a question about subscriptions answered
    «29,90 € en Gimnasio Altafit» -- the one shop the holder had just asked to leave out."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    first = await ask("¿Cuánto gasté en suscripciones este año?", ledger)
    ran = len(ledger.plans)

    result = await ask("¿y aparte del gimnasio?", ledger, first.conversation)

    assert all("Gimnasio Altafit" not in plan.merchants for plan in ledger.plans[ran:])
    assert "en Gimnasio Altafit" not in result.text
    assert "comercio" not in result.text


async def test_a_plan_that_leaves_a_shop_out_is_not_narrowed() -> None:
    """The window's shops are read under the plan's own exclusions, so a plan without Gimnasio
    Altafit found no gym and said so -- «ningún comercio lleva «gimnasio» en el nombre», about a
    holder who pays one. A plan that already leaves shops out is not this rule's to narrow."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    result = await ask(
        "¿Cuánto gasté en el gimnasio este año, quitando Altafit?",
        ledger,
        planner=PlansTheCategory(without=("Gimnasio Altafit",)),
    )

    assert [(plan.categories, plan.merchants) for plan in ledger.plans] == [
        (("SUBSCRIPTIONS",), ())
    ]
    assert "ningún comercio" not in result.text
    assert "Solo" not in result.text


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
    """The kind is on the plan, and the plan is what a follow-up inherits, so «¿y el año pasado?»
    is still about the gym rather than about every subscription last year."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    first = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    await ask("¿y el año pasado?", ledger, first.conversation)

    follow_up = ledger.plans[-1]
    assert follow_up.merchant_terms == ("gimnasio",)
    assert follow_up.categories == ("SUBSCRIPTIONS",)
    assert follow_up.date_range is not None
    assert follow_up.date_range.start.year == 2025


async def test_the_gym_survives_the_account_question() -> None:
    """The live session's own route: «¿En qué cuenta…?» in between, and «Todas mis cuentas» as the
    reply. The pending plan runs on the reply's turn, whose sentence names no gym at all, and the
    answer came back as every subscription the holder pays. The plan the question keeps is the
    gym's, so the reply's words do not have to be."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿Cuánto gasté en el gimnasio este año?", ledger, directory=TwoAccounts())
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans == []

    answered = await ask("Todas mis cuentas", ledger, asked.conversation, TwoAccounts())

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert ledger.plans[-1].across_accounts
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_a_shop_named_beside_its_category_survives_the_account_question() -> None:
    """The same route for a shop: the shop reader read the reply too, so «suscripciones de
    Netflix» answered every subscription once an account had been chosen. The plan the question
    keeps is Netflix's -- looked up on the turn that asked -- and the reply answers it."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask(
        "¿Cuánto gasté en suscripciones de Netflix este año?", ledger, directory=TwoAccounts()
    )
    # The holder's shops in the category, listed once, on the turn that asked.
    assert [plan.group_by for plan in ledger.plans] == [GroupBy.MERCHANT]

    answered = await ask("Todas mis cuentas", ledger, asked.conversation, TwoAccounts())

    assert (ledger.plans[-1].categories, ledger.plans[-1].merchants) == ((), ("Netflix",))
    assert "13,99 € en Netflix" in answered.text


async def test_the_gym_survives_the_window_question() -> None:
    """Probed by review: «¿Cuánto gasté en el gimnasio?» → «¿De qué periodo?» → «este año»
    answered every subscription. The reply completes a plan its own sentence never stated -- the
    draft the question kept, which carries the kind."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿Cuánto gasté en el gimnasio?", ledger, planner=AsksForTheWindow())
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, asked.conversation, planner=AsksForTheWindow())

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_a_category_given_as_the_reply_is_read_from_the_reply() -> None:
    """The other slot a reply can fill. When the question back was the category, the reply is
    the sentence that named it, and the turn that asked never said «gimnasio» at all."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask(
        "¿Cuánto gasté este año?",
        ledger,
        planner=AsksForTheCategory(),
    )
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    await ask("el gimnasio", ledger, asked.conversation, planner=AsksForTheCategory())

    assert ledger.plans[-1].merchant_terms == ("gimnasio",)


async def test_the_gym_survives_a_retry() -> None:
    """«Reintenta» names nothing, and needs to name nothing: the plan kept for the retry is the
    gym's."""
    ledger = Unreachable(GYM, NETFLIX, SPOTIFY, failures=2)
    failed = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)
    assert failed.outcome == "BANKING_UNAVAILABLE"

    retried = await ask("reintenta", ledger, failed.conversation)

    assert retried.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert "29,90 € en Gimnasio Altafit" in retried.text


# --- the plan carries it through every path a kept plan travels --------------------------------
#
# Each of these is a route review found the turn-log reading lost: a reply whose own words name
# nothing, completing or resuming a plan another turn built. The kind is on that plan, so no route
# has to find the sentence that asked.


async def test_the_gym_survives_the_window_and_then_the_account_question() -> None:
    """Probed by review: «¿gimnasio?» → «este año» → «¿En qué cuenta?» → «Todas mis cuentas»
    answered every subscription. Two replies, neither naming the gym, and the account question
    asked on the turn that completed the window."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿gimnasio?", ledger, directory=TwoAccounts(), planner=AsksForTheWindow())
    which = await ask(
        "este año", ledger, asked.conversation, TwoAccounts(), planner=AsksForTheWindow()
    )
    assert which.question == "¿En qué cuenta quieres consultarlo?"

    answered = await ask(
        "Todas mis cuentas", ledger, which.conversation, TwoAccounts(), planner=AsksForTheWindow()
    )

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert [(plan.merchant_terms, plan.across_accounts) for plan in ledger.plans] == [
        (("gimnasio",), True)
    ]
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_the_gym_survives_the_window_and_then_a_retry() -> None:
    """Probed by review: the turn the bank could not answer was the window's reply, «este año», and
    the retry read that turn's sentence as the question."""
    ledger = Unreachable(GYM, NETFLIX, SPOTIFY, failures=2)
    asked = await ask("¿Cuánto gasté en el gimnasio?", ledger, planner=AsksForTheWindow())
    failed = await ask("este año", ledger, asked.conversation, planner=AsksForTheWindow())
    assert failed.outcome == "BANKING_UNAVAILABLE"

    retried = await ask("reintenta", ledger, failed.conversation, planner=AsksForTheWindow())

    assert retried.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert "29,90 € en Gimnasio Altafit" in retried.text


async def test_the_gym_average_survives_its_own_window_question() -> None:
    """«¿Cuál es mi gasto medio en el gimnasio?» is built by a reader, not the planner, and asks
    its own period question. The draft it keeps is the gym's, and so is the average."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿cuál es mi gasto medio en el gimnasio?", ledger)
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, asked.conversation)

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert [(plan.metric, plan.merchant_terms) for plan in ledger.plans] == [
        (Metric.AVERAGE, ("gimnasio",))
    ]
    assert "medio por movimiento en Gimnasio Altafit" in answered.text
    assert "29,90 €" in answered.text


class CallsTheShopItsCategory(PlansTheCategory):
    """The model's misreading the demo reported -- a shop planned as the category it belongs to --
    made while it stops to ask for the window. Whatever the sentence, a draft of subscriptions."""

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        return PlanningResult.needs_clarification(
            "¿De qué periodo?",
            draft=FinancialPlanDraft(direction=Direction.EXPENSE, categories=("SUBSCRIPTIONS",)),
        )


@pytest.mark.parametrize(
    ("sentence", "shop", "figure"),
    [
        ("¿Cuánto gasté en Spotify?", "Spotify", "10,99 € en Spotify"),
        ("¿Cuánto gasté en Altafit?", "Gimnasio Altafit", "29,90 € en Gimnasio Altafit"),
    ],
)
async def test_a_shop_the_model_read_as_its_category_survives_the_window_question(
    sentence: str, shop: str, figure: str
) -> None:
    """Probed by review: the category came from the model, «este año» named nothing, and the
    answer was every subscription, 54,88 €, with no note. The shop is looked up among the holder's
    own shops on the turn that asks, and the draft the question keeps is that shop's."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    planner = CallsTheShopItsCategory()
    asked = await ask(sentence, ledger, planner=planner)
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, asked.conversation, planner=planner)

    assert (ledger.plans[-1].categories, ledger.plans[-1].merchants) == ((), (shop,))
    assert figure in answered.text
    assert "54,88" not in answered.text


async def test_another_shop_asked_while_the_window_is_open_is_that_shop() -> None:
    """Probed by review: «¿Cuánto gasté en el gimnasio?» → «¿y en Spotify?», the period asked
    again → «este año» answered «Solo Gimnasio Altafit», the older question. The draft the second
    question keeps is the second question's: Spotify's."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    planner = CallsTheShopItsCategory()
    gym = await ask("¿Cuánto gasté en el gimnasio?", ledger, planner=planner)
    spotify = await ask("¿y en Spotify?", ledger, gym.conversation, planner=planner)
    assert spotify.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, spotify.conversation, planner=planner)

    assert ledger.plans[-1].merchants == ("Spotify",)
    assert ledger.plans[-1].merchant_terms == ()
    assert "10,99 € en Spotify" in answered.text
    assert "Altafit" not in answered.text


@pytest.mark.parametrize(
    ("before", "failures"),
    [
        # Another plan's period question, still in the log: the average asked about the gym.
        (["¿cuál es mi gasto medio en el gimnasio?"], 0),
        # And a turn the bank could not answer, between the gym and the question being answered.
        (["¿Cuánto gasté en el gimnasio?", "este año"], 2),
    ],
)
async def test_an_older_question_about_the_gym_is_not_the_one_answered(
    before: list[str], failures: int
) -> None:
    """Probed by review: walking the log back for the sentence a window completes stepped over
    the question that asked it -- it named no category, the model had read one into «Spotify» --
    and found an older one about the gym. The window answers the draft its own question kept."""
    ledger = Unreachable(GYM, NETFLIX, SPOTIFY, failures=failures)
    planner = CallsTheShopItsCategory()
    conversation = IDLE
    for sentence in before:
        conversation = (await ask(sentence, ledger, conversation, planner=planner)).conversation
    spotify = await ask("¿Cuánto gasté en Spotify?", ledger, conversation, planner=planner)
    assert spotify.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, spotify.conversation, planner=planner)

    assert ledger.plans[-1].merchants == ("Spotify",)
    assert "10,99 € en Spotify" in answered.text


async def test_a_question_naming_no_shop_costs_no_list() -> None:
    """The list is read for a sentence with a word a shop could be called, and only for one: the
    category by its own name says nothing a list could match."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    asked = await ask("¿suscripciones?", ledger, planner=AsksForTheWindow())

    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert ledger.plans == []


async def test_a_gym_named_by_its_own_name_is_that_gym() -> None:
    """Probed by review: «el gimnasio Altafit» answered both gyms. The plan asks for the gyms, and
    the shop reader then finds the one the sentence names among the gyms the answer shows -- the
    evidence of a figure that already holds nothing but gyms."""
    ledger = Ledger(NETFLIX, GYM, SECOND_GYM, evidence=1)

    result = await ask("¿Cuánto gasté en el gimnasio Altafit este año?", ledger)

    assert ledger.plans[-1].merchants == ("Gimnasio Altafit",)
    assert ledger.plans[-1].merchant_terms == ()
    assert "29,90 € en Gimnasio Altafit" in result.text
    assert "Gimnasio Municipal" not in result.text
    assert result.text.splitlines()[-1] == (
        "(Solo Gimnasio Altafit, de los comercios con «gimnasio» en el nombre.)"
    )


MCFIT = Charge("McFit", "SUBSCRIPTIONS", 2_500)


@pytest.mark.parametrize(
    ("sentence", "shop", "charges", "figure", "covered"),
    [
        (
            "¿Cuánto gasté en el gimnasio McFit este año?",
            "McFit",
            (GYM, NETFLIX, MCFIT),
            "25,00 € en McFit",
            "suscripciones",
        ),
        # McFit the only gym: the shop is still the answer, never «ningún comercio lleva
        # «gimnasio»» beside it.
        (
            "¿Cuánto gasté en el gimnasio McFit este año?",
            "McFit",
            (NETFLIX, SPOTIFY, MCFIT),
            "25,00 € en McFit",
            "suscripciones",
        ),
        (
            "¿Cuánto gasté en el cine Kinepolis este año?",
            "Kinepolis",
            (Charge("Cines Yelmo", "LEISURE", 1_850), Charge("Kinepolis", "LEISURE", 1_200)),
            "12,00 € en Kinepolis",
            "ocio",
        ),
        (
            "¿Cuánto gasté en el taxi de Uber este año?",
            "Uber",
            (
                Charge("Taxi Express", "TRANSPORT_PUBLIC", 1_500),
                Charge("Uber", "TRANSPORT_PUBLIC", 2_200),
            ),
            "22,00 € en Uber",
            "transporte público",
        ),
    ],
)
async def test_a_shop_named_beside_its_kind_is_that_shop(
    sentence: str, shop: str, charges: tuple[Charge, ...], figure: str, covered: str
) -> None:
    """Probed by review: with a planner that read only the category, «el gimnasio McFit» answered
    Gimnasio Altafit, «el cine Kinepolis» Cines Yelmo -- the kind's query never shows a shop whose
    name lacks the kind. The category's shops are listed first, a shop the sentence names whole
    wins over its kind, and the line under the figure says the answer is narrower."""
    ledger = Ledger(*charges)

    result = await ask(sentence, ledger)

    assert [plan.group_by for plan in ledger.plans] == [GroupBy.MERCHANT, GroupBy.NONE]
    assert (ledger.plans[-1].merchants, ledger.plans[-1].merchant_terms) == ((shop,), ())
    assert figure in result.text
    assert result.text.splitlines()[-1] == (
        f"(Solo {shop}. La consulta que había construido cubría {covered} entera.)"
    )


async def test_a_shop_the_planner_copied_is_left_as_it_is() -> None:
    """The planner's prompt asks it to copy the shop a sentence names, and a plan that names one
    exactly is never narrowed to a kind, nor looked up again: it already says which shop."""
    ledger = Ledger(GYM, NETFLIX, MCFIT)

    result = await ask(
        "¿Cuánto gasté en el gimnasio McFit este año?",
        ledger,
        planner=PlansTheCategory(shops=("McFit",)),
    )

    assert [(plan.merchants, plan.merchant_terms) for plan in ledger.plans] == [(("McFit",), ())]
    assert "25,00 € en McFit" in result.text


async def test_a_place_beside_a_kind_names_no_shop_of_another_kind() -> None:
    """A shop that does not carry the kind is named whole or not at all: «el taxi de Madrid» is
    the taxis, not "EMT Madrid", which shares only the place."""
    ledger = Ledger(
        Charge("Taxi Express", "TRANSPORT_PUBLIC", 1_500),
        Charge("EMT Madrid", "TRANSPORT_PUBLIC", 400),
    )

    result = await ask("¿Cuánto gasté en el taxi de Madrid este año?", ledger)

    assert ledger.plans[-1].merchant_terms == ("taxi",)
    assert "15,00 € en Taxi Express" in result.text
    assert "EMT" not in result.text


class FailsTheShop(Ledger):
    """Answers the category and the kind, and fails every read of a plan that names a shop."""

    async def run(self, plan: FinancialPlan, **kwargs: object) -> QueryAnswer:
        if plan.merchants:
            raise BankingUnavailableError("down")
        return await super().run(plan, **kwargs)


@pytest.mark.parametrize(
    ("sentence", "figure", "note"),
    [
        (
            "¿Cuánto gasté en suscripciones de Netflix este año?",
            "72,88 € en suscripciones",
            "(Incluye toda la categoría suscripciones: no he podido consultar solo Netflix.)",
        ),
    ],
)
async def test_a_narrower_figure_the_bank_could_not_give_is_said(
    sentence: str, figure: str, note: str
) -> None:
    """The figure in hand is true and wider than the shop the sentence named, so it is given as
    what it is: the category's, or the kind's."""
    ledger = FailsTheShop(GYM, SECOND_GYM, NETFLIX, SPOTIFY)

    result = await ask(sentence, ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert figure in result.text
    assert result.text.splitlines()[-1] == note


async def test_a_gym_found_before_its_kind_ran_is_kept_for_the_retry() -> None:
    """«El gimnasio Altafit» is narrowed to the gym before the kind's query runs, so a bank that
    cannot read the gym has nothing wider in hand to give: the shop's plan is kept for a retry."""
    ledger = FailsTheShop(GYM, SECOND_GYM, NETFLIX, SPOTIFY)

    result = await ask("¿Cuánto gasté en el gimnasio Altafit este año?", ledger)

    assert result.outcome == "BANKING_UNAVAILABLE"
    retry = result.conversation.retry_plan
    assert isinstance(retry, FinancialPlan)
    assert retry.merchants == ("Gimnasio Altafit",)


async def test_more_gyms_than_a_plan_could_name_are_all_the_gym() -> None:
    """Eleven shops carry «gimnasio», and a plan names at most ten shops exactly -- which is why
    this used to give the category's figure with a note. A term names none of them: the bank adds
    up every gym it finds, however many, in the one query."""
    gyms = tuple(Charge(f"Gimnasio {n}", "SUBSCRIPTIONS", 1_000) for n in range(1, 12))
    ledger = Ledger(NETFLIX, *gyms)

    result = await ask("¿Cuánto gasté en el gimnasio este año?", ledger)

    assert [plan.merchant_terms for plan in ledger.plans] == [("gimnasio",)]
    assert "110,00 € en comercios con «gimnasio» en el nombre" in result.text
    assert "Incluye" not in result.text


async def test_a_plan_with_two_categories_is_answered_whole() -> None:
    """«El gimnasio y la farmacia» is two categories in one plan, and a plan cannot hold "these
    gyms, or else that pharmacy": narrowing the first would drop the second from the figure."""
    ledger = Ledger(GYM, NETFLIX, Charge("Farmacia Gran Via", "HEALTH", 1_250))

    result = await ask("¿Cuánto gasté en el gimnasio y la farmacia este año?", ledger)

    assert [(plan.categories, plan.merchants) for plan in ledger.plans] == [
        (("SUBSCRIPTIONS", "HEALTH"), ())
    ]
    assert "56,39" in result.text
    assert "comercio" not in result.text


# --- a reply that narrows on its own, and a question asked in between ---------------------------


@pytest.mark.parametrize(
    ("reply", "merchants", "terms", "figure"),
    [
        ("Netflix este año", ("Netflix",), (), "13,99 € en Netflix"),
        ("el gimnasio este año", (), ("gimnasio",), "29,90 € en Gimnasio Altafit"),
        ("este año, solo Netflix", ("Netflix",), (), "13,99 € en Netflix"),
    ],
)
async def test_a_window_reply_that_names_a_shop_is_read_itself(
    reply: str, merchants: tuple[str, ...], terms: tuple[str, ...], figure: str
) -> None:
    """The asking turn's sentence is the fallback, never the replacement. «¿Cuánto gasté en
    suscripciones?» → «¿De qué periodo?» → «Netflix este año» is about Netflix, and the reply is the
    only sentence that says so; reading the question in its place answered every subscription."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿Cuánto gasté en suscripciones?", ledger, planner=AsksForTheWindow())

    answered = await ask(reply, ledger, asked.conversation, planner=AsksForTheWindow())

    assert ledger.plans[-1].merchants == merchants
    assert ledger.plans[-1].merchant_terms == terms
    assert figure in answered.text


async def test_a_place_in_the_name_does_not_pick_a_shop_of_another_kind() -> None:
    """Probed by review: with only Renfe in the evidence, «el metro de Madrid» re-read the whole
    window by name and «Madrid» picked EMT Madrid -- a bus, not the metro the kind had found."""
    ledger = Ledger(
        Charge("Renfe", "TRANSPORT_PUBLIC", 900),
        Charge("EMT Madrid", "TRANSPORT_PUBLIC", 400),
        Charge("Metro de Madrid", "TRANSPORT_PUBLIC", 1_200),
        evidence=1,
    )

    result = await ask("¿Cuánto gasté en el metro de Madrid este año?", ledger)

    assert ledger.plans[-1].merchants == ("Metro de Madrid",)
    assert "12,00 € en Metro de Madrid" in result.text
    assert "EMT" not in result.text


async def test_a_place_in_the_name_does_not_swap_the_gyms_for_another_shop() -> None:
    ledger = Ledger(
        NETFLIX,
        SPOTIFY,
        GYM,
        SECOND_GYM,
        Charge("Netflix Madrid", "SUBSCRIPTIONS", 500),
        evidence=1,
    )

    result = await ask("¿Cuánto gasté en el gimnasio de Madrid este año?", ledger)

    # The category's shops, listed for the word «Madrid», and then the gyms: Netflix Madrid is not
    # a gym, and is not named whole.
    assert [(plan.group_by, plan.merchants, plan.merchant_terms) for plan in ledger.plans] == [
        (GroupBy.MERCHANT, (), ()),
        (GroupBy.NONE, (), ("gimnasio",)),
    ]
    assert "47,90" in result.text
    assert "Netflix Madrid" not in result.text


class KeepsAsking(PlansTheCategory):
    """Asks for the window every time, keeping the draft of the sentence that named a category."""

    def __init__(self) -> None:
        super().__init__()
        self.draft: FinancialPlanDraft | None = None

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        named = category_words.precisely_named_in(sentence)
        if self.draft is None or named:
            self.draft = FinancialPlanDraft(direction=Direction.EXPENSE, categories=named)
        return PlanningResult.needs_clarification("¿De qué periodo?", draft=self.draft)


async def test_the_question_asked_again_is_not_the_question() -> None:
    """«No sé» asks for the period a second time and states nothing. The planner's second draft is
    subscriptions -- a kind is not a field it can write -- and it replaces the draft the open
    question kept, which carried the gym: that is the question the reply still completes."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    planner = KeepsAsking()
    first = await ask("¿Cuánto gasté en el gimnasio?", ledger, planner=planner)
    again = await ask("no sé, el que quieras", ledger, first.conversation, planner=planner)
    assert again.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value

    answered = await ask("este año", ledger, again.conversation, planner=planner)

    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_the_question_asked_again_keeps_the_gym_through_the_account_question() -> None:
    """The re-asked window, and then the account question on the reply's turn: three turns after
    the one that named the gym, none of them naming it. Read back out of the log, the account's
    reply found «este año» as the question it answered."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    planner = KeepsAsking()
    first = await ask(
        "¿Cuánto gasté en el gimnasio?", ledger, directory=TwoAccounts(), planner=planner
    )
    again = await ask(
        "no sé, el que quieras", ledger, first.conversation, TwoAccounts(), planner=planner
    )
    which = await ask("este año", ledger, again.conversation, TwoAccounts(), planner=planner)
    assert which.question == "¿En qué cuenta quieres consultarlo?"

    answered = await ask(
        "Todas mis cuentas", ledger, which.conversation, TwoAccounts(), planner=planner
    )

    assert ledger.plans[-1].merchant_terms == ("gimnasio",)
    assert "29,90 € en Gimnasio Altafit" in answered.text


async def test_the_category_said_while_the_gym_is_open_is_the_category() -> None:
    """«¿Y en suscripciones?» while the gym's window is open says the category outright, and the
    draft it leaves is every subscription -- not the gym the open question carried."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    planner = KeepsAsking()
    first = await ask("¿Cuánto gasté en el gimnasio?", ledger, planner=planner)
    again = await ask("¿y en suscripciones?", ledger, first.conversation, planner=planner)

    answered = await ask("este año", ledger, again.conversation, planner=planner)

    assert ledger.plans[-1].merchant_terms == ()
    assert "54,88 € en suscripciones" in answered.text


@pytest.mark.parametrize(
    "in_between",
    [
        # What a drill-down leaves when it fits several bars: its own outcome, and the plan it
        # would have drilled into.
        Turn(
            sentence="¿y el cine?",
            outcome="DRILL_AMBIGUOUS",
            question="¿Cuál de ellas quieres ver: cine o libros?",
            plan="en lo que va de año, expense, leisure",
        ),
        # And an ask about the answer in view, as «nada» gets.
        Turn(
            sentence="el cine",
            outcome="NEEDS_CLARIFICATION",
            question=(
                "No sé a qué te refieres; ¿quieres cambiar el gráfico, el periodo o la categoría?"
            ),
            plan="en lo que va de año, expense, leisure",
        ),
    ],
)
async def test_a_question_asked_in_between_is_not_the_question(in_between: Turn) -> None:
    """While «¿De qué periodo?» stays open, other turns ask other things. One that says «cine» is
    not the sentence «este año» completes -- the plan is the one «¿cuánto gasté en ocio?» stated,
    and every leisure shop is its answer."""
    ledger = Ledger(Charge("Cines Yelmo", "LEISURE", 1_850), Charge("Fnac", "LEISURE", 3_499))
    asked = await ask("¿Cuánto gasté en ocio?", ledger, planner=AsksForTheWindow())

    await ask(
        "este año", ledger, asked.conversation.with_turn(in_between), planner=AsksForTheWindow()
    )

    assert ledger.plans[-1].merchants == ()
    assert ledger.plans[-1].categories == ("LEISURE",)


# --- what the next review found: the waiting plans, and the words that are no shop -------------


async def test_a_reader_draft_keeps_the_shop_its_question_named() -> None:
    """Probed by review: «¿cuál es mi gasto medio en suscripciones de Netflix?» is a reader's
    draft, and «este año» answered the average over every subscription. The draft is narrowed to
    Netflix on the turn that asks, and that turn says so."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)
    asked = await ask("¿cuál es mi gasto medio en suscripciones de Netflix?", ledger)
    assert asked.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert asked.text.splitlines()[-1] == (
        "(Solo Netflix. La consulta que había construido cubría suscripciones entera.)"
    )

    answered = await ask("este año", ledger, asked.conversation)

    assert ledger.plans[-1].merchants == ("Netflix",)
    assert "medio por movimiento en Netflix" in answered.text
    assert "13,99 €" in answered.text


async def test_the_question_that_narrowed_a_waiting_plan_says_so() -> None:
    """Probed by review: «¿Cuánto gasté en suscripciones en Madrid?» → «este año» answered Netflix
    Madrid with no word of the narrowing, which the same question with its window says. The
    waiting plan was narrowed on the turn that asked, and the line goes under that question."""
    ledger = Ledger(NETFLIX, SPOTIFY, Charge("Netflix Madrid", "SUBSCRIPTIONS", 500))
    asked = await ask(
        "¿Cuánto gasté en suscripciones en Madrid?", ledger, planner=AsksForTheWindow()
    )
    assert asked.text.splitlines()[-1] == (
        "(Solo Netflix Madrid. La consulta que había construido cubría suscripciones entera.)"
    )

    answered = await ask("este año", ledger, asked.conversation, planner=AsksForTheWindow())

    assert "5,00 € en Netflix Madrid" in answered.text


@pytest.mark.parametrize(
    "sentence",
    [
        "¿suscripciones?",
        "¿Cuánto gasté en suscripciones?",
        "¿Cuánto he gastado en el supermercado?",
        "¿Me gustaría saber cuánto he gastado en el gimnasio durante este año?",
        # Money coming in names who paid, not a shop: the corpus's own payroll question.
        "¿Sabrías decirme si he cobrado mi nómina en qué cuenta?",
    ],
)
async def test_the_words_every_question_is_made_of_cost_no_list(sentence: str) -> None:
    """Probed by review: «cuánto» and «gasté» passed for a shop's name, so nearly every window
    question paid for the list of shops. Only a word no question is built from does."""
    ledger = Ledger(GYM, NETFLIX, SPOTIFY)

    await ask(sentence, ledger, planner=AsksForTheWindow())

    assert all(plan.group_by is not GroupBy.MERCHANT for plan in ledger.plans)


@pytest.mark.parametrize(
    ("sentence", "charges", "wrong"),
    [
        (
            "¿Cuánto gasté en el gimnasio de Madrid este año?",
            (NETFLIX, SPOTIFY, Charge("Netflix Madrid", "SUBSCRIPTIONS", 500)),
            "Netflix Madrid",
        ),
        (
            "¿Cuánto gasté en el metro de Madrid este año?",
            (
                Charge("EMT Madrid", "TRANSPORT_PUBLIC", 400),
                Charge("Renfe Cercanias", "TRANSPORT_PUBLIC", 900),
                Charge("Consorcio Transportes", "TRANSPORT_PUBLIC", 2_000),
            ),
            "EMT Madrid",
        ),
        (
            "¿Cuánto gasté en la farmacia de Triana este año?",
            (
                Charge("Optica Triana", "HEALTH", 9_000),
                Charge("Clinica Dental Sur", "HEALTH", 5_000),
            ),
            "Optica Triana",
        ),
    ],
)
async def test_a_place_beside_a_kind_that_found_nothing_names_no_other_shop(
    sentence: str, charges: tuple[Charge, ...], wrong: str
) -> None:
    """When no shop carries the kind, the answer falls back to the whole category -- and the
    category's evidence then holds shops of other kinds that share the sentence's place word.
    «Madrid» is not a name for "EMT Madrid" when the question was about the metro: a shop that
    does not carry the kind is named only in full, as `merchant_words.named_beside_a_kind` says,
    on the fallback as everywhere else."""
    ledger = Ledger(*charges)

    answered = await ask(sentence, ledger)

    assert f"Solo {wrong}" not in answered.text
    assert all(plan.merchants != (wrong,) for plan in ledger.plans)
