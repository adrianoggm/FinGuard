from uuid import uuid4

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import (
    AccountCandidate,
    AccountScopeClarification,
)
from finguard_orchestrator.domain.value_objects.account_selection import (
    AccountSelection,
    AccountSelectionMode,
)
from finguard_orchestrator.domain.value_objects.financial_plan import RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


def test_account_selection_modes_are_exclusive_and_bounded() -> None:
    account = uuid4()
    assert AccountSelection(AccountSelectionMode.ALL).account_ids == ()
    assert AccountSelection(AccountSelectionMode.SELECTED, (account,)).account_ids == (account,)
    with pytest.raises(InvalidValueError):
        AccountSelection(AccountSelectionMode.ALL, (account,))
    with pytest.raises(InvalidValueError):
        AccountSelection(AccountSelectionMode.SELECTED)
    with pytest.raises(InvalidValueError):
        AccountSelection(AccountSelectionMode.SELECTED, (account, account))


def test_account_scope_clarification_carries_ranked_count_and_grounded_candidates() -> None:
    account = uuid4()
    plan = TopMovements(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR))
    clarification = AccountScopeClarification(
        pending=plan,
        candidates=(AccountCandidate(account, "Hucha", "SAVINGS"),),
        requested_count=10,
    )
    assert clarification.pending == plan
    assert clarification.candidates[0].account_id == account
    assert clarification.requested_count == 10
