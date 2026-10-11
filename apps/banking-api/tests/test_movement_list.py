from datetime import UTC, date, datetime
from uuid import UUID

from finguard_banking.domain.enums import Direction
from finguard_banking.domain.models.movement_list import MovementCursor, MovementListPlan
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.infrastructure.persistence.query_compiler import compile_movement_list

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
TX = UUID("33333333-3333-3333-3333-333333333333")


def test_latest_movements_need_no_period_and_are_always_holder_scoped() -> None:
    compiled = compile_movement_list(MovementListPlan(ACCOUNT, Direction.EXPENSE))

    assert "assistant_read.transactions_v1" in compiled.sql
    assert "t.account_id = $1" in compiled.sql
    assert "ORDER BY t.occurred_at DESC, t.transaction_id ASC" in compiled.sql
    assert "LIMIT $3" in compiled.sql
    assert compiled.parameters == [ACCOUNT, "EXPENSE", 21]
    assert "date" not in compiled.sql.lower()


def test_across_holder_list_omits_only_the_account_filter() -> None:
    compiled = compile_movement_list(
        MovementListPlan(ACCOUNT, Direction.EXPENSE, across_accounts=True)
    )

    assert "t.account_id =" not in compiled.sql
    assert "assistant_read.transactions_v1" in compiled.sql
    assert ACCOUNT not in compiled.parameters


def test_a_statement_of_every_account_with_no_filter_is_still_valid_sql() -> None:
    """Every movement, every account, transfers included: no condition is left, and an empty
    WHERE was a syntax error the moment the orchestrator asked for one."""
    compiled = compile_movement_list(
        MovementListPlan(ACCOUNT, Direction.ALL, across_accounts=True, include_internal=True)
    )

    assert "WHERE TRUE" in " ".join(compiled.sql.split())
    assert "is_internal" not in compiled.sql.split("WHERE")[1]


def test_keyset_page_cursor_has_stable_tiebreak_and_all_values_are_parameters() -> None:
    timestamp = datetime(2026, 10, 10, 8, tzinfo=UTC)
    plan = MovementListPlan(
        ACCOUNT,
        Direction.EXPENSE,
        page_size=10,
        cursor=MovementCursor(timestamp, TX),
        account_ids=(OTHER,),
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 10),
        categories=(Category("GROCERIES"),),
        merchant_terms=("Market",),
    )

    compiled = compile_movement_list(plan)

    assert "t.occurred_at < $" in compiled.sql
    assert "t.transaction_id > $" in compiled.sql
    assert "t.account_id = ANY($" in compiled.sql
    assert "GROCERIES" not in compiled.sql
    assert "Market" not in compiled.sql
    assert [OTHER] in compiled.parameters
    assert TX in compiled.parameters
    assert timestamp in compiled.parameters
    assert compiled.parameters[-1] == 11
