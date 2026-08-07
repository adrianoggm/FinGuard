"""AskFinancialQuestionQuery · Result · Handler — the whole phase 3 path.

Sentence in, plan, figures from banking, sentence out. The handler's only real job is
deciding what each planning outcome means, and five of the six never reach the ledger:
the cheapest wrong plan is the one that was never run.

`today` arrives on the query rather than being read here, for the same reason it does
in banking: a handler that reads the clock cannot be replayed, and this one has to be —
the evaluation depends on it.
"""

import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Final
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
    Spoken,
)
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    ActiveWorkflow,
    Conversation,
    FinancialClarification,
    PaymentInProgress,
    PlannedQuery,
    Slot,
    TransferInProgress,
    UnsupportedCapability,
    workflow_of,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.card_directory import CardDirectoryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.query_planner import QueryPlannerPort
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import (
    account_words,
    answer_writer,
    capability_guard,
    category_words,
    courtesy,
    explainer,
    explicit_facts,
    intent_router,
    merchant_words,
    payment_words,
    plan_verifier,
    query_recovery,
    spanish,
    spanish_numbers,
    window_words,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.observability import account_ref

# The default when a handler is built without one, which is every test that does not care. The
# deployment's number is `banking_read_attempts` in configuration — this is operational policy, and
# a constant here was a policy nobody could change without a release.
DEFAULT_BANK_READ_ATTEMPTS = 2

# How far back the wallet's "spent" column looks when the chat asks. The wallet screen has its own
# setting; this is the fallback for a handler built without one.
DEFAULT_CARD_DAYS = 30

_RECENT_MOVEMENTS: Final = re.compile(
    r"\b(?:ultim[oa]s?|utlim[oa]s?|recientes?)\s+"
    r"(?P<count>\d{1,3}|[a-z]+(?:\s+y\s+[a-z]+)?)\s+"
    r"(?:movimientos?|operaciones?|cargos?|compras?)\b"
    r"|\b(?P<count_first>\d{1,3})\s+"
    r"(?:ultim[oa]s?|utlim[oa]s?|recientes?)\s+"
    r"(?:movimientos?|operaciones?|cargos?|compras?)\b"
)
_MOVEMENT_EXAMPLE: Final = re.compile(
    r"\b(?:dame|ponme|muestrame|ensename)\b.{0,45}\b(?:ejemplo|ejemlo)\b.{0,45}"
    r"\b(?:esos?|estos?|los)?\s*(?:movimientos?|operaciones?|cargos?|compras?)\b"
    r"|\b(?:ejemplo|ejemlo)\s+de\s+(?:esos?|estos?|los)\s+"
    r"(?:movimientos?|operaciones?|cargos?|compras?)\b"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpokenGroup:
    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class SpokenPlan:
    """The plan in fields rather than in words, flat like every other Result piece.

    It exists so a follow-up can carry its context back without the client having to
    re-type a sentence — and, more to the point, without the *description* travelling,
    which put a client-supplied string inside the prompt's instructions.
    """

    direction: str
    period: str | None = None
    n: int | None = None
    start: str | None = None
    end: str | None = None
    categories: list[str] = field(default_factory=list)
    merchants: list[str] = field(default_factory=list)
    group_by: str = "NONE"
    metric: str = "SUM"
    include_internal: bool = False
    across_accounts: bool = False
    only_account_id: str | None = None
    only_account_ids: list[str] = field(default_factory=list)
    transaction_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpokenContact:
    """Somebody who can be paid, flat like every Result piece.

    The identifier is here so a client can offer a choice and send back *which* contact rather
    than a name to be interpreted again — which is how money reaches the wrong Marta.
    """

    contact_id: str
    display_name: str
    phone: str


@dataclass(frozen=True)
class SpokenMovement:
    occurred_at: str
    description: str
    # Unsigned, as banking returns it: a query filtered by EXPENSE comes back with positive
    # amounts and the direction beside them. The direction is what makes the sign, and dropping
    # it here is why every expense in the chat rendered as "+22,11 €" — money coming in.
    amount_minor: int
    direction: str
    merchant_name: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class AskFinancialQuestionResult:
    """Flat, like every Result: no domain object leaves the application layer.

    `plan` is the plan in words. It is here because an answer whose derivation the user
    cannot inspect is a number to be believed, and this pipeline has a language model in
    it — showing the question it actually asked is the difference between a tool and an
    oracle.
    """

    outcome: str
    text: str
    question: str | None = None
    plan: str | None = None
    plan_context: SpokenPlan | None = None
    # Where the conversation is after this turn: at most one active workflow, plus the memory of
    # the last answered question. The client stores it and sends it back without reading it.
    #
    # A domain object leaving this layer, which every other Result field avoids. The exception is
    # deliberate: the alternative is a third representation of the same five states — domain, wire,
    # and a flat one here — kept in sync by hand, and states drifting apart between layers is the
    # failure this type was introduced to end. The query already accepts domain objects coming in.
    conversation: Conversation = IDLE
    currency: str | None = None
    total_minor: int | None = None
    movement_count: int | None = None
    groups: list[SpokenGroup] = field(default_factory=list)
    movements: list[SpokenMovement] = field(default_factory=list)
    # Filled only by a question about who can be paid. Data as well as prose, so a client
    # can draw one button per contact instead of parsing names out of a paragraph.
    contacts: list[SpokenContact] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AskFinancialQuestionQuery(Query[AskFinancialQuestionResult]):
    sentence: str
    account_id: UUID
    today: date
    # The zone `today` was measured in. It travels with the date because neither describes a
    # window alone, and it belongs to the holder rather than the deployment: between
    # midnight and 01:00 peninsular time, today in the Canaries is yesterday in Madrid.
    reporting_timezone: str = "Europe/Madrid"
    # Where the conversation was before this turn, echoed back by the caller. Held by whoever is
    # having it rather than by this service, and already typed by the time it arrives here — the
    # edge rebuilt it through the domain, so nothing in here has to believe a string.
    conversation: Conversation = IDLE
    # What this turn was read as, when the caller already knows.
    #
    # The edge has to classify the sentence before it can decide whether to dispatch this query at
    # all — a payment order never reaches here. Left out, the classification happened again inside,
    # and two readings of one sentence is a thing that can disagree with itself. It already did:
    # the edge and the handler each grew their own exception for "¿a quién puedo hacer un bizum?"
    # and only one of them got it.
    #
    # It is *not* a client field. Nothing deserialises into it; the edge fills it from the
    # interpreter it just asked, and a caller that leaves it out gets the same reading computed
    # here. That keeps the handler usable on its own — every test and the corpus call it that way —
    # without letting a second reading exist on the path a user is actually on.
    intent: Intent | None = None


@dataclass(frozen=True)
class Stage:
    """A step finishing, for a caller watching it happen.

    The answer text is composed from a template, so streaming its tokens would be
    theatre: it exists all at once the instant the figures land. What actually takes
    time is the model deciding what to ask, so those are the stages worth emitting.

    It also puts the plan on screen before the number, which is the order that lets
    someone say "that is not what I asked" while it still matters.
    """

    name: str
    detail: str | None = None


class AskFinancialQuestionHandler:
    """Question in, figure and sentence out, with two deterministic gates around the model.

        question → capability guard → planner → plan verifier → ledger → sentence

    The gates are composed here rather than inside the planner on purpose. The planner's
    job is to produce the best plan it can; deciding whether a plan may run is policy, and
    policy belongs to the layer that also decides what each outcome means. It also keeps
    them measurable: they are pure functions of their inputs, so the evaluation can replay
    any combination of them over one recorded set of model answers.

    `policy` says which rules are on. It defaults to all of them, because a guard that is
    off by accident is worse than one never written — it reads like a control.
    """

    def __init__(
        self,
        planner: QueryPlannerPort,
        queries: FinancialQueryPort,
        policy: GuardPolicy = EVERY_RULE,
        *,
        accounts: AccountSummaryPort,
        account_directory: AccountDirectoryPort | None = None,
        contacts: ContactDirectoryPort,
        cards: CardDirectoryPort,
        card_days: int = DEFAULT_CARD_DAYS,
        read_attempts: int = DEFAULT_BANK_READ_ATTEMPTS,
        interpreter: TurnInterpreterPort | None = None,
    ) -> None:
        self._planner = planner
        self._queries = queries
        self._policy = policy
        self._read_attempts = read_attempts
        self._cards = cards
        # The window the wallet screen uses, so the chat and the screen cannot disagree about what
        # this month has cost. A default here and the deployment's number at the composition root,
        # like every other operational figure.
        self._card_days = card_days
        # Only reached when the caller did not classify the turn itself. On the live path the edge
        # always has — it had to, to know this query was the right one — so this is the fallback
        # for a handler used directly, and the default is the behaviour that already existed.
        self._interpreter = interpreter or Deterministic()
        self._accounts = accounts
        self._account_directory = account_directory
        self._contacts = contacts

    # `accounts` is keyword-only and has no default on purpose. A balance question that reached a
    # handler without it would fall back to the planner, which is the bug this exists to fix — and
    # it would do so silently. Required means mypy finds an unwired build instead of a user
    # finding it.

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        """The whole answer at once. Drains the same pipeline the stream walks, so the
        two cannot answer differently."""
        result: AskFinancialQuestionResult | None = None
        async for step in self.stream(query):
            if isinstance(step, AskFinancialQuestionResult):
                result = step
        assert result is not None  # the pipeline always ends with one
        return result

    async def stream(
        self, query: AskFinancialQuestionQuery
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        """The pipeline, with one record per turn wrapped around it.

        A wrapper rather than a log call in each branch: `_steps` ends in a dozen places and the
        interesting fact is the same one every time — what this turn decided and how long it took.
        `handle` drains this, so both routes are covered by writing it once.

        Nothing the holder wrote is in the record. See `observability` for the list and why.
        """
        started = time.perf_counter()
        outcome = "INCOMPLETE"
        try:
            async for step in self._steps(query):
                if isinstance(step, AskFinancialQuestionResult):
                    outcome = step.outcome
                yield step
        finally:
            logger.info(
                "turn",
                extra={
                    "account": account_ref(query.account_id),
                    "outcome": outcome,
                    "had_workflow": workflow_of(query.conversation.active),
                    "had_plan": query.conversation.last_plan is not None,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    async def _steps(
        self, query: AskFinancialQuestionQuery
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        was = query.conversation
        asked_again = query_recovery.retry_action(query.sentence)
        if asked_again is query_recovery.RetryAction.RETRY and was.retry_plan is None:
            # A whole turn asking to repeat something, and nothing saved to repeat. Said plainly
            # rather than planned: "vuelve a hacerla" reached the planner as a sentence to turn
            # into a query, and came back asking which period it referred to — which reads as the
            # assistant not having understood the last five turns either.
            #
            # A query is only kept when the *bank* could not run it. Everything else either
            # answered or was refused for a reason repeating cannot change, and offering a retry
            # there would be offering the same failure again with more ceremony.
            yield AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    "No tengo ninguna consulta guardada para repetir. "
                    "Vuelve a preguntármelo y la hago."
                ),
                conversation=was,
            )
            return
        retry_control = asked_again if was.retry_plan is not None else None
        if retry_control is query_recovery.RetryAction.RETRY:
            # Execute the validated plan verbatim. Re-planning the word "reintenta" would make a
            # transient bank outage change the question, which is the opposite of a retry.
            retry = was.retry_plan
            assert retry is not None
            yield Stage("planned", answer_writer.describe(retry))
            yield Stage("querying")
            if isinstance(retry, TopMovements):
                yield await self._ranked(query, retry)
            else:
                yield await self._answer(query, retry)
            return
        if retry_control in {
            query_recovery.RetryAction.CHANGE,
            query_recovery.RetryAction.CANCEL,
        }:
            changing = retry_control is query_recovery.RetryAction.CHANGE
            yield AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    "De acuerdo. Dime qué movimientos quieres consultar."
                    if changing
                    else "De acuerdo, he descartado esa consulta."
                ),
                conversation=was.without_retry(),
            )
            return
        previous = was.last_plan
        planning_previous = previous if isinstance(previous, FinancialPlan) else None
        force_follow_up = False
        intent = query.intent or await self._interpreter.interpret(query.sentence, conversation=was)

        if was.retry_plan is not None and intent not in _ASIDES:
            # Any other sentence starts a different turn. It must not leave a stale retry behind
            # that a later bare "reintenta" could unexpectedly revive.
            #
            # An aside is not another turn, which is why the intent has to be known first. This
            # ran before the routing, so "¿quién eres?" between a bank outage and the retry threw
            # the saved query away while the three buttons offering it were still on screen — and
            # pressing one then reached the planner as the word "Reintentar".
            was = was.without_retry()
            query = replace(query, conversation=was)

        if isinstance(was.active, PaymentInProgress) and intent not in _KEEPS_THE_PAYMENT:
            # This turn belongs to another subject. Keep the half-built Bizum, but mark that it
            # stepped aside so a later bare amount or name cannot resume it without warning.
            was = was.suspend()
            query = replace(query, conversation=was)

        relaxed = query_recovery.relaxation(query.sentence, previous)
        if relaxed is not None:
            # These are the exact actions offered after an empty result. They patch one field in
            # code and therefore cannot drift with the configured model.
            yield Stage("planned", answer_writer.describe(relaxed))
            yield Stage("querying")
            if isinstance(relaxed, TopMovements):
                yield await self._ranked(query, relaxed)
            else:
                yield await self._answer(query, relaxed)
            return

        if intent is Intent.COURTESY:
            # «Gracias», «hola», «adiós». Every one of these used to reach the planner — a language
            # model asked to build a query about last month's spending for the word «gracias» —
            # because six intents were about money and the seventh was the catch-all.
            #
            # `was` goes back untouched, and that is the whole behaviour. A pleasantry is not a
            # turn: the previous plan survives it, so «¿cuánto gasté?» → «gracias» → «¿y en la
            # hucha?» still narrows the spending, and the half-built Bizum above is not even
            # paused, because saying thank you is not changing the subject.
            yield AskFinancialQuestionResult(
                outcome=Spoken.COURTESY.value,
                # Never None here: the intent is only this because `courtesy.reply` answered.
                text=courtesy.reply(query.sentence, at_rest=was.at_rest) or "",
                conversation=was,
            )
            return

        if intent is Intent.IDENTITY_OR_HELP:
            # Answered from here, never from the ledger and never from a model. "¿Quién eres?" used
            # to reach the planner and come back as a refusal about averages and forecasts: true
            # about the product, and not an answer to the question.
            # An aside does not abandon a Bizum. It does pause it, because the next bare amount
            # should not silently jump back to a payment after the subject changed.
            yield AskFinancialQuestionResult(
                outcome=Spoken.IDENTITY.value, text=explainer.IDENTITY, conversation=was
            )
            return

        if intent is Intent.LIST_CATEGORIES:
            # The catalogue is product data. Keep any open clarification so the user can inspect
            # the options and answer it on the following turn.
            yield AskFinancialQuestionResult(
                outcome=Spoken.CATEGORIES.value,
                text=explainer.category_catalogue(),
                conversation=was,
            )
            return

        if intent is Intent.EXPLAIN_PREVIOUS:
            # The previous plan and the catalogue know this. Asking a model why *we* answered
            # something would be asking it to guess at a fact we hold.
            yield AskFinancialQuestionResult(
                outcome=Spoken.EXPLANATION.value,
                # The explainer speaks about aggregates — which query ran, what a category means —
                # and a ranking has no metric or grouping to explain. Narrowed here rather than
                # taught two shapes, until somebody asks "¿cómo has sacado el más caro?".
                text=explainer.explain(
                    query.sentence, previous if isinstance(previous, FinancialPlan) else None
                ),
                plan=answer_writer.describe(previous) if previous else None,
                conversation=was,
            )
            return

        if intent is Intent.LIST_CONTACTS:
            # An aside as well: this is usually asked *in order to* finish a payment, so the payment
            # is exactly what must not be lost.
            # "¿Qué contactos tengo para mandar bizum?" used to be read as a payment, because
            # "bizum" is a payment word — so the assistant asked who to pay, about a payment
            # nobody had ordered. The list needs no model and no plan.
            yield await self._contact_list(query, was)
            return

        if intent is Intent.LIST_CARDS:
            yield await self._card_list(query, was)
            return

        waiting = was.active if isinstance(was.active, TransferInProgress) else None
        if intent is Intent.OWN_TRANSFER or (
            waiting is not None and _answers_the_transfer(query.sentence, waiting)
        ):
            # A transfer under way takes the reply that answers its question, before the sentence is
            # classified again. "1000 pavos" is an amount because something asked for one, and
            # classifying it afresh is how it reached the planner and came back asking which month —
            # the same rule a half-built payment has had since the review.
            yield await self._own_transfer(query, was)
            return
        if waiting is not None:
            # It answers nothing the transfer asked, so it is a change of subject. The transfer ends
            # rather than swallowing it: routing *every* sentence here while one was open answered
            # "cuanto gaste el mes pasado" with "cuanto quieres mover", which is the payment defect
            # the review fixed, reintroduced by a state that had just been given one.
            #
            # Ends rather than suspends, like a clarification and unlike a payment: the question it
            # asked is either answered or it is not, and nothing has been staged.
            was = was.with_active(None)
            query = replace(query, conversation=was)

        if intent is Intent.FINANCIAL_QUERY and _narrows_the_balance(query, was, self._policy):
            # "¿Y en la hucha?" after a balance is about the balance of that account. The same words
            # after a spending question narrow the spending, which is why this reads the turn before
            # rather than the sentence: they are identical sentences and they mean different things.
            intent = Intent.ACCOUNT_BALANCE

        narrowing = _a_bare_shop_name(query, was) if intent is Intent.FINANCIAL_QUERY else None
        if narrowing is not None:
            shop, previous = narrowing
            # A whole turn that is one name, with a question behind it. "Spotify", typed after
            # being asked "¿querías solo Spotify?", used to reach the planner as a word with no
            # window and come back "¿de qué periodo?" — a suggestion that asks you something else
            # when you take it is worse than no suggestion at all.
            #
            # No model and no bank call to decide it: the previous plan carries the window and this
            # replaces one field. A name that matches nothing is not a problem either — the empty
            # answer then offers the shops that do exist, which is the same repair one turn later.
            yield await self._answer(query, replace(previous, merchants=(shop,)))
            return

        if intent is Intent.ACCOUNT_BALANCE:
            # Not a plan and not a model. `FinancialPlan` expresses aggregations over movements —
            # window, direction, categories, merchants, grouping, SUM or COUNT — and a balance is
            # none of those, so asking a model for one got the nearest expressible thing: today's
            # movements. The figure was already there, on the port the home screen reads.
            yield await self._balance(query, was)
            return

        recent = _recent_movement_count(query.sentence)
        if recent is not None:
            yield Stage("querying")
            yield await self._recent_movements(query, recent)
            return

        if (
            previous is not None
            and isinstance(previous, FinancialPlan)
            and _MOVEMENT_EXAMPLE.search(spanish.folded(query.sentence))
        ):
            yield Stage("querying")
            yield await self._movement_example(query, previous)
            return

        if intent is Intent.CORRECTION:
            if isinstance(previous, FinancialPlan) and intent_router.is_bare_correction(
                query.sentence
            ):
                # There is nothing to patch yet. Keep the validated plan in the state so the next
                # turn can name one replacement without having to restate every good field.
                yield AskFinancialQuestionResult(
                    outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                    text=(
                        "Entendido. ¿Qué debería cambiar: el periodo, gastos o ingresos, "
                        "la categoría, el comercio o el tipo de cálculo?"
                    ),
                    conversation=was.with_active(
                        FinancialClarification(
                            draft=FinancialPlanDraft.of(previous),
                            awaiting=Slot.CORRECTION,
                        )
                    ),
                )
                return
            if isinstance(previous, FinancialPlan):
                # The correction supplies a replacement. It is a patch even if its wording looks
                # like a complete sentence; the fields it does not correct remain authoritative.
                planning_previous = previous
                force_follow_up = True
            else:
                # Ranking patches have a different shape. Until they are expressible, re-read the
                # correction rather than pretending an aggregate context can represent one.
                planning_previous = None

        deeper = self._next_in_ranking(query)
        if deeper is not None:
            # "¿Y el segundo?" after a ranking. One field changes and nothing is read again, which
            # is what stops this from depending on how the continuation happens to be phrased — the
            # failure the review pointed at when this was a regex over ordinals.
            yield Stage("planned", answer_writer.describe(deeper))
            yield Stage("querying")
            yield await self._ranked(query, deeper)
            return

        shifted = self._a_year_earlier(query)
        if shifted is not None:
            # "¿Y el año anterior?" after a question about July is July of last year. Answering it
            # as the last twelve months is a different question, and refusing the phrase outright —
            # which is what happened, because no `Period` names it — sent the turn to the model and
            # started a clarification loop about a period the user had just given.
            yield Stage("planned", answer_writer.describe(shifted))
            yield Stage("querying")
            if isinstance(shifted, TopMovements):
                yield await self._ranked(query, shifted)
            else:
                yield await self._answer(query, shifted)
            return

        average = _average_plan(query)
        if isinstance(average, FinancialPlanDraft):
            conversation = (
                was
                if isinstance(was.active, PaymentInProgress) and was.active.suspended
                else was.with_active(FinancialClarification(draft=average, awaiting=Slot.WINDOW))
            )
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text="¿De qué periodo quieres que calcule la media?",
                question="¿De qué periodo quieres que calcule la media?",
                conversation=conversation,
                suggestions=["Este mes", "El mes pasado", "Los últimos 12 meses", "En 2025"],
            )
            return
        if average is not None:
            # Average vocabulary is closed enough to plan without a model. This branch sits before
            # the legacy guard that used to reject all time-normalised means.
            yield Stage("planned", answer_writer.describe(average))
            yield Stage("querying")
            yield await self._answer(query, average)
            return

        refused = capability_guard.unsupported_reason(query.sentence, self._policy)
        if refused is not None:
            # Before the model, on purpose. Refusing here is a control; refusing after is
            # whatever disposition the configured model happens to have, and that moved
            # by 60 points between two models we measured.
            yield self._declined(refused, was)
            return

        finished = self._finish_pending(query)
        if finished is not None:
            # No model. The question back was about a slot, the reply fills it, and both the window
            # and the slot are closed vocabularies — so there is nothing here to infer.
            yield Stage("planned", answer_writer.describe(finished))
            yield Stage("querying")
            yield await self._answer(query, finished)
            return

        pending_base = self._pending_patch_base(query)
        if pending_base is not None:
            planning_previous = pending_base
            force_follow_up = True

        yield Stage("planning")
        if force_follow_up:
            planned = await self._planner.plan(
                query.sentence,
                today=query.today,
                previous=planning_previous,
                force_follow_up=True,
            )
        else:
            planned = await self._planner.plan(
                query.sentence,
                today=query.today,
                # The follow-up merge is expressed over an aggregate's fields, so a ranking is not
                # context the prompt can inherit from. An ordinal follow-up is handled above, before
                # the model, which is the case that matters.
                previous=planning_previous,
            )

        if not planned.is_answerable:
            rescued = _from_what_the_sentence_said(planned, query)
            if rescued is None:
                yield _decline(planned, was)
                return
            if not rescued.is_answerable:
                # A question rather than a plan: the sentence named a category and no period.
                yield _decline(rescued, was)
                return
            planned = rescued

        assert planned.plan is not None  # is_answerable is exactly this condition

        # What the sentence stated outright wins over both the model's opinion about which fields
        # changed and the plan it inherited from. "Los últimos 3 meses" was answered over five
        # because the model emitted `changed: ["categories"]` and the merge is arithmetic on that
        # list — a real figure about a window the user had written down and nobody read.
        facts = explicit_facts.read(query.sentence, today=query.today)
        checked = explicit_facts.enforce(planned.plan, facts)
        if facts.window is not None:
            # The record that makes a degraded conversation readable after the fact. Three windows
            # in one line — what the sentence said, what the model produced, what ran — so the turn
            # where "los últimos 3 meses" became five is visible without reproducing it.
            #
            # A window is a period name and a count, or two dates. None of that is anything the
            # holder wrote: it is this service's own vocabulary, which is why it may be logged when
            # the sentence it came from may not.
            logger.info(
                "turn.window",
                extra={
                    "explicit": _window_of(checked),
                    "planned": _window_of(planned.plan),
                    "inherited": _window_of(planning_previous),
                },
            )
        correcting = force_follow_up or facts.corrects
        if correcting and planning_previous is not None and isinstance(checked, FinancialPlan):
            # A correction patches. The same turn that lost the window also lost the category the
            # correction never mentioned, and an empty list from a model cannot be told apart from
            # a filter it simply did not copy.
            #
            # `facts.corrects` and not only the router's intent: "no, durante todo el año 2025"
            # rejects *and* replaces, which the router deliberately does not treat as a bare
            # correction — that path is for a rejection with nothing behind it. The sentence is
            # still a patch, and its unmentioned fields still have to survive.
            checked = explicit_facts.restored_filters(checked, planning_previous, facts)

        broken = plan_verifier.disagreement(query.sentence, checked, self._policy)
        if broken is not None:
            # The plan is well formed, the domain accepts it, and it answers a different
            # question from the one asked. Nothing downstream can tell: banking would
            # return a real figure. This is the only place that compares the two.
            #
            # Some of those disagreements have one correction, and taking it is better than
            # asking the user to rephrase a sentence we understood: "cuantos fueron en el
            # supermercado" was planned with the right filter and the wrong metric. Only taken
            # when it verifies — a repair that moved the disagreement rather than resolving it
            # would be a guess with a correction's confidence.
            mended = plan_verifier.repaired(broken, checked)
            if mended is not None and plan_verifier.disagreement(query.sentence, mended) is None:
                checked = mended
            else:
                yield AskFinancialQuestionResult(
                    outcome=PlanningOutcome.INVALID_DOMAIN.value,
                    text=_refusal_for(broken, query.sentence),
                    plan=answer_writer.describe(checked),
                    # The plan is shown but not remembered: it is the one that misread the
                    # question, and inheriting from it would carry the misreading into the
                    # next turn.
                    conversation=was,
                )
                return

        yield Stage("planned", answer_writer.describe(checked))
        yield Stage("querying")
        if isinstance(checked, TopMovements):
            yield await self._ranked(query, checked)
            return
        yield await self._answer(query, checked)

    def _declined(self, capability: str, was: Conversation) -> AskFinancialQuestionResult:
        """A refusal that remembers what it was about.

        The draft is not available here — the guard runs before the model, so nothing has read the
        sentence yet — and the capability plus the rank are enough for the follow-up. What the state
        buys is that "¿y el segundo?" no longer has to be recognised as a sentence at all.
        """
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.UNSUPPORTED.value,
            text=_MISSING_CAPABILITY.get(capability, _CANNOT_YET),
            conversation=(
                # A suspended payment survives a refusal, exactly as it survives an answer: it
                # belongs to the conversation before this turn and stepped aside for it. Replacing
                # it lost a half-built Bizum to "¿cuál es mi gasto medio?" — a question about the
                # product, answered by throwing away something the user was in the middle of.
                was
                if isinstance(was.active, PaymentInProgress) and was.active.suspended
                else was.refused(capability)
            ),
        )

    async def _card_list(
        self, query: AskFinancialQuestionQuery, was: Conversation
    ) -> AskFinancialQuestionResult:
        """The wallet, and what each card has taken this month.

        Answered from the directory rather than from a plan, for the reason the balance is: a plan
        has no card dimension at all. Asked to answer "cuánto he gastado con cada tarjeta", the
        planner could only build the nearest thing it *could* express — the account total — and
        868,97 € arrived labelled as an answer about cards.

        The window is the deployment's card window, the same one the wallet screen uses, so the
        chat and the screen cannot disagree about what "this month" has cost. That is the same
        argument as the balance having one adapter.

        The conversation passes through untouched. Somebody checking which card they used is not
        abandoning the question they were asking.
        """
        since = query.today - timedelta(days=self._card_days)
        try:
            wallet = await self._cards.all_of(
                query.account_id,
                since=since,
                today=query.today,
                timezone=query.reporting_timezone,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PROVIDER_ERROR.value,
                text="No he podido consultar tus tarjetas ahora mismo. Vuelve a intentarlo.",
                conversation=was,
            )

        return AskFinancialQuestionResult(
            outcome=Spoken.CARDS.value,
            text=answer_writer.cards(wallet),
            plan=answer_writer.describe(was.last_plan) if was.last_plan else None,
            conversation=was,
        )

    async def _contact_list(
        self, query: AskFinancialQuestionQuery, was: Conversation
    ) -> AskFinancialQuestionResult:
        """Who can be paid.

        The conversation passes through untouched, which matters more here than anywhere else: this
        question is usually asked *in order to* finish a payment, so abandoning the payment to
        answer it would mean starting the bizum over.
        """
        try:
            found = await self._contacts.all_of(query.account_id)
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PROVIDER_ERROR.value,
                text="No he podido consultar tus contactos ahora mismo. Vuelve a intentarlo.",
                conversation=was,
            )

        return AskFinancialQuestionResult(
            outcome=Spoken.CONTACTS.value,
            text=answer_writer.contacts(found),
            plan=answer_writer.describe(was.last_plan) if was.last_plan else None,
            conversation=was,
            contacts=[
                SpokenContact(
                    contact_id=str(contact.contact_id),
                    display_name=contact.display_name,
                    phone=contact.phone,
                )
                for contact in found
            ],
        )

    async def _balance(
        self, query: AskFinancialQuestionQuery, was: Conversation
    ) -> AskFinancialQuestionResult:
        """How much money there is, from the fixed question rather than a planned one.

        The conversation passes through. A balance question is a turn, not a reset: asking it in the
        middle of an analysis of last month must not cost the window of the question before it, and
        asking it in the middle of a bizum must not abandon the bizum.
        """
        try:
            summary = await self._accounts.of(
                query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except BankingUnavailableError:
            # The one figure that must never be invented. "Tienes 0,00 €" for "we could not ask"
            # is the worst wrong answer a bank can give, so nothing numeric goes out.
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PROVIDER_ERROR.value,
                text="No he podido consultar tu saldo ahora mismo. Vuelve a intentarlo.",
                conversation=was,
            )

        # Which account the sentence asked about, if it said. Deterministic, and empty is the
        # ordinary case: "¿cuánto tengo?" names none and gets the total with a breakdown, because a
        # breakdown is a better answer than a question. That is also why no clarification is asked
        # here even when a kind matches two accounts — both are listed, and nobody has to reply.
        named = account_words.named_in(query.sentence)
        names = tuple(holding.display_name for holding in summary.holdings if holding.display_name)
        custom = account_words.custom_chosen_among(query.sentence, names)
        selected = (
            tuple(holding for holding in summary.holdings if holding.display_name in custom)
            if custom
            else (() if account_words.explicitly_names_a_custom_account(query.sentence) else None)
        )

        return AskFinancialQuestionResult(
            outcome=Spoken.BALANCE.value,
            text=answer_writer.balance(summary, named, selected=selected),
            plan=answer_writer.describe(was.last_plan) if was.last_plan else None,
            # Remembered, so "¿y en la hucha?" on the next turn is about the balance rather than
            # about spending. The rest of the conversation passes through: a balance is a turn, not
            # a reset.
            conversation=was.answering_the_balance(),
            currency=summary.currency,
            # The figure the sentence leads with, so a client showing it does not have to parse
            # prose to find it. It comes from the writer rather than being recomputed here,
            # because a second copy of the rule is a second answer waiting to disagree with the
            # first — and the two would sit side by side on one screen.
            total_minor=answer_writer.leading_figure(summary, named, selected=selected),
        )

    async def _own_transfer(
        self, query: AskFinancialQuestionQuery, was: Conversation
    ) -> AskFinancialQuestionResult:
        """Money into one of the holder's own accounts, asked for in words.

        No model anywhere on this path. The account comes from the holder's directory and a closed
        kind vocabulary; the amount comes from a closed grammar of digits or Spanish cardinals.

        It stops at a question rather than guessing, in both directions: an amount nobody said is
        asked for, and an account name matching two of the holder's accounts is asked about. Both
        questions leave a `TransferInProgress` behind, because a question this path could not hear
        the answer to is worse than no question — "¿cuánto quieres mover a Hucha?" answered "1000
        pavos" used to reach the financial planner and come back asking which period.

        `resuming` is what the previous turn had already read. On the first turn it is empty and
        everything comes from the sentence.
        """
        resuming = was.active if isinstance(was.active, TransferInProgress) else None
        try:
            summary = await self._accounts.of(
                query.account_id, today=query.today, reporting_timezone=query.reporting_timezone
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PROVIDER_ERROR.value,
                text="No he podido consultar tus cuentas ahora mismo. Vuelve a intentarlo.",
                conversation=was,
            )

        # Where the money is going, by the preposition in front of it — not every account the
        # sentence happens to mention. "Traspasar el total de fondo indexado a la cuenta de ahorros"
        # names two, and counting the one it comes *from* as somewhere to move it *into* produced an
        # ambiguity the reader had invented and then asked the user to resolve.
        towards = account_words.destination_in(query.sentence) or account_words.named_in(
            query.sentence
        )
        wanted = [holding for holding in summary.holdings if holding.kind in towards]
        account_names = tuple(
            holding.display_name for holding in summary.holdings if holding.display_name
        )
        custom = account_words.custom_destination_among(query.sentence, account_names)
        if custom:
            wanted = [holding for holding in summary.holdings if holding.display_name in custom]
        if resuming is not None and resuming.awaiting is Slot.ACCOUNT:
            # The reply to "¿a cuál lo muevo?", read by the holder's own name for the account —
            # the only thing that separates two of one kind, since the kind vocabulary answers
            # "SAVINGS" to both "Hucha" and "Hucha viajes".
            #
            # Only here, and deliberately. On a first turn "a la hucha" is the generic word as often
            # as it is the name of the account called Hucha, and picking the exact match would move
            # money into one of two accounts on the strength of a coincidence. Asked and then
            # answered, the reply is a choice among names the user was just shown.
            picked = account_words.chosen_among(query.sentence, account_names)
            if picked:
                wanted = [h for h in summary.holdings if h.display_name in picked]
        if not wanted and resuming is not None and resuming.to_account_id is not None:
            # The account was settled on an earlier turn and this sentence only carries the amount.
            wanted = [h for h in summary.holdings if h.account_id == resuming.to_account_id]
        amount = payment_words.amount_in(query.sentence) or (
            resuming.amount_minor if resuming else None
        )

        if (
            not wanted
            and not account_words.named_in(query.sentence)
            and not account_words.explicitly_names_a_custom_account(query.sentence)
        ):
            # The sentence says to move money and does not say where. "Me gustaría transferir dinero
            # entre cuentas" is that, and it used to reach the capability guard and be refused with
            # a sentence about *querying* the transfers — a refusal about reading, to somebody
            # asking to write. Asking which is the answer, and the accounts are listed so it can be
            # answered with a name.
            movable = [holding for holding in summary.holdings]
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=answer_writer.which_account(movable),
                question=answer_writer.which_account(movable),
                conversation=was.with_active(
                    TransferInProgress(amount_minor=amount, awaiting=Slot.ACCOUNT)
                ),
            )
        if not wanted:
            if account_words.explicitly_names_a_custom_account(query.sentence):
                movable = [holding for holding in summary.holdings]
                question = answer_writer.which_account(movable)
                return AskFinancialQuestionResult(
                    outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                    text=answer_writer.unknown_named_account_to_move_to(movable),
                    question=question,
                    conversation=was.with_active(
                        TransferInProgress(amount_minor=amount, awaiting=Slot.ACCOUNT)
                    ),
                )
            # A kind was named and the holder has none of it. Keeping the state alive when one was
            # under way: the question is still open and the reply simply named the wrong account.
            return AskFinancialQuestionResult(
                outcome=Spoken.BALANCE.value,
                text=answer_writer.no_such_account_to_move_to(summary),
                conversation=was.with_active(resuming) if resuming else was,
            )
        if len(wanted) > 1:
            # Two accounts of one kind. The amount is kept, so the reply only has to name which —
            # "a Hucha viajes" finishes it, and that was the turn that used to reach the financial
            # planner and come back asking which period.
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=answer_writer.which_account(wanted),
                question=answer_writer.which_account(wanted),
                conversation=was.with_active(
                    TransferInProgress(amount_minor=amount, awaiting=Slot.ACCOUNT)
                ),
            )
        if amount is None:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=answer_writer.how_much_to_move(wanted[0]),
                question=answer_writer.how_much_to_move(wanted[0]),
                conversation=was.with_active(
                    TransferInProgress(to_account_id=wanted[0].account_id, awaiting=Slot.AMOUNT)
                ),
            )

        return AskFinancialQuestionResult(
            outcome=Spoken.TRANSFER_READY.value,
            text=answer_writer.ready_to_move(wanted[0], amount),
            # Read in full, so nothing is left open. What is left is the confirmation, and that is
            # a screen with a `payments:prepare` scope behind it rather than a turn of this chat.
            conversation=was.with_active(None),
            currency=wanted[0].currency,
            total_minor=amount,
        )

    def _finish_pending(self, query: AskFinancialQuestionQuery) -> FinancialPlan | None:
        """The plan a reply to "¿de qué periodo?" completes, or None if this is not that.

        Three conditions, and each of them is a reason this shortcut is safe rather than a
        precaution. There has to be a draft that is missing its window; the reply has to name
        a window this vocabulary is sure about; and the resulting plan has to survive the same
        verifier every other plan does.

        The verifier is the interesting one. "¿Cuántas veces este año?" names a window *and*
        changes the metric — completing the draft would answer with a total where a tally was
        asked for, and the verifier would then refuse a plan the shortcut itself got wrong.
        Standing aside sends it to the model, which can read both things.

        A reply that goes to the model is handled just below as a forced patch. That path completes
        any window it can read here, then tells the model which existing fields are settled.
        """
        active = query.conversation.active
        if not isinstance(active, FinancialClarification):
            return None
        draft = active.draft
        if active.awaiting is Slot.CATEGORY:
            # A category answer is a closed-vocabulary completion just like a named period. Only
            # exact product terms take this shortcut; ambiguous words still go to the planner.
            named = category_words.precisely_named_in(query.sentence)
            if not named or draft.needs_a_window:
                return None
            finished = replace(draft, categories=named).into_plan()
            if finished is None:
                return None
            if plan_verifier.disagreement(query.sentence, finished, self._policy) is not None:
                return None
            return finished
        if not draft.needs_a_window:
            return None

        # Against the slot the question asked for, which is what makes a two-word reply readable:
        # "de este" names nothing on its own and is this year as the answer to "¿de qué año?".
        window = window_words.window_in(
            query.sentence, asked_for=active.awaiting, today=query.today
        )
        if window is None:
            return None

        try:
            finished = draft.with_window(window)
        except InvalidValueError:
            # The draft crossed the wire, so it is input rather than something we still hold.
            # A plan that will not build from it is not an error to report: the sentence goes
            # to the model like any other.
            return None

        if plan_verifier.disagreement(query.sentence, finished, self._policy) is not None:
            return None
        return finished

    def _pending_patch_base(self, query: AskFinancialQuestionQuery) -> FinancialPlan | None:
        """The validated fields behind an active clarification, completed as far as this turn can.

        A compound reply such as "este año, pero cuéntalas" supplies the missing window and also
        changes the metric. `_finish_pending` correctly refuses to execute the old metric; this
        method then gives the completed draft to the planner as a forced patch instead of losing it.
        """
        active = query.conversation.active
        if not isinstance(active, FinancialClarification):
            return None
        draft = active.draft
        complete = draft.into_plan()
        if complete is not None:
            return complete

        window = window_words.window_in(
            query.sentence, asked_for=active.awaiting, today=query.today
        )
        if window is None:
            return None
        try:
            return draft.with_window(window)
        except InvalidValueError:
            return None

    def _next_in_ranking(self, query: AskFinancialQuestionQuery) -> TopMovements | None:
        """The row of the ranking the sentence asks for, when that is what it asks for.

        Only when the last answered question *was* a ranking: "¿y el segundo?" after a total is not
        about a second total, and reading it as one would answer a question nobody asked. The
        sentence has to be nothing but a continuation, which is the same narrow rule the refusal
        used — but the payoff is different now, because there is an answer at the end of it.

        A stated ordinal goes to the position it states. This advanced by one whatever the word was,
        so "¿y el tercero?" after the largest expense came back with the second — the recogniser
        saw an ordinal and the position it saw was thrown away.
        """
        previous = query.conversation.last_plan
        if not isinstance(previous, TopMovements):
            return None
        ordinal = capability_guard.ordinal_in(query.sentence)
        if ordinal is None:
            return None
        try:
            if ordinal.mode == "ABSOLUTE":
                assert ordinal.rank is not None  # ABSOLUTE is exactly this condition
                return previous.at_rank(ordinal.rank)
            return previous.next_rank()
        except InvalidValueError:
            # Past the ceiling. Somebody a hundred movements down a list wants the list, and
            # silently repeating the hundredth would look like an answer.
            return None

    def _a_year_earlier(self, query: AskFinancialQuestionQuery) -> PlannedQuery | None:
        """The last question again, one year back, when that is all the sentence asks for.

        A patch on the window and nothing else, which is what makes it safe to apply without a
        model: the category, the direction and the metric are the ones already validated, and the
        only field that moves is the pair of dates.

        Only over an explicit range. Shifting a named period would mean resolving what LAST_MONTH
        covers, and this service deliberately does not know — banking owns the calendar, and two
        implementations of it is the two-clocks bug in a different costume.
        """
        previous = query.conversation.last_plan
        if previous is None or previous.date_range is None:
            return None
        moved = window_words.a_year_earlier(query.sentence, previous.date_range)
        if moved is None:
            return None
        return replace(previous, date_range=moved)

    async def _ranked(
        self, query: AskFinancialQuestionQuery, ranking: TopMovements
    ) -> AskFinancialQuestionResult:
        """One movement, named. The figure alone would send the user to the movement list."""
        for attempt in range(self._read_attempts):
            try:
                found = await self._queries.ranked(
                    ranking,
                    account_id=query.account_id,
                    today=query.today,
                    reporting_timezone=query.reporting_timezone,
                )
                break
            except QueryRejectedError:
                return AskFinancialQuestionResult(
                    outcome=PlanningOutcome.INVALID_DOMAIN.value,
                    text=(
                        "He entendido la pregunta, pero la consulta que he construido no es válida."
                    ),
                    plan=answer_writer.describe(ranking),
                    conversation=query.conversation,
                )
            except BankingUnavailableError:
                if attempt + 1 < self._read_attempts:
                    continue
                return AskFinancialQuestionResult(
                    outcome="BANKING_UNAVAILABLE",
                    text=(
                        "El banco no ha respondido. He conservado esta consulta para que puedas "
                        "reintentarlo sin volver a escribirla."
                    ),
                    plan=answer_writer.describe(ranking),
                    conversation=query.conversation.awaiting_retry(ranking),
                    suggestions=query_recovery.retry_suggestions(),
                )

        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.ranked(found, ranking),
            plan=answer_writer.describe(ranking),
            # Banking's, never this layer's. A hard-coded "EUR" here labelled a movement on a
            # dollar account as euros — the one kind of wrong answer a ledger-backed product
            # cannot afford, because the figure beside it is correct.
            currency=found.currency if found else None,
            total_minor=found.amount_minor if found else None,
            movement_count=1 if found else 0,
            movements=[
                SpokenMovement(
                    occurred_at=found.occurred_at.isoformat(),
                    description=found.description,
                    amount_minor=found.amount_minor,
                    direction=found.direction,
                    merchant_name=found.merchant_name,
                    category=found.category,
                )
            ]
            if found
            else [],
            suggestions=query_recovery.empty_suggestions(ranking) if found is None else [],
            # Remembered even when nothing was found: "¿y el segundo?" after "no hay quinto" is a
            # reasonable thing to say, and the question it refers to is this one.
            conversation=query.conversation.answered(ranking),
        )

    async def _recent_movements(
        self, query: AskFinancialQuestionQuery, limit: int
    ) -> AskFinancialQuestionResult:
        """The latest N rows, where N is a count and never a number of days."""
        plan = FinancialPlan(
            direction=Direction.ALL,
            period=RelativePeriod(period=Period.LAST_N_MONTHS, n=24),
            group_by=GroupBy.NONE,
            metric=Metric.SUM,
            include_internal=True,
            across_accounts=True,
        )
        scoped = await self._scoped(plan, query)
        try:
            answer = await self._queries.run(
                scoped,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=limit,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="He entendido la lista, pero el banco ha rechazado la consulta.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        movements = answer.movements[:limit]
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.recent_movements(len(movements)),
            plan=f"últimos {limit} movimientos, todas tus cuentas",
            conversation=query.conversation.answered(plan),
            currency=answer.currency,
            movement_count=len(movements),
            movements=[_spoken_movement(movement) for movement in movements],
        )

    async def _movement_example(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan
    ) -> AskFinancialQuestionResult:
        """One concrete row from the last answer, without asking the model what «esos» means."""
        scoped = await self._scoped(plan, query)
        try:
            answer = await self._queries.run(
                scoped,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=1,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="No he podido volver a consultar esos movimientos.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        movement = next(iter(answer.movements), None)
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.movement_example(movement, answer.currency),
            plan=answer_writer.describe(plan),
            conversation=query.conversation,
            currency=answer.currency,
            total_minor=movement.amount_minor if movement is not None else None,
            movement_count=1 if movement is not None else 0,
            movements=[_spoken_movement(movement)] if movement is not None else [],
        )

    async def _scoped(self, plan: FinancialPlan, query: AskFinancialQuestionQuery) -> FinancialPlan:
        """Which of the holder's accounts the question is about.

        The same rule the balance answer already uses, applied to the ledger path: name an account
        and the figures are that account's; name none and they are all of them. It is not a new
        judgement — it reads the same closed vocabulary, and the model never sees this decision.

        **Across accounts is the default, and that is a change of meaning worth stating.** Until
        there were three accounts the figures were the one the credential named, which was also
        every account the holder had. They are no longer the same thing, and keeping the old
        default would answer "cuánto gasté" with the spending of one account out of three — a real
        figure about part of somebody's money, presented as all of it.

        Internal transfers stay excluded, so widening does not double anything: money moved into
        the piggy bank is not income there any more than it was spending here.

        The holder's account directory is fetched only for an explicit custom-name form. Fixed
        kinds retain the existing summary path, while widening needs no list at all.
        """
        named = account_words.named_in(query.sentence)
        explicit_custom = account_words.explicitly_names_a_custom_account(query.sentence)
        if not named and not explicit_custom:
            return replace(plan, across_accounts=True)

        if not named and self._account_directory is not None:
            try:
                holdings = await self._account_directory.all_of(query.account_id)
            except BankingUnavailableError:
                logger.warning(
                    "scope.accounts_unavailable account=%s", account_ref(query.account_id)
                )
                return replace(plan, across_accounts=True)
            names = tuple(holding.display_name for holding in holdings if holding.display_name)
            custom = account_words.custom_chosen_among(query.sentence, names)
            wanted_custom = [holding for holding in holdings if holding.display_name in custom]
            if len(wanted_custom) == 1:
                return replace(
                    plan,
                    across_accounts=False,
                    only_account_id=wanted_custom[0].account_id,
                    only_account_ids=(),
                    include_internal=plan.include_internal or plan.direction is Direction.ALL,
                )
            if wanted_custom:
                return replace(
                    plan,
                    across_accounts=False,
                    only_account_id=None,
                    only_account_ids=tuple(holding.account_id for holding in wanted_custom),
                )
            return replace(plan, across_accounts=True)

        try:
            summary = await self._accounts.of(
                query.account_id, today=query.today, reporting_timezone=query.reporting_timezone
            )
        except BankingUnavailableError:
            # The account list is what narrowing needs, and without it the honest fallback is the
            # wider answer rather than the declared account: one account out of three, labelled as
            # though the word had been understood, is the failure this whole method exists to stop.
            logger.warning("scope.accounts_unavailable account=%s", account_ref(query.account_id))
            return replace(plan, across_accounts=True)

        names = tuple(holding.display_name for holding in summary.holdings if holding.display_name)
        custom = account_words.custom_chosen_among(query.sentence, names)
        wanted = (
            [holding for holding in summary.holdings if holding.display_name in custom]
            if custom
            else [holding for holding in summary.holdings if holding.kind in named]
        )
        if not wanted:
            # None of that kind. Narrowing to nothing would look like a genuinely empty period.
            return replace(plan, across_accounts=True)
        if len(wanted) > 1:
            return replace(
                plan,
                across_accounts=False,
                only_account_id=None,
                only_account_ids=tuple(holding.account_id for holding in wanted),
            )
        return replace(
            plan,
            across_accounts=False,
            only_account_id=wanted[0].account_id,
            only_account_ids=(),
            # Internal transfers come back in when the question is about *this account's*
            # movements, and only then. The exclusion exists to stop double counting: a traspaso is
            # a debit here and a credit there, so across accounts it appears twice. Scoped to one
            # account it appears once, and there is nothing to double.
            #
            # What that exclusion cost, reported from the demo: "¿qué movimientos tengo en el fondo
            # indexado?" answered "no he encontrado movimientos". Every one of the fund's eleven
            # movements is a traspaso, so the filter removed all of them — and the answer then
            # offered to widen the period, which could never have worked. A dead end dressed as
            # advice is worse than a refusal.
            #
            # `direction is ALL` is the whole condition. A traspaso out of the piggy bank is not
            # spending, so "¿cuánto gasté en la hucha?" must keep excluding them; the question that
            # earns them back is the one asking what *happened* in the account.
            include_internal=plan.include_internal or plan.direction is Direction.ALL,
        )

    async def _shops_like(
        self, plan: FinancialPlan, query: AskFinancialQuestionQuery
    ) -> tuple[str, ...]:
        """The shops in this account that look like the one nothing matched.

        A second query, and it only happens on a turn that already found nothing — so the cost is
        paid exactly where the alternative was telling the user to give up. The same plan with the
        merchant filter dropped and grouped by merchant answers "which shops are in this window",
        which is the list a suggestion has to come from: guessing from a vocabulary of our own
        would offer shops this account has never paid.

        An outage here is not worth reporting. The turn already has an answer — "no encontré
        nada" — and a second failure on top of it would replace a true statement with a vaguer
        one.
        """
        try:
            wider = await self._queries.run(
                replace(plan, merchants=(), group_by=GroupBy.MERCHANT),
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except (BankingUnavailableError, QueryRejectedError):
            return ()
        return merchant_words.close_to(
            plan.merchants[0], tuple(group.key for group in wider.groups)
        )

    async def _answer(
        self,
        query: AskFinancialQuestionQuery,
        plan: FinancialPlan,
        *,
        widened: bool = False,
        narrowed: bool = False,
    ) -> AskFinancialQuestionResult:
        # Scoped for the run and not on the plan itself. Which accounts the figures came from is a
        # property of how the question was answered; the plan is what was *asked*, and that is what
        # gets described to the user and remembered for a follow-up.
        scoped = await self._scoped(plan, query)
        for attempt in range(self._read_attempts):
            try:
                answer = await self._queries.run(
                    scoped,
                    account_id=query.account_id,
                    today=query.today,
                    reporting_timezone=query.reporting_timezone,
                )
                break
            except QueryRejectedError:
                # Our plan, banking's rules. Ours to fix, so it is reported as such rather
                # than dressed up as the user having asked something impossible.
                return AskFinancialQuestionResult(
                    outcome=PlanningOutcome.INVALID_DOMAIN.value,
                    text=(
                        "He entendido la pregunta, pero la consulta que he construido no es válida."
                    ),
                    plan=answer_writer.describe(plan),
                    conversation=query.conversation,
                )
            except BankingUnavailableError:
                if attempt + 1 < self._read_attempts:
                    continue
                return AskFinancialQuestionResult(
                    outcome="BANKING_UNAVAILABLE",
                    text=(
                        "El banco no ha respondido. He conservado esta consulta para que puedas "
                        "reintentarlo sin volver a escribirla."
                    ),
                    plan=answer_writer.describe(plan),
                    conversation=query.conversation.awaiting_retry(plan),
                    suggestions=query_recovery.retry_suggestions(),
                )

        if answer.is_empty and not widened:
            previous = query.conversation.last_plan
            wider = explicit_facts.without_the_inherited_category(
                plan,
                previous if isinstance(previous, FinancialPlan) else None,
                explicit_facts.read(query.sentence, today=query.today),
            )
            if wider is not None:
                # A shop the question introduced, a category it never named, and nothing found.
                # The user used to be shown "buscar sin categoría" and had to press it; this is
                # that press, made for them. `widened` stops it recursing: one relaxation per
                # turn, and if the wider query is empty too then the answer really is nothing.
                retried = await self._answer(query, wider, widened=True)
                if retried.outcome == PlanningOutcome.PLANNED.value and retried.movement_count:
                    # Named from the categories rather than `filter_of`, which answers with the
                    # merchant when a plan has both — and the merchant is the filter that stayed.
                    dropped = " y ".join(category_words.name_of(c) for c in plan.categories)
                    return replace(
                        retried,
                        text=(
                            f"{retried.text}\n"
                            f"(He quitado el filtro de {dropped}, que venía de la pregunta"
                            f" anterior.)"
                        ),
                    )

        if plan.categories and not plan.merchants and not narrowed:
            # The question named a shop and the plan filtered by the category the shop belongs to.
            # Reported from the demo: "¿cuánto llevo gastado en spotify este año?" answered
            # "421,79 € en suscripciones, en 23 movimientos" — five times the real figure, with
            # Netflix, Steam and the gym listed underneath as the evidence.
            #
            # The proof was already in hand. A category answer carries the movements behind it, and
            # those movements carry their merchants, so "did the sentence name one of these?" needs
            # no vocabulary, no second source of truth and no extra call to decide. It is the same
            # evidence a reader uses to notice the answer is wrong; the service had simply been the
            # last to see it.
            #
            # The mirror of the widening below, and it recurses the same way and for the same
            # reason: one repair per turn, and if the narrower query finds nothing then the shop
            # was not what the question was about after all.
            seen = tuple(
                dict.fromkeys(
                    movement.merchant_name
                    for movement in answer.movements
                    if movement.merchant_name
                )
            )
            shop = merchant_words.named_in(query.sentence, seen)
            if shop is None:
                # Named it, nearly. "Spoti" was in the sentence and Spotify was in the evidence,
                # and no token matched — so the answer stayed the whole category. A near miss is a
                # guess, though, and a guess that produces a figure is what this service does not
                # do, so it asks instead of narrowing. The next turn inherits this window and is an
                # ordinary follow-up.
                nearly = merchant_words.almost_named_in(query.sentence, seen)
                if nearly:
                    # The figure that was planned is still the honest answer to the plan, so it is
                    # given rather than thrown away, and the question goes underneath it. The plan
                    # stays as the last answered one, which is what makes the reply — "Spotify" —
                    # an ordinary follow-up carrying this window instead of a bare word nobody can
                    # date.
                    answered = _answered(plan, answer, query.conversation, query.sentence)
                    return replace(
                        answered,
                        text=(f"{answered.text}\n¿Querías solo {answer_writer.listed(nearly)}?"),
                        suggestions=list(nearly),
                    )
            if shop is not None:
                tighter = replace(plan, categories=(), merchants=(shop,))
                again = await self._answer(query, tighter, narrowed=True)
                if again.outcome == PlanningOutcome.PLANNED.value and again.movement_count:
                    named = " y ".join(category_words.name_of(c) for c in plan.categories)
                    return replace(
                        again,
                        text=(
                            f"{again.text}\n"
                            f"(Solo {shop}. La consulta que había construido cubría"
                            f" {named} entera.)"
                        ),
                    )

        if answer.is_empty and plan.merchants and not narrowed:
            did_you_mean = await self._shops_like(plan, query)
            if did_you_mean:
                # A merchant filter matches exactly, so a near miss is an empty answer — and the
                # advice under it, "puedes buscar sin comercio o ampliar el periodo", is a dead end
                # dressed as help: neither would ever find the shop. Reported from the demo three
                # turns in a row, "spoti", "spotiy" and then "spotify !!".
                #
                # After the widening above, deliberately. When a plan carries an inherited category
                # as well, that category is the likelier culprit — it is the case the widening was
                # written from — and asking about shops first would spend a query on a merchant
                # that was spelled correctly all along.
                #
                # It asks rather than assuming. The names are the ledger's and a wrong guess would
                # be a real figure about a shop nobody named, which is the failure this service is
                # built around — so the candidates go back as suggestions and the next turn, which
                # inherits the window from this plan, is an ordinary follow-up.
                asked = plan.merchants[0]
                return AskFinancialQuestionResult(
                    outcome=Spoken.GUIDANCE.value,
                    text=(
                        f"No he encontrado nada en «{asked}». "
                        f"¿Te refieres a {answer_writer.listed(did_you_mean)}?"
                    ),
                    plan=answer_writer.describe(plan),
                    conversation=query.conversation,
                    suggestions=list(did_you_mean),
                )

        return _answered(plan, answer, query.conversation, query.sentence)


# One sentence per missing capability, keyed by the guard's family name.
#
# The guard has carried that name since it was written and nothing read it: every refusal came out
# as "solo consulto lo que ya ha pasado, sin medias, comparaciones ni previsiones" — a list of three
# things, at least two of which the user had not asked for. Somebody asking for their largest
# expense was told the system does not do averages.
#
# Each one says what is missing and what to ask instead, because the second half is the only part
# the user can act on. Spanish, like every string a person reads.
_MISSING_CAPABILITY: Final[dict[str, str]] = {
    "INVESTING": (
        "Todavía no sé sumar lo que has invertido en un periodo: el dinero que entra en el fondo "
        "son traspasos entre tus cuentas y no puedo separarlos del resto. Pregúntame «¿cuánto "
        "tengo en el fondo?» y te digo lo que llevas puesto."
    ),
    "INTERNAL_ONLY": (
        "Todavía no sé separar los traspasos entre tus cuentas del resto de movimientos. "
        "Puedo darte el total de un periodo o lo que has gastado por categoría, y en la pantalla "
        "de movimientos los verás marcados uno a uno."
    ),
    "AVERAGE": (
        "Puedo calcular el importe medio por movimiento, pero todavía no medias mensuales o "
        "semanal que incluya periodos sin movimientos. Prueba con «importe medio de mis compras "
        "este mes»."
    ),
    "RATIO": (
        "Todavía no calculo porcentajes de una cifra sobre otra. Puedo darte las dos por "
        "separado: lo que ingresas y lo que gastas en esa categoría."
    ),
    "COMPARISON": (
        "Todavía no comparo dos periodos en la misma respuesta. Pregúntame por uno y luego por "
        "el otro, y los ves seguidos."
    ),
    "FORECAST": "Solo consulto lo que ya ha pasado, así que no hago previsiones ni estimaciones.",
    "ADVICE": (
        "No doy consejos sobre tu dinero. Puedo mostrarte lo que has gastado y en qué, y las "
        "decisiones las tomas tú."
    ),
    "ACTION": (
        "De momento solo puedo consultar tus movimientos y hacer bizums. Otras operaciones "
        "todavía no."
    ),
}

# For a family with nothing specific to say, which today is none of them. It exists so adding a
# family to the guard cannot silently produce an empty refusal.
_CANNOT_YET = "Eso no puedo calcularlo todavía."


def _average_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Build either kind of average from explicit words, preserving safe conversation context.

    The distinction is arithmetic, not semantic guesswork: AVERAGE divides by matching movements;
    PERIOD_AVERAGE divides by the DAY/WEEK/MONTH buckets named in the sentence. A missing window is
    kept as a draft so the next turn can answer it instead of being told the capability is absent.
    """
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.metric not in {Metric.AVERAGE, Metric.PERIOD_AVERAGE}:
        return None

    previous = query.conversation.last_plan
    named = category_words.precisely_named_in(query.sentence)
    previous_categories = previous.categories if previous is not None else ()
    previous_merchants = previous.merchants if previous is not None else ()
    categories = named or previous_categories
    merchants = () if named else previous_merchants
    asks_to_spend = re.search(
        r"\bgast|\bpagu|\bcompr|\bcargo|\bme\s+cobr", spanish.folded(query.sentence)
    )
    direction = facts.direction or (Direction.EXPENSE if asks_to_spend else None)
    direction = direction or (previous.direction if previous is not None else None)
    if direction is None:
        direction = _direction_of(categories) if categories else Direction.ALL

    if facts.group_by is not None:
        group_by = facts.group_by
    elif not named and isinstance(previous, FinancialPlan):
        group_by = previous.group_by
    else:
        group_by = GroupBy.NONE

    # PERIOD_AVERAGE is only produced together with a temporal group by explicit_facts. Keeping
    # this defensive check here turns future vocabulary drift into a clarification, not an invalid
    # plan sent to banking.
    if facts.metric is Metric.PERIOD_AVERAGE and group_by not in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        return None

    window = facts.window
    if window is None and previous is not None:
        window = previous.period or previous.date_range

    if window is None:
        return FinancialPlanDraft(
            direction=direction,
            categories=categories,
            merchants=merchants,
            group_by=group_by,
            metric=facts.metric,
        )
    return FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=categories,
        merchants=merchants,
        group_by=group_by,
        metric=facts.metric,
    )


def _from_what_the_sentence_said(
    planned: PlanningResult, query: AskFinancialQuestionQuery
) -> PlanningResult | None:
    """A plan built from the sentence alone, when the model gave up on one it could have made.

    **The guard owns refusals; the model does not get to invent one.** `capability_guard` is what
    decides which questions this product declines — ratios and multi-period comparisons — and
    it runs *before* the model. So an `UNSUPPORTED` naming a family the guard does not have is not a
    refusal this service makes; it is the model failing, dressed as a product limit. Handing it to
    the user is the worst kind of wrong answer: "no puedo" about something that can be, said with
    the authority of a rule.

    Ollama returned exactly that for "¿qué intereses me ha dado la hucha?" — a question whose
    every part is in a closed vocabulary this service reads itself.

    So the sentence is re-read deterministically, and a plan is built only when it states enough on
    its own: a window, and a category named precisely enough for the gate to be sure of it. Both,
    never one — a category with no window would answer about a period nobody chose, and a window
    with no category would total everything under a question about one thing. `None` when it does
    not, and then the decline stands: this rescues a plan the sentence contains, and never guesses
    at one it does not.
    """
    if planned.outcome is not PlanningOutcome.UNSUPPORTED:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    supported_average = planned.reason == "AVERAGE" and facts.metric is Metric.AVERAGE
    supported_comparison = planned.reason == "COMPARISON" and bool(facts.compared_categories)
    if planned.reason in _MISSING_CAPABILITY and not (supported_average or supported_comparison):
        # A family the guard owns. The refusal is this product's and it is correct.
        return None

    named = category_words.precisely_named_in(query.sentence)
    if not named:
        # Nothing certain to build from. The decline stands, and it is honest: what this rescues is
        # a plan the sentence contains, never one it does not.
        return None

    if facts.window is None:
        # Everything but the period. Asking for it is the true answer — "no puedo calcularlo" about
        # a question that only lacks a window is a capability limit that does not exist, and it
        # teaches somebody the product cannot do something it does.
        return PlanningResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION,
            question=(
                "¿De qué periodo? Por ejemplo este mes, el mes pasado o los últimos 30 días."
            ),
            draft=FinancialPlanDraft(
                direction=facts.direction or _direction_of(named),
                categories=named,
                group_by=facts.group_by or GroupBy.NONE,
                metric=facts.metric or Metric.SUM,
            ),
            awaiting=Slot.WINDOW,
        )

    plan = FinancialPlan(
        direction=facts.direction or _direction_of(named),
        period=facts.window if isinstance(facts.window, RelativePeriod) else None,
        date_range=facts.window if isinstance(facts.window, AbsoluteRange) else None,
        categories=named,
        group_by=facts.group_by or GroupBy.NONE,
        metric=facts.metric or Metric.SUM,
    )
    logger.info(
        "plan.rescued account=%s reason=%s categories=%s",
        account_ref(query.account_id),
        planned.reason or "none",
        ",".join(named),
    )
    return PlanningResult(outcome=PlanningOutcome.PLANNED, plan=plan)


# Categories that are money coming in. Read from the sentence's category rather than guessed from
# its verb: "¿qué intereses me ha dado la hucha?" has no verb of spending or earning that this
# service reads, and interest is income by what it *is*.
_INCOME_CATEGORIES: Final = frozenset({"SALARY", "INTEREST"})


def _direction_of(categories: tuple[str, ...]) -> Direction:
    if all(category in _INCOME_CATEGORIES for category in categories):
        return Direction.INCOME
    if any(category in _INCOME_CATEGORIES for category in categories):
        # A salary and a supermarket in one question. ALL rather than picking, because a signed
        # total over both is the only figure that is not wrong about one of them.
        return Direction.ALL
    return Direction.EXPENSE


# Turns that are *about* something else without changing the subject. Each of these already passes
# the conversation through untouched — the user did not stop asking about last month by asking who
# they can pay — so a saved retry survives them too, and for the same reason.
# Turns that leave a half-built Bizum exactly where it is, rather than setting it aside.
#
# Everything else pauses it, because after a change of subject the next bare amount must not
# silently resume a payment. These two are not a change of subject: asking who you can pay is
# about the payment, and «gracias» is about nothing at all.
_KEEPS_THE_PAYMENT: Final = frozenset({Intent.COURTESY, Intent.LIST_CONTACTS})


_ASIDES: Final = frozenset(
    {
        Intent.COURTESY,
        Intent.IDENTITY_OR_HELP,
        Intent.LIST_CATEGORIES,
        Intent.EXPLAIN_PREVIOUS,
        Intent.LIST_CONTACTS,
        Intent.ACCOUNT_BALANCE,
    }
)


def _answers_the_transfer(sentence: str, waiting: TransferInProgress) -> bool:
    """Whether this sentence fills the slot the transfer is waiting on.

    Narrow on purpose, and the mirror of `payment_router.continues`. A state that took every
    sentence would answer a genuine change of subject with its own question, which is exactly the
    defect the payment path was fixed for — and giving the transfer a state is what made it
    possible here.

    A short phrase may be an account choice because this branch runs only while an ACCOUNT slot is
    open. It still selects nothing here: the next layer compares it exactly with the holder's real
    account names, so a false positive can only repeat the question.
    """
    if waiting.awaiting is Slot.AMOUNT:
        return payment_words.amount_in(sentence) is not None
    return bool(account_words.named_in(sentence)) or account_words.looks_like_account_choice(
        sentence
    )


# "El total", "en total", "todo junto" - the whole of it, said about a balance that had just named
# one account. It is the second turn of a reported conversation, and it went to the planner and came
# back "sigo sin tener claro el periodo", to a sentence that is not about a period at all.
_THE_WHOLE_LOT: Final = re.compile(
    r"(?:el\s+)?total(?![a-z])|todo(?:\s+junto)?(?![a-z])|en\s+conjunto|todas(?![a-z])"
)


def _a_bare_shop_name(
    query: AskFinancialQuestionQuery, was: Conversation
) -> tuple[str, FinancialPlan] | None:
    """A turn that is nothing but a name, answered by narrowing the last question to it.

    Only reached once the router *and* the balance ellipsis have had their say, which is the guard
    that matters: "¿cuánto tengo?" and "el total" are both two words no vocabulary consulted here
    claims, and both are balances. Everything those two name is already gone by the time this
    runs, which is why this checks what is left rather than trying to recognise a shop.

    The reply to "¿querías solo Spotify?" is "Spotify", and it used to reach the planner as a bare
    word with no window attached. What makes it readable here is everything it is *not*: the other
    vocabularies of this service each own a word class, so a token none of them claims, with a
    question behind it, is a proper noun — and the only proper nouns a financial question carries
    are shops.

    Deliberately not checked against a list of real merchants. Doing so would need a query on every
    turn to answer a question that is usually no, and getting it wrong costs nothing: a name the
    ledger does not have produces an empty answer, and the empty answer already offers the shops
    it does have.
    """
    plan = was.last_plan
    if not isinstance(plan, FinancialPlan):
        return None
    said = spanish.folded(query.sentence).strip(" ¿?¡!.,;:")
    if not said or len(said.split()) > 2 or len(said) < 4 or any(c.isdigit() for c in said):
        # One or two words. Beyond that it is a sentence, and a sentence has a verb this cannot
        # read — the planner is better at those and this must not take them.
        return None
    if (
        category_words.named_in(query.sentence)
        or account_words.named_in(query.sentence)
        or explicit_facts.read(query.sentence, today=query.today).window is not None
        or intent_router.asks_for_something_else(query.sentence)
    ):
        # A word another vocabulary already owns. "Gasolina" is a category, "la hucha" an account
        # and "enero" a window, and each of them has a path that answers it properly.
        return None
    return query.sentence.strip(" ¿?¡!.,;:"), plan


def _narrows_the_balance(
    query: AskFinancialQuestionQuery, was: Conversation, policy: GuardPolicy
) -> bool:
    """Whether this sentence is a balance question that names only an account, or asks for all.

    Four conditions, and all four are needed. The previous turn answered a balance, or the same
    words are a follow-up about spending. The sentence names an account - or asks for the total,
    which is the same follow-up pointing the other way. It states no window and no category,
    because a sentence that does has moved on to a question the ledger answers: "y en la hucha el
    mes pasado" is about movements.

    And **the guard does not refuse it**, which is the fourth and was missing. An ellipsis is a
    fragment; the other three conditions try to say so and a sentence with a verb of its own slips
    through all of them the moment it happens to name an account:

        "¿cuál es mi gasto medio en la hucha?"   → "Tienes 5.485,50 € en Hucha."
        "vende mis acciones del fondo indexado"  → the fund's balance
        "¿qué porcentaje tengo en el fondo?"     → the fund's balance

    Every one is a refused capability answered with a real figure about something else, stated as
    fact, and none of them ever reached the guard — the balance is routed before it. Asking the
    guard here is the general rule rather than a list of families: if the domain would decline the
    sentence, it is not a follow-up about the balance.

    The policy travels rather than `EVERY_RULE` being assumed, so a deployment that switches a rule
    off gets one behaviour and not two.
    """
    if not was.answered_the_balance:
        return False
    folded = spanish.folded(query.sentence)
    if not account_words.named_in(query.sentence) and not _THE_WHOLE_LOT.search(folded):
        return False
    if capability_guard.unsupported_reason(query.sentence, policy) is not None:
        return False
    if intent_router.asks_for_something_else(query.sentence):
        # An ellipsis narrows the previous answer and carries nothing else with it. A sentence that
        # names what it wants — «movimientos», «compras» — is not narrowing anything, whatever it
        # omits, and the omission is the trap: a misspelt "utlimo" hid the period in an eleven-word
        # question about the fund's movements, and every remaining condition then read like a bare
        # account name. It was answered with the fund's balance.
        return False
    facts = explicit_facts.read(query.sentence, today=query.today)
    return facts.window is None and not facts.names_a_category


def _decline(planned: PlanningResult, was: Conversation) -> AskFinancialQuestionResult:
    """The five outcomes that never reach the ledger.

    Each gets its own sentence. Collapsing them into "no he podido" would tell the user
    nothing about whether to rephrase, wait, or give up.
    """
    suggestions: list[str] = []
    match planned.outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            text = planned.question or ""
        case PlanningOutcome.UNSUPPORTED:
            text = _MISSING_CAPABILITY.get(planned.reason or "", _CANNOT_YET)
        case PlanningOutcome.PROVIDER_ERROR:
            text = "No he podido pensar la respuesta ahora mismo. Vuelve a intentarlo."
        case PlanningOutcome.INVALID_STRUCTURE:
            text = (
                "He entendido tu petición, pero el planificador no ha devuelto una consulta "
                "válida. No necesitas cambiar la pregunta; vuelve a intentarlo."
            )
        case PlanningOutcome.INVALID_DOMAIN:
            text = (
                "He entendido la petición, pero no he podido convertirla en una consulta válida. "
                "Prueba a concretar el periodo o el filtro."
            )
        case _:
            text = "No he entendido la pregunta. ¿Puedes decirla de otra forma?"

    if _asks_the_same_thing_again(planned, was):
        # The reply did not answer the question and the model has asked it again, word for word.
        # A third "¿de qué periodo?" is not a clarification, it is a loop — and the user cannot
        # get out of one by rephrasing, because rephrasing is what already failed twice. Offer
        # the answers this service is certain about instead of asking an open question again.
        text, suggestions = query_recovery.repeated_question(planned.awaiting.value)

    left_open = _next_state(planned)
    conversation = (
        # The same rule the guard's own refusal follows one branch over: a refusal answers nothing,
        # so the thread the user was on survives it. Written here as well because a capability can
        # be missed by the guard and caught by the planner, and the two must not leave different
        # conversations behind for the same sentence.
        was.refused(left_open.capability)
        if isinstance(left_open, UnsupportedCapability)
        else was.with_active(left_open)
    )
    if planned.outcome is PlanningOutcome.NEEDS_CLARIFICATION:
        # Recorded whether or not a workflow survived. A clarification carrying a draft keeps its
        # slot on the state and `awaiting` reads it there; this is what remembers it when there is
        # no draft and therefore no state — the case that used to loop.
        conversation = conversation.asking(planned.awaiting)

    return AskFinancialQuestionResult(
        outcome=planned.outcome.value,
        text=text,
        question=planned.question,
        conversation=conversation,
        suggestions=suggestions,
    )


def _asks_the_same_thing_again(planned: PlanningResult, was: Conversation) -> bool:
    """Whether this turn is the previous question repeated.

    The slot rather than the wording, because the wording comes from a model and two phrasings of
    "which period?" are the same dead end.

    Two places to look, and they mean the same thing. `awaiting` is the slot of an open workflow,
    which exists when the clarification carried a draft; `last_asked` is the same slot when it did
    not, and there was nothing else to hang it on. Reading only the first was the hole: a
    draft-less question left no state, so every repeat of it looked like the first.
    """
    asked_before = was.awaiting or was.last_asked
    return (
        planned.outcome is PlanningOutcome.NEEDS_CLARIFICATION
        and asked_before is not None
        and asked_before is planned.awaiting
    )


def _next_state(planned: PlanningResult) -> ActiveWorkflow | None:
    """Where a declined turn leaves the conversation.

    A clarification becomes an open question carrying its draft and its slot, so the reply completes
    it. A missing capability becomes a state that remembers what it was about, so the follow-up is a
    patch. Everything else leaves nothing open: a malformed answer and a dead provider are not
    conversations, they are failures, and inviting the user to continue one would be a lie.
    """
    match planned.outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION if planned.draft is not None:
            return FinancialClarification(draft=planned.draft, awaiting=planned.awaiting)
        case PlanningOutcome.UNSUPPORTED if planned.reason in capability_guard.RULES:
            return UnsupportedCapability(capability=planned.reason or "")
        case _:
            return None


# The generic refusal, for a rule that has nothing specific to say. It asks for a rephrasing because
# that is the only thing the user can do about it.
_REPHRASE = (
    "He entendido la pregunta, pero la consulta que he construido no se corresponde con ella. "
    "¿Puedes decirla de otra forma?"
)


def _refusal_for(rule: str, sentence: str) -> str:
    """Why the plan was refused, in words, when the rule knows enough to say.

    A specific refusal is worth writing because the generic one leaves somebody guessing at what to
    change. `CATEGORY_MISREAD` knows exactly what went wrong — the sentence named a category and the
    plan filtered by something else — so it names the category it understood.

    It does **not** ask "¿te refieres a ocio?", which is what this said first. Nothing here can act
    on a "sí" yet: a clarification does not keep the partial plan, so the reply would arrive as a
    sentence with no context and be re-planned from two letters. Asking a question the system cannot
    follow up on is the same dishonesty as a button that does nothing, so it suggests a sentence
    instead — one the user can send and this pipeline can answer.
    """
    if rule != "CATEGORY_MISREAD":
        return _REPHRASE
    named = category_words.precisely_named_in(sentence)
    words = " y ".join(category_words.name_of(category) for category in named)
    return (
        f"Creo que preguntas por {words}, pero la consulta que he construido filtra por otra cosa, "
        f"así que no te doy una cifra que estaría mal. Prueba a decírmelo nombrando la categoría: "
        f"«¿cuánto gasté en {words} el mes pasado?»"
    )


def _window_of(plan: PlannedQuery | None) -> str | None:
    """A window as one short string, for a log line.

    `None` when there is no plan to read one off, which is itself the interesting value: it is
    what "nothing was inherited" looks like in the record.
    """
    if plan is None:
        return None
    if plan.period is not None:
        named = plan.period.period.value
        return f"{named}:{plan.period.n}" if plan.period.n else named
    assert plan.date_range is not None  # the plan's own invariant guarantees one
    return f"{plan.date_range.start}..{plan.date_range.end}"


def _spoken(plan: FinancialPlan) -> SpokenPlan:
    """Flattened, so no domain object leaves this layer."""
    return SpokenPlan(
        direction=plan.direction.value,
        period=plan.period.period.value if plan.period else None,
        n=plan.period.n if plan.period else None,
        start=plan.date_range.start.isoformat() if plan.date_range else None,
        end=plan.date_range.end.isoformat() if plan.date_range else None,
        categories=list(plan.categories),
        merchants=list(plan.merchants),
        group_by=plan.group_by.value,
        metric=plan.metric.value,
        include_internal=plan.include_internal,
        across_accounts=plan.across_accounts,
        only_account_id=str(plan.only_account_id) if plan.only_account_id else None,
        only_account_ids=[str(account_id) for account_id in plan.only_account_ids],
        transaction_ids=[str(transaction_id) for transaction_id in plan.transaction_ids],
    )


def _recent_movement_count(sentence: str) -> int | None:
    found = _RECENT_MOVEMENTS.search(spanish.folded(sentence))
    if found is None:
        return None
    written = found.group("count") or found.group("count_first")
    count = int(written) if written.isdigit() else spanish_numbers.integer(written)
    return count if count is not None and 1 <= count <= 50 else None


def _spoken_movement(movement: Movement) -> SpokenMovement:
    return SpokenMovement(
        occurred_at=movement.occurred_at.isoformat(),
        description=movement.description,
        amount_minor=movement.amount_minor,
        direction=movement.direction,
        merchant_name=movement.merchant_name,
        category=movement.category,
    )


def _answered(
    plan: FinancialPlan, answer: QueryAnswer, was: Conversation, sentence: str
) -> AskFinancialQuestionResult:
    # "¿Cuándo me ingresan la nómina?" ran the right query and was reported as a total. The
    # question decides which sentence the same figures become, so the reading happens here rather
    # than in the writer — a writer that inspected the question would be a writer with a parser
    # in it.
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=(
            answer_writer.when(answer, plan)
            if explicit_facts.asks_when(sentence)
            else answer_writer.write(answer, plan)
        ),
        plan=answer_writer.describe(plan),
        # Answered: nothing is left open, and this plan becomes the memory the next follow-up
        # inherits from.
        conversation=was.answered(plan),
        currency=answer.currency,
        total_minor=answer.total_minor,
        movement_count=answer.movement_count,
        groups=[SpokenGroup(g.key, g.total_minor, g.movement_count) for g in answer.groups],
        movements=[
            SpokenMovement(
                occurred_at=m.occurred_at.isoformat(),
                description=m.description,
                amount_minor=m.amount_minor,
                direction=m.direction,
                merchant_name=m.merchant_name,
                category=m.category,
            )
            for m in answer.movements
        ],
        suggestions=query_recovery.empty_suggestions(plan) if answer.is_empty else [],
    )
