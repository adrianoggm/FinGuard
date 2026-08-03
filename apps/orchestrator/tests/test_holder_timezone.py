"""Whose "today" is it?

The orchestrator decided what day it was from a module constant and sent banking a bare
date; banking binned timestamps with its own configurable zone. Two settings, one of them
not even configuration, and no way to notice when they disagreed — the two-clocks bug that
moved a figure by 185 €, one level up.

A zone per deployment is wrong even when the two agree. Between midnight and 01:00
peninsular time "hoy" in the Canaries is the previous day, so the holder there is told
about a window they are not in.
"""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.banking.http_financial_query import to_body

ACCOUNT = uuid.uuid4()


def test_a_holder_carries_the_zone_their_day_is_measured_in() -> None:
    laura = Caller(
        holder_reference="ES-0001", account_id=ACCOUNT, reporting_timezone="Europe/Madrid"
    )
    aitor = Caller(
        holder_reference="ES-0002", account_id=ACCOUNT, reporting_timezone="Atlantic/Canary"
    )

    assert (
        laura.today_in() != aitor.today_in() or laura.reporting_timezone != aitor.reporting_timezone
    )


def test_the_zone_travels_with_the_reference_date() -> None:
    """The pair is what describes a window. Sending the date alone asks banking to guess
    the half we did not send, and its guess is a deployment setting that can differ."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))

    body = to_body(
        plan,
        account_id=ACCOUNT,
        today=date(2026, 7, 15),
        supporting_movements=10,
        reporting_timezone="Atlantic/Canary",
    )

    assert body["period"]["reference"] == "2026-07-15"
    assert body["period"]["timezone"] == "Atlantic/Canary"


def test_an_unknown_zone_is_refused_at_construction() -> None:
    with pytest.raises(InvalidValueError):
        Caller(
            holder_reference="ES-0001", account_id=ACCOUNT, reporting_timezone="Mars/Olympus_Mons"
        )
