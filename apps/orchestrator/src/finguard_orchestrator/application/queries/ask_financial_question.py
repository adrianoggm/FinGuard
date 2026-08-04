"""AskFinancialQuestionQuery · Result · Handler — the whole phase 3 path.

Sentence in, plan, figures from banking, sentence out. The handler's only real job is
deciding what each planning outcome means, and five of the six never reach the ledger:
the cheapest wrong plan is the one that was never run.

`today` arrives on the query rather than being read here, for the same reason it does
in banking: a handler that reads the clock cannot be replayed, and this one has to be —
the evaluation depends on it.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.enums import PlanningOutcome, Spoken
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
    UnsupportedCapability,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.query_planner import QueryPlannerPort
from finguard_orchestrator.domain.services import (
    answer_writer,
    capability_guard,
    category_words,
    explainer,
    explicit_facts,
    intent_router,
    plan_verifier,
    query_recovery,
    window_words,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

# Reads have no side effects, so one immediate retry can recover a short transport interruption.
# A second failure becomes visible state and lets the user decide whether to try again; commands
# deliberately do not use this policy because their outcome may be indeterminate after a timeout.
BANK_READ_ATTEMPTS = 2


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
        contacts: ContactDirectoryPort,
    ) -> None:
        self._planner = planner
        self._queries = queries
        self._policy = policy
        self._accounts = accounts
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
        was = query.conversation
        retry_control = (
            query_recovery.retry_action(query.sentence) if was.retry_plan is not None else None
        )
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
        if was.retry_plan is not None:
            # Any other sentence starts a different turn. It must not leave a stale retry behind
            # that a later bare "reintenta" could unexpectedly revive.
            was = was.without_retry()
            query = replace(query, conversation=was)

        previous = was.last_plan
        planning_previous = previous if isinstance(previous, FinancialPlan) else None
        force_follow_up = False
        intent = intent_router.route(query.sentence, has_previous=previous is not None)

        if isinstance(was.active, PaymentInProgress) and intent is not Intent.LIST_CONTACTS:
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

        if intent is Intent.ACCOUNT_BALANCE:
            # Not a plan and not a model. `FinancialPlan` expresses aggregations over movements —
            # window, direction, categories, merchants, grouping, SUM or COUNT — and a balance is
            # none of those, so asking a model for one got the nearest expressible thing: today's
            # movements. The figure was already there, on the port the home screen reads.
            yield await self._balance(query, was)
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
            yield _decline(planned, was)
            return

        assert planned.plan is not None  # is_answerable is exactly this condition

        # What the sentence stated outright wins over both the model's opinion about which fields
        # changed and the plan it inherited from. "Los últimos 3 meses" was answered over five
        # because the model emitted `changed: ["categories"]` and the merge is arithmetic on that
        # list — a real figure about a window the user had written down and nobody read.
        facts = explicit_facts.read(query.sentence, today=query.today)
        checked = explicit_facts.enforce(planned.plan, facts)
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
            conversation=was.with_active(UnsupportedCapability(capability=capability)),
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

        return AskFinancialQuestionResult(
            outcome=Spoken.BALANCE.value,
            text=answer_writer.balance(summary),
            plan=answer_writer.describe(was.last_plan) if was.last_plan else None,
            conversation=was,
            currency=summary.currency,
            # The available balance, which is the figure the sentence leads with. A client showing
            # it should not have to parse the sentence to find it.
            total_minor=summary.available_minor,
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
        for attempt in range(BANK_READ_ATTEMPTS):
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
                if attempt + 1 < BANK_READ_ATTEMPTS:
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

    async def _answer(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan
    ) -> AskFinancialQuestionResult:
        for attempt in range(BANK_READ_ATTEMPTS):
            try:
                answer = await self._queries.run(
                    plan,
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
                if attempt + 1 < BANK_READ_ATTEMPTS:
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

        return _answered(plan, answer, query.conversation)


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
    "AVERAGE": (
        "Todavía no calculo medias. Puedo darte el total de cada mes por separado y así ves la "
        "evolución — por ejemplo «¿cuánto gasté en gasolina el mes pasado?»."
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


def _decline(planned: PlanningResult, was: Conversation) -> AskFinancialQuestionResult:
    """The five outcomes that never reach the ledger.

    Each gets its own sentence. Collapsing them into "no he podido" would tell the user
    nothing about whether to rephrase, wait, or give up.
    """
    match planned.outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            text = planned.question or ""
        case PlanningOutcome.UNSUPPORTED:
            text = _MISSING_CAPABILITY.get(planned.reason or "", _CANNOT_YET)
        case PlanningOutcome.PROVIDER_ERROR:
            text = "No he podido pensar la respuesta ahora mismo. Vuelve a intentarlo."
        case _:
            text = "No he entendido la pregunta. ¿Puedes decirla de otra forma?"

    return AskFinancialQuestionResult(
        outcome=planned.outcome.value,
        text=text,
        question=planned.question,
        conversation=was.with_active(_next_state(planned)),
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
    )


def _answered(
    plan: FinancialPlan, answer: QueryAnswer, was: Conversation
) -> AskFinancialQuestionResult:
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=answer_writer.write(answer, plan),
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
