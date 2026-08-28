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
from finguard_orchestrator.domain.value_objects.scope import Scope


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
    # What this credential may do. Empty is a legitimate credential that authenticates and
    # is allowed nothing, which is the correct reading of "granted nothing" and the right
    # default: a permission somebody has to grant is a permission somebody decided.
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.holder_reference.strip():
            raise InvalidValueError("an authenticated caller needs a holder reference")
        unknown = self.scopes - {scope.value for scope in Scope}
        if unknown:
            # At construction, so a typo in the token table stops the service rather than
            # granting nothing quietly. A caller who believes they have a permission and
            # does not is a support call; an unvalidated string is worse — it becomes a real
            # permission the day somebody adds a scope with that spelling.
            raise InvalidValueError(f"unknown scopes: {sorted(unknown)}")

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

        It is called by `assert_may_read`. There is exactly one production call site --
        `metrics_executor.py`, on a stored plan's account -- and the count is deliberate:
        the question path had one too, comparing `who.account_id` with itself, and that
        line was deleted rather than left reading like a control. See `llm_controller.ask`.

        Read `assert_may_read` before adding a second call site: this is account-granular
        and the layers beneath it are holder-granular, so it refuses more than they do.
        """
        return account_id == self.account_id

    def assert_may_read(self, account_id: UUID) -> None:
        """Raises unless the account is *this caller's own*, which is narrower than "the
        holder's".

        Cheap, and it closes the failure nobody would notice: a future route that takes
        an account from anywhere else would otherwise read another holder's figures and
        look completely normal doing it.

        The narrowness is a live defect, not a nuance. `account_id` is one account, while
        banking isolates by holder (`0016_holder_scope.py`) and a holder with several
        accounts is now the seeded reality -- Laura has three. Measured against the running
        service: `GET /v1/account/movements?account=<her savings>` answers 200 with 12
        movements, while `assert_may_read(<her savings>)` raises. So this refuses accounts
        the rest of the system correctly serves, and the one caller that reaches it
        (`metrics_executor`) makes a metric saved against a second account unopenable.
        Anything that needs "may this holder read this account?" must ask the account
        directory, which is what every echoed-id path in `ask_financial_question` does.
        """
        if not self.may_read(account_id):
            raise NotAuthorisedError()

    def may(self, scope: str) -> bool:
        """Deny by default, like may_read. Nothing is implied by anything else: a scope
        hierarchy is a place for a surprise, and there are five of them."""
        return scope in self.scopes

    def assert_may(self, scope: str) -> None:
        """Raises unless this credential was granted the scope.

        A distinct error from not being authenticated, because the answers differ: one is
        "log in", the other is "you are logged in and this is not yours to do".
        """
        if not self.may(scope):
            raise NotAuthorisedError(f"this credential is not allowed {scope}")
