"""The bank as the conversation tests see it: fakes at the ports, and the YAML corpus loader.

`Ledger`, `Accounts`, `Directory`, `Contacts` and `Bank` stand in for banking at the port, so
everything above them -- the graph, the payment commands, the HTTP routes -- is the real thing.
`a_stack` wires the question routes over them for a test that installs its own graph in
`app.state.engine`. `load` reads `evaluation/conversations/` for the vocabulary tests that walk it.
"""

from __future__ import annotations

import re
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareChosenPaymentCommand,
    PrepareChosenPaymentHandler,
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
)
from finguard_orchestrator.application.queries.list_contacts import (
    ContactRow,
    ListContactsQuery,
    ListContactsResult,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_command_bus,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Period,
    Rail,
)
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    RecipientHasNoAccountError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import AccountScopeDefault
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("08aba04f-2b30-4639-88a6-eac741153ce0")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
FUND = uuid.UUID("33333333-3333-3333-3333-333333333333")
INTENT = uuid.UUID("44444444-4444-4444-4444-444444444444")
MARTA = uuid.UUID("55555555-5555-5555-5555-555555555555")
OTHER_MARTA = uuid.UUID("66666666-6666-6666-6666-666666666666")
TODAY = date(2026, 8, 5)

# The demo dataset's own figures, so an episode that asserts one is asserting what is on screen.
HOLDINGS = (
    Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 215_648),
    Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 641_550),
    Holding(FUND, "ASSET", "Fondo indexado", "EUR", 315_000),
)

# Two Martas on purpose. Resolving a name has to be a question rather than a choice, and a directory
# where every name is unique would let that go untested until somebody's money went to the wrong
# person.
CONTACTS = (
    Contact(MARTA, "Marta Solis Vega", "+34600333444"),
    Contact(OTHER_MARTA, "Marta Ruiz Gomez", "+34600111222"),
    Contact(uuid.UUID(int=7), "Javi Nuñez", "+34600555666"),
    Contact(uuid.UUID(int=8), "Aitor Beltran Diaz", "+34600777888"),
)


# --- the fifteen places a turn can be handled ----------------------------------------------
#
# Named here rather than derived at the assertion, because "which tool answered" is the measurement
# and a measurement whose definition lives in three branches is three definitions.

BALANCE = "BALANCE"
CONTACTS_LIST = "CONTACTS"
IDENTITY = "IDENTITY"
CATEGORIES = "CATEGORIES"
EXPLANATION = "EXPLANATION"
GUIDANCE = "GUIDANCE"
COURTESY = "COURTESY"
CARDS = "CARDS"
TRANSFER = "TRANSFER"
PAYMENT = "PAYMENT"
QUERY = "QUERY"
CLARIFY = "CLARIFY"
REFUSED = "REFUSED"
VISUAL = "VISUAL"
AGENTS = "AGENTS"


# --- the stubs -----------------------------------------------------------------------------


# The shops this account has, and the only part of the ledger that reads the plan it is given.
#
# A stub that answers every plan with the same figure cannot see a filter that names something
# which is not a shop, and that is the defect the corpus found and then could not confirm:
# `_a_bare_shop_name` narrows the last question to any bare word, and the retraction that checks
# the guess only fires on an empty answer. Against a ledger where every filter matches, it never
# fires — so "ah vale" stayed a shop here while being fixed in the deployment.
#
# Three names rather than a dataset. What makes a merchant filter wrong is naming a shop nobody
# ever paid, and that needs a list to *not* be in, not a ledger to add up.
SHOPS = ("Mercadona", "Cepsa", "Spotify")

_MONTH_KEY = re.compile(r"(\d{4})-(\d{2})")


def _resolved_window(
    plan: FinancialPlan, groups: tuple[Group, ...]
) -> tuple[date | None, date | None]:
    """`period_start`/`period_end` the way banking answers them (Wave 4.3's fix wave):
    `http_financial_query.py` maps the ledger's own `periodStart`/`periodEnd`, and this fake used
    to answer neither, so a chart's `window` (`VisualDataset.window`, built from these two) was
    always `None` in the corpus regardless of what an episode's `groups:` described.

    An absolute plan is checked FIRST (fix round 2, alongside N-2): a plan carrying its own
    `date_range` names the exact window it asked for, and the real banking adapter runs that
    range verbatim (`http_financial_query.py:279-283`) -- so a faithful ledger's answer is the
    plan's own range, never the wider span of whichever calendar months its rows happen to fall
    in. Deriving from the injected `YYYY-MM` group keys instead (as this fake used to, for every
    plan alike) answered whole months for a plan that asked for a slice of one, which is exactly
    what let the corpus stay blind to C-1 -- `_answered_window`
    (`application/queries/ask_financial_question.py`) now guards against a lenient ledger doing
    this too, but the fake should not manufacture the lenient case in the first place.

    The month-key derivation applies only when the plan names a `RelativePeriod` (`LAST_MONTH`,
    `THIS_YEAR`, ...) and has no `date_range` of its own to answer with: the window is the first
    day of the earliest `YYYY-MM` key the groups carry to the last day of the latest -- clamped to
    `TODAY`, because the fake cannot answer with rows from a day that has not happened either.
    `None`/`None` when neither applies: the fake has nothing to resolve a window from, and that is
    the honest answer, not a guess.
    """
    if plan.date_range is not None:
        return plan.date_range.start, plan.date_range.end
    if plan.group_by is GroupBy.MONTH:
        months = [
            (int(match.group(1)), int(match.group(2)))
            for group in groups
            if (match := _MONTH_KEY.fullmatch(group.key)) is not None
        ]
        if months:
            months.sort()
            start = date(*months[0], 1)
            end_year, end_month = months[-1]
            end = date(end_year, end_month, monthrange(end_year, end_month)[1])
            return start, min(end, TODAY)
    return None, None


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []
        # Both kinds, in the order they ran, because "which query did this turn actually execute"
        # has to be one answer. An aggregate and a ranking are both queries a follow-up inherits
        # from, and keeping them in separate lists means the corpus cannot ask about the turn.
        self.ran: list[FinancialPlan | TopMovements] = []
        # Set from `turn.groups` immediately before the harness posts that turn and cleared right
        # after, so an episode naming a breakdown gets that breakdown back instead of every turn
        # sharing the one hardcoded group below.
        self.groups_for_next_turn: tuple[Group, ...] | None = None
        # Set from `turn.movements` at the same three sites as `groups_for_next_turn`, and cleared
        # right after. How many synthetic movements `run()` hands back -- but only on a call that
        # actually asked for them; see `_synthetic_movements`.
        self.movements_for_next_turn: int | None = None

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
        known = {shop.casefold() for shop in SHOPS}
        if plan.merchants and not any(shop.casefold() in known for shop in plan.merchants):
            return QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
        if self.groups_for_next_turn is not None:
            groups = self.groups_for_next_turn
            period_start, period_end = _resolved_window(plan, groups)
            return QueryAnswer(
                currency="EUR",
                total_minor=sum(group.total_minor for group in groups),
                movement_count=sum(group.movement_count for group in groups),
                period_start=period_start,
                period_end=period_end,
                groups=groups,
                movements=self._synthetic_movements(plan, groups, movements),
            )
        group_key = {
            GroupBy.CATEGORY: plan.categories[0] if plan.categories else "GROCERIES",
            GroupBy.MERCHANT: "Mercadona",
            GroupBy.COUNTERPARTY: "Mercadona",
            GroupBy.ACCOUNT: "Cuenta corriente",
        }.get(plan.group_by, "total")
        is_recent_list = (
            plan.direction is Direction.ALL
            and plan.period == RelativePeriod(Period.LAST_N_MONTHS, n=24)
            and plan.include_internal
        )
        groups = (Group(group_key, 46_556, 3),)
        return QueryAnswer(
            currency="EUR",
            total_minor=46_556,
            movement_count=3,
            groups=groups,
            movements=(
                tuple(
                    Movement(
                        occurred_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
                        description=f"Compra {day}",
                        amount_minor=10_000 + day,
                        direction=plan.direction.value,
                        merchant_name="Mercadona",
                        category="GROCERIES",
                        transaction_id=str(uuid.UUID(int=day)),
                    )
                    for day in range(1, 4)
                )
                if is_recent_list
                else self._synthetic_movements(plan, groups, movements)
            ),
        )

    def _synthetic_movements(
        self, plan: FinancialPlan, groups: tuple[Group, ...], movements: int | None
    ) -> tuple[Movement, ...]:
        """The evidence behind a drilled-down or referenced answer -- built like the recent-list
        ones above, deterministically, and handed back ONLY when the ledger was actually asked for
        them: the call carried `movements=<int>` (the drill's own `MAX_SELECTED_TRANSACTIONS`) or
        the plan carries `transaction_ids` (an id-selected plan -- «esos movimientos» -- is itself a
        request for those rows, the real ledger's own behaviour for one). A turn that never asks
        gets none, whatever `movements_for_next_turn` says: that is what makes `movements: 3` on a
        turn a check that the evidence was requested, not just a fixture handed over for free.

        M4 (fix wave): this is the right rule for what `Turn.movements` measures, and it reads as
        the opposite of production's. The real adapter (`http_financial_query.py`) always sends a
        positive `supportingLimit` -- the deployment default when the caller passes none, never
        "not asked" -- so a holder sees movements on turns where THIS fake shows none. Read
        `movements: 0` (or an unasserted `movements:`) on a turn here as "the code did not request
        evidence", never as "production would have shown nothing".
        """
        if self.movements_for_next_turn is None:
            return ()
        if movements is None and not plan.transaction_ids:
            return ()
        category = plan.categories[0] if plan.categories else "GROCERIES"
        merchant = groups[0].key if groups else "Mercadona"
        return tuple(
            Movement(
                occurred_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
                description=f"Compra {day}",
                amount_minor=10_000 + day,
                direction=plan.direction.value,
                merchant_name=merchant,
                category=category,
                transaction_id=str(uuid.UUID(int=day)),
            )
            for day in range(1, self.movements_for_next_turn + 1)
        )

    async def ranked(self, ranking: object, **_: object) -> Movement:
        if isinstance(ranking, TopMovements):
            self.ran.append(ranking)
        return Movement(
            occurred_at=datetime(2026, 3, 14, 10, 0, tzinfo=UTC),
            description="Repostaje",
            amount_minor=8_240,
            direction="EXPENSE",
            merchant_name="Cepsa",
            category="TRANSPORT_FUEL",
            currency="EUR",
        )


def _groups_from(turn: Turn) -> tuple[Group, ...] | None:
    """`turn.groups`, translated into what `Ledger.run` answers with -- or `None`, its default,
    when the turn does not override the fake's one hardcoded group."""
    if turn.groups is None:
        return None
    return tuple(Group(str(key), int(minor), int(count)) for key, minor, count in turn.groups)


def _movements_from(turn: Turn) -> int | None:
    """`turn.movements`, translated into what `Ledger.run` is told to hand back -- or `None`, its
    default, when the turn says nothing about how many movements its answer should show."""
    return turn.movements


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=215_648,
            available_minor=215_648,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=HOLDINGS,
        )


class Directory:
    """The holder's account names, which production wires and this harness did not.

    `AskFinancialQuestionHandler` reads the account scope of a question through this port when it
    has one, and through the account *summary* when it does not — and the two are not the same code.
    Three branches of `_scoped` exist only for the second case, so a corpus built without a
    directory replayed 1143 turns through a path no deployment runs: no turn had ever seen the
    account clarification, stored an `AccountSelection`, or read one back on a later turn.
    """

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        return HOLDINGS


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[Contact, ...]:
        return CONTACTS


class ContactDirectory:
    async def handle(self, query: ListContactsQuery) -> ListContactsResult:
        return ListContactsResult(
            contacts=[ContactRow(str(c.contact_id), c.display_name, c.phone) for c in CONTACTS]
        )


class Bank:
    """The ledger's payment side, as a double. Everything above it is the real thing.

    The first version of this file registered fakes for the *handlers*, which meant a confirmation
    never reached `confirmation.is_confirmation` — so "no, a la otra" answered a preview with
    "hecho, he enviado 20,00 €". The stub was paying on a refusal, and no episode could have caught
    it, because the code that decides was the code being replaced.

    So the doubles stop at the port. Reading a sentence into a payment, resolving two Martas, and
    deciding what counts as a yes are all the service, and all of it is what a corpus about
    conversations is for.
    """

    def __init__(self) -> None:
        self.prepared: list[PaymentRequest] = []
        self.no_transfer_contacts: set[uuid.UUID] = set()
        # The account each payment was prepared from, beside it: the credential's, which no
        # setting about what an analytics question means may move.
        self.sources: list[uuid.UUID] = []
        self.confirmed: list[uuid.UUID] = []
        self.cancelled: list[uuid.UUID] = []

    async def prepare(self, request: PaymentRequest, *, account_id: uuid.UUID, key: str):  # type: ignore[no-untyped-def]
        self.prepared.append(request)
        self.sources.append(account_id)
        if (
            request.rail is Rail.TRANSFER
            and request.recipient_contact_id in self.no_transfer_contacts
        ):
            raise RecipientHasNoAccountError(request.recipient_name)
        if request.rail is Rail.INTERNAL:
            # An internal transfer names an account and nobody at all, so there is no recipient to
            # resolve. Banking snapshots the holder's own label for it onto the intent, which is why
            # the double reads it off the same holdings the chat listed rather than inventing one:
            # a preview that named the wrong account would be the one mistake a confirmation exists
            # to catch.
            assert request.to_account_id is not None  # the rail's own invariant
            named = next(h for h in HOLDINGS if h.account_id == request.to_account_id)
            return PaymentPreview(
                intent_id=INTENT,
                recipient=named.display_name,
                recipient_phone=None,
                amount_minor=request.amount_minor,
                currency=request.currency,
                balance_before_minor=215_648,
                balance_after_minor=215_648 - request.amount_minor,
                expires_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
                rail=Rail.INTERNAL,
            )
        if request.recipient_iban is not None:
            return PaymentPreview(
                intent_id=INTENT,
                recipient=request.recipient_name,
                recipient_phone=None,
                amount_minor=request.amount_minor,
                currency=request.currency,
                balance_before_minor=215_648,
                balance_after_minor=215_648 - request.amount_minor,
                expires_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
                rail=Rail.TRANSFER,
                recipient_iban=request.recipient_iban,
            )
        wanted = request.recipient_name.lower()
        exact = [c for c in CONTACTS if c.display_name.lower() == wanted]
        partial = exact or [c for c in CONTACTS if wanted in c.display_name.lower()]
        if not partial:
            raise RecipientNotFoundError(request.recipient_name)
        if len(partial) > 1:
            raise AmbiguousRecipientError(
                request.recipient_name, tuple(c.display_name for c in partial)
            )
        who = partial[0]
        return PaymentPreview(
            intent_id=INTENT,
            recipient=who.display_name,
            recipient_phone=who.phone if request.rail is Rail.BIZUM else None,
            amount_minor=request.amount_minor,
            currency=request.currency,
            balance_before_minor=215_648,
            balance_after_minor=215_648 - request.amount_minor,
            expires_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
            rail=request.rail,
            recipient_iban="ES9121000418450200051332" if request.rail is Rail.TRANSFER else None,
        )

    async def confirm(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> PaymentReceipt:
        self.confirmed.append(intent_id)
        return PaymentReceipt(
            intent_id=intent_id,
            transaction_id=uuid.UUID(int=99),
            recipient="Marta Solis Vega",
            amount_minor=2_000,
            currency="EUR",
            balance_after_minor=213_648,
            confirmed_at=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
        )

    async def cancel(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> None:
        self.cancelled.append(intent_id)


# --- expert agents (A2A) ---------------------------------------------------------------------
#
# The gateway and the model that reads what it brings back, as doubles at their ports
# (`ExpertAgentsPort`, `SkillClassifierPort`, `ReplyNormaliserPort`). Everything between them is the
# real thing: the predicate that claims a sentence before the financial path, the search fan-out,
# the verifier, the sealed offers, the booking's two turns and the confirmation reader the payments
# use. The doubles stop at the ports for the reason `Bank` does -- the code that decides what a
# reply is worth is the code an episode is about.
#
# What each gym answers is canned, and so is what "the model" reads out of each answer. But the
# reading is a draft, and the real verifier still grounds every value of it in the canned reply:
# a draft naming a time, a place count or a reference the reply does not state is rejected here
# exactly as it would be in a deployment. The gyms speak the way `apps/simulated-gym-agent` does --
# norte in data parts of its own vocabulary, sur in Spanish text -- and both have already linked
# the holder, so the episodes are about the conversation rather than the linking page.


# --- which accounts a conversation is about ------------------------------------------------
#
# Production holds three accounts and may ask which one an unqualified spending question means. That
# is a real turn — «¿En qué cuenta quieres consultarlo?» — and it costs a thread its first
# exchange. A corpus that opened every conversation with nothing settled would spend its first turn
# answering that question 169 times over: the same preamble copied into every episode, proving
# nothing after the first, and drowning the thing each episode was written for.
#
# So an episode says once which accounts it is about and the harness opens the conversation there.
#
#   settled   the scope is already every account, which is the state a real thread is in from its
#             second turn onward. The default, because almost every episode is about something else
#             and was written as though the scope were not in question.
#   unset     nothing settled, so the clarification actually happens. `31-account-scope.yaml` is
#             written on this, because that turn is production behaviour and deserves conversations
#             of its own rather than a mention in 169 preambles.
#   legacy    no directory at all. Debt, not a choice — see `LEGACY_SCOPE` in
#             `tests/test_conversation_corpus.py` for the defect these six are waiting on.
#
# Since 2026-10-07 the clarification is asked only by a deployment configured to ask
# (`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask`); the shipped one reads every account. So `unset` alone
# is a thread with nothing chosen on the shipped product, and an episode about the question also
# declares `account_scope_default: ask` — which `31-account-scope.yaml` does.

SETTLED = "settled"
UNSET = "unset"
LEGACY = "legacy"
SCOPES = (SETTLED, UNSET, LEGACY)


# --- the corpus ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    say: str
    # Which conversation mechanism this turn exercises, as the evaluation battery's report names it
    # (`scripts/real_conversation.py` MECHANISMS). Reporting only: no assertion reads it, so the
    # corpus, which leaves it unset, is unaffected.
    mechanism: str | None = None
    route: str | None = None
    # One phrase, or several that must all appear: a search answered by two gyms names both.
    says: str | list[str] | None = None
    not_says: str | None = None
    keeps: str | None = None
    ends: bool = False
    awaiting: str | None = None
    no_model: bool = False
    total: int | None = None
    # Which query this turn executed, by whichever of its fields the episode cares about.
    #
    # The gap this closes is the one the corpus was built to find and could not see. `route` says a
    # financial question answered the turn; `says` and `total` compare a figure the stub ledger
    # returns for every plan alike. So a follow-up that inherited the wrong window, dropped the
    # category or flipped the direction scored as correct — and the metric named `lost_context`
    # counted only the turns where a *workflow* failed to survive, never a lost window.
    #
    # Worth most on `no_model: true` turns: there the plan came from this service's own inheritance
    # rather than from the stub planner, so the expectation is about the code under measurement.
    #
    # On a turn that *does* reach the planner, only some fields still mean anything. `AnyPlan` reads
    # the window, the categories and the grouping out of the sentence through the same services a
    # deployment uses, so those are ours; `direction` it hardcodes to EXPENSE. Asserting the
    # direction of a model-answered turn measures the stub and passes or fails for no reason.
    #
    # A list instead of a mapping asserts *every* query the turn ran, in order, which is what a
    # comparison needs: one mapping only ever reaches the last operand, so the other half of
    # "compara julio con junio" was unasserted.
    ran: dict[str, Any] | list[dict[str, Any]] = field(default_factory=dict)
    # The chart this turn's last visual context carries, by whichever of `canonical(spec)`'s
    # elements the episode cares about: `mark`, `stack`, `sort`, `sort_field`, `limit`, `channels`
    # (the set of `(channel, field, type)` triples an encoding carries, ignoring per-encoding
    # sorts) and `axis_sort` (the one per-encoding sort an axis carries, as a `[channel, mode,
    # field]` triple). Read off `conversation.visual_contexts[-1].spec` -- the object the service
    # remembers -- rather than `answer["visualization"]["spec"]`, the presented Vega-Lite, so a
    # presenter defect that only changed the wire picture would not slip past an assertion that
    # only ever reads the presenter's own output.
    chart: dict[str, Any] | None = None
    # How many times this turn called the ledger, against `len(ran)` -- so a restyle that must run
    # none and a reshape that must run one are each a number instead of a guess read off `route`.
    banking_calls: int | None = None
    # Whether the last visual context this turn leaves carries a `derived_from` -- the chart links
    # back to a previous one rather than starting fresh. Asserted only on a turn the classifier
    # actually links; a `NEW_REQUEST` reading derives from nothing however plausible the sentence.
    derived_from: bool | None = None
    # The `Ledger` fake's answer for this turn only, as `[[group_key, total_minor,
    # movement_count], ...]` -- overriding its one hardcoded group so a turn that reshapes a
    # breakdown gets the breakdown its expectation describes.
    groups: list[list[object]] | None = None
    # How many movements the turn's answer SHOWS -- `len(answer["movements"])`, the wire's
    # `QuestionResponse.movements` list. Asserted beside `banking_calls`, and nothing asserted when
    # `None`. The `Ledger` fake only ever hands these back when it was actually asked for them (see
    # `Ledger.run`), so `movements: 3` on a turn fails if the code forgot to ask -- that is the
    # point: the number here is not free-standing evidence, it is a check that the evidence was
    # requested at all.
    movements: int | None = None
    # Which endpoint this turn travels: `"TEXT"` posts to `/v1/questions`, byte-identical to every
    # turn before this field existed; `"VOICE"` posts to `/v1/questions/stream` and reads its SSE
    # frames instead. Why a corpus turn needs a channel at all: design §9 names five multimodal
    # cases -- a conversation that keeps its chart across the channel a turn arrives on -- and
    # nothing before this task could drive a turn through the streaming route.
    channel: Literal["TEXT", "VOICE"] = "TEXT"
    # Whether this turn's answer carries Task 3's chart-derived spoken summary --
    # `isinstance(answer.get("spoken"), str)` compared against this flag. What it measures is the
    # summary's *presence*, never its wording -- that is `test_visual_context_producers.py`'s own
    # business. `None` asserts nothing, matching every turn written before this field existed.
    spoken: bool | None = None
    # A substring the spoken line must contain, case-insensitively -- the noun the presenter
    # chose, not merely that a presenter spoke at all. `spoken` above went presence-only under
    # 4.4's R8; Task 7 round 0 of Wave 4.5 then silenced every chart's noun down to a generic
    # line and no corpus turn complained, because none checked the words. When set,
    # `answer.get("spoken")` must be a `str` containing this substring, `says`-style. `None`
    # asserts nothing, matching every turn written before this field existed.
    spoken_says: str | None = None
    # Whether this turn's answer carries Task 3's chart-derived explanation --
    # `isinstance(answer.get("explanation"), dict)` compared against this flag. What it measures is
    # the explanation's *presence* on the answer, never its wording -- that is
    # `tests/test_visual_explanation.py`'s own business. `None` asserts nothing, matching every turn
    # written before this field existed.
    explanation: bool | None = None
    # A substring the explanation's own words must contain, case-insensitively -- `explanation`
    # above is presence-only (B-5, «Corpus blind spots» #2 in the behaviour audit: exactly one
    # turn in the whole corpus pinned any explanation wording, and it did so through `says:`
    # rather than a field of its own, so a defect confined to `explanation.text` -- the known
    # "Datos: 0 grupos" bug named there -- was invisible to every other turn asserting
    # `explanation: true`). Read the same way `spoken_says` reads `answer["spoken"]`:
    # `answer["explanation"]["text"]` must be a string containing this substring. `None` asserts
    # nothing, matching every turn written before this field existed.
    explanation_says: str | None = None
    # Every one of these strings, case-insensitively, must appear among `answer["suggestions"]`
    # (blind spot #5: chips were asserted by no corpus turn at all, which is why B-7 -- a restyle
    # offering none -- went unnoticed). `None` asserts nothing.
    chips: list[str] | None = None
    # The turn's `suggestions` must be empty. The two fields are separate rather than `chips: []`
    # meaning the same thing, because an empty list is also every turn's dataclass default --
    # `chips: []` on a turn nobody wrote would silently assert "no chips" by omission, and this
    # says so in the file instead.
    no_chips: bool | None = None
    # B-5: whether the chart the conversation remembers agrees with the words the answer used to
    # describe it. What is compared is exactly the two things the harness already has in hand for
    # a charted turn -- the plan that actually ran (`ran[-1].group_by`, when it answered through a
    # `FinancialPlan`) and the chart's own sealed `group_by` (`VisualContext.group_by`, the same
    # field `_chart_complaints` reads) -- never a second reading of the prose. `True` requires the
    # two to be the same `GroupBy`; `False` requires them to differ (a probe fixture, not a turn
    # this service should ever produce). `None` asserts nothing, and asserting `True` on a turn
    # with no chart or no plan is treated as a failure the same way `chart:` is above.
    chart_text_agrees: bool | None = None


@dataclass(frozen=True)
class Episode:
    name: str
    why: str
    turns: tuple[Turn, ...]
    metrics: tuple[str, ...] = ()
    # Which of the holder's accounts this conversation is already about, once, for the episode.
    scope: str = SETTLED
    # `ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT` for this episode's deployment, when it is not the stack's
    # own: an episode about the account question needs a deployment that asks it.
    account_scope_default: AccountScopeDefault | None = None
    # The gyms the A2A gateway lists, from `CANNED_GYMS`. None is a deployment with no gateway,
    # which is every episode written before the expert agents existed.
    agents: tuple[str, ...] = ()


CORPUS = Path(__file__).resolve().parents[2] / "evaluation" / "conversations"


def load() -> list[Episode]:
    """Every episode of the corpus, from every file in it.

    One file per feature rather than one big one: an episode is read by whoever is adding to that
    feature, and a two-thousand-line file is one nobody reads before appending to it.
    """
    episodes: list[Episode] = []
    for path in sorted(CORPUS.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in loaded.get("episodes", []):
            episodes.append(
                Episode(
                    name=f"{path.stem}/{raw['name']}",
                    why=raw.get("why", ""),
                    metrics=tuple(raw.get("metrics", ())),
                    scope=raw.get("scope", SETTLED),
                    account_scope_default=raw.get("account_scope_default"),
                    agents=tuple(raw.get("agents", ())),
                    turns=tuple(Turn(**{**turn, "say": str(turn["say"])}) for turn in raw["turns"]),
                )
            )
    return episodes


# --- who claims a turn ---------------------------------------------------------------------
#
# `_steps` reads a sentence by asking twenty deterministic recognisers in a fixed order and taking
# the first that answers. The order is the design, and it is also invisible: the first one to fire
# returns, so nothing can observe that a second one would have fired too, with a different query.
#
# That is what makes the cascade hard to change. Fixing one turn by moving a branch can silently
# change another, and there is no measurement that says which turns are sitting on a tie. These
# twenty have the same signature — a query in, a reading or None out, no I/O — so asking all of
# them is a thing a test can just do.
#
# Test-only on purpose. Production still short-circuits: evaluating a recogniser that today is
# unreachable could raise, and a measurement is the right place to find that out.


# --- the corpus as a migration harness -----------------------------------------------------
#
# The reason this file can measure an interpreter at all: 879 turns whose right answer is already
# written down. A shadow comparison in production can only ever say *how often* two decisions
# differ, because the sentence may not be logged — this service reads a person's bank account and
# nothing they typed reaches a log file. Here the sentences are ours, so a disagreement can be read.


@dataclass
class Stack:
    client: TestClient
    ledger: Ledger
    bank: Bank


def a_stack() -> Stack:
    """The question routes over the fakes. The test installs the graph in `app.state.engine`; the
    payment commands a graph dispatches run against `bank`."""
    ledger, bank = Ledger(), Bank()
    commands = CommandBus()
    commands.register(ConfirmPaymentCommand, ConfirmPaymentHandler(bank))  # type: ignore[arg-type]
    commands.register(PrepareChosenPaymentCommand, PrepareChosenPaymentHandler(bank))  # type: ignore[arg-type]
    commands.register(PrepareOwnTransferCommand, PrepareOwnTransferHandler(bank))  # type: ignore[arg-type]
    queries = QueryBus()
    queries.register(ListContactsQuery, ContactDirectory())
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM}),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = True
    return Stack(TestClient(app), ledger, bank)
