"""The dataset exercises the layers it created tables for.

A feature with no seeded data is a feature nobody can look at. `movement_classifications` and
`movement_tags` shipped as two empty tables, so `category_source` was `BANK` for every movement in
the demo and the four layers were invisible in the one place they were meant to be shown.

These read the generated SQL rather than the database, because what is being checked is the
*dataset* — something that has to be true of the file in git, not of whatever happens to be loaded.
"""

import re
from pathlib import Path

import pytest

SEED = (Path(__file__).resolve().parents[1] / "seeds" / "0001_demo.sql").read_text(encoding="utf-8")


def rows_of(table: str) -> list[str]:
    """The VALUES lines of one INSERT block."""
    found = re.search(rf"INSERT INTO {table} \([^)]*\) VALUES\n(.*?);", SEED, re.DOTALL)
    return found.group(1).strip().splitlines() if found else []


@pytest.mark.parametrize(
    "table", ["movement_classifications", "movement_categories", "movement_tags"]
)
def test_every_layer_has_something_in_it(table: str) -> None:
    assert rows_of(table), f"{table} is a table with no rows and therefore an invisible layer"


def test_the_classifier_disagrees_with_the_bank_somewhere() -> None:
    """A layer that agrees with the one below it everywhere demonstrates nothing. The reclassified
    merchant is filed under leisure by the bank and under subscriptions by this service, so the
    same question answers differently depending on which layer is read."""
    assert "'SUBSCRIPTIONS'" in "\n".join(rows_of("movement_classifications"))


def test_a_movement_carries_more_than_one_tag() -> None:
    """The property that makes a tag not a category. Two labels on one movement is ordinary, and it
    is what would double that movement in any breakdown that grouped by them like a category."""
    tagged: dict[str, int] = {}
    for row in rows_of("movement_tags"):
        transaction = row.split(",")[1].strip()
        tagged[transaction] = tagged.get(transaction, 0) + 1

    assert max(tagged.values()) > 1, "no movement has two tags, so the case cannot be demonstrated"


def test_most_movements_still_come_straight_from_the_bank() -> None:
    """The layers are meant to be visible, not universal. A dataset where every movement carries
    three opinions is a demo of a bug, and it would also leave the plain path untested."""
    classified = len(rows_of("movement_classifications")) + len(rows_of("movement_categories"))
    transactions = SEED.count("seed-0")

    assert classified < transactions // 10, f"{classified} of {transactions} is not a minority"
