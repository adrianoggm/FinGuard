"""A date without a zone does not say what "hoy" means.

Two services resolve the same window: the orchestrator decides what day it is in order to
send `reference`, and this one bins timestamps with `AT TIME ZONE`. Until now each used its
own setting — one a module constant, the other configuration — so they could disagree and
nothing would notice. It is the two-clocks bug that moved a figure by 185 €, one level up.

And a zone per deployment is wrong even when both agree. Between midnight and 01:00
peninsular time, "hoy" in the Canaries is the previous day, so a holder there would be
told about a window they are not in.
"""

from datetime import date

import pytest

from finguard_banking.infrastructure.dto.banking_dto import FinancialQueryRequest
from finguard_banking.infrastructure.mappers.http_mapper import plan_from_dto
from finguard_banking.infrastructure.persistence.query_compiler import compile_aggregate

ACCOUNT = "08aba04f-2b30-4639-88a6-eac741153ce0"


def request(**period: object) -> FinancialQueryRequest:
    return FinancialQueryRequest.model_validate(
        {
            "accountId": ACCOUNT,
            "direction": "EXPENSE",
            "period": {"period": "TODAY", **period},
        }
    )


def test_a_request_may_say_which_zone_its_day_is_measured_in() -> None:
    body = request(reference="2026-07-15", timezone="Atlantic/Canary")

    assert body.period is not None
    assert body.period.timezone == "Atlantic/Canary"


def test_the_zone_reaches_the_sql_that_bins_the_timestamps() -> None:
    """The zone is not decoration. It decides which day a purchase at 23:30 belongs to, so
    binning in one zone while the window was chosen in another answers a question nobody
    asked — with a real figure."""
    body = request(reference="2026-07-15", timezone="Atlantic/Canary")
    plan = plan_from_dto(body, date(2026, 7, 15))

    compiled = compile_aggregate(plan, plan.reporting_timezone or "Europe/Madrid")

    assert "Atlantic/Canary" in compiled.parameters


def test_a_request_without_a_zone_keeps_the_deployment_default() -> None:
    # The contract stays backward compatible: an existing caller that sends only a
    # reference date is not broken by the field arriving.
    body = request(reference="2026-07-15")
    plan = plan_from_dto(body, date(2026, 7, 15))

    assert plan.reporting_timezone is None


def test_an_unknown_zone_is_refused_rather_than_ignored() -> None:
    """Silently falling back would be the dangerous outcome: the caller believes it asked
    about Canarian days and is answered about peninsular ones."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        request(reference="2026-07-15", timezone="Mars/Olympus_Mons")
