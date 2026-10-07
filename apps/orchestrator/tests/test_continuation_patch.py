from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services.continuation_patch import (
    GroundedContinuation,
    PatchStatus,
    apply,
    grouping_in,
    read,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.plan_patch import PatchField, PlanPatch

TODAY = date(2026, 8, 25)


def plan() -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("FOOD",),
        only_account_id=UUID("11111111-1111-1111-1111-111111111111"),
        counterparties=("marta",),
        transaction_ids=(UUID("22222222-2222-2222-2222-222222222222"),),
        internal_only=True,
        include_internal=True,
    )


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        ("Sin el alquiler", PatchStatus.APPLIED),
        ("Excepto Mercadona", PatchStatus.APPLIED),
        ("No, durante todo 2025", PatchStatus.APPLIED),
        ("No, durante 2025 en Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 Amazon", PatchStatus.AMBIGUOUS),
        ("Y en 2025 Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 Marta", PatchStatus.AMBIGUOUS),
        ("No, el mes pasado Amazon", PatchStatus.AMBIGUOUS),
        ("Y este mes Mercadona", PatchStatus.AMBIGUOUS),
        ("No, en abril Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante abril Marta", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 por Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 completo Amazon", PatchStatus.AMBIGUOUS),
        ("No, en 2025", PatchStatus.APPLIED),
        ("No, el mes pasado", PatchStatus.APPLIED),
        ("Y este mes", PatchStatus.APPLIED),
        ("No, en abril", PatchStatus.APPLIED),
        ("No, durante abril", PatchStatus.APPLIED),
        ("No, durante 2025 completo", PatchStatus.APPLIED),
        ("No, me refería a este año", PatchStatus.APPLIED),
        ("No, durante 2025 en ocio", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 en Hucha", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 ingresos", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 en la cuenta Hucha", PatchStatus.AMBIGUOUS),
        ("Y por meses", PatchStatus.APPLIED),
        ("¿Cuánto gasté en 2025?", PatchStatus.NOT_APPLICABLE),
        ("Y eso, ¿cuánto?", PatchStatus.AMBIGUOUS),
    ],
)
def test_table_driven(sentence: str, status: PatchStatus) -> None:
    result = read(sentence, today=TODAY)
    assert result.status is status


def test_canonical_reader_does_not_need_a_base_plan() -> None:
    result = read("Sin el alquiler", today=TODAY)

    assert result.status is PatchStatus.APPLIED
    assert result.patch is not None
    assert result.changed == frozenset({PatchField.EXCLUDED_CATEGORIES})
    assert result.patch.apply_to(plan()).excluded_categories == ("HOUSING_RENT",)


def test_status_and_patch_must_agree() -> None:
    with pytest.raises(InvalidValueError):
        GroundedContinuation(PatchStatus.APPLIED)
    with pytest.raises(InvalidValueError):
        GroundedContinuation(PatchStatus.AMBIGUOUS, read("Sin alquiler").patch)


def test_patch_preserves_sensitive_scope_and_is_idempotent() -> None:
    previous = plan()
    result = apply("Y por meses", previous, today=TODAY)
    assert result.plan is not None
    assert result.plan.group_by is GroupBy.MONTH
    assert result.plan.only_account_id == previous.only_account_id
    assert result.plan.counterparties == previous.counterparties
    assert result.plan.transaction_ids == previous.transaction_ids
    assert result.plan.internal_only == previous.internal_only
    again = apply("Y por meses", result.plan, today=TODAY)
    assert again.plan == result.plan


def test_exclude_category_keeps_window_and_scope() -> None:
    result = apply("Sin el alquiler", plan(), today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.period == RelativePeriod(Period.LAST_MONTH)
    assert result.plan.group_by is GroupBy.NONE
    assert result.plan.excluded_categories == ("HOUSING_RENT",)
    assert result.plan.categories == ("FOOD",)
    assert result.changed == frozenset({PatchField.EXCLUDED_CATEGORIES})


def test_exclude_merchant_keeps_window_and_scope() -> None:
    result = apply("Excepto Mercadona", plan(), today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.period == RelativePeriod(Period.LAST_MONTH)
    assert result.plan.group_by is GroupBy.NONE
    assert result.plan.excluded_merchants == ("mercadona",)
    assert result.plan.categories == ("FOOD",)


def test_year_correction_is_absolute_and_retains_every_filter() -> None:
    previous = plan()
    result = apply("No, durante todo 2025", previous, today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.date_range == AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31))
    assert result.plan.period is None
    assert result.plan.categories == previous.categories
    assert result.plan.counterparties == previous.counterparties
    assert result.plan.only_account_id == previous.only_account_id
    assert result.plan.transaction_ids == previous.transaction_ids
    assert result.plan.internal_only is previous.internal_only
    assert result.plan.include_internal is previous.include_internal
    assert result.changed == frozenset({PatchField.WINDOW})


def test_metric_continuation_changes_only_metric() -> None:
    previous = plan()
    result = apply("Y cuántos movimientos", previous, today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.metric is Metric.COUNT
    assert result.plan.period == previous.period
    assert result.plan.group_by == previous.group_by
    assert result.plan.categories == previous.categories
    assert result.plan.counterparties == previous.counterparties


# --- the readings that need the base (spec §3.4, Task 7) ----------------------------------------

TO_MARTA = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.THIS_MONTH),
    counterparties=("Marta",),
)


@pytest.mark.parametrize(
    ("sentence", "direction"),
    [
        ("¿y ella a mí?", Direction.INCOME),
        ("¿y él a mí?", Direction.INCOME),
        ("¿y Marta a mí?", Direction.INCOME),
        ("y lo que me mando ella?", Direction.INCOME),
        ("y lo que ella me mandó", Direction.INCOME),
        ("¿y lo que me ha pagado ella?", Direction.INCOME),
        ("¿y lo que me mandó Marta?", Direction.INCOME),
        ("¿y lo que me mandó?", Direction.INCOME),
        ("¿y cuánto me ha mandado ella?", Direction.INCOME),
        ("y lo que recibí de ella", Direction.INCOME),
        ("¿y yo a ella?", Direction.EXPENSE),
        ("y lo que le mandé", Direction.EXPENSE),
        ("y lo que yo le he pagado a ella", Direction.EXPENSE),
        ("y lo que ella recibió de mí", Direction.EXPENSE),
    ],
)
def test_the_question_turned_round_on_the_bases_person(sentence: str, direction: Direction) -> None:
    result = read(sentence, today=TODAY, base=TO_MARTA, answered_last=True)

    assert result.status is PatchStatus.APPLIED
    assert result.patch is not None and result.patch.direction is direction
    assert result.changed == frozenset({PatchField.DIRECTION})


@pytest.mark.parametrize(
    ("sentence", "base"),
    [
        # Somebody else, by name: Javi is never Marta, and «Marta Ruiz» has a word Marta lacks.
        ("¿y lo que me mandó Javi?", TO_MARTA),
        ("¿y Javi a mí?", TO_MARTA),
        ("¿y lo que me mandó Marta Ruiz?", TO_MARTA),
        # Nobody in the base: «ella» has no one to be.
        ("¿y lo que me mandó ella?", replace(TO_MARTA, counterparties=())),
        # Two people in the base: which one «ella» is would be a guess.
        ("¿y ella a mí?", replace(TO_MARTA, counterparties=("Marta", "Javi"))),
    ],
)
def test_a_turn_round_on_anyone_but_the_bases_one_person_is_not_a_patch(
    sentence: str, base: FinancialPlan
) -> None:
    assert read(sentence, today=TODAY, base=base).status is PatchStatus.NOT_APPLICABLE


def test_a_name_in_the_base_is_matched_word_by_word() -> None:
    """«Marta» is «Marta Solis Vega», which is how a contact's full name reaches a plan."""
    base = replace(TO_MARTA, counterparties=("Marta Solis Vega",))

    assert read("¿y lo que me mandó Marta?", today=TODAY, base=base).status is (PatchStatus.APPLIED)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto me mandó Javi este año?",
        "¿y lo que me mandó ella este mes?",
        "¿y ella a mí en julio?",
    ],
)
def test_a_turn_round_that_says_more_is_not_one(sentence: str) -> None:
    """Each shape is the whole turn: a window or a question of its own beside it makes the turn
    something the patch does not read, and it goes where it went before."""
    assert read(sentence, today=TODAY, base=TO_MARTA).status is not PatchStatus.APPLIED


def test_a_direction_the_sentence_states_wins_over_the_shape() -> None:
    """Ruling R24. `explicit_facts` reads «ingresé» as income and the shape reads money sent; a
    reading that disagrees with a stated direction is no reading."""
    assert read("y lo que le ingresé", today=TODAY, base=TO_MARTA, answered_last=True).status is (
        PatchStatus.AMBIGUOUS
    )


@pytest.mark.parametrize(
    ("sentence", "grouping"),
    [
        ("mejor por categoría", GroupBy.CATEGORY),
        ("¿y por categoría?", GroupBy.CATEGORY),
        ("por categorías", GroupBy.CATEGORY),
        ("agrupado por categoría", GroupBy.CATEGORY),
        ("mejor agrúpalo por comercio", GroupBy.COUNTERPARTY),
        ("¿y por comercios?", GroupBy.COUNTERPARTY),
        ("mejor por meses", GroupBy.MONTH),
    ],
)
def test_a_breakdown_said_as_the_whole_turn(sentence: str, grouping: GroupBy) -> None:
    result = read(sentence, today=TODAY, base=TO_MARTA)

    assert result.status is PatchStatus.APPLIED
    assert result.patch == PlanPatch(group_by=grouping)


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        ("¿y ella a mí?", PatchStatus.AMBIGUOUS),
        ("y lo que recibí de ella", PatchStatus.AMBIGUOUS),
        ("mejor por categoría", PatchStatus.AMBIGUOUS),
        ("por categoría", PatchStatus.NOT_APPLICABLE),
        ("agrupado por categoría", PatchStatus.NOT_APPLICABLE),
    ],
)
def test_without_a_base_nothing_new_is_read(sentence: str, status: PatchStatus) -> None:
    """The statuses a caller without a base read before Task 7, unchanged: `readers/follow_up.py`
    gates its window patch on whether this says AMBIGUOUS."""
    assert read(sentence, today=TODAY).status is status


@pytest.mark.parametrize(
    ("sentence", "grouping"),
    [
        ("este año por comercio", GroupBy.COUNTERPARTY),
        ("este año, mejor por categoría", GroupBy.CATEGORY),
        ("este año agrupado por meses", GroupBy.MONTH),
        # The singular is also an average's divisor, which only `explicit_facts` can tell apart.
        ("este año, de media por mes", None),
        ("este año", None),
    ],
)
def test_the_breakdown_a_reply_asks_for(sentence: str, grouping: GroupBy | None) -> None:
    assert grouping_in(sentence) is grouping


def test_a_named_turn_round_over_two_people_narrows_to_the_one_named() -> None:
    """Ruling R33: the patch says whose money it is, so the figure is not both people's."""
    base = replace(TO_MARTA, counterparties=("Marta Solis Vega", "Javi Nuñez"))

    result = read("¿y Marta a mí?", today=TODAY, base=base)

    assert result.patch == PlanPatch(
        direction=Direction.INCOME, counterparties=("Marta Solis Vega",)
    )
    assert result.changed == frozenset({PatchField.DIRECTION, PatchField.COUNTERPARTIES})


def test_a_name_two_of_the_people_share_is_not_a_patch() -> None:
    base = replace(TO_MARTA, counterparties=("Marta Solis Vega", "Marta Ruiz Gomez"))

    assert read("¿y Marta a mí?", today=TODAY, base=base).status is PatchStatus.NOT_APPLICABLE


@pytest.mark.parametrize("sentence", ["¿y ella a mí?", "¿y lo que me mandó?", "¿y yo a ella?"])
def test_a_pronoun_or_an_unsaid_person_needs_the_answer_to_be_the_last_turn(sentence: str) -> None:
    """Rulings R34/R35. «Ella» and a person left unsaid point at the answer just given; with
    another turn after it, the base's person is not who they point at any more."""
    assert read(sentence, today=TODAY, base=TO_MARTA, answered_last=False).status is (
        PatchStatus.NOT_APPLICABLE
    )


def test_a_name_needs_no_such_thing() -> None:
    result = read("¿y Marta a mí?", today=TODAY, base=TO_MARTA, answered_last=False)

    assert result.patch == PlanPatch(direction=Direction.INCOME)
