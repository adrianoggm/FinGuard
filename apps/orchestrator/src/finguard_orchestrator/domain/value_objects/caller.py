"""Who is asking, and what they are allowed to read.

The first two layers of the security stack (§12.1) are Identity and Authorisation, and
they were skipped: the account travelled in the request body, so any caller could name
any account. This is the seam that closes it — the account now comes from the identity,
and a caller has no way to ask about someone else's money because there is nowhere left
to say whose money they mean.

A value object rather than a session: it is derived from a credential on every request
and carries nothing mutable. Nothing here expires, because nothing here is stored.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finguard_orchestrator.domain.errors import InvalidValueError, NotAuthorisedError


@dataclass(frozen=True)
class Caller:
    """An authenticated account holder.

    `account_id` is the account they may read, singular on purpose. A holder with
    several accounts is a real thing and the day it arrives this becomes a set and the
    question has to name which — but a set of one, silently defaulted, would be the kind
    of shortcut that reads as support for multiple accounts and is not.
    """

    holder_reference: str
    account_id: UUID
    # The zone this holder's day is measured in. It belongs here rather than to the
    # deployment because "hoy" is a property of whoever is asking: between midnight and
    # 01:00 peninsular time, today in the Canaries is yesterday in Madrid, and a figure for
    # the wrong day is a real figure answering a question nobody asked.
    reporting_timezone: str = "Europe/Madrid"

    def __post_init__(self) -> None:
        if not self.holder_reference.strip():
            raise InvalidValueError("an authenticated caller needs a holder reference")
        try:
            ZoneInfo(self.reporting_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            # At construction, so a misconfigured token table fails at startup rather than
            # answering about a zone nobody has.
            raise InvalidValueError(f"unknown time zone: {self.reporting_timezone!r}") from exc

    def today_in(self) -> date:
        """What day it is for this holder.

        The one place the clock is read on the request path. It used to be a module
        constant, which meant every holder shared a day and the zone could silently differ
        from the one banking bins timestamps with.
        """
        return datetime.now(ZoneInfo(self.reporting_timezone)).date()

    def may_read(self, account_id: UUID) -> bool:
        """Deny by default.

        Authorisation here is enforced by construction — the account travels *from* this
        object into the query, so there is no second account to compare against on the
        normal path. That makes this method the belt to that braces, and review rightly
        flagged that an unused authorisation check is worse than none: it reads like a
        control while enforcing nothing.

        It is called by `assert_may_read`, which the handler path uses, so the guarantee
        stops depending on every future call site remembering to copy the right field.
        """
        return account_id == self.account_id

    def assert_may_read(self, account_id: UUID) -> None:
        """Raises unless the account is this caller's.

        Cheap, and it closes the failure nobody would notice: a future route that takes
        an account from anywhere else would otherwise read another holder's figures and
        look completely normal doing it.
        """
        if not self.may_read(account_id):
            raise NotAuthorisedError()
