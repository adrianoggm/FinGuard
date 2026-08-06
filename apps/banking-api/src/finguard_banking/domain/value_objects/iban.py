"""Where a transfer goes.

A Bizum names a person by phone and the bank finds the account. A transfer names the account, and
those twenty-four characters are the whole instruction — get one wrong and the money arrives
somewhere real, belonging to somebody else, and does not come back. That asymmetry is why this is
a value object with an invariant rather than a string field: an IBAN that has not been checked is
not an IBAN, it is a hope.

**The check is mod-97, and it is worth being precise about what it buys.** ISO 13616 reserves two
digits so that the whole number, rearranged, leaves a remainder of 1 when divided by 97. That
catches every single-character error and every transposition of adjacent characters — the two
mistakes a person typing actually makes. It does *not* catch a well-formed IBAN belonging to
somebody else, and nothing here can: the account exists, the checksum is fine, and only the holder
knows it is the wrong one. That is why the preview prints the destination in full and unmasked, and
why the number reaches this service as a field the user typed rather than as text a model read out
of a sentence.

Shape in SQL, arithmetic here. The same split the categories use: the column keeps a paragraph out
of the field, and the rule that needs computing lives where it can be tested.
"""

from dataclasses import dataclass
from typing import Final

from finguard_banking.domain.errors import InvalidValueError

# ISO 13616: two letters for the country, two check digits, then up to thirty of the country's own
# account number. Spain uses twenty-four in total; the ceiling is the standard's, because a contact
# list is exactly where a foreign account turns up first.
MIN_LENGTH: Final = 15
MAX_LENGTH: Final = 34

# A..Z become 10..35 when the number is rearranged for the division.
_LETTER_OFFSET: Final = ord("A") - 10


@dataclass(frozen=True)
class Iban:
    """An account number that has passed its own checksum.

    Stored and compared in the compact form — no spaces, upper case — because "ES91 2100" and
    "es9121 00" are the same account and a directory that treats them as two is a directory that
    cannot find the one it has.
    """

    value: str

    def __post_init__(self) -> None:
        compact = self.value
        if compact != compact.upper() or " " in compact:
            raise InvalidValueError("an IBAN is stored compact and upper case; use Iban.parse")
        if not MIN_LENGTH <= len(compact) <= MAX_LENGTH:
            raise InvalidValueError(
                f"an IBAN is between {MIN_LENGTH} and {MAX_LENGTH} characters, got {len(compact)}"
            )
        if not compact[:2].isalpha() or not compact[2:4].isdigit():
            raise InvalidValueError("an IBAN starts with two letters and two check digits")
        if not compact[4:].isalnum():
            raise InvalidValueError("an IBAN carries only letters and digits")
        if _remainder(compact) != 1:
            # The one error message worth writing carefully. "Invalid IBAN" sends somebody looking
            # for a bank that is down; the truth is that the number they typed is not an account.
            raise InvalidValueError(
                "that account number does not pass its check digits — one character is wrong"
            )

    @classmethod
    def parse(cls, written: str) -> "Iban":
        """From what a person typed. Spaces every four characters is how a bank prints one, so it
        is how a person copies one, so it is how one arrives."""
        return cls(written.replace(" ", "").replace("-", "").strip().upper())

    @property
    def spaced(self) -> str:
        """Grouped in fours, the way it is printed on a statement.

        For the preview and nowhere else. Somebody checking twenty-four characters against a note
        needs them in the shape the note is in, and an unbroken string is the shape that gets
        skimmed.
        """
        return " ".join(self.value[i : i + 4] for i in range(0, len(self.value), 4))

    def __str__(self) -> str:
        return self.value


def _remainder(compact: str) -> int:
    """The mod-97 of the rearranged number. 1 means the check digits agree with the rest."""
    rearranged = compact[4:] + compact[:4]
    digits = "".join(
        str(ord(character) - _LETTER_OFFSET) if character.isalpha() else character
        for character in rearranged
    )
    return int(digits) % 97
