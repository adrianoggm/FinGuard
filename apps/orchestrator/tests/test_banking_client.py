"""The hand-written contract, both directions.

`to_body` and `from_body` are pure, so most of this runs with nothing else alive. That
matters more than usual here: this client exists because we chose not to generate one,
and the whole cost of that choice is that these two shapes are stated twice. Tests are
what turns "stated twice" into "checked twice".
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.banking.http_financial_query import (
    HttpFinancialQuery,
    from_body,
    movement_from_body,
    ranked_body,
    to_body,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")


def body_for(plan: FinancialPlan) -> dict:
    return to_body(
        plan,
        account_id=ACCOUNT,
        today=date(2026, 7, 15),
        supporting_movements=10,
        reporting_timezone="Europe/Madrid",
    )


# --- plan to request ---------------------------------------------------------------


def test_a_named_period_travels_as_a_period() -> None:
    body = body_for(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH))
    )

    # The zone travels with the date, because neither describes a window alone.
    assert body["period"] == {
        "period": "LAST_MONTH",
        "reference": "2026-07-15",
        "timezone": "Europe/Madrid",
    }
    # No stray key: banking forbids extras, so an n that does not apply is a 422.
    assert "dateRange" not in body


def test_a_counted_period_carries_its_count() -> None:
    body = body_for(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.LAST_N_DAYS, n=30),
        )
    )

    assert body["period"] == {
        "period": "LAST_N_DAYS",
        "reference": "2026-07-15",
        "timezone": "Europe/Madrid",
        "n": 30,
    }


def test_an_absolute_window_uses_the_contract_spelling() -> None:
    # from/to, not start/end. The one thing a hand-written client gets wrong quietly.
    body = body_for(
        FinancialPlan(
            direction=Direction.EXPENSE,
            date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        )
    )

    assert body["dateRange"] == {"from": "2026-01-01", "to": "2026-01-31"}
    assert "period" not in body


def test_the_body_is_camel_case() -> None:
    body = body_for(
        FinancialPlan(
            direction=Direction.INCOME,
            period=RelativePeriod(period=Period.THIS_MONTH),
            categories=("GROCERIES",),
            group_by=GroupBy.CATEGORY,
            metric=Metric.COUNT,
        )
    )

    assert set(body) >= {"accountId", "groupBy", "supportingLimit"}
    assert body["direction"] == "INCOME"
    assert body["groupBy"] == "CATEGORY"
    assert body["metric"] == "COUNT"
    assert body["categories"] == ["GROCERIES"]


# --- response to model -------------------------------------------------------------


def test_a_response_becomes_the_assistants_own_model() -> None:
    answer = from_body(
        {
            "accountId": str(ACCOUNT),
            "currency": "EUR",
            "totalMinor": 142580,
            "movementCount": 16,
            "groups": [{"key": "GROCERIES", "totalMinor": 20465, "movementCount": 7}],
            "supportingMovements": [
                {
                    "transactionId": str(uuid.uuid4()),
                    "occurredAt": "2026-07-03T10:12:00Z",
                    "description": "Compra Mercadona",
                    "merchantName": "Mercadona",
                    "category": "GROCERIES",
                    "amountMinor": -4210,
                    "direction": "EXPENSE",
                }
            ],
        }
    )

    assert answer.currency == "EUR"
    assert answer.total_minor == 142580
    assert answer.groups[0].key == "GROCERIES"
    assert answer.movements[0].merchant_name == "Mercadona"
    # Z parsed as UTC rather than dropped on the floor.
    assert answer.movements[0].occurred_at == datetime(2026, 7, 3, 10, 12, tzinfo=UTC)
    assert not answer.is_empty


def test_nothing_matched_is_not_the_same_as_zero_spent() -> None:
    # They read identically to a user and mean different things: the second is usually
    # a plan that was wrong, which is worth saying out loud instead of reporting 0.
    answer = from_body({"currency": "EUR", "totalMinor": 0, "movementCount": 0})

    assert answer.is_empty
    assert answer.groups == ()
    assert answer.movements == ()


def test_amounts_stay_in_minor_units() -> None:
    # No float anywhere on the path. That is how cents go missing.
    answer = from_body({"currency": "EUR", "totalMinor": 142580, "movementCount": 1})

    assert isinstance(answer.total_minor, int)


def test_an_http_answer_must_identify_the_requested_account() -> None:
    body = {"currency": "EUR", "totalMinor": 142580, "movementCount": 1}

    with pytest.raises(BankingUnavailableError, match="does not identify"):
        from_body(body, account_id=ACCOUNT)


def test_an_http_answer_for_another_account_is_rejected() -> None:
    body = {
        "accountId": str(uuid.uuid4()),
        "currency": "EUR",
        "totalMinor": 142580,
        "movementCount": 1,
    }

    with pytest.raises(BankingUnavailableError, match="answer is about"):
        from_body(body, account_id=ACCOUNT)


# --- the service not being there ---------------------------------------------------


async def test_an_unreachable_banking_is_a_domain_error() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(period=Period.THIS_MONTH)
    )

    with pytest.raises(BankingUnavailableError) as caught:
        await HttpFinancialQuery(
            base_url="http://127.0.0.1:1", timeout_seconds=2.0, supporting_movements=10
        ).run(
            plan,
            account_id=ACCOUNT,
            today=date(2026, 7, 15),
            reporting_timezone="Europe/Madrid",
        )

    assert caught.value.code == "banking_unavailable"


# --- the ranking ---------------------------------------------------------------------


def test_the_ranking_body_carries_the_filters_and_the_rank() -> None:
    """It reuses the aggregate's body builder for the window and the filters — including the
    reference date, which is what makes both services agree on which days a period covers — and
    drops the three fields the ranking endpoint does not take."""
    body = ranked_body(
        TopMovements(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.THIS_YEAR),
            categories=("TRANSPORT_FUEL",),
            rank=2,
        ),
        account_id=ACCOUNT,
        today=date(2026, 8, 4),
        reporting_timezone="Europe/Madrid",
    )

    assert body["rank"] == 2
    assert body["categories"] == ["TRANSPORT_FUEL"]
    assert body["direction"] == "EXPENSE"
    assert body["period"]["reference"] == "2026-08-04"
    assert body["period"]["timezone"] == "Europe/Madrid"
    for absent in ("groupBy", "metric", "supportingLimit"):
        assert absent not in body, f"{absent} means nothing to a ranking"


def test_the_movement_of_a_ranking_is_read_whole() -> None:
    """Every field, because a maximum is answered by naming it: the amount alone would send the user
    to the movement list to work out which charge it was.

    One reader shared with the supporting movements of an aggregate. Two copies would drift the
    first time a field is added to one of them, and this is the one that would go unnoticed.
    """
    read = movement_from_body(
        {
            "transactionId": "11111111-1111-1111-1111-111111111111",
            "occurredAt": "2026-03-14T10:00:00+00:00",
            "description": "Repostaje",
            "merchantName": "Cepsa",
            "category": "TRANSPORT_FUEL",
            "categoryIsCustom": False,
            "amountMinor": 8240,
            "direction": "EXPENSE",
        }
    )

    assert read.amount_minor == 8240
    assert read.merchant_name == "Cepsa"
    assert read.category == "TRANSPORT_FUEL"
    assert read.direction == "EXPENSE"
    assert read.occurred_at.date() == date(2026, 3, 14)
