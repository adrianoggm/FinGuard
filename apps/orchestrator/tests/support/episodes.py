"""Whole conversations, driven through HTTP, measured rather than merely asserted.

The planning benchmark measures one sentence at a time and the conversation dataset beside it needs
a live model. Neither answers the question this file is about: **over a long conversation, does each
turn reach the right part of the service?**

Every defect reported from the demo so far has been that and only that. Not a wrong figure — a
question routed to the payment path, a balance sent to the planner, a reply to an open question read
as an order. The figures were right every time; the *tool* was wrong. So that is what an episode
asserts: which of fifteen places handled each turn, and what the turn left behind.

**No model, and that is the design.** The planner is stubbed to answer any sentence with a plan, so
`QUERY` means "it reached the planner" and nothing more. Everything else is a path this service
decides on its own — a vocabulary, a router, a state — and those are exactly the decisions that must
not vary with which model a deployment installed. A corpus that needed one could not run in CI, and
this one runs in a second.

What each turn may say:

    route        which of the fifteen handled it. The whole point of the file
    says         a phrase the answer must contain, or a list of them that all must
    not_says     a phrase it must not
    keeps        a workflow that has to still be open afterwards
    ends         nothing is left open
    awaiting     the slot the turn asked for
    no_model     this turn must not reach the planner at all
    total        the figure the answer leads with, in minor units

And what a whole episode may say:

    metrics      the ones this episode is meant to exercise, so a run can report per feature
    scope        which of the holder's accounts this conversation is already about
    agents       the gyms an A2A gateway lists (`CANNED_GYMS`); without it there is no gateway

**The stack is the deployment's, and `scope` is what made that affordable.** Production wires an
`AccountDirectory` and therefore asks «¿En qué cuenta quieres consultarlo?» before answering any
unqualified spending question. This harness did not wire one, so every turn here took three
branches of `_scoped` that no deployment reaches — which is how a real account-scope defect
survived 1143 turns at "0 % lost context". Declaring the scope once per episode buys the production
path without a preamble turn in every conversation; see `opening`.
"""

from __future__ import annotations

import json
import re
import uuid
from calendar import monthrange
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    ListContactsQuery,
    ListContactsResult,
    PreparePaymentCommand,
    QueryBus,
)
from finguard_orchestrator.application.commands.book_activity import BookActivityHandler
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PrepareOwnTransferCommand,
    PrepareOwnTransferHandler,
    PreparePaymentHandler,
)
from finguard_orchestrator.application.queries import (
    ask_financial_question as handler,
)
from finguard_orchestrator.application.queries.list_contacts import ContactRow
from finguard_orchestrator.application.queries.readers import (
    breakdowns,
    comparisons,
    follow_up,
    metrics,
    rankings,
    subjects,
)
from finguard_orchestrator.application.queries.search_activities import SearchActivitiesHandler
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    reporting_today,
    turn_interpreter,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    PaymentOutcome,
    Period,
    PlanningOutcome,
    Rail,
)
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentReply,
    AgentSkill,
    AvailabilityDraft,
    BookingDraft,
    Capability,
    DataReplyPart,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    SlotDraft,
    TextReplyPart,
)
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.models.visual_plan import canonical
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    payment_words,
    spanish,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper


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
    Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
    Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550),
    Holding(FUND, "ASSET", "Fondo indexado", "EUR", 270_000),
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

_SPOKEN = {
    "BALANCE": BALANCE,
    "CONTACTS": CONTACTS_LIST,
    "IDENTITY": IDENTITY,
    "CATEGORIES": CATEGORIES,
    "EXPLANATION": EXPLANATION,
    "GUIDANCE": GUIDANCE,
    "COURTESY": COURTESY,
    "CARDS": CARDS,
    "TRANSFER_READY": TRANSFER,
}
_PAYMENT_WORKFLOWS = {"PAYMENT_IN_PROGRESS", "PAYMENT_CONFIRMATION"}
_PAYMENT_OUTCOMES = {
    PaymentOutcome.AWAITING_CONFIRMATION.value,
    PaymentOutcome.PAID.value,
    PaymentOutcome.CANCELLED.value,
    PaymentOutcome.REFUSED.value,
    PaymentOutcome.BANKING_UNAVAILABLE.value,
}
# The expert agents' outcomes (spec §5, `infrastructure/controllers/expert_agent_turns.py`), every
# one of them: a search, a booking's two turns and each way either can end. One route, because the
# turn reached the gyms -- or was refused on the way there -- and nothing financial answered it.
# Named in full for the reason `_VISUAL_OUTCOMES` is: an outcome missing here falls through
# `tool_of` and is silently counted as a query.
_AGENT_OUTCOMES = {
    "ACTIVITIES_FOUND",
    "ACTIVITIES_NONE",
    "ACTIVITY_UNCLEAR",
    "AGENTS_UNAVAILABLE",
    "EXPERT_AGENTS_OFF",
    "EXPERT_AGENTS_FORBIDDEN",
    "BOOKED",
    "BOOKING_ALTERNATIVES",
    "BOOKING_AMBIGUOUS",
    "BOOKING_AWAITING_CONFIRMATION",
    "BOOKING_CANCELLED",
    "BOOKING_FAILED",
    "BOOKING_LINK_REQUIRED",
    "BOOKING_NOT_OFFERED",
    "BOOKING_ON_SCREEN",
    "BOOKING_PAYMENT_PAUSED",
    "BOOKING_SEARCH_AGAIN",
    "BOOKING_STARTED",
    "BOOKING_UNCLEAR",
    "BOOKING_UNAVAILABLE",
    "BOOKING_UNKNOWN",
    "BOOKING_UNSUPPORTED",
}
# The visual outcomes, checked before the `QUERY` fallthrough below -- without this, a
# `VISUAL_UPDATED` answer fell through unrecognised and was silently counted as a query, exactly
# the blindness a `route: QUERY` expectation on a restyle turn should have caught and could not.
_VISUAL_OUTCOMES = {
    "VISUAL_UPDATED",
    # F2 (fix wave 2): a restyle whose merged spec draws identically to what is already on
    # screen answers this instead of minting a version. Still the `VISUAL` route -- the turn
    # asked to change the chart and got an answer about the chart, the same as every sibling
    # below -- so it belongs in this set for the same reason `VISUAL_UPDATED` does: without it,
    # a `VISUAL_UNCHANGED` answer falls through unrecognised and is silently counted as a query.
    "VISUAL_UNCHANGED",
    # Task 4: an echoed chart whose seal does not check out is refused here, before a merge, a
    # verify or a single ledger call -- still the `VISUAL` route, for the same reason as its two
    # siblings above: the turn asked to change a chart and got an answer about a chart, so without
    # this a `VISUAL_UNTRUSTED` answer falls through unrecognised and is silently counted as a
    # query, exactly the blindness a tampered echo should be caught by and could not.
    "VISUAL_UNTRUSTED",
    "VISUAL_INCOMPATIBLE",
    "VISUAL_NEEDS_TARGET",
    "VISUAL_AMBIGUOUS",
    # Task 4: the turn asked about the chart and got an answer about the chart -- the same reason
    # every sibling above belongs in this set. Without it a `VISUAL_EXPLAINED` answer falls through
    # unrecognised and is silently counted as a query, exactly the blindness a «¿por qué este
    # gráfico?» turn should be caught answering and could not.
    "VISUAL_EXPLAINED",
}
# R9: a drill-down that fit several bars asks which one rather than guessing -- a question about
# the figures already on screen (which bar?), not a request to change the chart, so it does not
# belong beside `_VISUAL_OUTCOMES`; and not `NEEDS_CLARIFICATION` either, because no slot is opened
# and no workflow is left waiting -- the conversation is exactly as it was before the turn. A small
# named set of its own, the same shape as `_VISUAL_OUTCOMES`, for the same reason: without this,
# `DRILL_AMBIGUOUS` fell through to `tool_of`'s `QUERY` fallthrough and a turn that asked instead of
# answering was silently counted as having answered.
# Task 2 (Wave 3 hardening, A-2): `DRILL_UNBOUND` belongs beside `DRILL_AMBIGUOUS` for the same
# reason -- a question about the figures on screen (this chart is not this plan's), not a request
# to change the chart, and no slot opened or workflow left waiting either.
_ASKED_ABOUT_THE_FIGURES = {"DRILL_AMBIGUOUS", "DRILL_UNBOUND"}

# N-1 (4.4 re-review): a compound read probes as `QUERY` by fallthrough yet answers through
# `_open_payment_events` with a single answer frame -- no plan of its own runs through `_steps`,
# so no `Stage` is yielded. Named positively so the stage rule states which QUERY answers do
# not stage, instead of trusting the fallthrough to mean "planned".
_ANSWERED_WITHOUT_A_STAGE = {"COMPOUND"}


def tool_of(answer: dict[str, Any]) -> str:
    """Which of the fifteen answered this turn.

    The outcome alone is not enough and that is not an accident of the enums: a payment asking for
    an amount and a financial question asking for a period are both NEEDS_CLARIFICATION, and telling
    them apart is the whole difference between "it asked me which month" and "it started paying
    somebody". The workflow the turn leaves is what separates them.
    """
    outcome = answer["outcome"]
    workflow = (answer.get("conversation") or {}).get("workflow")

    # The outcome decides, and the workflow is only consulted to break the one tie. Reading the
    # workflow first counted a *suspended* payment as a payment turn — so "¿cuánto gasté el mes
    # pasado?" asked in the middle of a Bizum, answered correctly with a figure, was measured as
    # the payment path having taken it. The state that survives a turn is not the tool that
    # answered it, and conflating the two would have hidden the opposite defect too.
    if outcome in _SPOKEN:
        return _SPOKEN[outcome]
    if outcome in _PAYMENT_OUTCOMES:
        return PAYMENT
    if outcome in _AGENT_OUTCOMES:
        return AGENTS
    if outcome == PlanningOutcome.UNSUPPORTED.value:
        return REFUSED
    if outcome == PlanningOutcome.NEEDS_CLARIFICATION.value:
        # The one tie: a payment asking for an amount and a question asking for a period are the
        # same outcome, and the difference between them is everything.
        if workflow in _PAYMENT_WORKFLOWS:
            return PAYMENT
        return TRANSFER if workflow == "TRANSFER_IN_PROGRESS" else CLARIFY
    if outcome in _VISUAL_OUTCOMES:
        return VISUAL
    if outcome in _ASKED_ABOUT_THE_FIGURES:
        return CLARIFY
    return QUERY


# --- the stubs -----------------------------------------------------------------------------


class AnyPlan:
    """A planner that answers every sentence with a plan, and counts what it was asked.

    Answering everything is the point. This corpus does not measure whether a model reads a sentence
    well — it measures whether the sentence reached a model *at all*, which is a decision this
    service makes on its own. A turn expected to be answered from a vocabulary and found here is a
    failure whatever the plan says.
    """

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        self.asked.append(sentence)
        said = sentence.lower()
        if any(word in said for word in ("mas caro", "más caro", "mas alto", "más alto", "mayor")):
            # A ranking, because "¿y el segundo?" only means anything after one. The stub reads two
            # words to decide the *shape* of the answer; which movements are in it is the ledger's.
            return PlanningResult.planned(
                TopMovements(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(period=Period.THIS_YEAR),
                    categories=("TRANSPORT_FUEL",),
                )
            )
        # Built from what the sentence states outright, so the plan verifier has nothing to
        # disagree with. A stub that ignored the category the sentence names would be refused
        # as a misreading, and every episode would measure that instead of the routing.
        facts = explicit_facts.read(sentence, today=TODAY)
        # The window of the query behind the turn, when the sentence states none. A deployment's
        # planner is handed `previous` for exactly this, and a stub that threw it away made every
        # turn reaching the model look as though it had lost the thread — so an expectation about
        # one could not state a window without asserting this stub's default instead.
        previous = context.continuation.plan if context.continuation else None
        inherited = (previous.period or previous.date_range) if previous is not None else None
        window = facts.window or inherited or RelativePeriod(period=Period.LAST_MONTH)
        relative = isinstance(window, RelativePeriod)
        return PlanningResult.planned(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=window if relative else None,
                date_range=None if relative else window,
                categories=tuple(category_words.precisely_named_in(sentence)),
                group_by=facts.group_by or GroupBy.NONE,
            )
        )


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
            booked_minor=227_042,
            available_minor=227_042,
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
        self.confirmed: list[uuid.UUID] = []
        self.cancelled: list[uuid.UUID] = []

    async def prepare(self, request: PaymentRequest, *, account_id: uuid.UUID, key: str):  # type: ignore[no-untyped-def]
        self.prepared.append(request)
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
                balance_before_minor=227_042,
                balance_after_minor=227_042 - request.amount_minor,
                expires_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
                rail=Rail.INTERNAL,
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
            recipient_phone=who.phone,
            amount_minor=request.amount_minor,
            currency=request.currency,
            balance_before_minor=227_042,
            balance_after_minor=227_042 - request.amount_minor,
            expires_at=datetime(2026, 8, 5, 10, 5, tzinfo=UTC),
        )

    async def confirm(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> PaymentReceipt:
        self.confirmed.append(intent_id)
        return PaymentReceipt(
            intent_id=intent_id,
            transaction_id=uuid.UUID(int=99),
            recipient="Marta Solis Vega",
            amount_minor=2_000,
            currency="EUR",
            balance_after_minor=225_042,
            confirmed_at=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
        )

    async def cancel(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> None:
        self.cancelled.append(intent_id)


class Reader:
    """What a model would have read out of a payment sentence, read deterministically instead.

    A name and a figure, through the amount vocabulary the service already owns. It reads only what
    is written: a sentence with no figure comes back without one and the handler asks, which is the
    turn most of these episodes are about.
    """

    # A capitalised word after "a", when no known contact is in the sentence. An unknown name is
    # still a name: "hazle un bizum a Pepe" is a payment to somebody who is not in the list, and
    # reading it as a question about *who* would be a different answer to a different question.
    _A_NAME = re.compile(r"\ba\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)")

    async def read(self, sentence: str, **_: object) -> PaymentReading:
        said = spanish.folded(sentence)
        found = self._A_NAME.search(sentence)
        named = (
            next((c.display_name for c in CONTACTS if spanish.folded(c.display_name) in said), None)
            or next((first for first in ("Marta", "Javi") if first.lower() in said), None)
            or (found.group(1) if found else None)
        )
        named = payment_words.without_trailing_filler(named) if named else None
        amount = payment_words.amount_in(sentence)

        if named is None:
            # The draft travels even with no name in it, exactly like the production reader: a
            # question whose answer this path cannot hear is the defect the whole payment state
            # exists to prevent, and a double that drops it hides that from the corpus.
            return PaymentReading.needs_clarification(
                "¿A quién quieres enviárselo?",
                draft=PaymentDraft(amount_minor=amount) if amount else PaymentDraft(),
            )
        if amount is None:
            return PaymentReading.needs_clarification(
                f"¿De cuánto quieres hacerle el bizum a {named}?",
                draft=PaymentDraft(recipient_name=named),
            )
        return PaymentReading.read(
            PaymentRequest(
                recipient_name=named,
                amount_minor=amount,
                currency="EUR",
                concept=payment_words.concept_in(sentence),
            )
        )


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

_MADRID = ZoneInfo("Europe/Madrid")
# Wednesday 5 August 2026 at 10:00 in Madrid -- `TODAY`, so «el jueves» is Thursday the 6th.
AGENTS_NOW = datetime(2026, 8, 5, 10, 0, tzinfo=_MADRID)
_THURSDAY = "2026-08-06"
_FRIDAY = "2026-08-07"


def _norte_session(start: str, places: int, day: str = _THURSDAY) -> dict[str, object]:
    return {
        "clase": "Spinning",
        "fecha": day,
        "hora_inicio": start,
        "duracion_min": 45,
        "huecos": places,
        "ref": f"N-{day[5:7]}{day[8:]}-{start.replace(':', '')}",
    }


def _norte_slot(start: str, end: str, places: int, day: str = _THURSDAY) -> SlotDraft:
    """What a model reads out of `_norte_session`: the date it states, the end from its length."""
    reference = f"N-{day[5:7]}{day[8:]}-{start.replace(':', '')}"
    return SlotDraft("Spinning", f"{day}T{start}", f"{day}T{end}", places, reference)


def _reply(task: str, state: str, *parts: TextReplyPart | DataReplyPart) -> AgentReply:
    return AgentReply(state, task, f"ctx-{task.split('-')[0]}", parts, 40)  # type: ignore[arg-type]


@dataclass(frozen=True)
class CannedGym:
    """One gym as the gateway lists it, what its card's skills are read as, and what it answers.

    `answers` is keyed by what was asked -- `availability`, or `booking HH:MM` for the class at that
    time -- and holds the reply with the draft a model reads out of it.
    """

    agent: ExpertAgent
    skills: Mapping[Capability, str]
    answers: Mapping[str, tuple[AgentReply, AvailabilityDraft | BookingDraft]]


CANNED_GYMS: dict[str, CannedGym] = {
    "gym-norte": CannedGym(
        ExpertAgent(
            "gym-norte",
            "Gimnasio Norte",
            True,
            None,
            "sha256:corpus-gym-norte",
            ("gym-norte:8000", "127.0.0.1:9101"),
            (
                AgentSkill("timetable", "Horario de clases", "Clases por actividad y fecha."),
                AgentSkill("reserve", "Reservar plaza", "Reserva una plaza para un socio."),
            ),
        ),
        {Capability.AVAILABILITY: "timetable", Capability.BOOKING: "reserve"},
        {
            "availability": (
                _reply(
                    "norte-timetable",
                    "COMPLETED",
                    DataReplyPart(
                        {"sesiones": [_norte_session("19:00", 4), _norte_session("20:00", 0)]}
                    ),
                ),
                AvailabilityDraft(
                    (_norte_slot("19:00", "19:45", 4), _norte_slot("20:00", "20:45", 0))
                ),
            ),
            # The full class: the booking pauses and offers the next two instead, the second
            # on the next day -- as the simulated gym's `next_of` does (ruling R22).
            "booking 20:00": (
                _reply(
                    "norte-full",
                    "INPUT_REQUIRED",
                    DataReplyPart(
                        {
                            "alternativas": [
                                _norte_session("21:00", 3),
                                _norte_session("18:30", 5, _FRIDAY),
                            ]
                        }
                    ),
                ),
                AvailabilityDraft(
                    (_norte_slot("21:00", "21:45", 3), _norte_slot("18:30", "19:15", 5, _FRIDAY))
                ),
            ),
            "booking 21:00": (
                _reply(
                    "norte-booked",
                    "COMPLETED",
                    DataReplyPart(
                        {
                            "reserva": {
                                "codigo": "NT-4821",
                                "ref": "N-0806-2100",
                                "clase": "Spinning",
                                "fecha": _THURSDAY,
                                "hora_inicio": "21:00",
                            }
                        }
                    ),
                ),
                BookingDraft(True, "NT-4821", f"{_THURSDAY}T21:00", "Spinning", "N-0806-2100"),
            ),
        },
    ),
    "gym-sur": CannedGym(
        ExpertAgent(
            "gym-sur",
            "Gimnasio Sur",
            True,
            None,
            "sha256:corpus-gym-sur",
            ("gym-sur:8000", "127.0.0.1:9102"),
            (
                AgentSkill("classes", "Clases del centro", "Qué clases hay y cuántas plazas."),
                AgentSkill("book", "Apuntarse a una clase", "Apunta a un socio a una clase."),
            ),
        ),
        {Capability.AVAILABILITY: "classes", Capability.BOOKING: "book"},
        {
            "availability": (
                _reply(
                    "sur-classes",
                    "COMPLETED",
                    TextReplyPart(
                        "Spinning el jueves a las 18:30 (quedan 6 plazas) y a las 20:00 "
                        "(quedan 2 plazas)."
                    ),
                ),
                AvailabilityDraft(
                    (
                        SlotDraft("Spinning", "18:30", None, 6, None),
                        SlotDraft("Spinning", "20:00", None, 2, None),
                    )
                ),
            ),
            "booking 18:30": (
                _reply(
                    "sur-booked",
                    "COMPLETED",
                    TextReplyPart(
                        "Listo: te he apuntado a la clase de spinning del jueves 6 de agosto "
                        "a las 18:30. Código SUR-7731."
                    ),
                ),
                BookingDraft(True, "SUR-7731", f"{_THURSDAY}T18:30", "spinning", None),
            ),
        },
    ),
    # The non-compliant one: listed, refused by the gateway's gate, never asked (§4.1).
    "gym-legacy": CannedGym(
        ExpertAgent(
            "gym-legacy",
            "Gimnasio Antiguo",
            False,
            "version_unsupported",
            "sha256:corpus-gym-legacy",
            ("gym-legacy:8000",),
            (),
        ),
        {},
        {},
    ),
}
_DRAFTS: dict[str, AvailabilityDraft | BookingDraft] = {
    reply.task_id: draft
    for gym in CANNED_GYMS.values()
    for reply, draft in gym.answers.values()
    if reply.task_id is not None
}


def _asked(outbound: Outbound) -> str:
    """What a message asks, in `CannedGym.answers`' keys: read off the data part the template
    fills, so a booking is told apart by the class it names and nothing else."""
    request = str(outbound.data.get("request"))
    if request == "booking":
        return f"booking {datetime.fromisoformat(str(outbound.data['at'])):%H:%M}"
    return request


class Gyms:
    """`ExpertAgentsPort`: the gateway, listing the declared gyms and answering from their canned
    replies. Every send is recorded, so an episode's booking can be seen to have been sent once."""

    def __init__(self, ids: tuple[str, ...]) -> None:
        self._gyms = {agent_id: CANNED_GYMS[agent_id] for agent_id in ids}
        self.sent: list[tuple[str, str, str]] = []

    async def agents(self) -> tuple[ExpertAgent, ...]:
        return tuple(gym.agent for gym in self._gyms.values())

    async def send(
        self,
        agent_id: str,
        *,
        skill_id: str,
        message_id: str,
        outbound: Outbound,
        context_id: str | None,
    ) -> AgentReply:
        asked = _asked(outbound)
        self.sent.append((agent_id, skill_id, asked))
        answer = self._gyms[agent_id].answers.get(asked)
        if answer is None:
            # A request nobody canned is a gym that did not answer, never a guessed reply.
            raise ExpertAgentsError("agent_error")
        return answer[0]


class CardReader:
    """`SkillClassifierPort`: what a model reads off each card, canned."""

    async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
        return CANNED_GYMS[agent.id].skills


class ReplyReader:
    """`ReplyNormaliserPort`: the draft a model reads out of each canned reply. A draft, so the
    real verifier still decides whether any of it was in the reply."""

    async def availability(
        self, query: ActivityQuery, reply: AgentReply
    ) -> AvailabilityDraft | None:
        draft = _DRAFTS.get(reply.task_id or "")
        return draft if isinstance(draft, AvailabilityDraft) else None

    async def booking(self, slot: ActivitySlot, reply: AgentReply) -> BookingDraft | None:
        draft = _DRAFTS.get(reply.task_id or "")
        return draft if isinstance(draft, BookingDraft) else None


def _agents_clock(zone: ZoneInfo) -> datetime:
    return AGENTS_NOW.astimezone(zone)


@dataclass
class Stack:
    client: TestClient
    planner: AnyPlan
    ledger: Ledger
    bank: Bank
    # The gateway double, when the episode declared `agents:`; `None` is no gateway at all.
    gyms: Gyms | None = None


def a_stack(*, directory: bool = True, agents: tuple[str, ...] = ()) -> Stack:
    """The service, wired as a deployment wires it.

    `directory=False` is the shape every episode used to run in, and it is now only reached by the
    episodes that declare `scope: legacy` — see `opening`.

    `agents` are the gyms of `CANNED_GYMS` the gateway lists, for an episode that declares them;
    none is a deployment with no gateway configured, where the capability does not exist (§3.2).
    Set on every stack, because `app.state` outlives it: an episode without agents must not find
    the last one's gateway still wired.
    """
    planner, ledger, bank = AnyPlan(), Ledger(), Bank()
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),  # type: ignore[arg-type]
        account_directory=Directory() if directory else None,  # type: ignore[arg-type]
        contacts=Contacts(),
        cards=NoCards(),  # type: ignore[arg-type]
    )
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)
    queries.register(ListContactsQuery, ContactDirectory())
    commands = CommandBus()
    commands.register(
        PreparePaymentCommand,
        PreparePaymentHandler(Reader(), bank, contacts=Contacts()),  # type: ignore[arg-type]
    )
    commands.register(ConfirmPaymentCommand, ConfirmPaymentHandler(bank))  # type: ignore[arg-type]
    # A transfer between the holder's own accounts, staged from the chat like a Bizum. Registered
    # here because the corpus drives the real route: without it every episode that finishes a
    # transfer asks the bus for a handler nobody wired and the turn is a 500.
    commands.register(
        PrepareOwnTransferCommand,
        PrepareOwnTransferHandler(bank),  # type: ignore[arg-type]
    )

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[reporting_today] = lambda: TODAY
    scopes = {Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM}
    gyms = Gyms(agents) if agents else None
    if gyms is not None:
        # The web demo token's two agent scopes (§14 A4), only where there is a gateway to use.
        scopes |= {Scope.AGENTS_ASK, Scope.AGENTS_BOOK}
        app.state.expert_searches = SearchActivitiesHandler(
            gyms,
            CardReader(),
            ReplyReader(),
            pairwise_key=b"corpus-pairwise-key",
            budget_seconds=5.0,
            clock=_agents_clock,
        )
        app.state.expert_bookings = BookActivityHandler(
            gyms,
            CardReader(),
            ReplyReader(),
            pairwise_key=b"corpus-pairwise-key",
            budget_seconds=5.0,
            clock=_agents_clock,
        )
    else:
        app.state.expert_searches = None
        app.state.expert_bookings = None
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset(scopes),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = True
    return Stack(TestClient(app), planner, ledger, bank, gyms)


# --- which accounts a conversation is about ------------------------------------------------
#
# Production holds three accounts and asks which one an unqualified spending question means. That
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

SETTLED = "settled"
UNSET = "unset"
LEGACY = "legacy"
SCOPES = (SETTLED, UNSET, LEGACY)


# --- the corpus ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    say: str
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
    # The gyms the A2A gateway lists, from `CANNED_GYMS`. None is a deployment with no gateway,
    # which is every episode written before the expert agents existed.
    agents: tuple[str, ...] = ()


def opening(episode: Episode) -> dict[str, Any] | None:
    """The conversation a replay starts from, from what the episode declared.

    A settled `AccountSelection` rather than an extra turn. It is the same state the service stores
    the moment a holder answers the account question, so an episode opening here resumes a real
    thread rather than skipping a check: every turn in it still reads that selection back through
    the directory-backed branch of `_scoped`, which is the branch that had never run.

    `==` and not `is`. Until an episode wrote `scope: settled` out loud, the only settled episodes
    were the ones that omitted the field and took the dataclass default -- the interned literal, so
    identity happened to hold. A value read out of YAML is a fresh string, so the first episode to
    declare the default explicitly opened with nothing settled and every unqualified question in it
    was answered with the account clarification instead. The typo rule below cannot see this: it
    compares with `in`, which is `==` all the way down.
    """
    return {"account_selection_mode": "ALL"} if _scope_of(episode) == SETTLED else None


def a_stack_for(episode: Episode) -> Stack:
    """The stack this episode replays in. Only `scope: legacy` gets one without a directory, and
    only an episode declaring `agents:` gets a gateway."""
    return a_stack(directory=_scope_of(episode) != LEGACY, agents=episode.agents)


def _scope_of(episode: Episode) -> str:
    """The declared scope, refusing a typo rather than silently meaning `settled`."""
    if episode.scope not in SCOPES:
        raise ValueError(f"{episode.name}: scope {episode.scope!r} is not one of {SCOPES}")
    return episode.scope


@dataclass
class Failure:
    episode: str
    turn: int
    said: str
    complaint: str


@dataclass
class Report:
    """What the corpus measured, per metric the review asked for."""

    turns: int = 0
    episodes: int = 0
    wrong_tool: int = 0
    unnecessary_clarifications: int = 0
    lost_context: int = 0
    unsafe: int = 0
    failures: list[Failure] = field(default_factory=list)
    # Turns that ran a query and said nothing about which one. The corpus is a measurement of its
    # own coverage as well as of the service: a turn nobody wrote an expectation for is a turn
    # where the routing is checked and the plan behind it is not.
    unasserted: list[str] = field(default_factory=list)
    # Turns whose query carries a filter the expectation does not mention. The other half of the
    # same idea, and the answer to a question the exclusions raised: asserting a field on every
    # turn is what found the fifteen filler merchants, but six fields already make a line nobody
    # reads before appending to it. So the rule is narrower than the habit — say it where it is
    # doing something — and it cannot be forgotten, because the corpus checks rather than a person.
    unstated: list[str] = field(default_factory=list)
    # The sentences this service declined to read itself. Every fix that stops a vocabulary
    # guessing adds one, so this list grows as the deterministic path gets *better* — and what it
    # hands over is, by definition, exactly what the planner has to be good at.
    handed_over: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def rate(self, count: int) -> float:
        return 0.0 if not self.turns else round(100 * count / self.turns, 2)


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
                    agents=tuple(raw.get("agents", ())),
                    turns=tuple(Turn(**{**turn, "say": str(turn["say"])}) for turn in raw["turns"]),
                )
            )
    return episodes


def executed(query: FinancialPlan | TopMovements) -> dict[str, Any]:
    """The query that ran, in the vocabulary an episode writes.

    Named fields rather than a repr, so an expectation states the one thing it is about and a plan
    growing a field does not rewrite every episode. `window` folds the named period and the explicit
    range together because a plan carries exactly one of them — the same reason `plan_merger` treats
    them as one field.
    """
    ran: dict[str, Any] = {
        "window": _window(query),
        "direction": query.direction.value,
        "categories": sorted(query.categories),
        "merchants": sorted(query.merchants),
        # The negative half, and it was missing. "¿Y sin contar el alquiler?", "Ahora sin
        # Mercadona", "Quita ese filtro", "Déjalo como estaba antes" — a whole episode exists for
        # these and could assert nothing about the only field they change, so the exclusion being
        # added, kept or undone was invisible to every check in this corpus.
        "excluded_categories": sorted(query.excluded_categories),
        "excluded_merchants": sorted(query.excluded_merchants),
        # Which accounts, by the name the holder sees rather than by uuid. The episode called
        # "an account subset survives period and direction changes" ran "solo la principal",
        # "ahora solo la hucha" and "solo cuenta corriente y hucha, no el fondo" and asserted the
        # same fields for all three, none of which is the subset.
        "accounts": sorted(_named(query)),
        "counterparties": sorted(getattr(query, "counterparties", ())),
        # Movement between the holder's own accounts is a direction of its own — "¿cuánto he movido
        # a la hucha?" is neither spending nor income — and two episodes turn on it.
        "internal_only": getattr(query, "internal_only", False),
        "include_internal": getattr(query, "include_internal", False),
        # An id-selected plan -- «esos movimientos», the evidence turn after a drill-down -- names
        # its rows by identifier rather than by window or category, and by count rather than by the
        # identifiers themselves (M5, fix wave): the ids are `uuid.UUID(int=day)`, identical every
        # run, not invented fresh -- the real reason to assert only the count is that the ids are
        # this fixture's own, not the episode's to know or care about.
        "transaction_ids": len(getattr(query, "transaction_ids", ())),
    }
    if isinstance(query, TopMovements):
        return {**ran, "rank": query.rank}
    return {**ran, "group_by": query.group_by.value, "metric": query.metric.value}


_ACCOUNT_NAMES = {holding.account_id: holding.display_name for holding in HOLDINGS}


def _named(query: FinancialPlan | TopMovements) -> list[str]:
    """The accounts a query is restricted to, by the name the holder sees.

    A ranking restricts by one account and an aggregate by a set, so both are read through
    `getattr` rather than by branching on the type: what an episode means by "which accounts" is
    the same question of either.
    """
    chosen = list(getattr(query, "only_account_ids", ()))
    single = getattr(query, "only_account_id", None)
    if single is not None and single not in chosen:
        chosen.append(single)
    return [_ACCOUNT_NAMES.get(account, str(account)) for account in chosen]


def _window(query: FinancialPlan | TopMovements) -> str:
    if query.date_range is not None:
        return f"{query.date_range.start.isoformat()}..{query.date_range.end.isoformat()}"
    if query.period is None:
        return "NONE"
    named = query.period.period.value
    return f"{named}:{query.period.n}" if query.period.n else named


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

# Named by module since A2, which is also the answer to "where do I go and read this one": the
# readers are in `queries/readers/` and only `_account_activity_request` is still the handler's own.
_RECOGNISERS: tuple[tuple[str, Any], ...] = (
    ("ratio", subjects._ratio_request),
    ("gambling", subjects._gambling_plan),
    ("internal_movement", subjects._internal_movement_request),
    ("account_activity", handler._account_activity_request),
    ("salary_history", comparisons._salary_history_plans),
    ("period_comparison", comparisons._period_comparison_plans),
    ("previous_period_comparison", comparisons._comparison_with_previous_period),
    ("direct_period_comparison", comparisons._direct_period_comparison),
    ("named_window_comparison", comparisons._comparison_with_a_named_window),
    ("account_follow_up", follow_up._account_follow_up_plan),
    ("contextual_follow_up", follow_up.contextual_follow_up_plan),
    ("multiple_movement_ranking", rankings._multiple_movement_ranking),
    ("group_ranking", rankings._group_ranking_plan),
    ("natural_period", breakdowns._natural_period_aggregate_plan),
    ("deterministic_breakdown", breakdowns._deterministic_breakdown_plan),
    ("contextual_ranking", rankings._contextual_ranking_plan),
    ("cumulative", metrics._cumulative_plan),
    ("recurrence", metrics._recurrence_plan),
    ("frequency", metrics._frequency_plan),
    ("average", metrics._average_plan),
)


def _reading(value: Any) -> str:
    """One reading as a line that is both comparable and readable.

    Comparable, because two recognisers producing the same query are not a conflict. Readable,
    because the pairs are the deliverable of this measurement and a page of dataclass reprs is not
    something anybody reads before deciding what to do about them.
    """
    if isinstance(value, FinancialPlan | TopMovements | FinancialPlanDraft):
        # A draft carries the same fields, minus a window, so the same summary reads it. Named by
        # its type so a clarification is never mistaken for a query in the report.
        ran = executed(value)  # type: ignore[arg-type]
        parts = [str(ran.pop("window")), str(ran.pop("direction"))]
        parts += [f"{key}={ran[key]}" for key in sorted(ran) if ran[key] not in ((), [], "NONE", 0)]
        kind = {FinancialPlan: "plan", TopMovements: "ranking"}.get(type(value), "draft")
        return f"{kind} {' '.join(parts)}"
    if isinstance(value, ComparisonPlan):
        return f"comparison {_window(value.earlier)} vs {_window(value.latest)}"
    if isinstance(value, RatioPlan):
        return f"ratio {value.kind.value} {_reading(value.part)} of {_reading(value.whole)}"
    if isinstance(value, RatioDraft):
        return f"ratio-draft {value.kind.value}"
    if isinstance(value, tuple):
        return " + ".join(_reading(item) for item in value)
    return f"{type(value).__name__} {value!r}"


def claimants(
    sentence: str, state: dict[str, Any] | None, *, today: date = TODAY
) -> dict[str, str]:
    """Every recogniser that would answer this turn, and what each would run.

    Called with the conversation as it was *before* the turn, which is the only state the cascade
    sees. A recogniser that raises is recorded as raising rather than ending the run: on this path
    it is unreachable, so an exception is a finding about the tie and not a failure of the turn.
    """
    conversation = conversation_mapper.to_domain(
        ConversationPayload.model_validate(state) if state else None
    )
    query = AskFinancialQuestionQuery(
        sentence=sentence, account_id=ACCOUNT, today=today, conversation=conversation
    )
    claims: dict[str, str] = {}
    for name, recognise in _RECOGNISERS:
        try:
            reading = recognise(query)
        except Exception as raised:  # noqa: BLE001 - the point is to report it, not to handle it
            claims[name] = f"raised {type(raised).__name__}"
            continue
        if reading is not None:
            claims[name] = _reading(reading)
    return claims


@dataclass
class _Answered:
    """One turn's answer, however it travelled the wire.

    TEXT posts to `/v1/questions` and its body IS the answer; VOICE posts to
    `/v1/questions/stream` and the answer is the last SSE frame, minus the `type` key that frame
    alone carries. Every reader downstream of a post -- the three sites this replaces -- only ever
    wanted the answer, so `.json()` hands back that same shape on both channels and `frames` is
    where a VOICE-only check goes looking.
    """

    status_code: int
    frames: tuple[dict[str, Any], ...]
    _body: dict[str, Any] | None
    sentence: str = ""

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise AssertionError(
                f"json() called on a turn that never answered: {self.sentence!r} "
                f"(status {self.status_code})"
            )
        return self._body


def _post_turn(stack: Stack, turn: Turn, conversation: dict[str, Any] | None) -> _Answered:
    """Post one turn on its own channel, and read its answer back the same way regardless.

    A VOICE turn posts `channel: "VOICE"` to the streaming route and parses its SSE text the way
    `test_question_routing.py`'s own `events_of` does -- replicated rather than imported, because a
    support module reaching into a test module would make the test the thing under test. A TEXT
    turn is byte-identical to every call this helper replaces.
    """
    if turn.channel == "VOICE":
        response = stack.client.post(
            "/v1/questions/stream",
            json={"question": turn.say, "conversation": conversation, "channel": "VOICE"},
        )
        # D-4.4-10: mirror the TEXT branch below -- frames are parsed only once the request
        # actually answered, not on the strength of hoping the body is SSE-shaped.
        if response.status_code != 200:
            return _Answered(response.status_code, (), None, turn.say)
        frames = tuple(
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        body = {k: v for k, v in frames[-1].items() if k != "type"} if frames else None
        return _Answered(response.status_code, frames, body, turn.say)
    response = stack.client.post(
        "/v1/questions", json={"question": turn.say, "conversation": conversation}
    )
    body = response.json() if response.status_code == 200 else None
    return _Answered(response.status_code, (), body, turn.say)


def contested(episode: Episode) -> Iterator[tuple[int, str, dict[str, str]]]:
    """The turns of one episode where two recognisers want to run different queries.

    Two firing with the *same* reading is not a conflict: whichever wins, the same question is
    asked. Precedence is only load-bearing where the readings differ, and those are the turns a
    reordering would silently change the answer to.
    """
    stack = a_stack_for(episode)
    state: dict[str, Any] | None = opening(episode)
    for number, turn in enumerate(episode.turns, start=1):
        claims = claimants(turn.say, state)
        if len(set(claims.values())) > 1:
            yield number, turn.say, claims
        stack.ledger.groups_for_next_turn = _groups_from(turn)
        stack.ledger.movements_for_next_turn = _movements_from(turn)
        answered = _post_turn(stack, turn, state)
        stack.ledger.groups_for_next_turn = None
        stack.ledger.movements_for_next_turn = None
        if answered.status_code != 200:
            return
        state = answered.json()["conversation"]


def walk(episode: Episode, interpreter: object | None = None) -> Iterator[Failure]:
    """One episode, turn by turn, exactly as a browser would have it.

    `interpreter` swaps what reads a turn, and it is how a model gets measured on the question that
    matters. Scoring an interpreter against `intent_router` measures whether it guesses our enum;
    scoring it here measures whether the turn reached the right *place*, which is what the corpus
    wrote down and what a user experiences. The two are not the same, and the gap is not small:
    sixty-one turns are answered with a balance while the router calls them FINANCIAL_QUERY,
    because the balance is decided downstream in `_narrows_the_balance`. A model reading one of
    those as ACCOUNT_BALANCE is right about the destination and wrong about our plumbing, and only
    one of those two is worth counting.
    """
    for number, turn, answer, reached_model, ran, frames in replay(episode, interpreter):
        if answer is None:
            yield Failure(episode.name, number, turn.say, "HTTP error")
            return
        for complaint in _complaints(
            turn, answer, reached_model=reached_model, ran=ran, frames=frames
        ):
            yield Failure(episode.name, number, turn.say, complaint)


def replay(
    episode: Episode, interpreter: object | None = None
) -> Iterator[
    tuple[int, Turn, dict[str, Any] | None, bool, tuple[Any, ...], tuple[dict[str, Any], ...]]
]:
    """Each turn with what it reached: the answer, whether a model was asked, what the ledger ran,
    and the SSE frames a VOICE turn produced (empty for a TEXT turn).

    Separate from `walk` because two things read a replay and only one of them is looking for
    failures. Counting the turns that ran a query without saying which one is not a complaint about
    any episode — it is a fact about the corpus — and running the conversations twice to learn it
    would double the slowest test in the suite.
    """
    stack = a_stack_for(episode)
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = opening(episode)

    for number, turn in enumerate(episode.turns, start=1):
        before = len(stack.planner.asked)
        ran_before = len(stack.ledger.ran)
        stack.ledger.groups_for_next_turn = _groups_from(turn)
        stack.ledger.movements_for_next_turn = _movements_from(turn)
        answered = _post_turn(stack, turn, conversation)
        stack.ledger.groups_for_next_turn = None
        stack.ledger.movements_for_next_turn = None
        if answered.status_code != 200:
            yield number, turn, None, False, (), ()
            return
        answer = answered.json()
        conversation = answer["conversation"]
        # This turn's queries only. A turn asserting `ran` is asking about itself, and the ledger
        # holds the whole conversation.
        yield (
            number,
            turn,
            answer,
            len(stack.planner.asked) > before,
            tuple(stack.ledger.ran[ran_before:]),
            answered.frames,
        )


def _complaints(
    turn: Turn,
    answer: dict[str, Any],
    *,
    reached_model: bool,
    ran: tuple[FinancialPlan | TopMovements, ...] = (),
    frames: tuple[dict[str, Any], ...] = (),
) -> Iterator[str]:
    tool = tool_of(answer)
    workflow = (answer.get("conversation") or {}).get("workflow")
    text = answer["text"]

    if turn.spoken is not None and isinstance(answer.get("spoken"), str) != turn.spoken:
        yield (
            f"the answer should {'' if turn.spoken else 'not '}carry a spoken summary, "
            f"and {'does not' if turn.spoken else 'does'}"
        )
    if turn.spoken_says is not None:
        spoken_line = answer.get("spoken")
        if not isinstance(spoken_line, str) or turn.spoken_says.lower() not in spoken_line.lower():
            yield (
                f"turn {turn.say!r}'s spoken line does not say {turn.spoken_says!r}: "
                f"{(spoken_line or '')[:90]!r}"
            )
    has_explanation = isinstance(answer.get("explanation"), dict)
    if turn.explanation is not None and has_explanation != turn.explanation:
        yield (
            f"the answer should {'' if turn.explanation else 'not '}carry an explanation, "
            f"and {'does not' if turn.explanation else 'does'}"
        )
    # I-4 (D-4.4-9, ruling R16): every VOICE turn's *last* frame is the answer, always -- that
    # much `ask_streaming` guarantees regardless of which branch answered. A leading `stage`,
    # though, is only true for a turn `asking.stream` actually plans -- `tool_of`'s `QUERY`
    # fallthrough, the only category built by running a plan through `_steps`. Every other route
    # answers directly, with nothing to stage: `ACCOUNT_BALANCE`/`BALANCE` (`_balance` returns
    # before any `Stage` is yielded), the `VISUAL_*` outcomes (`ask_streaming`'s `visual is not
    # None` branch yields only `{"type": "answer", ...}`), the spoken categories -- COURTESY,
    # IDENTITY, CATEGORIES, EXPLANATION, GUIDANCE, CONTACTS, CARDS -- and payment turns answered
    # through `_confirmation_events`/`_open_payment_events` (no invented planning stage, by their
    # own docstrings), and a compound read (`_ANSWERED_WITHOUT_A_STAGE`) -- `QUERY` by
    # fallthrough, one answer frame. A `PAYMENT`-routed turn reaching `_payment_events` instead
    # *does* stage ("reading"), so the rule states a minimum, not an exact shape, for anything
    # but `QUERY`.
    if turn.channel == "VOICE" and (not frames or frames[-1]["type"] != "answer"):
        yield f"a VOICE turn should close with an answer frame, and closes with {frames[-1:]!r}"
    if (
        turn.channel == "VOICE"
        and tool == QUERY
        and answer.get("outcome") not in _ANSWERED_WITHOUT_A_STAGE
        and (not frames or frames[0]["type"] != "stage")
    ):
        yield (
            "a VOICE turn answering a query should open with a stage frame, and opens with "
            f"{frames[:1]!r}"
        )
    if turn.route and tool != turn.route:
        yield f"{turn.route} was the right place, {tool} answered"
    if turn.no_model and reached_model:
        yield "it reached the model, and this turn is answered from a vocabulary"
    for phrase in [turn.says] if isinstance(turn.says, str) else turn.says or []:
        if phrase.lower() not in text.lower():
            yield f"the answer does not say {phrase!r}: {text[:90]!r}"
    if turn.not_says and turn.not_says.lower() in text.lower():
        yield f"the answer says {turn.not_says!r}, and must not"
    if turn.keeps and workflow != turn.keeps:
        yield f"{turn.keeps} had to survive this turn, and {workflow or 'nothing'} did"
    if turn.ends and workflow is not None:
        yield f"nothing should be open and {workflow} is"
    if turn.awaiting and (answer.get("conversation") or {}).get("awaiting") != turn.awaiting:
        got = (answer.get("conversation") or {}).get("awaiting")
        yield f"it should be waiting for {turn.awaiting} and waits for {got or 'nothing'}"
    if turn.total is not None and answer.get("total_minor") != turn.total:
        yield f"the figure should be {turn.total} and is {answer.get('total_minor')}"
    if turn.banking_calls is not None and len(ran) != turn.banking_calls:
        yield (
            f"the turn should call the ledger {turn.banking_calls} time(s) and called it {len(ran)}"
        )
    if turn.movements is not None:
        shown = len(answer.get("movements") or [])
        if shown != turn.movements:
            yield f"the answer should show {turn.movements} movement(s) and shows {shown}"
    if turn.derived_from is not None:
        contexts = (answer.get("conversation") or {}).get("visual_contexts") or []
        derived = bool(contexts) and contexts[-1].get("derived_from") is not None
        if derived != turn.derived_from:
            yield (
                f"the chart should {'' if turn.derived_from else 'not '}derive from a previous "
                f"one, and {'does' if derived else 'does not'}"
            )
    if turn.chart is not None:
        yield from _chart_complaints(turn.chart, answer)
    if turn.explanation_says is not None:
        explanation = answer.get("explanation")
        explanation_text = explanation.get("text") if isinstance(explanation, dict) else None
        if (
            not isinstance(explanation_text, str)
            or turn.explanation_says.lower() not in explanation_text.lower()
        ):
            yield (
                f"the explanation does not say {turn.explanation_says!r}: "
                f"{(explanation_text or '')[:90]!r}"
            )
    if turn.chips is not None:
        offered = {chip.lower() for chip in (answer.get("suggestions") or [])}
        missing = [chip for chip in turn.chips if chip.lower() not in offered]
        if missing:
            yield (
                f"the suggestions should include {missing}, and offer {answer.get('suggestions')!r}"
            )
    if turn.no_chips and answer.get("suggestions"):
        yield f"there should be no suggestions, and there are {answer['suggestions']!r}"
    if turn.chart_text_agrees is not None:
        yield from _chart_text_complaints(turn.chart_text_agrees, answer, ran)
    yield from _query_complaints(turn, ran)


def _chart_text_complaints(
    expected: bool, answer: dict[str, Any], ran: tuple[FinancialPlan | TopMovements, ...]
) -> Iterator[str]:
    """B-5: the chart's own dimension against the plan that actually drew it.

    Neither side is read from prose. The plan side is `ran[-1].group_by` -- the last
    `FinancialPlan` this turn ran, the same evidence `_query_complaints` already trusts over
    `says:` — and the chart side is `VisualContext.group_by`, sealed onto the wire the same
    turn (Task 2, Wave 3 hardening). A turn with no chart, or whose plan is not a `FinancialPlan`
    (a comparison, a ratio), has nothing to compare and counts as disagreeing -- `chart_text_agrees:
    true` on such a turn is the harness telling the episode it named the wrong turn.
    """
    conversation = conversation_mapper.to_domain(
        ConversationPayload.model_validate(answer["conversation"])
    )
    plan = next((item for item in reversed(ran) if isinstance(item, FinancialPlan)), None)
    agrees = (
        plan is not None
        and bool(conversation.visual_contexts)
        and conversation.visual_contexts[-1].group_by == plan.group_by
    )
    if agrees != expected:
        chart_group = (
            conversation.visual_contexts[-1].group_by if conversation.visual_contexts else None
        )
        plan_group = plan.group_by if plan is not None else None
        yield (
            f"the chart's group_by ({chart_group}) should "
            f"{'' if expected else 'not '}agree with the plan's ({plan_group}), "
            f"and {'does' if agrees else 'does not'}"
        )


def _chart_complaints(expected: dict[str, Any], answer: dict[str, Any]) -> Iterator[str]:
    """`turn.chart` against the chart the conversation actually remembers.

    Read off `conversation.visual_contexts[-1].spec` -- the domain `ChartSpec` the service carries
    forward -- and not `answer["visualization"]["spec"]`, the presented Vega-Lite: the assertion is
    on the same object a later turn's «el gráfico anterior» resolves against.

    The shape a `chart:` expectation names a subset of is the domain's own
    `visual_plan.canonical(spec)` -- not a second implementation of it. A harness-local copy used
    to stand here, and it disagreed with the domain on one thing: `present()` (`vega_lite.py`)
    always lets an axis's own per-encoding sort win over the chart-wide `sort`/`sort_field` on the
    wire, so the domain's `canonical()` nulls the latter whenever an axis already carries its own
    sort -- a rule its own regression test pins down (`test_visual_plan.py`,
    `test_canonical_ignores_a_chart_wide_sort_the_presenter_would_drop`). The copy that used to
    live here returned `spec.sort`/`spec.sort_field` unconditionally, so a `chart: {sort: ...,
    sort_field: ...}` expectation could have validated a value dead on the wire -- exactly the
    shape the shipped episode's restyle turn produces (a chart-wide sort equal to, and shadowed
    by, the y axis's own sort).
    """
    conversation = conversation_mapper.to_domain(
        ConversationPayload.model_validate(answer["conversation"])
    )
    if not conversation.visual_contexts:
        yield f"the turn expects a chart {expected}, and none was remembered"
        return
    mark, raw_encodings, stack, sort, sort_field, limit = canonical(
        conversation.visual_contexts[-1].spec
    )
    # The domain's own encodings: sorted (order is not a decision there either) and marking "no
    # per-encoding sort" with `""` rather than `None`. Normalised back to `None` here so the rest
    # of this function reads exactly as an expectation names it, and an axis with no sort at all
    # still fails an `axis_sort` expectation rather than matching one that also happens to read
    # `""`.
    encodings = tuple(
        (channel, field, kind, mode or None, sort_name or None)
        for channel, field, kind, mode, sort_name in raw_encodings
    )
    if "mark" in expected and expected["mark"] != mark:
        yield f"the chart's mark should be {expected['mark']!r} and is {mark!r}"
    if "stack" in expected and expected["stack"] != stack:
        yield f"the chart's stack should be {expected['stack']!r} and is {stack!r}"
    if "sort" in expected and expected["sort"] != sort:
        yield f"the chart's sort should be {expected['sort']!r} and is {sort!r}"
    if "sort_field" in expected and expected["sort_field"] != sort_field:
        yield f"the chart's sort_field should be {expected['sort_field']!r} and is {sort_field!r}"
    if "limit" in expected and expected["limit"] != limit:
        yield f"the chart's limit should be {expected['limit']!r} and is {limit!r}"
    if "channels" in expected:
        wanted = {tuple(triple) for triple in expected["channels"]}
        seen = {(chan, fld, kind) for chan, fld, kind, _, _ in encodings}
        if seen != wanted:
            yield f"the chart's channels should be {sorted(wanted)} and are {sorted(seen)}"
    if "axis_sort" in expected:
        axis_channel, axis_mode, axis_field = expected["axis_sort"]
        match = next((item for item in encodings if item[0] == axis_channel), None)
        got = (match[3], match[4]) if match is not None else None
        if got != (axis_mode, axis_field):
            yield (
                f"the {axis_channel} axis should sort {axis_mode} by {axis_field!r} and sorts {got}"
            )


def _query_complaints(turn: Turn, ran: tuple[FinancialPlan | TopMovements, ...]) -> Iterator[str]:
    """What the turn asked the ledger, against what the episode says it should have.

    One mapping means the last query of the turn, because a derived answer runs two — a ratio, a
    comparison — and the one an episode usually means by "this question" is the narrow side.

    A list means every query, in the order they ran. That is what a comparison turn needs: with one
    mapping, "compara julio con junio" asserts July and says nothing about June, so a comparison
    that dropped an operand, ran the same window twice or reversed the pair passed. Both operands
    are the answer there, and half of it is not a weaker check — it is no check at all for the
    half that goes missing.
    """
    if not turn.ran:
        return
    if not ran:
        yield f"the query ran nothing, and the turn expects {turn.ran}"
        return
    expectations = turn.ran if isinstance(turn.ran, list) else [turn.ran]
    queries = ran if isinstance(turn.ran, list) else ran[-1:]
    if len(queries) != len(expectations):
        yield f"the query ran {len(queries)} queries, and the turn expects {len(expectations)}"
        return
    for position, (expected, query) in enumerate(zip(expectations, queries, strict=True)):
        actual = executed(query)
        where = f" (query {position + 1})" if len(expectations) > 1 else ""
        for field_name, wanted_raw in expected.items():
            if field_name not in actual:
                yield f"the query ran no field called {field_name!r}{where}"
                continue
            seen = actual[field_name]
            wanted = sorted(wanted_raw) if isinstance(seen, list) else wanted_raw
            if seen != wanted:
                yield (
                    f"the query ran {field_name}={seen!r}, and the turn expects {wanted!r}{where}"
                )


# Fields whose non-default value changes what a figure *means* rather than how much of it there is:
# a subset of accounts, movement between the holder's own accounts, a named counterparty. A turn
# whose plan carries one and whose expectation is silent about it has the same hole the exclusions
# had — the episode written for the distinction cannot see it.
#
# Asserted where they are doing something rather than everywhere. Six fields on 364 turns is
# already a line nobody reads before appending to it, and the value of `merchants: []` on every
# turn was that a shop filter appearing from nowhere is a defect; an account subset appearing from
# nowhere is caught by this instead, and costs no line at all where it never happens.
_LOAD_BEARING = ("accounts", "counterparties", "internal_only", "include_internal")


def _unstated(turn: Turn, ran: tuple[FinancialPlan | TopMovements, ...]) -> Iterator[str]:
    """Filters this turn's queries carry that its expectation does not mention."""
    if not turn.ran or not ran:
        return
    expectations = turn.ran if isinstance(turn.ran, list) else [turn.ran]
    queries = ran if isinstance(turn.ran, list) else ran[-1:]
    for expected, query in zip(expectations, queries, strict=False):
        actual = executed(query)
        for field_name in _LOAD_BEARING:
            if actual.get(field_name) and field_name not in expected:
                yield field_name


def run(episodes: list[Episode], interpreter: object | None = None) -> Report:
    """Every episode, with the review's metrics counted as it goes.

    With an `interpreter`, this is the honest version of the migration measurement: install the
    candidate where the deployment installs one and count the turns that end up in the wrong
    place. The number it produces is about the product — "N of 879 turns were answered by the
    wrong tool" — rather than about how well a model predicts an internal enum.
    """
    report = Report(episodes=len(episodes))
    for episode in episodes:
        report.turns += len(episode.turns)
        for number, turn, answer, reached_model, ran, frames in replay(episode, interpreter):
            if answer is None:
                report.failures.append(Failure(episode.name, number, turn.say, "HTTP error"))
                break
            if reached_model:
                report.handed_over.append(turn.say)
            if ran and not turn.ran:
                report.unasserted.append(f"{episode.name} · turn {number} · {turn.say!r}")
            for field_name in _unstated(turn, ran):
                report.unstated.append(
                    f"{episode.name} · turn {number} · {turn.say!r} · {field_name}"
                )
            for complaint in _complaints(
                turn, answer, reached_model=reached_model, ran=ran, frames=frames
            ):
                report.failures.append(Failure(episode.name, number, turn.say, complaint))
                if "answered" in complaint:
                    report.wrong_tool += 1
                # Both halves of losing context, and the second one is why this metric read 0.0 %
                # on a corpus full of ellipsis: a workflow that failed to survive was counted, and
                # a window, category or direction that failed to survive was invisible.
                if "had to survive" in complaint or "the query ran" in complaint:
                    report.lost_context += 1
                if "reached the model" in complaint:
                    report.unnecessary_clarifications += 1
                if "PAYMENT answered" in complaint or "TRANSFER answered" in complaint:
                    report.unsafe += 1
    return report


# --- the corpus as a migration harness -----------------------------------------------------
#
# The reason this file can measure an interpreter at all: 879 turns whose right answer is already
# written down. A shadow comparison in production can only ever say *how often* two decisions
# differ, because the sentence may not be logged — this service reads a person's bank account and
# nothing they typed reaches a log file. Here the sentences are ours, so a disagreement can be read.


@dataclass
class Agreement:
    """How one interpreter did against the corpus, per intent and overall."""

    turns: int = 0
    agreed: int = 0
    # Which intent was expected and which was read, counted. The pairs are what a migration is
    # steered by: "it calls a balance a query" is actionable and "92 %" is not.
    confusions: dict[tuple[str, str], int] = field(default_factory=dict)
    # A few of the sentences behind each pair. A count says a disagreement exists; the sentence is
    # what says whose fault it is — five models reading one transfer as a payment is a prompt to
    # fix, and the same count spread over five different sentences is a model to reject.
    #
    # These are ours. The corpus is written in this repository and read from it, so printing one is
    # printing a fixture — it is not the rule about a person's typing reaching a log, which is why
    # the shadow in production counts and this names.
    examples: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return 0.0 if not self.turns else round(100 * self.agreed / self.turns, 2)

    def worst(self, limit: int = 8) -> list[tuple[tuple[str, str], int]]:
        return sorted(self.confusions.items(), key=lambda pair: -pair[1])[:limit]

    def saw(self, expected: str, read: str, sentence: str, *, keep: int = 4) -> None:
        key = (expected, read)
        self.confusions[key] = self.confusions.get(key, 0) + 1
        seen = self.examples.setdefault(key, [])
        # Capped, because a full run is 879 turns and a report nobody reads to the end is a report.
        # The first few are enough to tell one bad rule from a scattering of bad guesses.
        if len(seen) < keep and sentence not in seen:
            seen.append(sentence)


class _Scored:
    """Both interpreters, in the seat the service asks. Answers with the reference.

    Installed as the deployment's interpreter, so the *route* decides when a reading is needed and
    this only records what happened. That is the whole point of doing it this way rather than
    walking the sentences: a payment under way answers "sí", "20 euros" and "era para Javi Nuñez"
    without classifying them at all, and scoring those turns measured a model on questions the
    service never asks it.

    It cost 47 of 49 "invented payments" in the first full run — the model noticing, correctly,
    that a sentence in the middle of a Bizum is about the Bizum, counted as it volunteering to move
    money. A measurement that makes a model look reckless for being right is worse than no
    measurement.

    The reference is what it returns, so the conversation evolves exactly as it does in production
    and the state each turn is scored against is the real one.
    """

    def __init__(
        self,
        reference: Any,
        candidate: Any,
        report: Agreement,
        only: frozenset[Intent] = frozenset(),
    ) -> None:
        self._reference = reference
        self._candidate = candidate
        self._report = report
        self._only = only

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        expected = await self._reference.interpret(sentence, conversation=conversation)
        if self._only and expected not in self._only:
            # Not asked, and not counted either. The turn still replays, because the state is half
            # of what decides the *next* one — a filtered run that skipped the replay would be
            # measuring sentences in isolation, which is the thing this harness exists not to do.
            return expected
        read = await self._candidate.interpret(sentence, conversation=conversation)
        self._report.turns += 1
        if read is expected:
            self._report.agreed += 1
        else:
            self._report.saw(expected.value, read.value, sentence)
        return expected  # type: ignore[no-any-return]


async def compare(
    episodes: list[Episode],
    candidate: object,
    authority: object | None = None,
    only: frozenset[Intent] = frozenset(),
) -> Agreement:
    """Replay every episode through the service and score `candidate` against `authority`.

    The conversation is rebuilt by replaying rather than read from the file, because the state is
    half of what decides a turn: scoring an interpreter on the sentences alone would be scoring it
    on a question nobody can answer. That makes this slower than reading a file, and it is the only
    version of it that measures the right thing.

    `report.turns` is therefore the turns that were *classified*, not the turns in the corpus. The
    gap between the two is not loss — it is the part of the conversation the deterministic state
    machine owns, which no interpreter is consulted about and none can get wrong.

    `only` narrows it further, to the turns whose *reference* answer is one of the given intents.
    It exists because a hosted endpoint charges per turn and rate-limits by the minute, so
    "re-measure the one bucket I just changed" is the difference between a fifteen-call check and
    a twelve-minute run. Everything still replays; only the asking is skipped. A report produced
    with this set describes that slice and nothing else, and printing it as an overall rate would
    be lying with a real number.
    """
    from finguard_orchestrator.domain.services.turn_interpreters import Deterministic

    reference = authority or Deterministic()
    report = Agreement()
    for episode in episodes:
        stack = a_stack_for(episode)
        app.dependency_overrides[turn_interpreter] = lambda: _Scored(
            reference, candidate, report, only
        )
        conversation: dict[str, Any] | None = opening(episode)
        for turn in episode.turns:
            stack.ledger.groups_for_next_turn = _groups_from(turn)
            stack.ledger.movements_for_next_turn = _movements_from(turn)
            answered = _post_turn(stack, turn, conversation)
            stack.ledger.groups_for_next_turn = None
            stack.ledger.movements_for_next_turn = None
            if answered.status_code != 200:
                break
            conversation = answered.json()["conversation"]
    return report
