"""Direct value assertions on the money formatter.

Every other assertion on `money()` in this suite reaches it through a whole sentence, which is
why a mutation audit could delete its sign branch and leave 2932 tests green. These are the
properties its docstring already claims, asserted on the function itself.
"""

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, ResultKind
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services.answer_writer import money, write
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


@pytest.mark.parametrize(
    ("amount_minor", "currency", "expected"),
    [
        # The sign, which is a live path and not a hypothetical. `ratio_writer._net_cash_flow`
        # renders `money(income - expenses, ...)` unmodified, so any month where spending beat
        # income prints "Tu flujo neto ... fue de -500,00 €". Dropping the minus turns an
        # overspend into a surplus in a sentence a user reads.
        (-189_597, "EUR", "-1.895,97 €"),
        (-1, "EUR", "-0,01 €"),
        # Zero takes no sign: "-0,00 €" is not a number anybody writes.
        (0, "EUR", "0,00 €"),
        # Cents are padded to two digits, or 82,04 € reads as 82,40 €.
        (8_204, "EUR", "82,04 €"),
        (8_240, "EUR", "82,40 €"),
        # Sub-euro amounts still carry the whole part.
        (7, "EUR", "0,07 €"),
        (70, "EUR", "0,70 €"),
        # Spanish grouping: a dot every three digits, a comma before the cents.
        (100_000, "EUR", "1.000,00 €"),
        (123_456_789, "EUR", "1.234.567,89 €"),
        # A space before the symbol, and the ISO code for anything that is not the euro.
        (8_240, "USD", "82,40 USD"),
        # An unknown currency prints no unit at all: a bare "82,40" is a number somebody asks
        # about, while "82,40 €" on a dollar account is a number they believe.
        (8_240, None, "82,40"),
        (-8_240, None, "-82,40"),
    ],
)
def test_money_writes_the_amount_it_was_given(
    amount_minor: int, currency: str | None, expected: str
) -> None:
    assert money(amount_minor, currency) == expected


@pytest.mark.parametrize("amount_minor", [-1, -7, -8_240, -189_597, -123_456_789])
def test_a_negative_amount_keeps_its_minus(amount_minor: int) -> None:
    """The sign is the whole meaning of a net figure, so it is pinned on its own as well.

    A refund read as a charge and an overdraft read as savings are the same one-character bug.
    """
    assert money(amount_minor, "EUR").startswith("-")
    assert money(amount_minor, "EUR") == f"-{money(-amount_minor, 'EUR')}"


def test_the_month_of_largest_spending_is_named_as_a_month_with_its_article() -> None:
    """It read «La mes con mayor importe total … · 2026-04»: mes takes «El», and banking's
    YYYY-MM key is a month a person says."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        group_by=GroupBy.MONTH,
        group_limit=1,
        result_kind=ResultKind.RANKING,
    )
    answer = QueryAnswer(
        "EUR",
        329_413,
        52,
        groups=(Group("2026-01", 100_000, 20), Group("2026-04", 229_413, 32)),
    )

    text = write(answer, plan)

    assert text.startswith("El mes con mayor importe total")
    assert "abril de 2026" in text
    assert "2026-04" not in text and "enero" not in text
