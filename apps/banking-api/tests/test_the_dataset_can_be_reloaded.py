"""The seed starts with a TRUNCATE, and postgres refuses an incomplete one.

CI went red on this, in the step that loads the dataset:

    ERROR:  cannot truncate a table referenced in a foreign key constraint
    DETAIL:  Table "movement_categories" references "ledger_transactions".

`movement_categories` arrived in migration 0011 and the seed's TRUNCATE was not updated, so the
statement — and with it the whole dataset — stopped loading. Nothing failed at the point the table
was added: the migration is correct, the tests passed, and the break surfaced two changes later in
a step that looks unrelated to categories.

That is the gap this closes. The rule is a property of the schema, not of the migration that
happened to add a table: *every* table with a foreign key into the truncated set has to be named in
the statement. Asking the database means a migration adding one fails here, next to the reason,
rather than in a CI step nobody associates with it.

Never `TRUNCATE ... CASCADE`, which is the other way to make the error go away. It would silently
empty whatever a future migration hangs off the ledger, and the point of naming the tables is that
somebody enumerated them.
"""

import re
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.integration

SEED = Path(__file__).resolve().parents[1] / "seeds" / "0001_demo.sql"

_TRUNCATE = re.compile(r"\bTRUNCATE\s+(.*?);", re.IGNORECASE | re.DOTALL)

# Which tables the seed empties before it inserts anything. Read from the file rather than
# duplicated here: a copy would be one more thing to update, which is the failure being fixed.
_REFERENCES_SQL = """
    SELECT DISTINCT src.relname AS referencing, tgt.relname AS referenced
    FROM pg_constraint c
    JOIN pg_class src ON src.oid = c.conrelid
    JOIN pg_class tgt ON tgt.oid = c.confrelid
    WHERE c.contype = 'f'
      AND tgt.relname = ANY($1::text[])
      AND src.relname <> ALL($1::text[])
    ORDER BY 1
"""


def truncated() -> list[str]:
    match = _TRUNCATE.search(SEED.read_text(encoding="utf-8"))
    assert match is not None, "the dataset no longer starts with a TRUNCATE"
    return [name.strip() for name in match.group(1).split(",")]


def test_the_dataset_still_starts_by_emptying_the_ledger() -> None:
    """A guard on the guard. If the statement is ever replaced by DELETEs or by a CASCADE, the
    check below would pass by having nothing to check."""
    statement = _TRUNCATE.search(SEED.read_text(encoding="utf-8"))

    assert statement is not None
    assert "CASCADE" not in statement.group(1).upper()
    assert "ledger_transactions" in truncated()


async def test_every_table_that_references_the_dataset_is_truncated_with_it(
    connection: asyncpg.Connection,
) -> None:
    """The one that failed. Postgres refuses the statement unless every referencing table is
    named, so an unnamed one does not lose a row — it stops the dataset loading at all."""
    dangling = await connection.fetch(_REFERENCES_SQL, truncated())

    assert dangling == [], (
        "these tables reference the dataset and are not in the seed's TRUNCATE, so loading it "
        "fails: " + ", ".join(f"{row['referencing']} -> {row['referenced']}" for row in dangling)
    )


async def test_the_seed_names_no_table_that_does_not_exist(
    connection: asyncpg.Connection,
) -> None:
    """The other direction. A table renamed or dropped by a migration leaves a name here that
    postgres rejects just as flatly, and with a message about the wrong thing."""
    named = truncated()
    existing = {
        row["relname"]
        for row in await connection.fetch(
            "SELECT relname FROM pg_class WHERE relname = ANY($1::text[]) AND relkind = 'r'",
            named,
        )
    }

    assert set(named) - existing == set()
