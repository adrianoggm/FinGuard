"""Where a transfer goes, checked before it goes there.

A Bizum names a person and the bank finds the account. A transfer names the account, and those
twenty-four characters are the whole instruction — one wrong and the money arrives somewhere real,
belonging to somebody else, and does not come back.

What mod-97 buys is precise and worth stating in tests rather than in prose: every single-character
error and every adjacent transposition, which are the two mistakes a person typing makes. What it
does not buy is a well-formed account belonging to the wrong person, and nothing here can.
"""

import pytest

from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.iban import Iban

# Real-shaped and checksum-valid. Spain, twenty-four characters.
VALID = "ES9121000418450200051332"


def test_a_valid_account_number_is_accepted() -> None:
    assert Iban(VALID).value == VALID


@pytest.mark.parametrize(
    "written",
    [
        "ES91 2100 0418 4502 0005 1332",
        "es9121000418450200051332",
        "  ES91-2100-0418-4502-0005-1332  ",
    ],
)
def test_it_is_read_the_way_a_person_copies_one(written: str) -> None:
    """Spaces every four is how a bank prints one, so it is how it arrives. A directory that
    treated "ES91 2100" and "es912100" as two accounts could not find the one it has."""
    assert Iban.parse(written) == Iban(VALID)


def test_every_single_character_error_is_caught() -> None:
    """The property mod-97 exists for, asserted as a property rather than on one example."""
    for position in range(4, len(VALID)):
        original = VALID[position]
        for replacement in "0123456789":
            if replacement == original:
                continue
            with pytest.raises(InvalidValueError):
                Iban(VALID[:position] + replacement + VALID[position + 1 :])


def test_transposing_two_adjacent_characters_is_caught() -> None:
    """The other mistake a person actually makes. Every adjacent pair that differ."""
    for position in range(4, len(VALID) - 1):
        if VALID[position] == VALID[position + 1]:
            continue
        swapped = VALID[:position] + VALID[position + 1] + VALID[position] + VALID[position + 2 :]
        with pytest.raises(InvalidValueError):
            Iban(swapped)


@pytest.mark.parametrize(
    "written",
    [
        "",
        "ES91",
        "9121000418450200051332ES",
        "ESXX21000418450200051332",
        "ES91 2100 0418 4502 0005 1332",  # only `parse` cleans it; the type stores it compact
        "ES912100041845020005133200000000000000",
        "ES912100041845020005133$",
    ],
)
def test_something_that_is_not_an_account_number_is_refused(written: str) -> None:
    with pytest.raises(InvalidValueError):
        Iban(written)


def test_the_preview_shape_is_grouped_in_fours() -> None:
    """Somebody checking twenty-four characters against a note needs them in the shape the note is
    in. An unbroken string is the shape that gets skimmed instead of read."""
    assert Iban(VALID).spaced == "ES91 2100 0418 4502 0005 1332"


def test_a_well_formed_account_belonging_to_somebody_else_passes() -> None:
    """The limit of the check, written down so nobody mistakes it for a guarantee. This number is
    valid and is not the one the user meant, and no arithmetic here can know that — which is why
    the preview prints the destination in full and why the digits are typed rather than read out
    of a sentence by a model."""
    assert Iban("ES7921000813610123456789").value.startswith("ES79")


def test_every_account_number_in_the_seed_is_one_a_transfer_could_use() -> None:
    """The dataset must not be the reason a demo transfer is refused.

    The IBANs in the seed were produced by solving mod-97 rather than typed, and this is what says
    so. A generator that stopped doing the arithmetic would otherwise be found by somebody trying
    to send money in front of an audience.
    """
    import re
    from pathlib import Path

    seed = (Path(__file__).resolve().parents[1] / "seeds" / "0001_demo.sql").read_text(
        encoding="utf-8"
    )
    written = re.findall(r"'(ES\d{22})'", seed)

    assert written, "the seed has no account numbers at all"
    for account in written:
        Iban(account)  # raises if the check digits disagree with the rest
