"""B-1 (Wave 3 hardening): «quita X» excludes the category, in every form a holder says it.

The exclusion vocabulary (`category_words._EXCLUSION_PREFIX`) used to know the gerund
("quitando restaurantes") and the negative-clause forms ("sin restaurantes", "excepto
restaurantes") but not the imperative a holder actually types -- "quita", "elimina", "borra",
"saca", "fuera", "excluye". Probed directly against the production service (the design audit's
own words): "'quita restaurantes' incl=('RESTAURANTS',) excl=()" -- the sentence named the
category and nothing marked it negative, so the deterministic follow-up reader below
(`facts.excluded_categories or facts.excluded_merchants`, `readers/follow_up.py`) never claimed
it and a model downstream was free to build `categories=['RESTAURANTS']`: the exact inversion,
answered as confidently as the inclusion it was never asked for.

Once the vocabulary recognises the imperative, this reader claims the turn deterministically --
zero model calls -- over a category chart exactly the way "sin restaurantes" already does.
"""

from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.application.queries.readers.follow_up import read_contextual_follow_up
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = UUID(int=1)
TODAY = date(2026, 8, 26)

# A category chart: a breakdown across restaurants and groceries, the shape a holder restyles or
# narrows next.
CATEGORY_CHART = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.THIS_YEAR),
    categories=("RESTAURANTS", "GROCERIES"),
    group_by=GroupBy.CATEGORY,
)


def query(sentence: str) -> AskFinancialQuestionQuery:
    return AskFinancialQuestionQuery(
        sentence, ACCOUNT, TODAY, conversation=Conversation(last_plan=CATEGORY_CHART)
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Quita restaurantes",
        "Quita los restaurantes del gráfico",
        "Elimina restaurantes",
        "Borra restaurantes",
        "Saca restaurantes",
        "Fuera restaurantes",
        "Excluye restaurantes",
    ],
)
def test_an_imperative_exclusion_removes_the_category_deterministically(sentence: str) -> None:
    patched = read_contextual_follow_up(query(sentence))

    assert patched is not None
    assert "RESTAURANTS" not in patched.categories
    assert "RESTAURANTS" in patched.excluded_categories
    # The category the sentence never mentioned survives -- an exclusion narrows, it does not
    # reset the chart back to nothing.
    assert "GROCERIES" in patched.categories
