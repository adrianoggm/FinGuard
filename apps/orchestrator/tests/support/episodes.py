"""Whole conversations, driven through HTTP, measured rather than merely asserted.

The planning benchmark measures one sentence at a time and the conversation dataset beside it needs
a live model. Neither answers the question this file is about: **over a long conversation, does each
turn reach the right part of the service?**

Every defect reported from the demo so far has been that and only that. Not a wrong figure — a
question routed to the payment path, a balance sent to the planner, a reply to an open question read
as an order. The figures were right every time; the *tool* was wrong. So that is what an episode
asserts: which of thirteen places handled each turn, and what the turn left behind.

**No model, and that is the design.** The planner is stubbed to answer any sentence with a plan, so
`QUERY` means "it reached the planner" and nothing more. Everything else is a path this service
decides on its own — a vocabulary, a router, a state — and those are exactly the decisions that must
not vary with which model a deployment installed. A corpus that needed one could not run in CI, and
this one runs in a second.

What each turn may say:

    route        which of the thirteen handled it. The whole point of the file
    says         a phrase the answer must contain
    not_says     a phrase it must not
    keeps        a workflow that has to still be open afterwards
    ends         nothing is left open
    awaiting     the slot the turn asked for
    no_model     this turn must not reach the planner at all
    total        the figure the answer leads with, in minor units

And what a whole episode may say:

    metrics      the ones this episode is meant to exercise, so a run can report per feature
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

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
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentHandler,
)
from finguard_orchestrator.application.queries.list_contacts import ContactRow
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
)
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import (
    category_words,
    explicit_facts,
    payment_words,
    spanish,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
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


# --- the thirteen places a turn can be handled ---------------------------------------------
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


def tool_of(answer: dict[str, Any]) -> str:
    """Which of the thirteen answered this turn.

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
    if outcome == PlanningOutcome.UNSUPPORTED.value:
        return REFUSED
    if outcome == PlanningOutcome.NEEDS_CLARIFICATION.value:
        # The one tie: a payment asking for an amount and a question asking for a period are the
        # same outcome, and the difference between them is everything.
        if workflow in _PAYMENT_WORKFLOWS:
            return PAYMENT
        return TRANSFER if workflow == "TRANSFER_IN_PROGRESS" else CLARIFY
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

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
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
        window = facts.window or RelativePeriod(period=Period.LAST_MONTH)
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


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []
        # Both kinds, in the order they ran, because "which query did this turn actually execute"
        # has to be one answer. An aggregate and a ranking are both queries a follow-up inherits
        # from, and keeping them in separate lists means the corpus cannot ask about the turn.
        self.ran: list[FinancialPlan | TopMovements] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
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
        return QueryAnswer(
            currency="EUR",
            total_minor=46_556,
            movement_count=3,
            groups=(Group(group_key, 46_556, 3),),
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
                else ()
            ),
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


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=HOLDINGS,
        )


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
        amount = payment_words.amount_in(sentence)

        if named is None:
            return PaymentReading.needs_clarification("¿A quién quieres enviárselo?")
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


@dataclass
class Stack:
    client: TestClient
    planner: AnyPlan
    ledger: Ledger
    bank: Bank


def a_stack() -> Stack:
    planner, ledger, bank = AnyPlan(), Ledger(), Bank()
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),  # type: ignore[arg-type]
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

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM}),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = True
    return Stack(TestClient(app), planner, ledger, bank)


# --- the corpus ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    say: str
    route: str | None = None
    says: str | None = None
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
    ran: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Episode:
    name: str
    why: str
    turns: tuple[Turn, ...]
    metrics: tuple[str, ...] = ()


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
    }
    if isinstance(query, TopMovements):
        return {**ran, "rank": query.rank}
    return {**ran, "group_by": query.group_by.value, "metric": query.metric.value}


def _window(query: FinancialPlan | TopMovements) -> str:
    if query.date_range is not None:
        return f"{query.date_range.start.isoformat()}..{query.date_range.end.isoformat()}"
    if query.period is None:
        return "NONE"
    named = query.period.period.value
    return f"{named}:{query.period.n}" if query.period.n else named


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
    stack = a_stack()
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = None

    for number, turn in enumerate(episode.turns, start=1):
        before = len(stack.planner.asked)
        ran_before = len(stack.ledger.ran)
        response = stack.client.post(
            "/v1/questions", json={"question": turn.say, "conversation": conversation}
        )
        if response.status_code != 200:
            yield Failure(episode.name, number, turn.say, f"HTTP {response.status_code}")
            return
        answer = response.json()
        conversation = answer["conversation"]

        for complaint in _complaints(
            turn,
            answer,
            reached_model=len(stack.planner.asked) > before,
            # This turn's queries only. A turn asserting `ran` is asking about itself, and the
            # ledger holds the whole conversation.
            ran=tuple(stack.ledger.ran[ran_before:]),
        ):
            yield Failure(episode.name, number, turn.say, complaint)


def _complaints(
    turn: Turn,
    answer: dict[str, Any],
    *,
    reached_model: bool,
    ran: tuple[FinancialPlan | TopMovements, ...] = (),
) -> Iterator[str]:
    tool = tool_of(answer)
    workflow = (answer.get("conversation") or {}).get("workflow")
    text = answer["text"]

    if turn.route and tool != turn.route:
        yield f"{turn.route} was the right place, {tool} answered"
    if turn.no_model and reached_model:
        yield "it reached the model, and this turn is answered from a vocabulary"
    if turn.says and turn.says.lower() not in text.lower():
        yield f"the answer does not say {turn.says!r}: {text[:90]!r}"
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
    yield from _query_complaints(turn, ran)


def _query_complaints(turn: Turn, ran: tuple[FinancialPlan | TopMovements, ...]) -> Iterator[str]:
    """What the turn asked the ledger, against what the episode says it should have.

    The last query of the turn, because a derived answer runs two — a ratio, a comparison — and the
    one an episode means by "this question" is the narrow side. An episode needing both operands
    would say so with two expectations, and none does yet.
    """
    if not turn.ran:
        return
    if not ran:
        yield f"the query ran nothing, and the turn expects {turn.ran}"
        return
    actual = executed(ran[-1])
    for field_name, expected in turn.ran.items():
        if field_name not in actual:
            yield f"the query ran no field called {field_name!r}"
            continue
        seen = actual[field_name]
        wanted = sorted(expected) if isinstance(seen, list) else expected
        if seen != wanted:
            yield f"the query ran {field_name}={seen!r}, and the turn expects {wanted!r}"


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
        for failure in walk(episode, interpreter):
            report.failures.append(failure)
            if "answered" in failure.complaint:
                report.wrong_tool += 1
            # Both halves of losing context, and the second one is why this metric read 0.0 % on a
            # corpus full of ellipsis: a workflow that failed to survive was counted, and a window,
            # category or direction that failed to survive was invisible.
            if "had to survive" in failure.complaint or "the query ran" in failure.complaint:
                report.lost_context += 1
            if "reached the model" in failure.complaint:
                report.unnecessary_clarifications += 1
            if "PAYMENT answered" in failure.complaint or "TRANSFER answered" in failure.complaint:
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
        stack = a_stack()
        app.dependency_overrides[turn_interpreter] = lambda: _Scored(
            reference, candidate, report, only
        )
        conversation: dict[str, Any] | None = None
        for turn in episode.turns:
            answered = stack.client.post(
                "/v1/questions", json={"question": turn.say, "conversation": conversation}
            )
            if answered.status_code != 200:
                break
            conversation = answered.json()["conversation"]
    return report
