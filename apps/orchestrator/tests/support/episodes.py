"""Whole conversations, driven through HTTP, measured rather than merely asserted.

The planning benchmark measures one sentence at a time and the conversation dataset beside it needs
a live model. Neither answers the question this file is about: **over a long conversation, does each
turn reach the right part of the service?**

Every defect reported from the demo so far has been that and only that. Not a wrong figure — a
question routed to the payment path, a balance sent to the planner, a reply to an open question read
as an order. The figures were right every time; the *tool* was wrong. So that is what an episode
asserts: which of eleven places handled each turn, and what the turn left behind.

**No model, and that is the design.** The planner is stubbed to answer any sentence with a plan, so
`QUERY` means "it reached the planner" and nothing more. Everything else is a path this service
decides on its own — a vocabulary, a router, a state — and those are exactly the decisions that must
not vary with which model a deployment installed. A corpus that needed one could not run in CI, and
this one runs in a second.

What each turn may say:

    route        which of the eleven handled it. The whole point of the file
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
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

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
)


# --- the eleven places a turn can be handled ---------------------------------------------
#
# Named here rather than derived at the assertion, because "which tool answered" is the measurement
# and a measurement whose definition lives in three branches is three definitions.

BALANCE = "BALANCE"
CONTACTS_LIST = "CONTACTS"
IDENTITY = "IDENTITY"
CATEGORIES = "CATEGORIES"
EXPLANATION = "EXPLANATION"
GUIDANCE = "GUIDANCE"
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
    """Which of the eleven answered this turn.

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

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(
            currency="EUR",
            total_minor=46_556,
            movement_count=3,
            groups=(Group("total", 46_556, 3),),
        )

    async def ranked(self, ranking: object, **_: object) -> Movement:
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

    async def read(self, sentence: str, **_: object) -> PaymentReading:
        said = spanish.folded(sentence)
        named = next(
            (c.display_name for c in CONTACTS if spanish.folded(c.display_name) in said), None
        ) or next((first for first in ("Marta", "Javi") if first.lower() in said), None)
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
        contacts=Contacts(),  # type: ignore[arg-type]
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


def walk(episode: Episode) -> Iterator[Failure]:
    """One episode, turn by turn, exactly as a browser would have it."""
    stack = a_stack()
    conversation: dict[str, Any] | None = None

    for number, turn in enumerate(episode.turns, start=1):
        before = len(stack.planner.asked)
        response = stack.client.post(
            "/v1/questions", json={"question": turn.say, "conversation": conversation}
        )
        if response.status_code != 200:
            yield Failure(episode.name, number, turn.say, f"HTTP {response.status_code}")
            return
        answer = response.json()
        conversation = answer["conversation"]

        for complaint in _complaints(turn, answer, reached_model=len(stack.planner.asked) > before):
            yield Failure(episode.name, number, turn.say, complaint)


def _complaints(turn: Turn, answer: dict[str, Any], *, reached_model: bool) -> Iterator[str]:
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


def run(episodes: list[Episode]) -> Report:
    """Every episode, with the review's metrics counted as it goes."""
    report = Report(episodes=len(episodes))
    for episode in episodes:
        report.turns += len(episode.turns)
        for failure in walk(episode):
            report.failures.append(failure)
            if "answered" in failure.complaint:
                report.wrong_tool += 1
            if "had to survive" in failure.complaint:
                report.lost_context += 1
            if "reached the model" in failure.complaint:
                report.unnecessary_clarifications += 1
            if "PAYMENT answered" in failure.complaint or "TRANSFER answered" in failure.complaint:
                report.unsafe += 1
    return report
