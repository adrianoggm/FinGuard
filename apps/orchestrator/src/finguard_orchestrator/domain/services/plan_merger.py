"""A follow-up inherits what it does not mention.

The prompt used to ask the model for three things at once: whether the sentence continues the
previous one, which fields it changes, and the full value of every field it does not. The
third is the one that failed. Measured on the held-out set, "¿Y la semana pasada?" asked after
a question about the supermarket came back with `categories=()` — the filter it was meant to
keep was the field it dropped. Nothing was wrong with its reading of the sentence; it was
asked to copy six fields and copied five.

So it stops copying. It says which fields the sentence changes, and the merge is arithmetic on
a plan the domain has already validated. A field nobody named comes from the previous plan,
and there is no way for that to be lost, because no model is involved in it.

Deliberately explicit rather than inferred from emptiness. `categories=()` cannot mean both
"unchanged" and "the filter is gone", and "¿y en general?" is a real thing to say — so the
list of changed fields is data the model emits rather than something read out of the values.
"""

from dataclasses import replace
from enum import StrEnum

from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)


class Field(StrEnum):
    """What a follow-up can change.

    `WINDOW` covers both the named period and the explicit range, because they spell one
    thing: a plan carries exactly one of them, so inheriting a period while accepting a range
    would build a plan the domain refuses.
    """

    WINDOW = "window"
    DIRECTION = "direction"
    CATEGORIES = "categories"
    MERCHANTS = "merchants"
    GROUP_BY = "groupBy"
    METRIC = "metric"


def merge(
    previous: FinancialPlan, emitted: FinancialPlan, *, changed: frozenset[Field]
) -> FinancialPlan:
    """The previous plan with the named fields taken from the new one.

    An empty `changed` repeats the previous question, which is not an error: "¿y eso?" is a
    real thing to say, and answering it again is better than inventing a window for it.

    Returns a `FinancialPlan`, so every invariant runs on the way out. A merge is the last
    place worth skipping them: it is the one plan in the pipeline nobody wrote in full.
    """
    # Taken as a pair, never field by field: a plan carries exactly one of the two, so mixing
    # an inherited period with a new range builds something the domain refuses.
    source = emitted if Field.WINDOW in changed else previous

    return replace(
        previous,
        period=source.period,
        date_range=source.date_range,
        direction=emitted.direction if Field.DIRECTION in changed else previous.direction,
        categories=emitted.categories if Field.CATEGORIES in changed else previous.categories,
        merchants=emitted.merchants if Field.MERCHANTS in changed else previous.merchants,
        group_by=emitted.group_by if Field.GROUP_BY in changed else previous.group_by,
        metric=emitted.metric if Field.METRIC in changed else previous.metric,
    )


def merge_draft(
    previous: FinancialPlan,
    emitted: FinancialPlanDraft,
    *,
    changed: frozenset[Field],
) -> FinancialPlanDraft:
    """Apply the same patch to a clarification that has not become a query yet.

    A model may ask another question while correcting an existing plan. The new draft can be
    incomplete, but every field it did not ask to replace must still come from the validated plan.
    """
    source = emitted if Field.WINDOW in changed else previous
    return replace(
        emitted,
        period=source.period,
        date_range=source.date_range,
        direction=emitted.direction if Field.DIRECTION in changed else previous.direction,
        categories=emitted.categories if Field.CATEGORIES in changed else previous.categories,
        merchants=emitted.merchants if Field.MERCHANTS in changed else previous.merchants,
        group_by=emitted.group_by if Field.GROUP_BY in changed else previous.group_by,
        metric=emitted.metric if Field.METRIC in changed else previous.metric,
    )
