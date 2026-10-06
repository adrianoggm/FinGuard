"""The handler for a financial question — the whole phase 3 path.

Sentence in, plan, figures from banking, sentence out. The handler's only real job is
deciding what each planning outcome means, and five of the six never reach the ledger:
the cheapest wrong plan is the one that was never run.

`today` arrives on the query rather than being read here, for the same reason it does
in banking: a handler that reads the clock cannot be replayed, and this one has to be —
the evaluation depends on it.

**Two things that used to be here are not any more.** The query and its answer are in
`financial_turn`, because the nineteen readers take an `AskFinancialQuestionQuery` and a
reader module cannot import the handler that dispatches them. The readers themselves are
in `queries/readers/`, in six modules cut where their Spanish is shared — that package's
docstring is where the grouping is argued. What is left here is the handler: the order of
the turn, and what each claim becomes.

`_steps` asks the readers in five declared runs rather than at nineteen interleaved call
sites; see the comment above `_BEFORE_THE_ASIDES` for why it is five and not one.
"""

import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    SpokenContact,
    SpokenGroup,
    SpokenMovement,
    SpokenPlan,
    _across_every_account,
    _clean_turn,
    _direction_of,
    _query_for_reader,
    _same_window,
    _starts_new_financial_scope,
    _window_of,
)
from finguard_orchestrator.application.queries.reader_registry import Claim
from finguard_orchestrator.application.queries.readers import seam
from finguard_orchestrator.application.queries.readers.drill_down import (
    DrillAsk,
    DrillDown,
    DrillUnbound,
    read_drill_down,
)
from finguard_orchestrator.application.queries.readers.follow_up import (
    _CATEGORY_REFERENCE,
    _account_follow_up_plan,
)
from finguard_orchestrator.application.queries.readers.subjects import _INVESTMENT_HISTORY
from finguard_orchestrator.application.queries.readers.visual_follow_up import (
    classify as classify_visual_follow_up,
)
from finguard_orchestrator.application.queries.readers.visual_follow_up import (
    record_visual_follow_up,
)
from finguard_orchestrator.application.queries.readers.windows import (
    _is_whole_month,
    _is_whole_year,
)
from finguard_orchestrator.domain.constants import (
    MAX_MERCHANTS,
    MAX_SELECTED_TRANSACTIONS,
    MAX_SUPPORTING_MOVEMENTS,
    MAX_TURN_SENTENCE,
)
from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
    Spoken,
    TransactionType,
)
from finguard_orchestrator.domain.errors import (
    AccountScopeNotFoundError,
    BankingUnavailableError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.chart import (
    PresentationContext,
    VisualContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.conversation import (
    AccountAlias,
    AccountCandidate,
    AccountScopeClarification,
    AccountSelection,
    AccountSelectionMode,
    ActiveWorkflow,
    Conversation,
    CounterpartyAlias,
    FinancialClarification,
    PaymentInProgress,
    PlannedQuery,
    Slot,
    TransferInProgress,
    Turn,
    UnsupportedCapability,
    workflow_of,
)
from finguard_orchestrator.domain.models.planning_context import (
    ContinuationBase,
    ContinuationSource,
    QueryFocus,
    focus_of,
    planning_context,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.models.result_reference import (
    ResultReference,
    answered_with_result,
    focused_on_result,
    result_reference_of,
)
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.domain.models.visual_plan import Rejection, canonical
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.card_directory import CardDirectoryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.contextual_query_planner import ContextualQueryPlannerPort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import (
    account_words,
    answer_writer,
    capability_guard,
    category_words,
    continuation_patch,
    courtesy,
    cumulative_calculator,
    entity_alias_words,
    explainer,
    explicit_facts,
    frequency_analyzer,
    incoming_payment_words,
    intent_router,
    merchant_words,
    payment_router,
    payment_words,
    plan_verifier,
    query_recovery,
    ratio_writer,
    recurrence_analyzer,
    spanish,
    spanish_numbers,
    turn_analyzer,
    window_words,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.plan_digest import plan_digest
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.observability import (
    Step,
    Verdict,
    account_ref,
    current_turn,
    record,
)


class AccountScopeRequiredError(Exception):
    """A read that cannot pick an account by itself, raised from `_scoped`.

    `conversation` is the one the read was running in — `_steps`' working conversation, which
    keeps `query.conversation` in step with `was` at every change it makes. The backstop builds its
    question from this rather than from the conversation the turn arrived with: by the time a read
    raises, the turn may already have ended a transfer, suspended a payment or dropped a retry, and
    the arriving conversation brings each of them back. A transfer resurrected that way left the
    account question behind a slot of its own, so the reply was never read as an answer to it.
    """

    def __init__(
        self,
        plan: PlannedQuery,
        candidates: tuple[AccountCandidate, ...],
        count: int | None = None,
        *,
        conversation: Conversation,
    ):
        self.plan = plan
        self.candidates = candidates
        self.count = count
        self.conversation = conversation


# The question `_scope_question` asks, in the words the resumable paths use too. Named because a
# later turn reads it back: the backstop leaves no workflow, only this question on its turn in the
# log, and `_asked_which_account_about` knows a reply is answering it by finding it there.
_WHICH_ACCOUNT: Final = "¿En qué cuenta quieres consultarlo?"

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
    r"|\blos?\s+(?P<count_before>\d{1,3}|[a-z]+(?:\s+y\s+[a-z]+)?)\s+"
    r"(?:ultim[oa]s?|utlim[oa]s?|recientes?)\b.{0,40}\bda\s+igual\s+cuando\b"
    r"|\b(?!los?\s+(?:ultim|utlim|recient))"
    r"(?P<count_before_bare>\d{1,3}|[a-z]+(?:\s+y\s+[a-z]+)?)\s+"
    r"(?:ultim[oa]s?|utlim[oa]s?|recientes?)\b.{0,40}\bda\s+igual\s+cuando\b"
    r"|\b(?P<count_first>\d{1,3}|[a-z]+(?:\s+y\s+[a-z]+)?)\s+"
    r"(?:ultim[oa]s?|utlim[oa]s?|recientes?)\s+"
    r"(?:movimientos?|operaciones?|cargos?|compras?)\b"
)
_RECENT_UNBOUNDED: Final = re.compile(
    r"\b(?:ensename|muestrame|quiero\s+ver)\b.{0,30}"
    r"\b(?:lo\s+ultimo|los\s+movimientos\s+recientes)\b"
)
_MOVEMENT_EXAMPLE: Final = re.compile(
    r"\b(?:dame|ponme|muestrame|ensename)\b.{0,45}\b(?:ejemplo|ejemlo)\b.{0,45}"
    r"\b(?:esos?|estos?|los)?\s*"
    r"(?:movimientos?|operaciones?|cargos?|compras?|pagos?|gastos?|ingresos?)\b"
    r"|\b(?:ejemplo|ejemlo)\s+de\s+(?:esos?|estos?|los)\s+"
    r"(?:movimientos?|operaciones?|cargos?|compras?|pagos?|gastos?|ingresos?)\b"
    r"|\b(?:dame|ponme|muestrame|ensename)\b.{0,20}\b(?:un|dos|tres|par\s+de)?\s*"
    r"(?:ejemplo|ejemplos|ejemlo)s?\b"
)
_MOVEMENT_CLASSIFICATION: Final = re.compile(
    r"\b(?:eso|esto|este|ese|el\s+movimiento|el\s+ingreso)\b.{0,25}"
    r"\b(?:es|era|fue)\b.{0,35}\b(?:bizum|nomina|transferencia)\b"
)
_RETURN_TO_PREVIOUS: Final = re.compile(
    r"^(?:vale\s+|bueno\s+|y\s+)?(?:"
    r"vuelve?\s+(?:a\s+)?(?:la\s+consulta\s+|lo\s+)?anterior"
    r"|volvamos?\s+(?:a\s+)?(?:la\s+consulta\s+|lo\s+)?anterior"
    r"|dejalo\s+como\s+estaba(?:\s+antes)?"
    r"|como\s+estaba\s+antes"
    r"|deshaz\s+(?:eso|el\s+ultimo\s+cambio)"
    r")$"
)
_UNDO_FILTER: Final = re.compile(r"^(?:quita|elimina|deshaz)\s+(?:ese|el\s+ultimo)\s+filtro$")

_NUMBERED_MONTH_FALLBACK: Final = re.compile(
    r"\b(?:el\s+)?mes\s+(?P<first>0?[1-9]|1[0-2])\b.{0,24}"
    r"\b(?:y\s+)?si\s+no\b\s+(?:(?:el\s+)?mes\s+|el\s+)?"
    r"(?P<second>0?[1-9]|1[0-2])\b"
)
_ACCOUNT_ACTIVITY: Final = re.compile(
    r"\b(?:movimientos?|operaciones?|ingresos?|income|gast\w*|cargos?|compr\w*|pag\w*)\b"
)
_ASKS_ACCOUNT_ACTIVITY: Final = re.compile(
    r"\b(?:cuanto|cuantos|dime|dame|lista|muestrame|ensename|quiero\s+ver)\b"
    r"|^(?:[¿¡]\s*)?(?:que|cual)\b"
)
_ACCOUNT_YIELD_AMOUNT: Final = re.compile(
    r"\bcuantos?\s+ingresos?\b.{0,45}\b(?:me\s+da|genera|produce|renta)\w*\b"
)

logger = logging.getLogger(__name__)

# How a turn's outcome reads as a verdict. A table because the mapping is data, and `AGREED` is
# the default because everything not listed is an answer: the eleven `Spoken` outcomes are answers
# this service already held, and `PLANNED` is one it went and got.
#
# The three that decline are this service choosing — it will not express the question, it does not
# know which one was asked, or the rules rejected the plan. The two that fail are not: bytes that
# did not match a schema, and a bank that did not answer.
_TURN_VERDICT: Final = {
    PlanningOutcome.UNSUPPORTED.value: Verdict.DECLINED,
    PlanningOutcome.NEEDS_CLARIFICATION.value: Verdict.DECLINED,
    PlanningOutcome.INVALID_DOMAIN.value: Verdict.DECLINED,
    PlanningOutcome.INVALID_STRUCTURE.value: Verdict.FAILED,
    PlanningOutcome.PROVIDER_ERROR.value: Verdict.FAILED,
    "BANKING_UNAVAILABLE": Verdict.FAILED,
}


def _legacy_turn_analysis(intent: Intent, conversation: Conversation) -> TurnAnalysis:
    """Supply relation/focus for direct callers that predate the edge analysis handoff."""
    if conversation.awaiting is not None or conversation.last_asked is not None:
        relation = TurnRelation.ANSWER_TO_SLOT
        focus = TurnFocus.ACTIVE_WORKFLOW
    elif intent is Intent.CORRECTION:
        relation = TurnRelation.CORRECTION
        focus = TurnFocus.LAST_ANSWER
    else:
        relation = TurnRelation.NEW_REQUEST
        focus = TurnFocus.NEW_REQUEST
    return TurnAnalysis(intent=intent, relation=relation, focus=focus)


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


class _RecordedLedger:
    """The ledger port, with what the bank returned recorded on the way past.

    **A wrapper and not a `record` call at the reads, because there are sixteen of them.** They are
    spread over the comparison pair, both ratio operands, the salary history, the payee count, the
    rankings, the movement lists and the ordinary answer, and a telemetry block at each would be
    sixteen blocks in the file this design promised not to make harder to read — and the
    seventeenth read, added next month, would forget. One seam, one place, every read.

    `changed` is the run crossing accounts. Across every account of the holder is now the default
    and it was not always: until there were three accounts the figures were the credential's one,
    which was also all of them. `_scoped` calls that a change of meaning worth stating, and this is
    where it becomes a figure — a turn answered over three accounts answered a wider question than
    the plan on its own says.

    `detail` is `account_ref`, never the account. `now` is a row count. Neither is anything the
    holder wrote, and nothing about the movements themselves is recorded: what the ledger holds is
    on the prohibited list beside what the holder typed.
    """

    def __init__(self, queries: FinancialQueryPort) -> None:
        self._queries = queries

    async def run(
        self,
        plan: FinancialPlan,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
        movements: int | None = None,
    ) -> QueryAnswer:
        # Forwarded exactly as it arrived. Passing `movements=None` through would change the call
        # every adapter and every fake sees, for a value that means "do not ask".
        extra: dict[str, int] = {} if movements is None else {"movements": movements}
        try:
            answer = await self._queries.run(
                plan,
                account_id=account_id,
                today=today,
                reporting_timezone=reporting_timezone,
                **extra,
            )
        except Exception:
            record(
                Step.LEDGER,
                Verdict.FAILED,
                changed=plan.across_accounts,
                field="rows",
                detail=account_ref(account_id),
            )
            raise
        record(
            Step.LEDGER,
            Verdict.AGREED,
            changed=plan.across_accounts,
            field="rows",
            now=str(answer.movement_count),
            detail=account_ref(account_id),
        )
        return answer

    async def ranked(
        self,
        ranking: TopMovements,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
    ) -> Movement | None:
        found = await self._queries.ranked(
            ranking,
            account_id=account_id,
            today=today,
            reporting_timezone=reporting_timezone,
        )
        # No row count and no `FAILED`: this one answers with a movement or with None, and None is
        # an answer — "el quinto" of three movements does not exist. A count of one or zero would
        # be a figure about the ledger's contents rather than about what the run did.
        record(
            Step.LEDGER,
            Verdict.AGREED,
            changed=ranking.across_accounts,
            field="rank",
            now=str(ranking.rank),
            detail=account_ref(account_id),
        )
        return found


# ----------------------------------------------------------------------------------------------
# The nineteen readers, in the five runs `_steps` asks them in.
#
# **This is the fact A2 had to preserve and very nearly did not.** A1's handover said to replace the
# nineteen interleaved call sites with one `select()`, and that is wrong — measurably, not as a
# matter of taste. The readers were never consulted as one block. Between them sat branches that
# are not readers and can also answer the turn, and each one is a sentence that would change
# meaning if a reader below it were hoisted above it:
#
# | between which runs | what sits there |
# | --- | --- |
# | 1 → 2 | «gracias», «¿quién eres?», a transfer under way, a category refusal, two Bizums |
# | 2 → 3 | `_account_activity_request` — a plan or a draft about one account |
# | 3 → 4 | other accounts, an account follow-up, a bare shop name, a balance, «¿y el |
# |       | segundo?», six pronoun branches over the last answer, and a referenced result |
# | 4 → 5 | frustration, a bare correction, the next in a ranking, a year earlier |
#
# One `select()` at the top would hand `_average_plan` a sentence `Intent.COURTESY` answers today.
# So the runs are declared, each is asked where its members were asked, and `select(among=...)`
# walks the derived order restricted to the run.
#
# **Restricting a global order to a subset is only safe while every edge points backwards**, which
# is true of all five `yields_to` edges in the table and is checked by
# `test_no_run_yields_to_a_later_one` rather than assumed. `test_the_five_runs_are_the_nineteen`
# checks the other half: the runs concatenate to `CASCADE` exactly, once each. Those two together
# are what replaced `test_the_cascade_order_is_still_what_the_handler_does`, which read `_steps`'
# source because there was no other way to see an order spread over nineteen call sites.
#
# The names say what the run is *for*, because that is the only thing that makes a fifth run's
# position reviewable: a new reader belongs in the run whose question it answers.
_BEFORE_THE_ASIDES: Final = ("_ratio_request",)
_ON_A_NAMED_SUBJECT: Final = (
    "_gambling_plan",
    "_how_many_payees_plan",
    "_internal_movement_request",
)
_ON_A_COMPARISON: Final = (
    "_salary_history_plans",
    "_period_comparison_plans",
    "_comparison_with_previous_period",
    "_direct_period_comparison",
    "_comparison_with_a_named_window",
)
_ON_THE_QUERY_IN_VIEW: Final = ("contextual_follow_up_plan",)
_ON_THE_SENTENCE_ITSELF: Final = (
    "_multiple_movement_ranking",
    "_group_ranking_plan",
    "_natural_period_aggregate_plan",
    "_deterministic_breakdown_plan",
    "_contextual_ranking_plan",
    "_cumulative_plan",
    "_recurrence_plan",
    "_frequency_plan",
    "_average_plan",
)
# In the order `_steps` asks them, which is the tuple `test_the_five_runs_are_the_nineteen` reads.
RUNS: Final = (
    _BEFORE_THE_ASIDES,
    _ON_A_NAMED_SUBJECT,
    _ON_A_COMPARISON,
    _ON_THE_QUERY_IN_VIEW,
    _ON_THE_SENTENCE_ITSELF,
)
# F-H3 (Wave 3 hardening, fix round): the two runs whose readers resolve their plan from
# `focus_of(conversation)` -- a patch, a regroup or a split of whatever the conversation already
# carried, never a fresh read of the sentence alone. `_answering` stamps
# `plan_continues_conversation` from membership in this set.
_CONTINUATION_READERS: Final = _ON_A_COMPARISON + _ON_THE_QUERY_IN_VIEW


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
        planner: ContextualQueryPlannerPort,
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
        visual_policy: VisualPlanningPolicy | None = None,
    ) -> None:
        self._planner = planner
        # Wrapped here rather than at the composition root, so a handler built directly — by a
        # test, or by the evaluation harness — records its bank reads too. An instrument only the
        # deployment wires is an instrument no test has ever executed.
        self._queries: FinancialQueryPort = _RecordedLedger(queries)
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
        # The chart a rule chooses, wrapped in the policy that also knows how to hold a model
        # planner beside it. A handler built without one — every test that does not care, and any
        # composition root that has not wired a model yet — gets the deterministic floor and
        # nothing else, which is today's behaviour unchanged.
        self._visual_policy = visual_policy or VisualPlanningPolicy(
            model=None,
            fallback=DeterministicVisualPlanner(),
            verifier=verify,
            mode="deterministic",
            # Unused in deterministic mode; the policy refuses a non-positive budget outright.
            budget_seconds=1.0,
        )

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
        if result is None:
            # `assert` here vanished under `python -O`, and this is the one where that changed the
            # outcome rather than the error message: a `handle()` typed to return an answer returned
            # None instead, and what a caller does with that is decided a layer away from here.
            raise RuntimeError("the question pipeline ended without a result")
        return result

    async def stream(
        self, query: AskFinancialQuestionQuery
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        """The pipeline, with one record per turn wrapped around it.

        A wrapper rather than a log call in each branch: `_steps` ends in a dozen places and the
        interesting fact is the same one every time — what this turn decided and how long it took.
        `handle` drains this, so both routes are covered by writing it once.

        The conversation's own record of the turn is appended here for exactly that reason. Forty
        branches build a result and every one of them would have had to remember; the turn is the
        same fact in all of them, and it is already on the result by the time it passes through.

        Nothing the holder wrote is in the *observability* record. See `observability` for the list
        and why — the conversational log below is a different thing, held by the caller and never
        written anywhere this service owns.
        """
        started = time.perf_counter()
        outcome = "INCOMPLETE"
        # A one-element box, not a return value: `_steps` is an async generator and PEP 525 forbids
        # `return <value>` in one. `_classify_and_record_visual_follow_up`, deep inside it,
        # classifies what this turn wants from the chart it follows long before any result exists;
        # this is where that value is written, and read back below at the same convergence that
        # decides `visual_decision` — for the same reason `metric_definition`'s own fallback is
        # here rather than in each of `_steps`'s branches, except that fallback reads
        # `step.conversation` (already on the result) while this one depends on `analysis`, which
        # never is.
        follow_up_seen: list[VisualFollowUp | None] = [None]
        # The same kind of box, for the sentence the turn ends up answering. It is the holder's
        # sentence except on one path: a reply to the backstop's account question answers the
        # question it settled, and `_steps` writes that one here when it resumes it.
        answering: list[str] = [query.sentence]
        try:
            async for step in self._steps_answering_scope(query, follow_up_seen, answering):
                if isinstance(step, AskFinancialQuestionResult):
                    outcome = step.outcome
                    # The turn, as one decision. Here rather than in the `finally` below on
                    # purpose: an abandoned generator runs its `finally` in whatever context the
                    # finaliser is on, where the recorder this turn owns is not reachable — so the
                    # one record that says how every turn ended would be the one missing from the
                    # turns that ended badly. This line runs in the caller's own context, where the
                    # recorder is, and every branch of `_steps` passes through it.
                    #
                    # `changed` is whether a figure was actually shown. It is the fact `outcome`
                    # cannot carry: `PLANNED` with no figure is a plan that ran and answered
                    # nothing, and it counts as a success in every instrument that reads the
                    # outcome alone.
                    record(
                        Step.OUTCOME,
                        _TURN_VERDICT.get(step.outcome, Verdict.AGREED),
                        changed=step.total_minor is not None,
                        now=step.outcome,
                    )
                    recorder = current_turn()
                    if recorder is not None:
                        recorder.outcome = step.outcome
                    step = replace(
                        step,
                        conversation=step.conversation.with_turn(
                            Turn(
                                sentence=query.sentence,
                                outcome=step.outcome,
                                # The identifier line, never `plan_in_words`: the turn
                                # interpreter's prompt quotes this log, and was measured on it.
                                plan=step.plan,
                                question=step.question,
                            )
                        ),
                    )
                    if step.metric_definition is None and isinstance(
                        step.conversation.last_plan, FinancialPlan
                    ):
                        # R14 (Wave 4.5, closes D-4.5-6/M-2): this is the one place
                        # `metric_definition` is ever assigned a value it did not already have --
                        # every producer of a chart in this handler (`_visual_dataset`,
                        # `_how_many_payees`, `_compare_periods`, the ratio branches, the salary
                        # history) leaves it `None` and lets THIS line supply `conversation.
                        # last_plan` instead. So the flag is set here, in the same `replace`, and
                        # nowhere else -- it says the fact this line alone knows: the plan on the
                        # result did not come from this turn's own producer.
                        step = replace(
                            step,
                            metric_definition=step.conversation.last_plan,
                            metric_definition_borrowed=True,
                        )
                    # A fingerprint of `step.metric_definition` as held at this line, borrowed or
                    # not. `None` when `metric_definition` is not a `FinancialPlan` at all (a
                    # comparison, a ratio, or no plan ran). Sealed as data for 4.3's drill to bind
                    # to; the presenter reads `metric_definition_borrowed` above instead (a digest
                    # of this same field cannot tell borrowed from owned -- only the back-fill
                    # above can, so it says so directly).
                    step = replace(
                        step,
                        visual_plan_digest=(
                            plan_digest(step.metric_definition)
                            if isinstance(step.metric_definition, FinancialPlan)
                            else None
                        ),
                    )
                    # The chart is decided once for the turn using the presentation circumstances
                    # supplied at the edge. The deterministic floor may currently ignore some of
                    # them, but the application seam is complete for planners that do not.
                    # The question answered, not the chip that settled its scope: the visual
                    # planner reads it to choose the chart.
                    decided = await _decided_visual(
                        step, self._visual_policy, query.presentation, answering[0]
                    )
                    step = replace(decided, visual_follow_up=follow_up_seen[0])
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

    async def _steps_answering_scope(
        self,
        query: AskFinancialQuestionQuery,
        follow_up_seen: list[VisualFollowUp | None],
        answering: list[str],
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        """`_steps`, with an account-scope requirement answered instead of raised.

        `_scoped` raises `AccountScopeRequiredError` from eleven call sites and exactly two of them
        translated it. The other nine — the comparison pair, the ratio operands and the ratio
        draft, the payee count, the group reference, the recent movements and the salary history —
        let it out of the handler, where a first question from anybody holding more than one
        account became an HTTP 500. Nine `try`/`except` blocks are nine chances to forget, and the
        tenth path added next month would forget too.

        Wrapped here rather than inside `stream`: this is the outermost place a result can still be
        yielded *and still be recorded*, so the clarification travels through the same turn record
        and reports the same `outcome` to the log as every other answer, instead of needing a
        second copy of both.

        `follow_up_seen` and `answering` are `stream`'s own boxes, passed through untouched: this
        layer decides nothing about the chart, it only forwards what `_steps` writes into them.

        The question is built from the conversation the read raised in, never from `query`'s: see
        `AccountScopeRequiredError`.
        """
        try:
            async for step in self._steps(query, follow_up_seen, answering):
                yield step
        except AccountScopeRequiredError as required:
            yield self._scope_question(required, required.conversation)

    async def _steps(
        self,
        query: AskFinancialQuestionQuery,
        follow_up_seen: list[VisualFollowUp | None],
        answering: list[str],
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
            if retry is None:
                # `retry_control` is only RETRY when this is set, three lines up — so this is the
                # coupling written down rather than a case anybody has reached.
                raise RuntimeError("a retry was accepted with no plan to retry")
            yield Stage("planned", answer_writer.plan_in_words(retry))
            yield Stage("querying")
            if isinstance(retry, TopMovements):
                yield await self._ranked(query, retry)
            else:
                # «Reintenta» names nothing; the turn the bank failed on is the question.
                yield await self._answer(
                    query, retry, asked=_the_sentence_that_asked(was, retrying=retry)
                )
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
        continuation = (
            ContinuationBase(previous, ContinuationSource.LAST_ANSWER)
            if isinstance(previous, FinancialPlan)
            else None
        )

        learned_alias = await self._learn_counterparty_alias(query)
        if learned_alias is None:
            learned_alias = await self._learn_account_alias(query)
        if learned_alias is not None:
            yield learned_alias
            return

        previous_request = _previous_plan_request(query.sentence, was)
        if previous_request is not None:
            if previous_request is False:
                yield AskFinancialQuestionResult(
                    outcome=Spoken.GUIDANCE.value,
                    text="No tengo una consulta anterior compatible a la que volver.",
                    conversation=was,
                )
                return
            target = previous_request
            yield Stage("planned", answer_writer.plan_in_words(target))
            yield Stage("querying")
            restored = (
                await self._ranked(query, target)
                if isinstance(target, TopMovements)
                else await self._answer(query, target)
            )
            yield replace(restored, text=f"He vuelto a la consulta anterior. {restored.text}")
            return

        season = window_words.season_ask(query.sentence, today=query.today)
        if season is not None:
            # B-4 (Wave 3 hardening): «este verano»/«el verano pasado»/«en primavera» are windows
            # `window_words.window_in` keeps refusing to guess -- see its own module docstring.
            # Before this, the refusal was silent: the sentence reached the planner (or a
            # follow-up reader) with no signal that the season was never read, and whatever window
            # was already in view answered a different question (§11's own opening sentence, «¿En
            # qué he gastado más este verano?», ran with the inherited `LAST_N_MONTHS:3`
            # unchanged). Ahead of intent classification and the planner both, on purpose: no
            # vocabulary here is more certain about what the sentence is missing, and answering
            # with the wrong months is worse than asking which ones were meant.
            question, chips = season
            # F-H2 (Wave 3 hardening, fix round): the ask used to leave `conversation=was`
            # unchanged -- an honest question with no state behind it, so the next turn (a bare
            # "sí", "esos tres", or the chip itself) was read exactly like any fresh sentence, and
            # the wrong-but-confident failure this whole finding exists to close just moved one
            # turn later. Reusing the same clarification workflow the planner's own window
            # question opens (`FinancialClarification`, `awaiting=Slot.WINDOW`) closes it: the
            # direction is the one fact this sentence states or inherits, nothing else is settled,
            # and `_finish_pending`'s ordinary reply-to-a-clarification path resolves the window on
            # the next turn exactly like any other answer to "¿de qué periodo?" would -- the chip
            # itself now being a range phrase `window_words.window_in` can read back (see its own
            # module docstring).
            facts = explicit_facts.read(query.sentence, today=query.today)
            direction = facts.direction or (
                previous.direction if isinstance(previous, FinancialPlan) else Direction.EXPENSE
            )
            # F-4 (fix wave, D-H-7/Important 4): "en qué he gastado más este verano" asks for a
            # breakdown by category, not a total -- the shape the round trip must carry too, or
            # the resolved plan draws a single number for a question that asked which category.
            group_by = (
                GroupBy.CATEGORY
                if window_words.season_asks_a_breakdown(query.sentence)
                else GroupBy.NONE
            )
            yield AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=question,
                question=question,
                conversation=was.with_active(
                    FinancialClarification(
                        draft=FinancialPlanDraft(direction=direction, group_by=group_by),
                        awaiting=Slot.WINDOW,
                    )
                ),
                suggestions=list(chips),
            )
            return

        if query.analysis is not None:
            analysis = query.analysis
        elif query.intent is not None:
            analysis = _legacy_turn_analysis(query.intent, was)
        else:
            analysis = await turn_analyzer.analyze(
                query.sentence,
                conversation=was,
                interpreter=self._interpreter,
                today=query.today,
            )
        # The backstop's account question, answered. `_scope_question` asks «¿En qué cuenta…?»
        # with no `AccountScopeClarification` behind it, so the resume further down never sees the
        # reply — and read as a sentence of its own, «Todas mis cuentas» was a balance question.
        # Here, once the intent is known (a payment or a transfer keeps its turn), and before the
        # chart relationship is classified: the turn this becomes is the pending question, which
        # is the one whose relationship to a chart is worth recording.
        rescoped = await self._answering_the_scope_question(query, analysis.intent)
        if isinstance(rescoped, AskFinancialQuestionResult):
            yield rescoped
            return
        if rescoped is not None:
            answering[0] = rescoped.sentence
            async for step in self._steps(rescoped, follow_up_seen, answering):
                yield step
            return
        follow_up_seen[0] = _classify_and_record_visual_follow_up(query, analysis, was)
        intent = analysis.intent
        if intent is Intent.CORRECTION:
            continuation = (
                ContinuationBase(
                    previous,
                    ContinuationSource.LAST_ANSWER,
                    force_follow_up=True,
                )
                if isinstance(previous, FinancialPlan)
                else None
            )

        # Resolve account-scope choices only after the deterministic intent router has had a
        # chance to claim courtesy, balance, payment and transfer turns.  Those intents are
        # independent of a pending ledger choice and must never be swallowed by its chip parser.
        account_choice = isinstance(
            was.active, AccountScopeClarification
        ) and _answers_account_scope(
            query.sentence, tuple(candidate.display_name for candidate in was.active.candidates)
        )
        if isinstance(was.active, AccountScopeClarification) and (
            intent
            not in {
                *_ASIDES,
                Intent.PAYMENT,
                Intent.OWN_TRANSFER,
                Intent.LIST_CARDS,
            }
            or account_choice
        ):
            account_scope = was.active
            if _starts_new_financial_scope(query.sentence, today=query.today):
                query = replace(query, conversation=replace(was, active=None))
                was = query.conversation
            else:
                resumed = await self._resume_account_scope(query, account_scope)
                if resumed is not None:
                    yield resumed
                    return

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

        scope_followup = await self._scope_change_followup(query)
        if scope_followup is not None:
            yield scope_followup
            return

        # A complete two-read calculation is a new question even when a previous plan exists.
        # Generic recovery runs very early and used to read "balance entre ingresos y gastos" as
        # several corrections to the previous expense plan, answering "¿con cuál empezamos?".
        # Recognise the closed compound shape first; its branch still executes below, after the
        # conversational asides and transfer workflow have had their normal precedence.
        relationship = seam.READERS.select(query, among=_BEFORE_THE_ASIDES)
        relaxed = (
            None
            if relationship is not None
            else query_recovery.relaxation(query.sentence, previous)
        )
        if relaxed is not None:
            # These are the exact actions offered after an empty result. They patch one field in
            # code and therefore cannot drift with the configured model.
            yield Stage("planned", answer_writer.plan_in_words(relaxed))
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
                plan_in_words=answer_writer.plan_in_words(previous) if previous else None,
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

        early_refusal = capability_guard.unsupported_reason(query.sentence, self._policy)
        if early_refusal == "CATEGORY_EXCLUSION":
            # The one refusal the guard makes before the deterministic chain rather than after it,
            # and therefore the one that never reaches the record below. Recorded where it acts:
            # the same reading taken at the ordinary seam would be an opinion the turn never used.
            record(
                Step.CAPABILITY_GUARD,
                Verdict.DECLINED,
                changed=True,
                field="family",
                now=early_refusal,
            )
            yield self._declined(early_refusal, was)
            return

        if relationship is not None:
            # Asked far above and answered here, which is the one run whose ask and dispatch are
            # not in the same place. The ask has to happen before `query_recovery.relaxation`,
            # because a ratio suppresses it; the answer has to happen after the asides, because
            # «gracias» is not a ratio question however the sentence reads. Two positions for one
            # reader, and the comment above the ask is the one that says why.
            async for step in self._answering(relationship, query, was):
                yield step
            return
        if was.pending_ratio is not None and intent not in _ASIDES:
            # The pending pair asked only for a period. A different financial question abandons
            # that half-built calculation exactly as an ordinary clarification does.
            was = was.without_pending_ratio()
            query = replace(query, conversation=was)
            # And the continuation base goes with it. `last_plan` behind a half-built ratio is a
            # *mirror* of the focus being abandoned, not a question this turn continues —
            # `contextual_follow_up_plan` declines on exactly that state. Dropping the pending
            # ratio is what makes the mirror indistinguishable from a base, because
            # `planning_context.can_continue` tells the two apart by whether the focus is a
            # `QueryFocus`, and this line has just made it one. Left standing, "ahora en todas mis
            # cuentas" over an abandoned ratio inherits the mirrored twelve months.
            continuation = None

        incoming_payment = await self._incoming_person_payment_plan(query)
        if isinstance(incoming_payment, FinancialPlanDraft):
            person = incoming_payment.counterparties[0]
            question = f"¿De qué periodo quieres comprobar los Bizums recibidos de {person}?"
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=was.with_active(
                    FinancialClarification(draft=incoming_payment, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Los últimos 3 meses", "Este año"],
            )
            return
        if isinstance(incoming_payment, FinancialPlan):
            yield Stage("planned", answer_writer.plan_in_words(incoming_payment))
            yield Stage("querying")
            yield await self._answer(query, incoming_payment)
            return

        past_payment = await self._past_person_payment_plan(query)
        if isinstance(past_payment, FinancialPlanDraft):
            person = past_payment.counterparties[0]
            sent = "los Bizums" if past_payment.categories else "los pagos"
            question = f"¿De qué periodo quieres comprobar {sent} que le has hecho a {person}?"
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=was.with_active(
                    FinancialClarification(draft=past_payment, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Los últimos 3 meses", "Este año"],
            )
            return
        if isinstance(past_payment, FinancialPlan):
            yield Stage("planned", answer_writer.plan_in_words(past_payment))
            yield Stage("querying")
            yield await self._answer(query, past_payment)
            return

        named_subject = seam.READERS.select(query, among=_ON_A_NAMED_SUBJECT)
        if named_subject is not None:
            async for step in self._answering(named_subject, query, was):
                yield step
            return

        account_activity = (
            _account_activity_request(query) if intent is Intent.FINANCIAL_QUERY else None
        )
        if isinstance(account_activity, FinancialPlanDraft):
            account_activity = await self._scoped_financial_draft(account_activity, query)
            question = "¿De qué periodo quieres consultar esa cuenta?"
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=was.with_active(
                    FinancialClarification(draft=account_activity, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Este año", "Los últimos 12 meses"],
            )
            return
        if isinstance(account_activity, FinancialPlan):
            yield Stage("querying")
            yield await self._answer(query, account_activity)
            return

        # The five that answer with two figures. `_comparison_with_a_named_window` stood last of
        # them so the cheapest read first, and its own docstring is where that argument lives; the
        # order is `stood_at` now and nothing here restates it. What did not survive as a comment is
        # the four-branch `if period_comparison is None` chain, because a run of readers asked until
        # one claims is what `select` is.
        comparison = seam.READERS.select(query, among=_ON_A_COMPARISON)
        if comparison is not None:
            async for step in self._answering(comparison, query, was):
                yield step
            return

        if intent is Intent.FINANCIAL_QUERY and account_words.asks_for_other_accounts(
            query.sentence
        ):
            yield Stage("querying")
            yield await self._other_accounts(query)
            return

        account_follow_up = (
            _account_follow_up_plan(query)
            if intent in {Intent.FINANCIAL_QUERY, Intent.CORRECTION}
            else None
        )
        if account_follow_up is not None:
            yield Stage("planned", answer_writer.plan_in_words(account_follow_up))
            yield Stage("querying")
            yield await self._answer(query, account_follow_up)
            return

        # N-H1 (Wave 3 hardening, round 2): `_a_bare_shop_name`'s own stop-list check only ever
        # runs with `was.last_plan` set, because narrowing a *plan* is what that function does.
        # From the state every settled conversation actually opens in -- nothing pending, no chart
        # -- the same bare "todos"/"límite" fell through here to the planner instead, and came
        # back a confident total with an invented LAST_MONTH window (a euro figure for a word that
        # named nothing, Important 6's shape one route over). Checked here, before the planner and
        # before `_a_bare_shop_name`'s narrowing, so the honest ask is reached with a plan pending
        # or without one.
        #
        # `merchant_words.names_no_scope_at_all` rather than the broader `never_a_merchant`: most
        # of that list is conversation control ("sí", "no", "gracias", "déjalo") and each already
        # has its own deliberate fallback when nothing is pending -- a cancelled payment's stray
        # "sí" becomes an ordinary LAST_MONTH query, and the corpus pins exactly that
        # (`07-cancellations.yaml`, `08-confirmations.yaml`). Only F-2's own words carry no such
        # second job. `was.active is None` keeps this from a workflow already open on this very
        # sentence, for the same reason -- a clarification slot or a half-built payment reads its
        # own answer here, and the repeated-question recovery further down this pipeline
        # (`_asks_the_same_thing_again`) is what must still run for those, unchanged.
        stopword_only_sentence = (
            intent is Intent.FINANCIAL_QUERY
            and was.active is None
            and merchant_words.names_no_scope_at_all(query.sentence)
        )
        if stopword_only_sentence:
            stopword_said = query.sentence.strip(" ¿?¡!.,;:")
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=(
                    f"No sé a qué te refieres con «{stopword_said}»; ¿quieres cambiar el "
                    "gráfico, el periodo o la categoría?"
                ),
                question=(
                    f"No sé a qué te refieres con «{stopword_said}»; ¿quieres cambiar el "
                    "gráfico, el periodo o la categoría?"
                ),
                plan=(
                    answer_writer.describe(was.last_plan)
                    if isinstance(was.last_plan, FinancialPlan)
                    else None
                ),
                plan_in_words=(
                    answer_writer.plan_in_words(was.last_plan)
                    if isinstance(was.last_plan, FinancialPlan)
                    else None
                ),
                conversation=was,
            )
            return

        narrowing = _a_bare_shop_name(query, was) if intent is Intent.FINANCIAL_QUERY else None
        if isinstance(narrowing, _NeverAMerchant):
            # Reached, and this comment used to claim otherwise -- reported by review on
            # 2026-09-14. The gate above is `merchant_words.names_no_scope_at_all`, which is five
            # words and one phrase («todos», «todas», «todo», «límite», «sin límite») and is
            # deliberately narrower than `never_a_merchant`: that list also holds «sí», «no»,
            # «gracias», «nada» and the rest of conversation control, and those have to keep
            # falling through to whatever state was waiting on them -- a cancelled payment, a
            # repeated clarification -- rather than being asked about from anywhere.
            #
            # So «nada», with a plan already answered, still arrives here, and is asked about
            # instead of guessed at as a shop. A comment that calls a live branch dead is an
            # invitation to delete it, and deleting this one reopens the bug it closes.
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=(
                    f"No sé a qué te refieres con «{narrowing.said}»; ¿quieres cambiar el "
                    "gráfico, el periodo o la categoría?"
                ),
                question=(
                    f"No sé a qué te refieres con «{narrowing.said}»; ¿quieres cambiar el "
                    "gráfico, el periodo o la categoría?"
                ),
                plan=(
                    answer_writer.describe(was.last_plan)
                    if isinstance(was.last_plan, FinancialPlan)
                    else None
                ),
                plan_in_words=(
                    answer_writer.plan_in_words(was.last_plan)
                    if isinstance(was.last_plan, FinancialPlan)
                    else None
                ),
                conversation=was,
            )
            return
        if narrowing is not None:
            shop, previous = narrowing
            # A whole turn that is one name, with a question behind it. "Spotify", typed after
            # being asked "¿querías solo Spotify?", used to reach the planner as a word with no
            # window and come back "¿de qué periodo?" — a suggestion that asks you something else
            # when you take it is worse than no suggestion at all.
            #
            # No model and no bank call to decide it: the previous plan carries the window and this
            # replaces one field.
            #
            # **It guesses, and then it checks.** Deciding by what a word is *not* — no category, no
            # account, no window, no verb any vocabulary here owns — is a rule that cannot abstain,
            # and the corpus says what that costs once the plans are asserted: "gracais", "déjalo",
            # "nada", "es que", "ah vale" and eleven more became shop filters, and two of them
            # outlived their turn. "¿Cuánto gasté el mes pasado?" ran with merchants=['nada'].
            #
            # The check needs no vocabulary and no extra call. An empty answer already spends a
            # query listing the shops this window does have, and answers `¿te refieres a...?` when
            # one of them is close — so a PLANNED answer with nothing in it is the ledger saying
            # the word matched no shop and resembled none either. That is not a shop, and the
            # question the user was already in is the true thing to answer instead.
            answered = await self._answer(
                query,
                replace(
                    previous,
                    merchants=(shop,),
                    excluded_merchants=tuple(
                        merchant
                        for merchant in previous.excluded_merchants
                        if merchant.casefold() != shop.casefold()
                    ),
                ),
            )
            if answered.outcome == PlanningOutcome.PLANNED.value and not answered.movement_count:
                # The one real shop this gives up on: a shop with no movements in *this* window,
                # where "0 €" was a true answer. It costs a second query on the turn that was
                # producing a wrong one, and the answer it retracts to is the question the user
                # asked, not an invented one.
                answered = await self._answer(query, previous)
            yield answered
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
            yield await self._movement_example(
                query, previous, limit=_movement_example_count(query.sentence)
            )
            return

        if isinstance(previous, TopMovements) and _MOVEMENT_CLASSIFICATION.search(
            spanish.folded(query.sentence)
        ):
            ranked = await self._ranked(query, previous)
            movement = ranked.movements[0] if ranked.movements else None
            if movement is None:
                yield replace(ranked, text="No he encontrado ese movimiento para clasificarlo.")
            elif movement.category == "SALARY":
                yield replace(ranked, text="Fue un ingreso de nómina, no un bizum.")
            elif movement.category == "TRANSFER_BIZUM":
                yield replace(ranked, text="Fue un bizum, no un ingreso de nómina.")
            else:
                category = (
                    category_words.name_of(movement.category)
                    if movement.category
                    else "sin categoría"
                )
                yield replace(ranked, text=f"Ese movimiento está clasificado como {category}.")
            return

        if isinstance(previous, TopMovements) and _RANKED_DATE.fullmatch(
            _clean_turn(query.sentence)
        ):
            ranked = await self._ranked(query, previous)
            movement = ranked.movements[0] if ranked.movements else None
            text = (
                answer_writer.movement_date(movement.occurred_at)
                if movement is not None
                else "No he encontrado ese movimiento para decirte la fecha."
            )
            yield replace(ranked, text=text)
            return

        if (
            isinstance(previous, FinancialPlan)
            and previous.group_by is GroupBy.CATEGORY
            and len(previous.categories) >= 2
            and _COMPARISON_DIFFERENCE.fullmatch(_clean_turn(query.sentence))
        ):
            yield Stage("querying")
            yield await self._group_reference(query, previous, difference=True)
            return

        if (
            isinstance(previous, FinancialPlan)
            and previous.group_by
            in {GroupBy.CATEGORY, GroupBy.MERCHANT, GroupBy.COUNTERPARTY, GroupBy.ACCOUNT}
            and _NEXT_GROUP.fullmatch(_clean_turn(query.sentence))
        ):
            yield Stage("querying")
            yield await self._group_reference(query, previous, difference=False)
            return

        # §3.3's act, gated on the classification this turn already took: a drill-down that named
        # one bar of the chart in view runs the bounded plan behind it; one that fits several bars
        # asks instead of guessing, with no ledger call and the conversation unchanged; `None` is
        # today's path, falling through to everything below exactly as before this task.
        if follow_up_seen[0] is not None and follow_up_seen[0].kind == "DRILL_DOWN":
            drill = read_drill_down(query.sentence, was, today=query.today)
            if isinstance(drill, DrillAsk):
                yield AskFinancialQuestionResult(
                    outcome="DRILL_AMBIGUOUS",
                    text=drill.text,
                    question=drill.text,
                    plan=answer_writer.describe(previous) if previous is not None else None,
                    plan_in_words=answer_writer.plan_in_words(previous)
                    if previous is not None
                    else None,
                    conversation=was,
                    # Item 5 (Wave 3 hardening, T8b): empty for the ordinary several-bars ask
                    # (the question already names its own alternatives); the NONE-grouped ask's
                    # own two chips, otherwise.
                    suggestions=list(drill.suggestions),
                )
                return
            if isinstance(drill, DrillUnbound):
                # A-2 (Wave 3 hardening): the chart's plan was borrowed, not drawn this turn -- the
                # honest ask, the same shape `DRILL_AMBIGUOUS` above already uses, never a silent
                # narrow of whatever `conversation.last_plan` happens to hold.
                yield AskFinancialQuestionResult(
                    outcome="DRILL_UNBOUND",
                    text=drill.text,
                    question=drill.text,
                    plan=answer_writer.describe(previous) if previous is not None else None,
                    plan_in_words=answer_writer.plan_in_words(previous)
                    if previous is not None
                    else None,
                    conversation=was,
                )
                return
            if isinstance(drill, DrillDown):
                yield Stage("planned", answer_writer.plan_in_words(drill.plan))
                yield Stage("querying")
                answered = await self._answer(
                    query, drill.plan, movements=MAX_SELECTED_TRANSACTIONS
                )
                # F-H3 (Wave 3 hardening, fix round): `read_drill_down` clamps the previous plan's
                # own window to the bar the sentence named -- a continuation exactly like a
                # reshape or a regroup, and outside `_answering`'s reader-registry dispatch (this
                # branch answers directly, before `contextual_follow_up_plan` is ever asked), so
                # the causal flag `derived_from` reads is stamped here rather than there.
                yield replace(answered, plan_continues_conversation=True)
                return

        referenced_result = await self._referenced_result_plan(query)
        if referenced_result is not None:
            yield Stage("planned", answer_writer.plan_in_words(referenced_result))
            yield Stage("querying")
            # I-2 (fix wave): ask the ledger for exactly the movements this turn re-lists, rather
            # than none -- the real adapter substitutes the deployment's own `supporting_movements`
            # default (10 in `compose.yaml`) against up to `MAX_SELECTED_TRANSACTIONS` (50)
            # selected ids, so a caller asking with no `movements=` gets ten of the up-to-fifty rows
            # the drill just showed. `None` when the referenced plan names no ids at all -- an
            # ordinary contextual reference, not the evidence path -- so this line changes nothing
            # for it.
            yield await self._answer(
                query,
                referenced_result,
                movements=(
                    len(referenced_result.transaction_ids)
                    if referenced_result.transaction_ids
                    else None
                ),
            )
            return

        in_view = seam.READERS.select(query, among=_ON_THE_QUERY_IN_VIEW)
        if in_view is not None:
            async for step in self._answering(in_view, query, was):
                yield step
            return

        if intent is Intent.CORRECTION:
            if intent_router.is_frustration(query.sentence):
                yield AskFinancialQuestionResult(
                    outcome=Spoken.GUIDANCE.value,
                    text=(
                        "Perdona: he perdido el hilo de la conversación. "
                        "Conservo la última consulta, así que puedes corregirme o hacer otra "
                        "pregunta sin empezar de cero."
                    ),
                    plan=answer_writer.describe(previous) if previous else None,
                    plan_in_words=answer_writer.plan_in_words(previous) if previous else None,
                    conversation=was,
                )
                return
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
            # The correction supplies a replacement. Its forced continuation was selected as soon
            # as the intent became known, before any deterministic branch could return. Ranking
            # patches have a different shape and therefore selected no continuation there.

        deeper = self._next_in_ranking(query)
        if deeper is not None:
            # "¿Y el segundo?" after a ranking. One field changes and nothing is read again, which
            # is what stops this from depending on how the continuation happens to be phrased — the
            # failure the review pointed at when this was a regex over ordinals.
            yield Stage("planned", answer_writer.plan_in_words(deeper))
            yield Stage("querying")
            yield await self._ranked(query, deeper)
            return

        shifted = self._a_year_earlier(query)
        if shifted is not None:
            # "¿Y el año anterior?" after a question about July is July of last year. Answering it
            # as the last twelve months is a different question, and refusing the phrase outright —
            # which is what happened, because no `Period` names it — sent the turn to the model and
            # started a clarification loop about a period the user had just given.
            yield Stage("planned", answer_writer.plan_in_words(shifted))
            yield Stage("querying")
            if isinstance(shifted, TopMovements):
                yield await self._ranked(query, shifted)
            else:
                yield await self._answer(query, shifted)
            return

        # The last run, and the only one whose members were already contiguous: nine readers with
        # nothing between them but each other's dispatch. This is the run the interleaving was
        # hiding — nine `if plan is not None` blocks that differed in four of the nine and were
        # identical in the other five.
        on_the_sentence = seam.READERS.select(query, among=_ON_THE_SENTENCE_ITSELF)
        if on_the_sentence is not None:
            async for step in self._answering(on_the_sentence, query, was):
                yield step
            return

        refused = capability_guard.unsupported_reason(query.sentence, self._policy)
        # The guard's own verdict, both ways, from the one place it is acted on. `AGREED` is here
        # rather than left implicit because an override rate with no agreements in the denominator
        # is a count: without this line the seam would report that the guard refuses 100% of the
        # turns it sees. `now` carries the rule that refused and `None` when none did, which is
        # `capability_guard`'s own vocabulary either way.
        record(
            Step.CAPABILITY_GUARD,
            Verdict.DECLINED if refused is not None else Verdict.AGREED,
            changed=refused is not None,
            field="family",
            now=refused,
        )
        if refused is not None:
            # Before the model, on purpose. Refusing here is a control; refusing after is
            # whatever disposition the configured model happens to have, and that moved
            # by 60 points between two models we measured.
            yield self._declined(refused, was)
            return

        month_fallback = self._pending_month_fallback(query)
        if month_fallback is not None:
            first, second = month_fallback
            yield Stage("planned", answer_writer.plan_in_words(first))
            yield Stage("querying")
            yield await self._answer_with_month_fallback(query, first, second)
            return

        finished = self._finish_pending(query)
        if finished is not None:
            # No model. The question back was about a slot, the reply fills it, and both the window
            # and the slot are closed vocabularies — so there is nothing here to infer.
            yield Stage("planned", answer_writer.plan_in_words(finished))
            yield Stage("querying")
            # «Este año» completes a plan the turn that asked stated, so that turn's sentence is
            # the one the shop readers read. Not when the reply was the category: then the reply
            # is the sentence that named it, and the question never did.
            asked_for_a_category = (
                isinstance(was.active, FinancialClarification)
                and was.active.awaiting is Slot.CATEGORY
            )
            yield await self._answer(
                query,
                finished,
                asked=None if asked_for_a_category else _the_sentence_that_asked(was),
            )
            return

        pending_continuation = self._pending_continuation(query)
        if pending_continuation is not None:
            continuation = pending_continuation

        if (
            continuation is not None
            and not continuation.force_follow_up
            and _starts_new_financial_scope(query.sentence, today=query.today)
        ):
            continuation = None

        context = planning_context(
            was,
            today=query.today,
            reporting_timezone=query.reporting_timezone,
            analysis=analysis,
            continuation=continuation,
        )

        yield Stage("planning")
        grounded = continuation_patch.read(query.sentence, today=context.today)
        grounded_plan: FinancialPlan | None = None
        if (
            grounded.status is continuation_patch.PatchStatus.APPLIED
            and grounded.patch is not None
            and context.continuation is not None
        ):
            try:
                grounded_plan = grounded.patch.apply_to(context.continuation.plan)
            except InvalidValueError:
                # A syntactically grounded patch can still violate a plan invariant (for example,
                # a cumulative metric grouped by category). Fall through to the planner instead
                # of turning a conservative recogniser into a 500.
                grounded_plan = None
        if grounded_plan is not None:
            # Closed-vocabulary continuation: retain the validated plan and still run the
            # ordinary facts, verifier, and answer path below.
            planned = PlanningResult.planned(grounded_plan)
        else:
            planned = await self._planner.plan(
                query.sentence,
                context=context,
            )

        if not planned.is_answerable:
            # Read here as well as below, because a turn that ends in a question read a sentence
            # too. The draft it leaves behind is the only thing the reply has to build on, so what
            # this sentence stated has to reach it — see `_next_state`.
            said = explicit_facts.read(query.sentence, today=query.today)
            rescued = _from_what_the_sentence_said(planned, query)
            if rescued is None:
                yield _decline(planned, was, said)
                return
            if not rescued.is_answerable:
                # A question rather than a plan: the sentence named a category and no period.
                yield _decline(rescued, was, said)
                return
            planned = rescued

        assert planned.plan is not None  # is_answerable is exactly this condition

        # What the sentence stated outright wins over both the model's opinion about which fields
        # changed and the plan it inherited from. "Los últimos 3 meses" was answered over five
        # because the model emitted `changed: ["categories"]` and the merge is arithmetic on that
        # list — a real figure about a window the user had written down and nobody read.
        facts = explicit_facts.read(query.sentence, today=query.today)
        checked = explicit_facts.enforce(planned.plan, facts)
        _enforced(planned.plan, checked, facts)
        # And what its silence means, which `enforce` cannot read: on a continuation naming no
        # period at all, the window is the one from the turn being continued.
        continued = inherited_window(
            checked,
            context.continuation.plan if context.continuation is not None else None,
            facts,
        )
        # Read off the two windows and not off object identity. `inherited_window` builds a new
        # plan whenever it fills a silence, and the merge has usually already carried the same
        # window down — so identity says "inherited" on turns where the figure was never going to
        # move, and `changed` would then disagree with the `was`/`now` it reports.
        model_window, ran_window = _window_of(checked), _window_of(continued)
        record(
            Step.INHERITED_WINDOW,
            Verdict.OVERRODE if ran_window != model_window else Verdict.AGREED,
            changed=ran_window != model_window,
            field="window",
            was=model_window,
            now=ran_window,
        )
        checked = continued
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
                    # What was available to inherit, even when a self-contained question correctly
                    # starts a fresh scope and therefore sends no previous plan to the planner.
                    "inherited": _window_of(previous),
                },
            )
        correcting = (
            context.continuation is not None and context.continuation.force_follow_up
        ) or facts.corrects
        if correcting and context.continuation is not None and isinstance(checked, FinancialPlan):
            # A correction patches. The same turn that lost the window also lost the category the
            # correction never mentioned, and an empty list from a model cannot be told apart from
            # a filter it simply did not copy.
            #
            # `facts.corrects` and not only the router's intent: "no, durante todo el año 2025"
            # rejects *and* replaces, which the router deliberately does not treat as a bare
            # correction — that path is for a rejection with nothing behind it. The sentence is
            # still a patch, and its unmentioned fields still have to survive.
            checked = explicit_facts.restored_filters(checked, context.continuation.plan, facts)

        broken = plan_verifier.disagreement(query.sentence, checked, self._policy)
        # `changed` is the disagreement, not the refusal. A rule that disagreed either had the
        # plan repaired or had the turn refused, and both altered what was answered — the figure
        # that would otherwise have come back is the one this step exists to stop. Which of the
        # two it was is the turn's outcome, and `Step.OUTCOME` records that.
        record(
            Step.PLAN_VERIFIER,
            Verdict.DECLINED if broken is not None else Verdict.AGREED,
            changed=broken is not None,
            detail=broken,
        )
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
                    text=_refusal_for(broken, query.sentence, checked),
                    plan=answer_writer.describe(checked),
                    plan_in_words=answer_writer.plan_in_words(checked),
                    # The plan is shown but not remembered: it is the one that misread the
                    # question, and inheriting from it would carry the misreading into the
                    # next turn.
                    conversation=was,
                )
                return

        yield Stage("planned", answer_writer.plan_in_words(checked))
        yield Stage("querying")
        if isinstance(checked, TopMovements):
            yield await self._ranked(query, checked)
            return
        yield await self._answer(query, checked)

    # --- what a claim becomes -------------------------------------------------------------------
    #
    # **The selection was the easy half.** `select` returns which of the nineteen took the turn;
    # the eleven methods below are what the turn then *is*, and they are not interchangeable. Nine
    # of the nineteen readers share one shape — describe the plan, say a query is running, answer
    # it, which is `_planned_answer` — and each of the other ten differs in something a table
    # cannot hold: a draft that opens a clarification with its own Spanish question, a pair of
    # plans splatted into one call, a count in the `planned` stage, an answer method that is not
    # `_answer`.
    #
    # So the dispatch is a **table from reader to shape**, not a shape computed from the plan's
    # type. Deciding by type was tried on paper and does not survive contact: `_gambling_plan` and
    # `_average_plan` both return `FinancialPlan | FinancialPlanDraft | None` and their drafts ask
    # different questions, `_internal_movement_request` returns the same plan type as eight others
    # and deliberately emits no `planned` stage, and `_multiple_movement_ranking` returns a tuple.
    # A reader is the thing that knows what its answer means, so the reader is the key.
    #
    # `test_every_reader_has_a_shape_to_be_answered_in` is what stops the twentieth reader arriving
    # with no entry and being silently dropped — which is the one failure this design could have
    # that the cascade could not.
    #
    # **`if isinstance(plan, FinancialPlanDraft): … else the plan` is exact, not a simplification.**
    # `_steps` wrote the second half as `isinstance(plan, FinancialPlan)` for two readers and as
    # `is not None` for two others, and all four return `FinancialPlan | FinancialPlanDraft | None`
    # — `_ratio_request` returns `RatioPlan | RatioDraft | None`. `select` never hands over `None`,
    # so the union is exhausted by the two branches and the four spellings were one behaviour
    # written four ways. mypy holds the signatures that make this true.

    async def _answering(
        self, claimed: Claim, query: AskFinancialQuestionQuery, was: Conversation
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        """Turn one reader's claim into the stages and the answer the cascade produced for it.

        `was` is passed rather than read off `query.conversation`, and they are the same object at
        every one of the five call sites — every reassignment of `was` in `_steps` is followed by
        `query = replace(query, conversation=was)`. Passing it is how that stays true by
        construction instead of by inspection: this method uses exactly the variable the inline
        block it replaced used.

        F-H3 (Wave 3 hardening, fix round): every reader this method ever dispatches to is one of
        the five runs `_steps` asks in order, and `claimed.reader` names exactly which one claimed
        the turn. `_CONTINUATION_READERS` is `_ON_A_COMPARISON` and `_ON_THE_QUERY_IN_VIEW` --
        the two runs that resolve their own plan from `focus_of(conversation)`, i.e. patch,
        regroup or split whatever the conversation already carried, as opposed to
        `_ON_THE_SENTENCE_ITSELF` (a fresh plan the sentence alone produced) or
        `_ON_A_NAMED_SUBJECT`. Stamped onto the result here, once, rather than in each of the
        eleven producer methods those two runs dispatch to.
        """
        continuation = claimed.reader in _CONTINUATION_READERS
        async for step in _ANSWERING[claimed.reader](self, query, was, claimed.plan):
            if continuation and isinstance(step, AskFinancialQuestionResult):
                step = replace(step, plan_continues_conversation=True)
            yield step

    async def _a_ratio(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        if isinstance(plan, RatioDraft):
            scoped = await self._scoped_ratio_draft(plan, query)
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text="¿De qué periodo quieres calcularlo?",
                question="¿De qué periodo quieres calcularlo?",
                conversation=was.waiting_for_ratio(scoped),
                suggestions=["Este mes", "El mes pasado", "Este año"],
            )
            return
        # No `planned` stage, which is not an oversight: a ratio is two questions and
        # `answer_writer.describe` speaks about one plan. `_answer_ratio` says what it computed.
        yield Stage("querying")
        yield await self._answer_ratio(query, plan)

    async def _a_gambling_plan(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        if isinstance(plan, FinancialPlanDraft):
            question = (
                "¿De qué periodo quieres que lo compruebe? Buscaré en ocio y en comercios "
                "cuyo nombre contenga apuesta o bet."
            )
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question="¿De qué periodo quieres que lo compruebe?",
                conversation=was.with_active(
                    FinancialClarification(draft=plan, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Los últimos 3 meses", "Este año"],
            )
            return
        async for step in self._planned_answer(query, was, plan):
            yield step

    async def _a_payee_count(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        yield Stage("planned", answer_writer.plan_in_words(plan))
        yield Stage("querying")
        yield await self._how_many_payees(query, plan)

    async def _an_internal_movement(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        if isinstance(plan, FinancialPlanDraft):
            try:
                internal_scoped = await self._scoped_internal_draft(plan, query)
            except AccountScopeNotFoundError:
                yield AskFinancialQuestionResult(
                    outcome=Spoken.GUIDANCE.value,
                    text="No tienes una cuenta de ese tipo en la que buscar traspasos.",
                    conversation=was,
                )
                return
            question = "¿De qué periodo quieres consultar esos traspasos?"
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=was.with_active(
                    FinancialClarification(draft=internal_scoped, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Los últimos 3 meses", "Este año"],
            )
            return
        # And no `planned` stage here either, which *is* the odd one out among the plan-shaped
        # nine. Left exactly as it was: A2 moves code and does not decide that a missing stage was
        # a bug. If it was one, it is a one-line change with its own measurement.
        yield Stage("querying")
        yield await self._answer(query, plan)

    async def _a_salary_history(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        # A pair, splatted: this reader is the only one that answers with two plans rather than a
        # plan or a draft, which is why its shape cannot be shared with the comparisons it sits
        # beside in the same run.
        yield Stage("querying")
        yield await self._compare_salary_history(query, *plan)

    async def _a_period_comparison(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        # Two complete, already validated questions are safer context than asking a model to
        # reconstruct both from "compáralos". Run them again so the comparison uses current
        # bank data, while the conversation keeps the pair available for another pronoun.
        yield Stage("querying")
        yield await self._compare_periods(query, plan)

    async def _a_movement_ranking(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        multi_ranking, count = plan
        yield Stage("planned", f"{answer_writer.plan_in_words(multi_ranking)}, top {count}")
        yield Stage("querying")
        yield await self._ranked_many(query, multi_ranking, count)

    async def _a_single_movement(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        yield Stage("planned", answer_writer.plan_in_words(plan))
        yield Stage("querying")
        yield await self._ranked(query, plan)

    async def _a_breakdown(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        if isinstance(plan, FinancialPlanDraft):
            comparing = bool(
                explicit_facts.read(query.sentence, today=query.today).compared_categories
            )
            question = (
                "¿De qué periodo quieres que compare ambos gastos?"
                if comparing
                else (
                    "¿De qué periodo quieres que busque la nómina y la cuenta en la que entró?"
                    if plan.group_by is GroupBy.ACCOUNT and plan.categories == ("SALARY",)
                    else "¿De qué periodo quieres que haga el desglose?"
                )
            )
            conversation = was.with_active(FinancialClarification(draft=plan, awaiting=Slot.WINDOW))
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=conversation,
                suggestions=["Este mes", "Este año", "Los últimos 4 meses"],
            )
            return
        async for step in self._planned_answer(query, was, plan):
            yield step

    async def _an_average(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        if isinstance(plan, FinancialPlanDraft):
            conversation = (
                was
                if isinstance(was.active, PaymentInProgress) and was.active.suspended
                else was.with_active(FinancialClarification(draft=plan, awaiting=Slot.WINDOW))
            )
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text="¿De qué periodo quieres que calcule la media?",
                question="¿De qué periodo quieres que calcule la media?",
                conversation=conversation,
                suggestions=[
                    "Este mes",
                    "El mes pasado",
                    "Los últimos 12 meses",
                    f"En {query.today.year}",
                ],
            )
            return
        # Average vocabulary is closed enough to plan without a model. This shape runs before the
        # legacy guard that used to reject all time-normalised means.
        async for step in self._planned_answer(query, was, plan):
            yield step

    async def _planned_answer(
        self, query: AskFinancialQuestionQuery, was: Conversation, plan: Any
    ) -> AsyncIterator[Stage | AskFinancialQuestionResult]:
        """Describe it, say a query is running, answer it. Nine of the nineteen, spelled once.

        It was spelled nine times in `_steps` — nine identical four-line blocks, which is the part
        of the interleaving that was pure repetition rather than a difference anybody chose.
        """
        yield Stage("planned", answer_writer.plan_in_words(plan))
        yield Stage("querying")
        yield await self._answer(query, plan)

    def _declined(self, capability: str, was: Conversation) -> AskFinancialQuestionResult:
        """A refusal that remembers what it was about.

        The draft is not available here — the guard runs before the model, so nothing has read the
        sentence yet — and the capability plus the rank are enough for the follow-up. What the state
        buys is that "¿y el segundo?" no longer has to be recognised as a sentence at all.
        """
        text = _MISSING_CAPABILITY.get(capability, _CANNOT_YET)
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.UNSUPPORTED.value,
            text=text,
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

    async def _incoming_person_payment_plan(
        self, query: AskFinancialQuestionQuery
    ) -> FinancialPlan | FinancialPlanDraft | None:
        """Read an incoming person-to-person payment without inheriting the previous subject."""
        if not incoming_payment_words.is_incoming(query.sentence):
            return None
        try:
            directory = await self._contacts.all_of(query.account_id)
        except BankingUnavailableError:
            directory = ()
        person = incoming_payment_words.counterparty_in(query.sentence, directory)
        if person is None:
            return None
        return self._person_payment_plan(query, person, Direction.INCOME)

    async def _past_person_payment_plan(
        self, query: AskFinancialQuestionQuery
    ) -> FinancialPlan | FinancialPlanDraft | None:
        """The same reading in the other direction: a payment the holder says they already made.

        "Le he mandado 20 € a Marta Solis Vega" is a statement about a movement that exists. The
        payment router already refuses to *repeat* it — that is the expensive half, and offering to
        pay twice is the worst thing this rail can do — but the sentence then reached the planner
        with nothing recognised in it, and what came back was a query about a period nobody named.

        Every word it needs is grounded here: the tense from the payment vocabulary, the person
        from this holder's own directory. A name the directory does not have is nobody, and the
        turn goes on to the model rather than becoming a query about a stranger.
        """
        if not payment_router.describes_a_past_payment(query.sentence):
            return None
        try:
            directory = await self._contacts.all_of(query.account_id)
        except BankingUnavailableError:
            directory = ()
        person = payment_words.name_as_written(query.sentence, directory)
        if person is None:
            return None
        return self._person_payment_plan(query, person, Direction.EXPENSE)

    def _person_payment_plan(
        self, query: AskFinancialQuestionQuery, person: str, direction: Direction
    ) -> FinancialPlan | FinancialPlanDraft:
        """Movements between the holder and one person, in whichever direction was read.

        A draft when the sentence names no window and there is nothing behind it to inherit one
        from: the period is the one thing neither vocabulary can know, and asking for it is cheaper
        than a month somebody did not ask about.
        """
        window = window_words.window_in(query.sentence, today=query.today)
        if (
            window is None
            and query.conversation.active is None
            and query.conversation.last_plan is not None
        ):
            previous = query.conversation.last_plan
            window = previous.period or previous.date_range
        is_bizum = "bizum" in spanish.folded(query.sentence)
        categories = ("TRANSFER_BIZUM",) if is_bizum else ()
        transaction_types = (TransactionType.BIZUM,) if is_bizum else (TransactionType.TRANSFER,)
        if window is None:
            return FinancialPlanDraft(
                direction=direction,
                categories=categories,
                counterparties=(person,),
                counterparty_types=(CounterpartyType.PERSON,),
                transaction_types=transaction_types,
            )
        return FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            counterparties=(person,),
            counterparty_types=(CounterpartyType.PERSON,),
            transaction_types=transaction_types,
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
            plan_in_words=answer_writer.plan_in_words(was.last_plan) if was.last_plan else None,
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
            plan_in_words=answer_writer.plan_in_words(was.last_plan) if was.last_plan else None,
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

    async def _learn_account_alias(
        self, query: AskFinancialQuestionQuery
    ) -> AskFinancialQuestionResult | None:
        definition = account_words.alias_definition_in(query.sentence)
        if definition is None:
            return None
        alias, target = definition
        entity_aliases = {
            known.alias.casefold().strip() for known in query.conversation.counterparty_aliases
        }
        if alias.casefold().strip() in entity_aliases:
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    f"No puedo usar «{alias}» para esa cuenta porque ya identifica una "
                    "contraparte. Elige otro nombre para no mezclar ambos filtros."
                ),
                conversation=query.conversation,
            )
        if account_words.named_in(alias):
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    f"No puedo redefinir «{alias}» porque ya es un nombre general de cuenta. "
                    "Elige un alias más personal, como «viajes» o «recibos»."
                ),
                conversation=query.conversation,
            )
        try:
            if self._account_directory is not None:
                holdings = await self._account_directory.all_of(query.account_id)
            else:
                summary = await self._accounts.of(
                    query.account_id,
                    today=query.today,
                    reporting_timezone=query.reporting_timezone,
                )
                holdings = summary.holdings
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No he podido comprobar esa cuenta, así que no he guardado el alias.",
                conversation=query.conversation,
            )

        display_names = tuple(holding.display_name for holding in holdings if holding.display_name)
        chosen = account_words.chosen_among(target, display_names)
        wanted = [holding for holding in holdings if holding.display_name in chosen]
        if not wanted:
            kinds = account_words.named_in(target)
            wanted = [holding for holding in holdings if holding.kind in kinds]
        if len(wanted) != 1:
            available = ", ".join(holding.display_name for holding in holdings)
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    f"No he podido asociar ese alias a una sola cuenta. "
                    f"Tus cuentas son: {available}."
                ),
                conversation=query.conversation,
            )
        holding = wanted[0]
        remembered = query.conversation.with_account_alias(
            AccountAlias(
                alias=alias,
                account_id=holding.account_id,
                display_name=holding.display_name,
            )
        )
        return AskFinancialQuestionResult(
            outcome=Spoken.GUIDANCE.value,
            text=f"Entendido: cuando digas «{alias}», usaré {holding.display_name}.",
            conversation=remembered,
        )

    async def _learn_counterparty_alias(
        self, query: AskFinancialQuestionQuery
    ) -> AskFinancialQuestionResult | None:
        definition = entity_alias_words.alias_definition_in(query.sentence)
        if definition is None:
            return None
        alias, target, counterparty_type = definition
        account_aliases = {
            known.alias.casefold().strip() for known in query.conversation.account_aliases
        }
        if alias.casefold().strip() in account_aliases or account_words.named_in(alias):
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    f"No puedo usar «{alias}» para esa contraparte porque ya identifica "
                    "una cuenta. "
                    "Elige otro nombre para evitar mezclar saldos y movimientos."
                ),
                conversation=query.conversation,
            )

        # Learning is grounded in the authenticated ledger. Storing arbitrary prose here would
        # turn every future use into a valid-looking empty query and would let a PERSON drift back
        # into the historical merchant field.
        probe = FinancialPlan(
            direction=Direction.ALL,
            period=RelativePeriod(Period.LAST_N_MONTHS, 24),
            counterparties=(target,),
            counterparty_types=(counterparty_type,),
            metric=Metric.COUNT,
            include_internal=True,
            across_accounts=True,
        )
        try:
            found = await self._queries.run(
                probe,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=0,
            )
        except (BankingUnavailableError, QueryRejectedError):
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No he podido comprobar esa contraparte, así que no he guardado el alias.",
                conversation=query.conversation,
            )
        if found.movement_count < 1:
            label = answer_writer.COUNTERPARTY_LABELS[counterparty_type]
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    f"No encuentro a {target} como {label} en tus movimientos recientes, "
                    "así que no he guardado el alias."
                ),
                conversation=query.conversation,
            )
        remembered = query.conversation.with_counterparty_alias(
            CounterpartyAlias(alias, target, counterparty_type)
        )
        return AskFinancialQuestionResult(
            outcome=Spoken.GUIDANCE.value,
            text=(
                f"Entendido: cuando digas «{alias}», filtraré por {target} "
                f"como {answer_writer.COUNTERPARTY_LABELS[counterparty_type]}."
            ),
            conversation=remembered,
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
        alias_names = account_words.learned_aliases_in(
            query.sentence,
            tuple(alias.alias for alias in was.account_aliases),
        )
        alias_ids = {
            alias.account_id for alias in was.account_aliases if alias.alias in alias_names
        }
        selected = (
            tuple(holding for holding in summary.holdings if holding.account_id in alias_ids)
            if alias_ids
            else (
                tuple(holding for holding in summary.holdings if holding.display_name in custom)
                if custom
                else (
                    () if account_words.explicitly_names_a_custom_account(query.sentence) else None
                )
            )
        )

        return AskFinancialQuestionResult(
            outcome=Spoken.BALANCE.value,
            text=answer_writer.balance(summary, named, selected=selected),
            plan=answer_writer.describe(was.last_plan) if was.last_plan else None,
            plan_in_words=answer_writer.plan_in_words(was.last_plan) if was.last_plan else None,
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
            # The fallback wording, for a deployment whose caller may not stage money. The route
            # replaces both the text and the state when it can offer a preview instead — which is
            # what a holder who just said how much and where is asking for.
            text=answer_writer.ready_to_move(wanted[0], amount),
            # Read in full, so nothing is left open here. Whether a preview follows is the route's
            # decision, because staging it needs a scope this read path does not have.
            conversation=was.with_active(None),
            currency=wanted[0].currency,
            total_minor=amount,
            transfer_to=str(wanted[0].account_id),
        )

    def _pending_month_fallback(
        self, query: AskFinancialQuestionQuery
    ) -> tuple[FinancialPlan, FinancialPlan] | None:
        """Two numbered months supplied as an ordered fallback to a payroll clarification."""
        active = query.conversation.active
        if (
            not isinstance(active, FinancialClarification)
            or active.awaiting is not Slot.WINDOW
            or active.draft.categories != ("SALARY",)
        ):
            return None
        found = _NUMBERED_MONTH_FALLBACK.search(spanish.folded(query.sentence))
        if found is None:
            return None
        first_window = _numbered_month_window(int(found.group("first")), query.today)
        second_window = _numbered_month_window(int(found.group("second")), query.today)
        try:
            first = active.draft.with_window(first_window)
            second = active.draft.with_window(second_window)
        except InvalidValueError:
            return None
        if (
            plan_verifier.disagreement(f"mes {found.group('first')}", first, self._policy)
            is not None
            or plan_verifier.disagreement(f"mes {found.group('second')}", second, self._policy)
            is not None
        ):
            return None
        return first, second

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

        **And the sentence has to be an answer.** That was the fourth condition and it was missing:
        a whole new financial question names a window too, so "¿cuánto gasté el mes pasado?" asked
        with "¿qué movimientos he tenido entre mis cuentas?" still open completed *that* draft and
        came back restricted to internal movements — and in the fund episode, restricted to the
        fund as well. The holder asked what they spent and was told what they had moved into their
        own accounts, as a real figure with the right window on it.

        A state that takes every sentence is the shape this service keeps being wrong in. The draft
        is waiting for one missing field; a sentence that states a question of its own is not that
        field, and it belongs to the path that reads whole questions.
        """
        active = query.conversation.active
        if not isinstance(active, FinancialClarification):
            return None
        if _starts_new_financial_scope(query.sentence, today=query.today):
            return None
        draft = active.draft
        if active.awaiting is Slot.CATEGORY:
            # A category answer is a closed-vocabulary completion just like a named period. Only
            # exact product terms take this shortcut; ambiguous words still go to the planner.
            named = category_words.precisely_named_in(query.sentence)
            if not named or draft.needs_a_window:
                return None
            finished = replace(
                draft,
                categories=named,
                excluded_categories=tuple(
                    category for category in draft.excluded_categories if category not in named
                ),
            ).into_plan()
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

    def _pending_continuation(self, query: AskFinancialQuestionQuery) -> ContinuationBase | None:
        """The validated fields behind an active clarification, completed as far as this turn can.

        A compound reply such as "este año, pero cuéntalas" supplies the missing window and also
        changes the metric. `_finish_pending` correctly refuses to execute the old metric; this
        method then gives the completed draft to the planner as a forced patch instead of losing it.

        The same fourth condition as above, and for the same reason: forcing the draft's settled
        fields on a whole new question hands the model the wrong ones to keep.
        """
        active = query.conversation.active
        if not isinstance(active, FinancialClarification):
            return None
        if _starts_new_financial_scope(query.sentence, today=query.today):
            return None
        draft = active.draft
        complete = draft.into_plan()
        if complete is not None:
            return ContinuationBase(
                complete,
                ContinuationSource.ACTIVE_CLARIFICATION,
                force_follow_up=True,
            )

        window = window_words.window_in(
            query.sentence, asked_for=active.awaiting, today=query.today
        )
        if window is None:
            return None
        try:
            return ContinuationBase(
                draft.with_window(window),
                ContinuationSource.ACTIVE_CLARIFICATION,
                force_follow_up=True,
            )
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
        focus = focus_of(query.conversation)
        previous = focus.current if isinstance(focus, QueryFocus) else None
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
        focus = focus_of(query.conversation)
        previous = focus.current if isinstance(focus, QueryFocus) else None
        if previous is None or previous.date_range is None:
            return None
        moved = window_words.a_year_earlier(query.sentence, previous.date_range)
        if moved is None:
            return None
        return replace(previous, date_range=moved)

    async def _scope_change_followup(
        self, query: AskFinancialQuestionQuery
    ) -> AskFinancialQuestionResult | None:
        """Change only the account scope of the last validated query.

        Phrases such as ``ahora en todas mis cuentas`` contain no new period, so sending them
        through planning loses the previous window.  They are a deterministic patch to the
        already validated plan instead.
        """
        selected = _query_for_reader(query)
        previous = selected.plan
        if previous is None or not _is_scope_change_followup(selected.wording, today=query.today):
            return None
        reader_query = replace(query, sentence=selected.wording)
        scoped: PlannedQuery | AskFinancialQuestionResult
        if account_words.asks_for_all_accounts(selected.wording):
            scoped = replace(
                previous, across_accounts=True, only_account_id=None, only_account_ids=()
            )
        elif isinstance(previous, TopMovements):
            # The previous ranking may already carry its old scope.  Clear it before resolving
            # the newly named account; otherwise `_scope_ranking` quite correctly treats the
            # existing scope as authoritative and ignores the follow-up.
            unscoped = replace(
                previous, across_accounts=False, only_account_id=None, only_account_ids=()
            )
            scoped = await self._scope_ranking(reader_query, unscoped)
            if isinstance(scoped, AskFinancialQuestionResult):
                return scoped
        else:
            try:
                scoped = await self._scoped(previous, reader_query, clarify=True)
            except AccountScopeRequiredError as required:
                return AskFinancialQuestionResult(
                    outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                    text="Elige una de estas cuentas: "
                    + ", ".join(candidate.display_name for candidate in required.candidates)
                    + ", o Todas mis cuentas.",
                    suggestions=[candidate.display_name for candidate in required.candidates]
                    + ["Todas mis cuentas"],
                    conversation=query.conversation,
                )
            except BankingUnavailableError:
                return AskFinancialQuestionResult(
                    outcome="BANKING_UNAVAILABLE",
                    text="No puedo comprobar tus cuentas para cambiar el alcance.",
                    conversation=query.conversation,
                )
        if isinstance(scoped, TopMovements):
            return await self._ranked(query, scoped)
        return await self._answer(query, scoped)

    async def _scope_ranking(
        self,
        query: AskFinancialQuestionQuery,
        ranking: TopMovements,
        *,
        requested_count: int | None = None,
    ) -> TopMovements | AskFinancialQuestionResult:
        if ranking.across_accounts or ranking.only_account_id or ranking.only_account_ids:
            return ranking
        if account_words.asks_for_all_accounts(query.sentence):
            return replace(ranking, across_accounts=True)
        if self._account_directory is None:
            return ranking
        named = account_words.named_in(query.sentence)
        if named:
            try:
                holdings = (
                    await self._account_directory.all_of(query.account_id)
                    if self._account_directory is not None
                    else (
                        await self._accounts.of(
                            query.account_id,
                            today=query.today,
                            reporting_timezone=query.reporting_timezone,
                        )
                    ).holdings
                )
            except BankingUnavailableError:
                return AskFinancialQuestionResult(
                    outcome="BANKING_UNAVAILABLE",
                    text="No puedo comprobar tus cuentas para consultar ese movimiento.",
                    conversation=query.conversation,
                )
            chosen = tuple(h.account_id for h in holdings if h.kind in named)
            if chosen:
                return replace(
                    ranking,
                    only_account_id=chosen[0] if len(chosen) == 1 else None,
                    only_account_ids=chosen if len(chosen) > 1 else (),
                )
        selection = query.conversation.account_selection
        if selection is not None:
            if selection.mode is AccountSelectionMode.ALL:
                return replace(ranking, across_accounts=True)
            if self._account_directory is not None:
                holdings = await self._account_directory.all_of(query.account_id)
                live = {holding.account_id: holding for holding in holdings}
                if not set(selection.account_ids).issubset(live):
                    candidates = tuple(
                        AccountCandidate(h.account_id, h.display_name, h.kind)
                        for h in holdings
                        if h.display_name
                    )
                    return AskFinancialQuestionResult(
                        outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                        text="La cuenta seleccionada ya no está disponible. Elige otra.",
                        suggestions=[candidate.display_name for candidate in candidates]
                        + ["Todas mis cuentas"],
                        conversation=query.conversation.with_active(
                            AccountScopeClarification(
                                ranking,
                                candidates,
                                requested_count=requested_count,
                                explicit_visualization=_asks_for_visualization(query.sentence),
                            )
                        ),
                    )
            return replace(
                ranking,
                only_account_id=(
                    selection.account_ids[0] if len(selection.account_ids) == 1 else None
                ),
                only_account_ids=selection.account_ids if len(selection.account_ids) > 1 else (),
            )
        try:
            holdings = (
                await self._account_directory.all_of(query.account_id)
                if self._account_directory is not None
                else (
                    await self._accounts.of(
                        query.account_id,
                        today=query.today,
                        reporting_timezone=query.reporting_timezone,
                    )
                ).holdings
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No puedo comprobar tus cuentas para consultar ese movimiento.",
                conversation=query.conversation,
            )
        if len(holdings) <= 1:
            if len(holdings) == 1:
                return replace(ranking, only_account_id=holdings[0].account_id)
            return ranking
        candidates = tuple(
            AccountCandidate(h.account_id, h.display_name, h.kind)
            for h in holdings
            if h.display_name
        )
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text="¿En qué cuenta quieres consultarlo? Puedes elegir "
            + ", ".join(candidate.display_name for candidate in candidates)
            + " o Todas mis cuentas.",
            question="¿En qué cuenta quieres consultarlo?",
            suggestions=[candidate.display_name for candidate in candidates]
            + ["Todas mis cuentas"],
            conversation=query.conversation.with_active(
                AccountScopeClarification(
                    ranking,
                    candidates,
                    requested_count=requested_count,
                    explicit_visualization=_asks_for_visualization(query.sentence),
                )
            ),
        )

    async def _ranked(
        self, query: AskFinancialQuestionQuery, ranking: TopMovements
    ) -> AskFinancialQuestionResult:
        """One movement, named. The figure alone would send the user to the movement list."""
        scoped = await self._scope_ranking(query, ranking)
        if isinstance(scoped, AskFinancialQuestionResult):
            return scoped
        ranking = scoped
        query = _remember_explicit_scope(query, ranking)
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
                    plan_in_words=answer_writer.plan_in_words(ranking),
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
                    plan_in_words=answer_writer.plan_in_words(ranking),
                    conversation=query.conversation.awaiting_retry(ranking),
                    suggestions=query_recovery.retry_suggestions(),
                )

        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=(
                answer_writer.ranked(found, ranking)
                + (
                    f"\n{_scope_note(ranking, query.sentence)}"
                    if _scope_note(ranking, query.sentence)
                    else ""
                )
            ),
            plan=answer_writer.describe(ranking),
            plan_in_words=answer_writer.plan_in_words(ranking),
            # Banking's, never this layer's. A hard-coded "EUR" here labelled a movement on a
            # dollar account as euros — the one kind of wrong answer a ledger-backed product
            # cannot afford, because the figure beside it is correct.
            currency=found.currency if found else None,
            total_minor=found.amount_minor if found else None,
            movement_count=1 if found else 0,
            movements=[_spoken_movement(found)] if found else [],
            suggestions=query_recovery.empty_suggestions(ranking) if found is None else [],
            # Remembered even when nothing was found: "¿y el segundo?" after "no hay quinto" is a
            # reasonable thing to say, and the question it refers to is this one.
            conversation=answered_with_result(
                query.conversation,
                ResultReference(
                    source_query=ranking,
                    transaction_ids=_evidence_ids([found]) if found is not None else (),
                ),
            ),
        )

    async def _ranked_many(
        self, query: AskFinancialQuestionQuery, ranking: TopMovements, count: int
    ) -> AskFinancialQuestionResult:
        """Read a bounded top-N while keeping each row grounded in banking's ranking."""
        scoped = await self._scope_ranking(query, ranking, requested_count=count)
        if isinstance(scoped, AskFinancialQuestionResult):
            return scoped
        ranking = scoped
        query = _remember_explicit_scope(query, ranking)
        found: list[Movement] = []
        for rank in range(1, count + 1):
            candidate = ranking.at_rank(rank)
            movement: Movement | None = None
            for attempt in range(self._read_attempts):
                try:
                    movement = await self._queries.ranked(
                        candidate,
                        account_id=query.account_id,
                        today=query.today,
                        reporting_timezone=query.reporting_timezone,
                    )
                    break
                except QueryRejectedError:
                    return AskFinancialQuestionResult(
                        outcome=PlanningOutcome.INVALID_DOMAIN.value,
                        text="He entendido el ranking, pero el banco ha rechazado la consulta.",
                        plan=f"{answer_writer.describe(ranking)}, top {count}",
                        plan_in_words=f"{answer_writer.plan_in_words(ranking)}, top {count}",
                        conversation=query.conversation,
                    )
                except BankingUnavailableError:
                    if attempt + 1 < self._read_attempts:
                        continue
                    return AskFinancialQuestionResult(
                        outcome="BANKING_UNAVAILABLE",
                        text="El banco no ha respondido. Vuelve a intentarlo.",
                        plan=f"{answer_writer.describe(ranking)}, top {count}",
                        plan_in_words=f"{answer_writer.plan_in_words(ranking)}, top {count}",
                        conversation=query.conversation,
                    )
            if movement is None:
                break
            found.append(movement)

        remembered = ranking.at_rank(max(1, len(found)))
        currencies = {movement.currency for movement in found}
        one_currency = next(iter(currencies)) if len(currencies) == 1 else None
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=(
                answer_writer.ranked_many(tuple(found), ranking, requested=count)
                + (
                    f"\n{_scope_note(ranking, query.sentence)}"
                    if _scope_note(ranking, query.sentence)
                    else ""
                )
            ),
            plan=f"{answer_writer.describe(ranking)}, top {count}",
            plan_in_words=f"{answer_writer.plan_in_words(ranking)}, top {count}",
            currency=one_currency,
            # A ranked list can span accounts. Different currencies must stay as separate rows;
            # adding them would manufacture a monetary total with no valid unit.
            total_minor=(
                sum(movement.amount_minor for movement in found)
                if found and len(currencies) == 1
                else None
            ),
            movement_count=len(found),
            movements=[_spoken_movement(movement) for movement in found],
            suggestions=query_recovery.empty_suggestions(ranking) if not found else [],
            conversation=answered_with_result(
                query.conversation,
                ResultReference(
                    source_query=remembered,
                    transaction_ids=_evidence_ids(found),
                ),
            ),
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
        selected_ids = _evidence_ids(movements)
        remembered = replace(plan, transaction_ids=selected_ids) if selected_ids else plan
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.recent_movements(len(movements)),
            plan=f"últimos {limit} movimientos, todas tus cuentas",
            conversation=answered_with_result(
                query.conversation,
                ResultReference(
                    source_query=remembered,
                    transaction_ids=selected_ids,
                ),
            ),
            currency=answer.currency,
            movement_count=len(movements),
            movements=[_spoken_movement(movement) for movement in movements],
        )

    async def _movement_example(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan, *, limit: int = 1
    ) -> AskFinancialQuestionResult:
        """One concrete row from the last answer, without asking the model what «esos» means."""
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
                text="No he podido volver a consultar esos movimientos.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        movements = answer.movements[:limit]
        movement = next(iter(movements), None)
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.movement_examples(movements, answer.currency),
            plan=answer_writer.describe(plan),
            plan_in_words=answer_writer.plan_in_words(plan),
            conversation=query.conversation,
            currency=answer.currency,
            total_minor=movement.amount_minor if movement is not None else None,
            movement_count=len(movements),
            movements=[_spoken_movement(item) for item in movements],
        )

    async def _referenced_result_plan(
        self, query: AskFinancialQuestionQuery
    ) -> FinancialPlan | None:
        """Turn a pronoun into the bank-backed row or evidence set already in view."""
        selected_query = _query_for_reader(query)
        focus = focus_of(query.conversation)
        reference = result_reference_of(
            query.conversation,
            focus,
            returned_from_balance=selected_query.returned_from_balance,
        )
        if reference is None or selected_query.plan is not reference.source_query:
            return None
        previous = reference.source_query
        folded = spanish.folded(selected_query.wording)
        facts = explicit_facts.read(selected_query.wording, today=query.today)

        if _MOVEMENT_SET_REFERENCE.search(folded):
            selected = reference.transaction_ids
            if not selected:
                return None
            if isinstance(previous, TopMovements):
                return explicit_facts.reshaped(
                    FinancialPlan(
                        direction=facts.direction or previous.direction,
                        period=previous.period,
                        date_range=previous.date_range,
                        categories=previous.categories,
                        excluded_categories=previous.excluded_categories,
                        merchants=previous.merchants,
                        merchant_terms=previous.merchant_terms,
                        excluded_merchants=previous.excluded_merchants,
                        transaction_ids=selected,
                    ),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or Metric.SUM,
                )
            if not isinstance(previous, FinancialPlan):
                return None
            # Through `reshaped`, because ungrouping is half a decision. "Esos cinco" states no
            # grouping, so this resets it to NONE — and after "¿cuál es mi gasto medio mensual?"
            # the inherited PERIOD_AVERAGE has no buckets left to average over. Neither turn said
            # anything contradictory; the pair simply arrived one field at a time.
            return explicit_facts.enforce(
                explicit_facts.reshaped(
                    replace(previous, transaction_ids=selected),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )

        if not isinstance(previous, FinancialPlan):
            return None

        if _CATEGORY_REFERENCE.search(folded):
            category = reference.category
            if category is None or category_words.words_for(category) is None:
                return None
            return explicit_facts.enforce(
                explicit_facts.reshaped(
                    replace(
                        previous,
                        categories=(category,),
                        excluded_categories=tuple(
                            excluded
                            for excluded in previous.excluded_categories
                            if excluded != category
                        ),
                        merchants=(),
                        excluded_merchants=(),
                        transaction_ids=(),
                    ),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )

        dimension = reference.group_by
        key = reference.group_key
        if dimension is None or key is None:
            return None
        if dimension is GroupBy.MERCHANT and _MERCHANT_REFERENCE.search(folded):
            return explicit_facts.enforce(
                explicit_facts.reshaped(
                    replace(
                        previous,
                        merchants=(key,),
                        excluded_merchants=tuple(
                            merchant
                            for merchant in previous.excluded_merchants
                            if merchant.casefold() != key.casefold()
                        ),
                        transaction_ids=(),
                    ),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )
        if dimension is GroupBy.COUNTERPARTY and (
            _COUNTERPARTY_REFERENCE.search(folded) or _MERCHANT_REFERENCE.search(folded)
        ):
            return explicit_facts.enforce(
                explicit_facts.reshaped(
                    replace(
                        previous,
                        counterparties=(key,),
                        excluded_counterparties=tuple(
                            counterparty
                            for counterparty in previous.excluded_counterparties
                            if counterparty.casefold() != key.casefold()
                        ),
                        transaction_ids=(),
                    ),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )
        if dimension is not GroupBy.ACCOUNT or _ACCOUNT_REFERENCE.search(folded) is None:
            return None

        try:
            if self._account_directory is not None:
                holdings = await self._account_directory.all_of(query.account_id)
            else:
                summary = await self._accounts.of(
                    query.account_id,
                    today=query.today,
                    reporting_timezone=query.reporting_timezone,
                )
                holdings = summary.holdings
        except BankingUnavailableError:
            return None
        selected_account = next(
            (
                holding.account_id
                for holding in holdings
                if str(holding.account_id) == key
                or spanish.folded(holding.display_name) == spanish.folded(key)
            ),
            None,
        )
        if selected_account is None:
            return None
        return explicit_facts.enforce(
            explicit_facts.reshaped(
                replace(
                    previous,
                    across_accounts=False,
                    only_account_id=selected_account,
                    only_account_ids=(),
                    transaction_ids=(),
                ),
                group_by=facts.group_by or GroupBy.NONE,
                metric=facts.metric or previous.metric,
            ),
            facts,
        )

    async def _how_many_payees(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan
    ) -> AskFinancialQuestionResult:
        """Run the merchant breakdown and lead with how many payees it found.

        The same query the ordinary breakdown runs — this differs only in the sentence, because the
        question is about the size of the list rather than about the money in it. Empty groups are
        dropped: banking may return a merchant with no movements in the window, and counting one
        as a subscription somebody has would be inventing the answer.
        """
        scoped = await self._scoped(plan, query)
        try:
            answer = await self._queries.run(
                scoped,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="El banco ha rechazado esa consulta.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )
        counted = replace(answer, groups=tuple(g for g in answer.groups if g.movement_count > 0))
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=answer_writer.how_many_payees(counted, scoped),
            plan=answer_writer.describe(scoped),
            plan_in_words=answer_writer.plan_in_words(scoped),
            plan_context=_spoken(scoped),
            conversation=answered_with_result(
                query.conversation,
                ResultReference(source_query=scoped),
            ),
            currency=counted.currency,
            # The figure the question asked for. It is the size of the list, not the money in it —
            # a total here would put the wrong number in the field a client draws large.
            movement_count=len([g for g in counted.groups if g.key]),
            groups=[
                SpokenGroup(
                    key=group.key,
                    total_minor=group.total_minor,
                    movement_count=group.movement_count,
                )
                for group in counted.groups
            ],
            # `window` stays `None` here (the dataclass default) and at every other
            # `VisualDataset(` this handler builds outside `_visual_dataset` -- a payee count, a
            # period comparison, a ratio share, a salary history. None of those is one plan's own
            # answered window over its own rows the way `_visual_dataset`'s three call sites are,
            # so a month drill over them is not a case Wave 4.3 covers; `read_drill_down` reads
            # `window is None` as "refuse, never guess" for exactly this reason.
            visual_dataset=VisualDataset(
                rows=tuple(
                    VisualRow(dimension=group.key, value_minor=group.movement_count)
                    for group in counted.groups
                ),
                metric=Metric.COUNT.value,
                title=answer_writer.describe(scoped),
            ),
        )

    async def _group_reference(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan, *, difference: bool
    ) -> AskFinancialQuestionResult:
        """Answer a pronoun about the grouped result already on screen.

        The group values are queried again rather than stored in conversation state. That keeps
        the client carrying meaning (the validated plan), not cached financial data, while making
        "what comes after that?" and "what is the difference?" real follow-ups.
        """
        scoped = await self._scoped(plan, query)
        try:
            answer = await self._queries.run(
                scoped,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="He entendido la referencia, pero el banco ha rechazado la consulta.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        groups = [group for group in answer.groups if group.movement_count > 0]
        if difference:
            if len(groups) < 2:
                text = "No hay dos resultados con movimientos entre los que calcular la diferencia."
            else:
                first, second = groups[0], groups[1]
                high, low = (
                    (first, second) if first.total_minor >= second.total_minor else (second, first)
                )
                amount = answer_writer.money(high.total_minor - low.total_minor, answer.currency)
                text = (
                    f"Hay {amount} de diferencia: "
                    f"{category_words.name_of(high.key)} supera a "
                    f"{category_words.name_of(low.key)}."
                )
        elif len(groups) < 2:
            text = "No hay un segundo resultado en ese desglose."
        else:
            second = groups[1]
            name = (
                category_words.name_of(second.key)
                if plan.group_by is GroupBy.CATEGORY
                else second.key
            )
            text = (
                f"El siguiente es {name}: "
                f"{answer_writer.money(second.total_minor, answer.currency)}, "
                f"en {second.movement_count} "
                f"{'movimiento' if second.movement_count == 1 else 'movimientos'}."
            )
        result = _answered(
            plan,
            answer,
            query.conversation,
            query.sentence,
            reporting_timezone=query.reporting_timezone,
            ran_as=scoped,
        )
        if not difference and len(groups) >= 2:
            # Re-read the typed reference projected by ``_answered``.  In particular, do not
            # reconstruct evidence from the legacy wire fields: those may be stale or incomplete.
            reference = result_reference_of(result.conversation, focus_of(result.conversation))
            assert reference is not None
            assert reference.source_query is plan
            reference = replace(
                reference,
                category=(
                    groups[1].key if plan.group_by is GroupBy.CATEGORY else reference.category
                ),
                group_by=plan.group_by,
                group_key=groups[1].key,
            )
            result = replace(
                result,
                conversation=focused_on_result(result.conversation, reference),
            )
        return replace(result, text=text)

    async def _compare_periods(
        self,
        query: AskFinancialQuestionQuery,
        comparison: ComparisonPlan,
    ) -> AskFinancialQuestionResult:
        """Compare the two compatible financial questions immediately behind this turn."""
        earlier = comparison.earlier
        latest = comparison.latest
        try:
            scoped_earlier = await self._scoped(earlier, query)
            scoped_latest = await self._scoped(latest, query)
            earlier_answer = await self._queries.run(
                scoped_earlier,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
            latest_answer = await self._queries.run(
                scoped_latest,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="He entendido los dos periodos, pero el banco ha rechazado la comparación.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        if latest.metric is not Metric.COUNT and earlier_answer.currency != latest_answer.currency:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="No puedo comparar importes expresados en monedas distintas.",
                conversation=query.conversation,
            )

        earlier_window = _comparison_window_label(earlier)
        latest_window = _comparison_window_label(latest)
        earlier_value = answer_writer.comparison_value(earlier_answer, earlier)
        latest_value = answer_writer.comparison_value(latest_answer, latest)
        earlier_figure = answer_writer.comparison_figure(earlier_answer, earlier)
        latest_figure = answer_writer.comparison_figure(latest_answer, latest)
        difference = abs(latest_value - earlier_value)
        difference_figure = answer_writer.comparison_difference(
            difference, latest_answer.currency, latest.metric
        )
        concept = answer_writer.comparison_concept(latest)
        grouped_text = answer_writer.grouped_period_comparison(
            earlier_answer,
            latest_answer,
            earlier,
            latest,
            earlier_window=earlier_window,
            latest_window=latest_window,
        )
        if latest.metric is Metric.AVERAGE and (earlier_answer.is_empty or latest_answer.is_empty):
            empty_window = earlier_window if earlier_answer.is_empty else latest_window
            conclusion = (
                f"No hubo movimientos {empty_window}, así que la media de ese periodo "
                "no está definida."
            )
        elif latest_value == earlier_value:
            conclusion = (
                "Hubo el mismo número de movimientos en ambos periodos."
                if latest.metric is Metric.COUNT
                else f"El {concept} fue el mismo en ambos periodos."
            )
        else:
            higher_window = latest_window if latest_value > earlier_value else earlier_window
            conclusion = (
                f"Hubo {difference_figure} más {higher_window}."
                if latest.metric is Metric.COUNT
                else f"El {concept} fue {difference_figure} mayor {higher_window}."
            )

        movements = (*latest_answer.movements, *earlier_answer.movements)
        remembered_earlier = scoped_earlier
        remembered_latest = scoped_latest
        stored_latest = query.conversation.last_plan
        stored_earlier = query.conversation.prior_plan
        if isinstance(stored_latest, FinancialPlan) and _same_window(stored_latest, latest):
            if isinstance(stored_earlier, FinancialPlan) and _same_window(stored_earlier, earlier):
                candidate_earlier = stored_earlier
            else:
                candidate_earlier = replace(
                    stored_latest,
                    period=earlier.period,
                    date_range=earlier.date_range,
                )
            candidate = ComparisonPlan.between(candidate_earlier, stored_latest)
            if candidate == comparison:
                # Preserve presentation grouping only when the stored pair measures the exact
                # same scalar. Matching just the dates let an older SUM overwrite a new COUNT in
                # memory: the current answer was right, and "compáralos otra vez" became euros.
                remembered_latest = _with_execution_scope(stored_latest, scoped_latest)
                remembered_earlier = _with_execution_scope(candidate_earlier, scoped_earlier)
        remembered_comparison = ComparisonPlan.between(remembered_earlier, remembered_latest)
        assert remembered_comparison is not None  # execution only adds scope to a validated pair
        comparison_memory = query.conversation.answered_comparison(remembered_comparison)
        if grouped_text is not None:
            comparison_visual_rows = (
                *(
                    VisualRow(
                        dimension=group.key,
                        value_minor=_visual_metric_value(latest, group),
                        series=latest_window,
                    )
                    for group in latest_answer.groups
                    if group.key != "total"
                ),
                *(
                    VisualRow(
                        dimension=group.key,
                        value_minor=_visual_metric_value(earlier, group),
                        series=earlier_window,
                    )
                    for group in earlier_answer.groups
                    if group.key != "total"
                ),
            )
        else:
            comparison_visual_rows = (
                VisualRow(dimension=latest_window, value_minor=latest_value),
                VisualRow(dimension=earlier_window, value_minor=earlier_value),
            )
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=(
                grouped_text
                or (
                    f"{latest_window.capitalize()}: {latest_figure}; "
                    f"{earlier_window}: {earlier_figure}. {conclusion}"
                )
            ),
            plan=(f"{answer_writer.describe(latest)} frente a {answer_writer.describe(earlier)}"),
            plan_in_words=(
                f"{answer_writer.plan_in_words(latest)} frente a "
                f"{answer_writer.plan_in_words(earlier)}"
            ),
            plan_context=_spoken(latest),
            # Keep the compared pair as validated query memory. This makes a direct comparison
            # behave like two consecutive questions: a later "compáralos otra vez" or a repair can
            # still point at the same two windows without reconstructing either from prose.
            conversation=replace(
                comparison_memory,
                # Keep the original presentation grouping in the legacy slots. Older clients and
                # ordinary "vuelve a la anterior" follow-ups still use them; the typed comparison
                # itself stores the normalised scalar pair separately.
                last_plan=remembered_latest,
                prior_plan=remembered_earlier,
            ),
            currency=latest_answer.currency,
            # A comparison has no single leading figure: the sentence states both windows and the
            # change between them. So this stays empty and the change goes out under a name that
            # says it is one. `difference_minor` on the result carries the whole argument.
            total_minor=None,
            difference_minor=(
                None if latest.metric is Metric.COUNT else latest_value - earlier_value
            ),
            movement_count=latest_answer.movement_count + earlier_answer.movement_count,
            groups=(
                [
                    *[
                        SpokenGroup(
                            f"{group.key} · {latest_window}",
                            group.total_minor,
                            group.movement_count,
                            _visual_metric_value(latest, group),
                        )
                        for group in latest_answer.groups
                        if group.key != "total"
                    ],
                    *[
                        SpokenGroup(
                            f"{group.key} · {earlier_window}",
                            group.total_minor,
                            group.movement_count,
                            _visual_metric_value(earlier, group),
                        )
                        for group in earlier_answer.groups
                        if group.key != "total"
                    ],
                ]
                if grouped_text is not None
                else [
                    SpokenGroup(
                        latest_window,
                        (
                            latest_value
                            if latest.metric is Metric.PERIOD_AVERAGE
                            else latest_answer.total_minor
                        ),
                        latest_answer.movement_count,
                        latest_value,
                    ),
                    SpokenGroup(
                        earlier_window,
                        (
                            earlier_value
                            if earlier.metric is Metric.PERIOD_AVERAGE
                            else earlier_answer.total_minor
                        ),
                        earlier_answer.movement_count,
                        earlier_value,
                    ),
                ]
            ),
            movements=[_spoken_movement(movement) for movement in movements],
            visual_dataset=VisualDataset(
                rows=comparison_visual_rows,
                metric="COMPARISON",
                title=(
                    f"{answer_writer.describe(latest)} frente a {answer_writer.describe(earlier)}"
                ),
            ),
        )

    async def _scoped_ratio_draft(
        self, draft: RatioDraft, query: AskFinancialQuestionQuery
    ) -> RatioDraft:
        """Resolve account words once and put the same scope on both pending operands."""
        placeholder = RelativePeriod(Period.THIS_YEAR)
        scoped_whole = await self._scoped(draft.whole.with_window(placeholder), query)
        scoped_part = _with_ratio_execution_scope(draft.part.with_window(placeholder), scoped_whole)
        return RatioDraft(
            part=replace(FinancialPlanDraft.of(scoped_part), period=None, date_range=None),
            whole=replace(FinancialPlanDraft.of(scoped_whole), period=None, date_range=None),
            kind=draft.kind,
        )

    async def _scoped_internal_draft(
        self, draft: FinancialPlanDraft, query: AskFinancialQuestionQuery
    ) -> FinancialPlanDraft:
        """Resolve a named destination before the short period reply loses its words."""
        return await self._scoped_financial_draft(draft, query)

    async def _scoped_financial_draft(
        self, draft: FinancialPlanDraft, query: AskFinancialQuestionQuery
    ) -> FinancialPlanDraft:
        """Resolve an account while its name is present, before a short slot reply loses it."""
        placeholder = RelativePeriod(Period.THIS_YEAR)
        scoped = await self._scoped(draft.with_window(placeholder), query)
        return replace(FinancialPlanDraft.of(scoped), period=None, date_range=None)

    async def _answer_ratio(
        self, query: AskFinancialQuestionQuery, ratio: RatioPlan
    ) -> AskFinancialQuestionResult:
        """Run both operands and perform the requested percentage in deterministic code."""
        try:
            scoped_whole = await self._scoped(ratio.whole, query)
            scoped_part = _with_ratio_execution_scope(ratio.part, scoped_whole)
            part_answer = await self._queries.run(
                scoped_part,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
            whole_answer = await self._queries.run(
                scoped_whole,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="He entendido el porcentaje, pero el banco ha rechazado una de las cifras.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        if scoped_part.metric is not Metric.COUNT and part_answer.currency != whole_answer.currency:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="No puedo calcular un porcentaje entre importes de monedas distintas.",
                conversation=query.conversation,
            )

        executed = RatioPlan(part=scoped_part, whole=scoped_whole, kind=ratio.kind)
        if ratio.kind is RatioKind.SHARE:
            # The denominator contains the numerator when it is total spending. Showing both would
            # duplicate the category's rows and make the unified movement count meaningless.
            movements = part_answer.movements
            movement_count = part_answer.movement_count
        else:
            movements = _distinct_movements((*part_answer.movements, *whole_answer.movements))
            movement_count = part_answer.movement_count + whole_answer.movement_count
        net_cash_flow = (
            whole_answer.total_minor - part_answer.total_minor
            if ratio.kind is RatioKind.NET_CASH_FLOW
            else None
        )
        ratio_visual_dataset: VisualDataset | None = None
        if (
            ratio.kind is RatioKind.SHARE
            and 0 <= part_answer.total_minor <= whole_answer.total_minor
        ):
            ratio_visual_dataset = VisualDataset(
                rows=(
                    VisualRow(dimension="Parte", value_minor=part_answer.total_minor),
                    VisualRow(
                        dimension="Resto",
                        value_minor=whole_answer.total_minor - part_answer.total_minor,
                    ),
                ),
                metric="RATIO_SHARE",
                title=answer_writer.describe(scoped_part),
            )
        elif ratio.kind in {RatioKind.NET_CASH_FLOW, RatioKind.SAVINGS_RATE}:
            ratio_visual_dataset = VisualDataset(
                rows=(
                    VisualRow(dimension="Ingresos", value_minor=whole_answer.total_minor),
                    VisualRow(dimension="Gastos", value_minor=part_answer.total_minor),
                ),
                metric="RATIO",
                title=(
                    f"{answer_writer.describe(scoped_part)} respecto a "
                    f"{answer_writer.describe(scoped_whole)}"
                ),
            )
        elif ratio.kind is RatioKind.PERCENT_CHANGE:
            ratio_visual_dataset = VisualDataset(
                rows=(
                    VisualRow(dimension="Actual", value_minor=part_answer.total_minor),
                    VisualRow(dimension="Anterior", value_minor=whole_answer.total_minor),
                ),
                metric="RATIO",
                title=(
                    f"{answer_writer.describe(scoped_part)} respecto a "
                    f"{answer_writer.describe(scoped_whole)}"
                ),
            )
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=ratio_writer.write(part_answer, whole_answer, executed),
            plan=(
                f"{answer_writer.describe(scoped_part)} respecto a "
                f"{answer_writer.describe(scoped_whole)}"
            ),
            plan_in_words=(
                f"{answer_writer.plan_in_words(scoped_part)} respecto a "
                f"{answer_writer.plan_in_words(scoped_whole)}"
            ),
            plan_context=_spoken(scoped_whole),
            conversation=query.conversation.answered_ratio(executed),
            currency=part_answer.currency,
            total_minor=net_cash_flow,
            # Income minus expenses is a difference and the two rows below are its operands, so
            # this says so. Here it is *also* the figure the sentence leads with -- «tu flujo neto
            # fue …» -- which is why `total_minor` keeps it and is not lying: the two fields answer
            # different questions, what to render and whether the rows beside it add up to it.
            difference_minor=net_cash_flow,
            movement_count=movement_count,
            groups=(
                [
                    SpokenGroup("Ingresos", whole_answer.total_minor, whole_answer.movement_count),
                    SpokenGroup("Gastos", part_answer.total_minor, part_answer.movement_count),
                ]
                if ratio.kind is RatioKind.NET_CASH_FLOW
                else []
            ),
            movements=[_spoken_movement(movement) for movement in movements],
            visual_dataset=ratio_visual_dataset,
        )

    async def _compare_salary_history(
        self,
        query: AskFinancialQuestionQuery,
        current: FinancialPlan,
        history: FinancialPlan,
    ) -> AskFinancialQuestionResult:
        """Say whether the payroll in view exceeds each of the preceding monthly payrolls."""
        try:
            current_answer = await self._queries.run(
                await self._scoped(current, query),
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
            history_answer = await self._queries.run(
                await self._scoped(history, query),
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
                movements=5,
            )
        except QueryRejectedError:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="El banco ha rechazado la comparaci\u00f3n de n\u00f3minas.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido. Vuelve a intentarlo.",
                conversation=query.conversation,
            )

        if current_answer.currency != history_answer.currency:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="No puedo comparar n\u00f3minas expresadas en monedas distintas.",
                conversation=query.conversation,
            )
        if current_answer.is_empty:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text=(
                    "No he encontrado la n\u00f3mina consultada, as\u00ed que no puedo compararla."
                ),
                plan=answer_writer.describe(current),
                plan_in_words=answer_writer.plan_in_words(current),
                conversation=query.conversation,
                currency=current_answer.currency,
            )

        previous_months = tuple(
            group
            for group in history_answer.groups
            if group.movement_count > 0 and group.key != "total"
        )
        if not previous_months:
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text=(
                    "He encontrado esa n\u00f3mina, pero ninguna anterior en los doce meses "
                    "precedentes con la que compararla."
                ),
                plan=answer_writer.describe(history),
                plan_in_words=answer_writer.plan_in_words(history),
                conversation=query.conversation,
                currency=current_answer.currency,
                total_minor=current_answer.total_minor,
                movement_count=current_answer.movement_count,
                movements=[_spoken_movement(movement) for movement in current_answer.movements],
            )

        highest = max(previous_months, key=lambda group: group.total_minor)
        current_amount = answer_writer.money(current_answer.total_minor, current_answer.currency)
        previous_amount = answer_writer.money(highest.total_minor, history_answer.currency)
        difference = abs(current_answer.total_minor - highest.total_minor)
        difference_amount = answer_writer.money(difference, current_answer.currency)
        previous_label = answer_writer.month_group_name(highest.key)
        current_window = current.date_range
        assert current_window is not None
        current_label = answer_writer.month_name(
            current_window.start.year, current_window.start.month
        )
        if current_answer.total_minor > highest.total_minor:
            text = (
                f"S\u00ed. La n\u00f3mina de {current_label} fue de {current_amount}, "
                "la m\u00e1s alta de las "
                f"\u00faltimas doce: {difference_amount} m\u00e1s que la anterior m\u00e1s alta, "
                f"la de {previous_label} ({previous_amount})."
            )
        elif current_answer.total_minor == highest.total_minor:
            text = (
                f"La n\u00f3mina de {current_label} fue de {current_amount}. "
                "No fue mayor: igual\u00f3 la "
                f"de {previous_label}."
            )
        else:
            text = (
                f"No. La n\u00f3mina de {current_label} fue de {current_amount}; "
                "la anterior m\u00e1s alta "
                f"fue la de {previous_label}, con {previous_amount} "
                f"({difference_amount} m\u00e1s)."
            )

        movements = (*current_answer.movements, *history_answer.movements)
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=text,
            plan=f"{answer_writer.describe(current)} frente a las doce n\u00f3minas anteriores",
            plan_in_words=(
                f"{answer_writer.plan_in_words(current)} frente a las doce n\u00f3minas anteriores"
            ),
            plan_context=_spoken(current),
            # This is an answer about the payroll already in view. Keep that plan in view so
            # "compruebalo con el mes anterior" is anchored to its month, not to today's month.
            conversation=query.conversation,
            currency=current_answer.currency,
            # The payroll the question is about, which is the figure the sentence leads with. This
            # was `current - highest`, so «¿he cobrado más?» answered −22,75 € under a sentence
            # saying "22,75 € más" -- the same defect as the period comparison, one path over. The
            # no-previous-payroll branch above already answered with exactly this figure.
            total_minor=current_answer.total_minor,
            difference_minor=current_answer.total_minor - highest.total_minor,
            movement_count=current_answer.movement_count + history_answer.movement_count,
            groups=[
                SpokenGroup(
                    current_label, current_answer.total_minor, current_answer.movement_count
                ),
                *[
                    SpokenGroup(
                        answer_writer.month_group_name(group.key),
                        group.total_minor,
                        group.movement_count,
                    )
                    for group in previous_months
                ],
            ],
            movements=[_spoken_movement(movement) for movement in movements],
            visual_dataset=VisualDataset(
                rows=(
                    VisualRow(dimension=current_label, value_minor=current_answer.total_minor),
                    *(
                        VisualRow(
                            dimension=answer_writer.month_group_name(group.key),
                            value_minor=group.total_minor,
                        )
                        for group in previous_months
                    ),
                ),
                metric=Metric.SUM.value,
                title=answer_writer.describe(current),
            ),
        )

    async def _answer_with_month_fallback(
        self,
        query: AskFinancialQuestionQuery,
        first: FinancialPlan,
        second: FinancialPlan,
    ) -> AskFinancialQuestionResult:
        """Try the preferred month, and only query its fallback when the first is empty."""
        first_result = await self._answer(query, first)
        if first_result.movement_count:
            return first_result

        second_result = await self._answer(query, second)
        first_window = first.date_range
        second_window = second.date_range
        assert first_window is not None and second_window is not None
        first_label = answer_writer.month_name(first_window.start.year, first_window.start.month)
        second_label = answer_writer.month_name(second_window.start.year, second_window.start.month)
        if second_result.movement_count:
            return replace(
                second_result,
                text=(
                    f"No la he encontrado en {first_label}; "
                    f"sí en {second_label}. {second_result.text}"
                ),
            )
        return replace(
            second_result,
            text=f"No he encontrado la nómina ni en {first_label} ni en {second_label}.",
        )

    async def _other_accounts(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        """Run the previous query over every holder account it did not select.

        A complement cannot be represented by an account kind: it depends on the actual accounts
        the authenticated holder has. Resolve it here and remember concrete identifiers, which is
        also what prevents a later ellipsis from widening back to every account.
        """
        previous = query.conversation.last_plan
        if not isinstance(previous, FinancialPlan):
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text=(
                    "No tengo una consulta anterior con cuentas seleccionadas. "
                    "Primero consulta una cuenta y después puedo enseñarte las demás."
                ),
                conversation=query.conversation,
            )
        selected = (
            frozenset({previous.only_account_id})
            if previous.only_account_id is not None
            else frozenset(previous.only_account_ids)
        )
        if not selected:
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text="La consulta anterior ya incluía todas tus cuentas; no quedan otras fuera.",
                conversation=query.conversation,
            )
        try:
            summary = await self._accounts.of(
                query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="El banco no ha respondido y no puedo identificar las otras cuentas.",
                conversation=query.conversation,
            )
        others = tuple(
            holding.account_id for holding in summary.holdings if holding.account_id not in selected
        )
        if not others:
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text="No tienes otras cuentas fuera de las que acabamos de consultar.",
                conversation=query.conversation,
            )
        plan = replace(
            previous,
            group_by=GroupBy.NONE,
            across_accounts=False,
            only_account_id=others[0] if len(others) == 1 else None,
            only_account_ids=others if len(others) > 1 else (),
            include_internal=previous.include_internal or previous.direction is Direction.ALL,
        )
        return await self._answer(query, plan)

    async def _resume_account_scope(
        self, query: AskFinancialQuestionQuery, pending: AccountScopeClarification
    ) -> AskFinancialQuestionResult | None:
        """Resolve an account choice against the live directory and run the pending plan."""
        candidate_names = tuple(candidate.display_name for candidate in pending.candidates)
        if (
            _starts_new_financial_scope(query.sentence, today=query.today)
            and not account_words.asks_for_all_accounts(query.sentence)
            and not account_words.custom_chosen_among(query.sentence, candidate_names)
            and not account_words.named_in(query.sentence)
        ):
            return None
        if self._account_directory is None:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No puedo comprobar tus cuentas para aplicar esa selección.",
                conversation=query.conversation,
            )
        try:
            holdings = await self._account_directory.all_of(query.account_id)
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No puedo comprobar tus cuentas para aplicar esa selección.",
                conversation=query.conversation,
            )
        valid = {holding.account_id: holding for holding in holdings}
        candidates = {candidate.account_id for candidate in pending.candidates}
        selection = _chosen_account_scope(query.sentence, holdings, candidates)
        if selection is None:
            return _choose_one_of(
                tuple(
                    valid[account_id].display_name
                    for account_id in candidates
                    if account_id in valid
                ),
                query.conversation,
            )
        if selection.mode is AccountSelectionMode.ALL:
            scoped = replace(
                pending.pending, across_accounts=True, only_account_id=None, only_account_ids=()
            )
        else:
            scoped = replace(
                pending.pending,
                across_accounts=False,
                only_account_id=selection.account_ids[0],
                only_account_ids=(),
            )
        conversation = replace(
            query.conversation,
            active=None,
            account_selection=selection,
        )
        resumed_query = replace(query, conversation=conversation)
        if isinstance(scoped, TopMovements):
            if pending.requested_count is not None:
                result = await self._ranked_many(resumed_query, scoped, pending.requested_count)
            else:
                result = await self._ranked(resumed_query, scoped)
        else:
            # The plan is the question's and the sentence is the reply's: «Todas mis cuentas»
            # names no shop and no kind of one, so the readers that narrow a category answer
            # after the bank has answered it read the question, from the turn that asked.
            result = await self._answer(
                resumed_query,
                scoped,
                asked=_the_sentence_that_asked(
                    query.conversation,
                    tuple(candidate.display_name for candidate in pending.candidates),
                    question=_WHICH_ACCOUNT,
                ),
            )
        if pending.explicit_visualization:
            result = replace(result, explicit_visualization=True)
        return result

    async def _answering_the_scope_question(
        self, query: AskFinancialQuestionQuery, intent: Intent
    ) -> AskFinancialQuestionQuery | AskFinancialQuestionResult | None:
        """The question the backstop asked about, asked again under the account the reply chose.

        `_scope_question` asks «¿En qué cuenta…?» for a reader — a comparison, a payee count, a
        ratio — that raised before it had a plan anybody could keep, so there is no
        `AccountScopeClarification` for `_resume_account_scope` to finish. What the conversation
        does keep is the sentence that asked: `stream` records every turn, and the backstop's turn
        is the newest one carrying that question. So the reply is resolved exactly as on the
        resumable path — the same chip test and the same matching, against the accounts the
        question offered — and that *sentence* is returned to go through the whole pipeline again,
        with the selection in place. Re-planning rather than replaying is the only option and also
        the honest one: there is no plan, and the same sentence under a settled scope is exactly
        what the holder would have got had the scope been settled first. It does not ask a second
        time: with a selection just checked against the directory, `_scoped` answers from it
        before either of the two raises the backstop catches.

        `None` means this turn is not an answer to that question, and is then read exactly as it
        always was: nothing pending, a reply that chooses no account, a payment or a transfer
        (they own their turns whatever account they name), a deployment without a directory, or a
        directory that cannot be reached — the sentence read as itself is what happened before
        this existed. A reply naming an account the question did not offer gets the resumable
        path's own answer: the choice again.
        """
        if (
            query.conversation.last_asked is not Slot.ACCOUNT
            or intent in {Intent.PAYMENT, Intent.OWN_TRANSFER}
            or self._account_directory is None
        ):
            return None
        try:
            holdings = await self._account_directory.all_of(query.account_id)
        except BankingUnavailableError:
            return None
        # What `_scoped` offered when it raised: every account with a name to show.
        offered = tuple(holding for holding in holdings if holding.display_name)
        names = tuple(holding.display_name for holding in offered)
        asked_about = _asked_which_account_about(query.conversation, names)
        if asked_about is None or not _answers_account_scope(query.sentence, names):
            return None
        selection = _chosen_account_scope(
            query.sentence, holdings, {holding.account_id for holding in offered}
        )
        if selection is None:
            return _choose_one_of(names, query.conversation)
        return replace(
            query,
            sentence=asked_about,
            conversation=replace(query.conversation, last_asked=None, account_selection=selection),
            # Read afresh. What the edge read was the reply, and the reply is not what is being
            # asked now: «Todas mis cuentas» read as a balance is the defect this exists for. It
            # makes this turn the one that reads two sentences — the edge's read of the reply and
            # `_steps`' read of the question, a second interpreter call in `hybrid` when the
            # question is worth one — and that is kept on purpose: reusing the reading the
            # question got when it was first asked would mean carrying an analysis on the wire.
            intent=None,
            analysis=None,
        )

    async def _scoped(
        self, plan: FinancialPlan, query: AskFinancialQuestionQuery, *, clarify: bool = False
    ) -> FinancialPlan:
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
        entity_alias_names = entity_alias_words.learned_aliases_in(
            query.sentence,
            tuple(alias.alias for alias in query.conversation.counterparty_aliases),
        )
        entity_aliases = tuple(
            alias
            for alias in query.conversation.counterparty_aliases
            if alias.alias in entity_alias_names
        )
        if entity_aliases:
            plan = replace(
                plan,
                counterparties=tuple(
                    dict.fromkeys(alias.counterparty_name for alias in entity_aliases)
                ),
                counterparty_types=tuple(
                    dict.fromkeys(alias.counterparty_type for alias in entity_aliases)
                ),
            )

        alias_names = account_words.learned_aliases_in(
            query.sentence,
            tuple(alias.alias for alias in query.conversation.account_aliases),
        )
        aliased = tuple(
            alias for alias in query.conversation.account_aliases if alias.alias in alias_names
        )
        if aliased:
            wanted_ids = tuple(dict.fromkeys(alias.account_id for alias in aliased))
            try:
                if self._account_directory is not None:
                    holdings = await self._account_directory.all_of(query.account_id)
                else:
                    summary = await self._accounts.of(
                        query.account_id,
                        today=query.today,
                        reporting_timezone=query.reporting_timezone,
                    )
                    holdings = summary.holdings
            except BankingUnavailableError:
                # Banking remains the final authority for the account id. Keeping the explicit
                # scope is safer than silently widening an alias to every account.
                holdings = ()
            if holdings:
                real_ids = {holding.account_id for holding in holdings}
                if any(account_id not in real_ids for account_id in wanted_ids):
                    raise AccountScopeNotFoundError(alias_names)
            return replace(
                plan,
                group_by=GroupBy.NONE,
                across_accounts=False,
                only_account_id=wanted_ids[0] if len(wanted_ids) == 1 else None,
                only_account_ids=wanted_ids if len(wanted_ids) > 1 else (),
                include_internal=plan.include_internal or plan.direction is Direction.ALL,
            )

        excluded = frozenset(account_words.excluded_in(query.sentence))
        named = tuple(
            kind for kind in account_words.named_in(query.sentence) if kind not in excluded
        )
        if "SALARY" in plan.categories and "cuenta" not in spanish.folded(query.sentence):
            # "Mi nómina" names the income category. The same noun is also a legacy alias for a
            # CURRENT account, but applying that alias here silently hides payroll paid elsewhere.
            # "Cuenta nómina" and "cuenta principal" remain explicit account selections.
            named = tuple(kind for kind in named if kind != "CURRENT")
        if plan.internal_only and _INVESTMENT_HISTORY.search(spanish.folded(query.sentence)):
            # "Cuánto he invertido" names the purpose rather than saying "fondo". It still has one
            # exact destination: the holder's asset account(s), resolved from banking below.
            named = ("ASSET",)
        explicit_custom = account_words.explicitly_names_a_custom_account(query.sentence)
        asks_account_breakdown = explicit_facts.read(
            query.sentence, today=query.today
        ).asks_account_breakdown
        # A learned counterparty alias is a complete, typed filter.  In legacy
        # handlers without an AccountDirectory, do not force an unrelated account
        # summary lookup merely to decide the account scope.
        if entity_aliases and not named and not excluded and not explicit_custom:
            return plan
        if plan.group_by is GroupBy.ACCOUNT and (
            asks_account_breakdown or (not named and not excluded and not explicit_custom)
        ):
            return _across_every_account(plan)
        if not named and not excluded and not explicit_custom:
            if account_words.asks_for_all_accounts(query.sentence):
                return _across_every_account(plan)
            if plan.only_account_id is not None or plan.only_account_ids:
                return plan
            # Persisted account scope belongs to the directory-backed workflow.  A legacy handler
            # has no live directory to revalidate it against, so an unqualified follow-up keeps
            # the historical all-visible-accounts meaning.
            if self._account_directory is None:
                return _across_every_account(plan)
            persisted = query.conversation.account_selection
            if persisted is not None:
                if persisted.mode is AccountSelectionMode.ALL:
                    return _across_every_account(plan)
                if self._account_directory is not None:
                    live = await self._account_directory.all_of(query.account_id)
                    live_ids = {holding.account_id for holding in live}
                    if not set(persisted.account_ids).issubset(live_ids):
                        candidates = tuple(
                            AccountCandidate(h.account_id, h.display_name, h.kind)
                            for h in live
                            if h.display_name
                        )
                        raise AccountScopeRequiredError(
                            plan, candidates, conversation=query.conversation
                        )
                return replace(
                    plan,
                    group_by=GroupBy.NONE,
                    across_accounts=False,
                    only_account_id=(
                        persisted.account_ids[0] if len(persisted.account_ids) == 1 else None
                    ),
                    only_account_ids=persisted.account_ids
                    if len(persisted.account_ids) > 1
                    else (),
                )
            # The directory-backed clarification is opt-in.  Legacy handlers deliberately
            # provide an Accounts summary that may reject this path (and their credential already
            # represents the complete visible scope), so do not probe it for an unqualified read.
            try:
                holdings = (
                    await self._account_directory.all_of(query.account_id)
                    if self._account_directory is not None
                    else (
                        await self._accounts.of(
                            query.account_id,
                            today=query.today,
                            reporting_timezone=query.reporting_timezone,
                        )
                    ).holdings
                )
            except BankingUnavailableError:
                raise
            # Legacy account fixtures do not expose the new directory.  Their account
            # summary is still useful for resolving explicit account kinds/names, but
            # must never trigger the new multi-account clarification workflow.
            if self._account_directory is None:
                return _across_every_account(plan)
            if len(holdings) == 0:
                return _across_every_account(plan)
            if len(holdings) == 1:
                return replace(
                    plan,
                    across_accounts=False,
                    only_account_id=holdings[0].account_id,
                    only_account_ids=(),
                )
            candidates = tuple(
                AccountCandidate(h.account_id, h.display_name, h.kind)
                for h in holdings
                if h.display_name
            )
            raise AccountScopeRequiredError(plan, candidates, conversation=query.conversation)

        if not named and self._account_directory is not None:
            try:
                holdings = await self._account_directory.all_of(query.account_id)
            except BankingUnavailableError:
                logger.warning(
                    "scope.accounts_unavailable account=%s", account_ref(query.account_id)
                )
                if clarify:
                    raise
                return _across_every_account(plan)
            names = tuple(holding.display_name for holding in holdings if holding.display_name)
            custom = account_words.custom_chosen_among(query.sentence, names)
            wanted_custom = [holding for holding in holdings if holding.display_name in custom]
            if len(wanted_custom) == 1:
                return replace(
                    plan,
                    group_by=GroupBy.NONE,
                    across_accounts=False,
                    only_account_id=wanted_custom[0].account_id,
                    only_account_ids=(),
                    include_internal=plan.include_internal or plan.direction is Direction.ALL,
                )
            if wanted_custom:
                return replace(
                    plan,
                    group_by=GroupBy.NONE,
                    across_accounts=False,
                    only_account_id=None,
                    only_account_ids=tuple(holding.account_id for holding in wanted_custom),
                )
            if clarify:
                candidates = tuple(
                    AccountCandidate(h.account_id, h.display_name, h.kind)
                    for h in holdings
                    if h.display_name
                )
                raise AccountScopeRequiredError(plan, candidates, conversation=query.conversation)
            return _across_every_account(plan)

        try:
            summary = await self._accounts.of(
                query.account_id, today=query.today, reporting_timezone=query.reporting_timezone
            )
        except BankingUnavailableError:
            # The account list is what narrowing needs, and without it the honest fallback is the
            # wider answer rather than the declared account: one account out of three, labelled as
            # though the word had been understood, is the failure this whole method exists to stop.
            logger.warning("scope.accounts_unavailable account=%s", account_ref(query.account_id))
            return _across_every_account(plan)

        names = tuple(holding.display_name for holding in summary.holdings if holding.display_name)
        custom = account_words.custom_chosen_among(query.sentence, names)
        wanted = (
            [holding for holding in summary.holdings if holding.display_name in custom]
            if custom
            else [
                holding
                for holding in summary.holdings
                if holding.kind in named or (not named and holding.kind not in excluded)
            ]
        )
        if not wanted:
            # None of that kind. Narrowing to nothing would look like a genuinely empty period.
            if plan.internal_only and named:
                raise AccountScopeNotFoundError(named)
            return _across_every_account(plan)
        if len(wanted) > 1:
            return replace(
                plan,
                group_by=GroupBy.NONE,
                across_accounts=False,
                only_account_id=None,
                only_account_ids=tuple(holding.account_id for holding in wanted),
            )
        return replace(
            plan,
            group_by=GroupBy.NONE,
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
        paid exactly where the alternative was telling the user to give up. The list a suggestion
        has to come from is the shops in this window: guessing from a vocabulary of our own would
        offer shops this account has never paid.

        An outage here is not worth reporting. The turn already has an answer — "no encontré
        nada" — and a second failure on top of it would replace a true statement with a vaguer
        one.
        """
        return merchant_words.close_to(
            plan.merchants[0], await self._merchants_in(plan, query) or ()
        )

    async def _merchants_in(
        self, plan: FinancialPlan, query: AskFinancialQuestionQuery
    ) -> tuple[str, ...] | None:
        """Every shop in this plan's window, as the ledger spells them -- or None when the ledger
        could not say, which is not the same answer as no shops at all.

        The same plan with the merchant filter dropped and grouped by merchant answers "which shops
        are in this window". Two callers: a misspelt shop wants candidates, and a kind of thing
        named inside its category («el gimnasio») wants every shop of that kind, which the handful
        of movements beside a figure cannot promise to hold.
        """
        try:
            wider = await self._queries.run(
                replace(
                    plan,
                    merchants=(),
                    group_by=GroupBy.MERCHANT,
                    # PERIOD_AVERAGE is valid only over calendar buckets. This auxiliary query is
                    # discovering merchant names, so retaining the temporal divisor here turns a
                    # harmless misspelling into a domain-level 422.
                    metric=Metric.SUM,
                    # Every shop, not the plan's top few: a ranking's cut decides which rows are
                    # drawn, never which shops the window holds.
                    group_limit=None,
                ),
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
        except (BankingUnavailableError, QueryRejectedError):
            return None
        return tuple(group.key for group in wider.groups)

    def _scope_question(
        self, required: AccountScopeRequiredError, was: Conversation
    ) -> AskFinancialQuestionResult:
        """The clarification a scope requirement is, wherever it was raised.

        `_answer` below and the scope-change path translate this themselves, because each has
        something to add — a resumable `AccountScopeClarification`, and its own wording for
        changing a scope already settled. This is the backstop for the other nine call sites,
        which had nothing and therefore produced a 500: a comparison, a ratio or a net cash flow
        asked first, by anybody with more than one account.

        Still no `AccountScopeClarification`, because there is no plan to put in one: the readers
        that land here raise from inside their own reading, and a half-read comparison is not a
        `PlannedQuery`. What this leaves is two facts, and together they make the question
        answerable all the same.

        `asking` records the slot. The three sites that build the clarification record it on the
        workflow and `awaiting` reads it there; this path leaves no workflow, so `last_asked` is
        the only thing that can remember the question — the case that comment on `last_asked` was
        written for. Without it the backstop asked "¿en qué cuenta?" and left behind a
        conversation claiming nothing had been asked, so a comparison or a ratio could re-ask it
        every turn and no counter would see it.

        And `question` is `_WHICH_ACCOUNT`, which `stream` writes onto this turn in the log beside
        the sentence that raised. That pair is the pending question. On the next turn,
        `_answering_the_scope_question` finds it, resolves the reply against the accounts offered
        here, and asks the sentence again with the selection in place. Before it did, the reply
        was read as a sentence of its own, and «Todas mis cuentas» after «¿Gasté más en gasolina en
        junio o en julio?» answered the balance — a true figure for a question nobody had asked.
        """
        names = [candidate.display_name for candidate in required.candidates]
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=f"{_WHICH_ACCOUNT} Puedes elegir " + ", ".join(names) + " o Todas mis cuentas.",
            question=_WHICH_ACCOUNT,
            suggestions=[*names, "Todas mis cuentas"],
            conversation=was.asking(Slot.ACCOUNT),
        )

    async def _answer(
        self,
        query: AskFinancialQuestionQuery,
        plan: FinancialPlan,
        *,
        widened: bool = False,
        narrowed: bool = False,
        movements: int | None = None,
        asked: str | None = None,
    ) -> AskFinancialQuestionResult:
        # `asked` is the sentence that asked this plan, when this turn's is not it: a reply to the
        # account question runs a plan its own sentence never stated. The shop readers fall back
        # to it; they never prefer it to a reply that names a shop itself.
        #
        # Scoped for the run and not on the plan itself. Which accounts the figures came from is a
        # property of how the question was answered; the plan is what was *asked*, and that is what
        # gets described to the user and remembered for a follow-up.
        try:
            scoped = await self._scoped(plan, query, clarify=True)
        except AccountScopeRequiredError as required:
            names = [candidate.display_name for candidate in required.candidates]
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text="¿En qué cuenta quieres consultarlo? Puedes elegir "
                + ", ".join(names)
                + " o Todas mis cuentas.",
                question="¿En qué cuenta quieres consultarlo?",
                suggestions=[*names, "Todas mis cuentas"],
                conversation=query.conversation.with_active(
                    AccountScopeClarification(
                        pending=required.plan,
                        candidates=required.candidates,
                        requested_count=required.count,
                        explicit_visualization=_asks_for_visualization(query.sentence),
                    )
                ),
            )
        except AccountScopeNotFoundError:
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text="No tienes una cuenta de ese tipo en la que buscar traspasos.",
                conversation=query.conversation,
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No puedo comprobar tus cuentas antes de consultar ese periodo.",
                conversation=query.conversation,
            )
        if self._account_directory is not None:
            query = _remember_explicit_scope(query, scoped)
        # The ordinary all-accounts default is execution policy, not conversational data. An
        # explicit account choice is different: losing it makes the very next ellipsis widen back
        # to every account. Keep only scopes the user actually selected (plus ACCOUNT breakdown,
        # whose meaning is intrinsically across accounts).
        remembered = (
            scoped
            if scoped.only_account_id is not None
            or scoped.only_account_ids
            or (scoped.across_accounts and account_words.asks_for_all_accounts(query.sentence))
            or plan.group_by is GroupBy.ACCOUNT
            or plan.internal_only
            else plan
        )
        for attempt in range(self._read_attempts):
            try:
                if scoped.metric in {Metric.FREQUENCY, Metric.RECURRENCE}:
                    answer = await self._queries.run(
                        scoped,
                        account_id=query.account_id,
                        today=query.today,
                        reporting_timezone=query.reporting_timezone,
                        movements=MAX_SUPPORTING_MOVEMENTS,
                    )
                else:
                    answer = await self._queries.run(
                        scoped,
                        account_id=query.account_id,
                        today=query.today,
                        reporting_timezone=query.reporting_timezone,
                        movements=movements,
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
                    plan=answer_writer.describe(remembered),
                    plan_in_words=answer_writer.plan_in_words(remembered),
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
                    plan=answer_writer.describe(remembered),
                    plan_in_words=answer_writer.plan_in_words(remembered),
                    conversation=query.conversation.awaiting_retry(remembered),
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
            # The reply's own words first. A reply that names a shop or a word of a category --
            # «Netflix este año», «el gimnasio este año» -- is the sentence that says what to narrow
            # to, and reading the question in its place answered every subscription. The sentence
            # that asked is the fallback, for a reply that names neither: «este año», «Todas mis
            # cuentas», «reintenta». A near miss is not naming: a guess decides nothing here.
            replied = query.sentence
            question = (
                replied
                if asked is None
                or category_words.named_in(replied)
                or merchant_words.named_in(replied, seen) is not None
                else asked
            )
            shop = merchant_words.named_in(question, seen)
            named = " y ".join(category_words.name_of(c) for c in plan.categories)
            shops: tuple[str, ...] = ()
            why = ""
            if shop is not None:
                shops = (shop,)
                why = f"Solo {shop}. La consulta que había construido cubría {named} entera."
            # Named a kind of thing inside the category, not the category. Reported from a live
            # session: «¿cuánto gasté en el gimnasio este año?» answered "421,79 € en
            # suscripciones, en 23 movimientos" -- Netflix, Spotify and Steam with the gym, which
            # alone is 29,90 € a month. «Gimnasio» is a word of the SUBSCRIPTIONS vocabulary, so the
            # planner, the gate and the reader above all agreed it named the category, and the
            # reader drops it on purpose: it returns one shop, and the holder may pay two gyms.
            #
            # So the word is matched here against every shop the window holds, and the answer is
            # narrowed to all of those that carry it. The handful of movements beside a figure is
            # that list only when it holds every movement; otherwise it is asked for, which costs a
            # query on exactly the turns that named a kind. Nothing a model reads changes: the
            # planner's prompt and its evaluations still see the category, and this runs after the
            # bank has answered it.
            #
            # A category called by its own name -- «suscripciones», «supermercado» -- names no kind
            # (`category_words.kinds_named_in`), and one plan with two categories is left whole: a
            # plan cannot hold "these gyms, or else groceries", and answering half of it would be
            # the wider-figure failure pointing the other way.
            #
            # Nor a plan that already leaves shops out. The window's shops are read under the plan's
            # own exclusions, so «el gimnasio, quitando Altafit» found no gym and said none was
            # called that, about a holder who pays one; which of the shops left out the kind should
            # cover is the holder's to say, not this rule's to guess.
            kinds = (
                category_words.kinds_named_in(question, plan.categories[0])
                if shop is None
                and len(plan.categories) == 1
                and not plan.merchant_terms
                and not plan.excluded_merchants
                else ()
            )
            said = " o ".join(f"«{kind}»" for kind in kinds)
            in_window = (
                (
                    seen
                    if len(answer.movements) >= answer.movement_count
                    else await self._merchants_in(scoped, query)
                )
                if kinds
                else None
            )
            of_that_kind = merchant_words.called(kinds, in_window or ())
            # Which of them the sentence names, when it names one. The reader above saw only the
            # evidence, so «el gimnasio Altafit» with Altafit outside it found no shop by name and
            # then every gym by kind. Only among the kind's own shops, though: a word one of them
            # shares with a shop of another kind is not that shop's name. Read over the whole
            # window, «el metro de Madrid» was EMT Madrid and «el gimnasio de Madrid» a shop called
            # Netflix Madrid.
            by_name = (
                merchant_words.named_in(question, of_that_kind) if len(of_that_kind) > 1 else None
            )
            if by_name is not None:
                shops = (by_name,)
                why = f"Solo {by_name}. La consulta que había construido cubría {named} entera."
            # More shops than one plan may name is an answer it cannot give whole, and one of them
            # is the failure this exists for; the category's own figure is the honest one left.
            elif 0 < len(of_that_kind) <= MAX_MERCHANTS:
                shops = of_that_kind
                which, carry = (
                    ("el comercio", "lleva") if len(shops) == 1 else ("los comercios", "llevan")
                )
                why = (
                    f"Solo {answer_writer.listed(shops)}, {which} de {named} que {carry}"
                    f" {said} en el nombre."
                )
            if not shops:
                # Named it, nearly. "Spoti" was in the sentence and Spotify was in the evidence,
                # and no token matched — so the answer stayed the whole category. A near miss is a
                # guess, though, and a guess that produces a figure is what this service does not
                # do, so it asks instead of narrowing. The next turn inherits this window and is an
                # ordinary follow-up.
                nearly = merchant_words.almost_named_in(question, seen)
                # And a kind the figure could not be narrowed to: the category's figure is still
                # the honest answer to the plan, but not to the question, so it says which one it
                # is and why. Silence here was the reported defect itself -- the whole category,
                # read as the gym. Not over an empty answer, where there is no total to explain.
                whole_because = None
                if kinds and not answer.is_empty:
                    if in_window is None:
                        whole_because = (
                            f"no he podido comprobar qué comercios llevan {said} en el nombre"
                        )
                    elif of_that_kind:
                        whole_because = (
                            f"más de {MAX_MERCHANTS} comercios llevan {said} en el nombre,"
                            " demasiados para una sola consulta"
                        )
                    else:
                        whole_because = f"en este periodo ningún comercio lleva {said} en el nombre"
                if nearly or whole_because:
                    # The figure that was planned is still the honest answer to the plan, so it is
                    # given rather than thrown away, and the question goes underneath it. The plan
                    # stays as the last answered one, which is what makes the reply — "Spotify" —
                    # an ordinary follow-up carrying this window instead of a bare word nobody can
                    # date.
                    answered = _answered(
                        remembered,
                        answer,
                        query.conversation,
                        query.sentence,
                        reporting_timezone=query.reporting_timezone,
                        ran_as=scoped,
                    )
                    if nearly:
                        return replace(
                            answered,
                            text=(
                                f"{answered.text}\n¿Querías solo {answer_writer.listed(nearly)}?"
                            ),
                            suggestions=list(nearly),
                        )
                    return replace(
                        answered,
                        text=(
                            f"{answered.text}\n"
                            f"(Incluye toda la categoría {named}: {whole_because}.)"
                        ),
                    )
            if shops:
                kept = {name.casefold() for name in shops}
                tighter = replace(
                    plan,
                    categories=(),
                    merchants=shops,
                    excluded_merchants=tuple(
                        merchant
                        for merchant in plan.excluded_merchants
                        if merchant.casefold() not in kept
                    ),
                )
                again = await self._answer(query, tighter, narrowed=True)
                if again.outcome == PlanningOutcome.PLANNED.value and again.movement_count:
                    return replace(again, text=f"{again.text}\n({why})")
                if again.outcome != PlanningOutcome.PLANNED.value:
                    # The narrower figure could not be read. The category's is in hand and true,
                    # so it is given -- as what it is, and not as the answer about the shops.
                    answered = _answered(
                        remembered,
                        answer,
                        query.conversation,
                        query.sentence,
                        reporting_timezone=query.reporting_timezone,
                        ran_as=scoped,
                    )
                    return replace(
                        answered,
                        text=(
                            f"{answered.text}\n(Incluye toda la categoría {named}: no he podido"
                            f" consultar solo {answer_writer.listed(shops)}.)"
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
                    plan_in_words=answer_writer.plan_in_words(plan),
                    conversation=query.conversation,
                    suggestions=list(did_you_mean),
                )

        return _answered(
            remembered,
            answer,
            query.conversation,
            query.sentence,
            reporting_timezone=query.reporting_timezone,
            ran_as=scoped,
        )


# Which shape answers which reader. Below the class because the values are its methods, and a table
# of nineteen names is a thing to read rather than nineteen `elif`s to scroll through.
#
# The nine that share `_planned_answer` are the nine whose inline blocks were identical character
# for character. The other ten each say what is different about them, in the method.
_ANSWERING: Final[
    dict[
        str,
        Callable[
            [AskFinancialQuestionHandler, AskFinancialQuestionQuery, Conversation, Any],
            AsyncIterator[Stage | AskFinancialQuestionResult],
        ],
    ]
] = {
    "_ratio_request": AskFinancialQuestionHandler._a_ratio,
    "_gambling_plan": AskFinancialQuestionHandler._a_gambling_plan,
    "_how_many_payees_plan": AskFinancialQuestionHandler._a_payee_count,
    "_internal_movement_request": AskFinancialQuestionHandler._an_internal_movement,
    "_salary_history_plans": AskFinancialQuestionHandler._a_salary_history,
    "_period_comparison_plans": AskFinancialQuestionHandler._a_period_comparison,
    "_comparison_with_previous_period": AskFinancialQuestionHandler._a_period_comparison,
    "_direct_period_comparison": AskFinancialQuestionHandler._a_period_comparison,
    "_comparison_with_a_named_window": AskFinancialQuestionHandler._a_period_comparison,
    "contextual_follow_up_plan": AskFinancialQuestionHandler._planned_answer,
    "_multiple_movement_ranking": AskFinancialQuestionHandler._a_movement_ranking,
    "_group_ranking_plan": AskFinancialQuestionHandler._planned_answer,
    "_natural_period_aggregate_plan": AskFinancialQuestionHandler._planned_answer,
    "_deterministic_breakdown_plan": AskFinancialQuestionHandler._a_breakdown,
    "_contextual_ranking_plan": AskFinancialQuestionHandler._a_single_movement,
    "_cumulative_plan": AskFinancialQuestionHandler._planned_answer,
    "_recurrence_plan": AskFinancialQuestionHandler._planned_answer,
    "_frequency_plan": AskFinancialQuestionHandler._planned_answer,
    "_average_plan": AskFinancialQuestionHandler._an_average,
}


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
    "CATEGORY_EXCLUSION": (
        "Puedo excluir categorías y comercios concretos, pero no he identificado con seguridad "
        "qué quieres dejar fuera. Prueba, por ejemplo, con «sin contar el alquiler» o «sin "
        "Mercadona»."
    ),
    "INVESTING": (
        "Puedo sumar las aportaciones que llegaron a tu fondo mediante traspasos, pero no he "
        "distinguido si preguntas por aportaciones, saldo, valoración o rentabilidad. Prueba con "
        "«¿cuánto he invertido este año?» o «¿cuánto tengo en el fondo?»."
    ),
    "INTERNAL_ONLY": (
        "Puedo consultar sólo los traspasos entre tus cuentas, pero no he identificado qué "
        "historial quieres ver. Prueba con «movimientos entre mis cuentas este año» o «cuánto he "
        "movido a la hucha este mes»."
    ),
    "AVERAGE": (
        "Puedo calcular medias por movimiento, día, semana o mes. Dime cuál de ellas quieres; "
        "por ejemplo, «ticket medio por compra» o «gasto medio mensual este año»."
    ),
    "RATIO": (
        "Puedo calcular qué porcentaje de tus ingresos o gastos representa una categoría, "
        "tu tasa de ahorro y la variación entre dos periodos. No he identificado con seguridad "
        "las dos cifras de este porcentaje; prueba, por ejemplo, con «qué porcentaje de mi "
        "nómina se va en alquiler este año»."
    ),
    "COMPARISON": (
        "Entiendo que quieres comparar, pero no he podido delimitar dos periodos calendario "
        "completos. Prueba, por ejemplo, con «compara mis gastos de julio con junio» o consulta "
        "dos periodos por separado y dime «compáralos»."
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

_MERCHANT_REFERENCE: Final = re.compile(
    r"\b(?:ese|este|aquel|el\s+mismo)\s+"
    r"(?:comercio|establecimiento|negocio|supermercado|sitio)\b"
    r"|\b(?:esa|esta|aquella|la\s+misma)\s+tienda\b"
)
_COUNTERPARTY_REFERENCE: Final = re.compile(
    r"\b(?:esa|esta|aquella|la\s+misma)\s+"
    r"(?:persona|empresa|contraparte|entidad)\b"
    r"|\b(?:ese|este|aquel|el\s+mismo)\s+(?:emisor|empleador|banco|contacto)\b"
)
_ACCOUNT_REFERENCE: Final = re.compile(r"\b(?:esa|esta|aquella|la\s+misma)\s+cuenta\b")
_MOVEMENT_SET_REFERENCE: Final = re.compile(
    r"\b(?:ese|este|aquel|el\s+mismo)\s+(?:movimiento|cargo|pago|ingreso)\b"
    r"|\b(?:esa|esta|aquella|la\s+misma)\s+(?:operacion|compra)\b"
    r"|\b(?:esos|estos|aquellos|los\s+mismos)\s+"
    r"(?:movimientos|cargos|pagos|ingresos)\b"
    r"|\b(?:esas|estas|aquellas|las\s+mismas)\s+(?:operaciones|compras)\b"
    r"|\blos\s+movimientos\s+(?:responsables|detras|que\s+lo\s+explican)\b"
    r"|\b(?:ensename|muestrame|dame)\s+(?:esos|estos|los)\s+movimientos[\s.!?]*$"
)


_COMPARISON_DIFFERENCE: Final = re.compile(
    r"^(?:y\s+)?(?:cuanta|que)\s+diferencia(?:\s+hay)?(?:\s+entre\s+(?:las|esas)\s+dos)?$"
)
_NEXT_GROUP: Final = re.compile(
    r"^(?:y\s+)?(?:despues\s+de\s+(?:eso|esa|ese)|cual\s+va\s+despues|el\s+siguiente)$"
)
_RANKED_DATE: Final = re.compile(
    r"^(?:y\s+)?(?:esa|ese|esta|este)?\s*,?\s*(?:de\s+)?que\s+fecha\s+(?:es|fue)$"
    r"|^(?:y\s+)?cuando\s+(?:fue|ocurrio|entro|salio)$"
)


def _previous_plan_request(
    sentence: str, conversation: Conversation
) -> PlannedQuery | Literal[False] | None:
    """The prior validated query, when this turn explicitly asks to restore it.

    False means the wording was recognised but the requested history does not exist. That is a
    useful distinction from None: the former gets an honest local answer; the latter remains an
    ordinary turn. Active payments are deliberately excluded because "deshaz eso" there is about
    money, not a query filter, and must stay on the confirmation/cancellation path.
    """
    if not conversation.at_rest:
        return None
    cleaned = _clean_turn(sentence)
    back = _RETURN_TO_PREVIOUS.fullmatch(cleaned)
    undo_filter = _UNDO_FILTER.fullmatch(cleaned)
    if back is None and undo_filter is None:
        return None
    previous = conversation.prior_plan
    current = conversation.last_plan
    if previous is None:
        return False
    if undo_filter is not None and not _only_filters_changed(current, previous):
        return False
    return previous


def _only_filters_changed(current: PlannedQuery | None, previous: PlannedQuery) -> bool:
    if current is None or type(current) is not type(previous):
        return False
    without_current = replace(
        current,
        categories=(),
        excluded_categories=(),
        merchants=(),
        excluded_merchants=(),
    )
    without_previous = replace(
        previous,
        categories=(),
        excluded_categories=(),
        merchants=(),
        excluded_merchants=(),
    )
    return without_current == without_previous


def _account_activity_request(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """A typed activity query for an account explicitly named in the same sentence."""
    folded = spanish.folded(query.sentence)
    named_accounts = account_words.named_in(query.sentence)
    if "nomina" in folded and "cuenta" not in folded:
        # The payroll category shares a legacy alias with the current account. Only "cuenta
        # nómina" selects the account; "mi nómina" remains income paid wherever it arrived.
        named_accounts = tuple(kind for kind in named_accounts if kind != "CURRENT")
    facts = explicit_facts.read(query.sentence, today=query.today)
    if (
        not named_accounts
        or _ACCOUNT_ACTIVITY.search(folded) is None
        or _ASKS_ACCOUNT_ACTIVITY.search(folded) is None
        or facts.asks_account_breakdown
        # Spending and generic movement queries already have a mature planner/scoping path. This
        # typed branch fixes the narrower gap observed here: income produced by one named account.
        or not (
            facts.direction is Direction.INCOME or re.search(r"\b(?:ingresos?|income)\b", folded)
        )
    ):
        return None

    metric = Metric.SUM if _ACCOUNT_YIELD_AMOUNT.search(folded) else (facts.metric or Metric.SUM)
    group_by = (
        facts.group_by
        if metric is Metric.PERIOD_AVERAGE and facts.group_by is not None
        else GroupBy.NONE
    )
    window = facts.window
    if window is not None:
        return FinancialPlan(
            direction=Direction.INCOME,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            group_by=group_by,
            metric=metric,
        )
    return FinancialPlanDraft(direction=Direction.INCOME, group_by=group_by, metric=metric)


def _distinct_movements(movements: tuple[Movement, ...]) -> tuple[Movement, ...]:
    """Keep evidence from both operands without repeating subset rows in the denominator."""
    found: list[Movement] = []
    seen: set[tuple[object, ...]] = set()
    for movement in movements:
        identity: tuple[object, ...] = (
            ("id", movement.transaction_id)
            if movement.transaction_id is not None
            else (
                "fallback",
                movement.occurred_at,
                movement.description,
                movement.amount_minor,
                movement.merchant_name,
            )
        )
        if identity not in seen:
            seen.add(identity)
            found.append(movement)
    return tuple(found)


def _with_execution_scope(plan: FinancialPlan, scoped: FinancialPlan) -> FinancialPlan:
    """Keep a plan's conversational shape together with the account scope that actually ran."""
    if (
        scoped.only_account_id is None
        and not scoped.only_account_ids
        and plan.only_account_id is None
        and not plan.only_account_ids
    ):
        # Banking executes an otherwise unscoped aggregate across the holder's accounts. That is
        # an adapter default, not a conversational choice, so it must not turn the remembered plan
        # into an explicit "all accounts" selection.
        return plan
    return replace(
        plan,
        include_internal=scoped.include_internal,
        internal_only=scoped.internal_only,
        across_accounts=scoped.across_accounts,
        only_account_id=scoped.only_account_id,
        only_account_ids=scoped.only_account_ids,
    )


def _with_ratio_execution_scope(plan: FinancialPlan, scoped: FinancialPlan) -> FinancialPlan:
    """Force both operands through the exact same set of accounts."""
    return replace(
        plan,
        include_internal=scoped.include_internal,
        internal_only=scoped.internal_only,
        across_accounts=scoped.across_accounts,
        only_account_id=scoped.only_account_id,
        only_account_ids=scoped.only_account_ids,
    )


def _comparison_window_label(plan: FinancialPlan) -> str:
    """A compact natural label for the two sides of a comparison."""
    if plan.date_range is not None and _is_whole_month(plan.date_range):
        start = plan.date_range.start
        return f"en {answer_writer.month_name(start.year, start.month)}"
    if plan.date_range is not None and _is_whole_year(plan.date_range):
        return f"en {plan.date_range.start.year}"
    return answer_writer.window_of(plan)


def _numbered_month_window(month: int, today: date) -> AbsoluteRange:
    """The most recent occurrence of a numbered month, like the named-month vocabulary."""
    year = today.year if month <= today.month else today.year - 1
    return window_words.whole_month(year, month)


def inherited_window[T: FinancialPlan | FinancialPlanDraft | TopMovements](
    plan: T, previous: FinancialPlan | None, facts: explicit_facts.ExplicitFacts
) -> T:
    """The plan, over the window of the turn it continues, when this sentence names none.

    `explicit_facts.enforce` imposes what the sentence *stated* over the model's reading, and that
    is where this stops: silence is not a statement, so a continuation naming no period leaves
    `facts.window` None, `enforce` with nothing to impose, and whatever the model produced standing.
    On a follow-up, silence about the window is not an absence of information — it means the one
    from before. That is a rule, not a judgement, and it is the same principle `enforce` embodies,
    carried from what the sentence said to what its silence means in a continuation.

    **`plan_merger` already promises exactly this** — "a field nobody named comes from the previous
    plan, and there is no way for that to be lost, because no model is involved in it" — and the
    promise is broken one layer up, because whether the field was *named* is read out of the
    model's own `changed` list rather than out of the sentence. A model emitting
    `changed: ["window"]` about a sentence containing no period has contradicted the user in the
    way that module's docstring says it must not, and both the merge and the planner's
    `inherits_window` obey it. This restores the promise from the sentence, where it was always
    meant to come from.

    Measured on the held-out set: «¿Y lo que cobré?» after a question about last month came back
    over 2026-07-15 .. 2026-07-15 — one day of income under a headline about a month — and the E2E
    harness reported the same collapse independently on the same sentence.

    **`previous` is the continuation base, never `conversation.last_plan`.** The two differ exactly
    where it matters: after a balance the spending plan is still mirrored in `last_plan` while
    `planning_context` withholds it as a base, and `a_category_follow_up_after_a_balance_has_no
    _spending_base` exists to say that "¿y en restaurantes?" must not silently revive that month.
    Reading the mirror instead of the base would revive it.

    **A sentence that names its own window never reaches the replacement**, which is what keeps
    `eight_windows_that_may_not_be_inherited` green: all eight name a period, so `facts.window` is
    not None for every one of them and `enforce` has already imposed it above. The boundary is not
    a second opinion about which turns stand alone — it is the same fact `_starts_new_financial
    _scope` is built on, since a window is the half of that predicate this one reads. A sentence
    with no window cannot start a new scope, so the two can never disagree.

    Narrow in the one way that matters: a window the model got right out of words this service's
    own reader does not know would be replaced here. `continuation_patch` cannot produce that case
    — it and `explicit_facts` read the same `window_words`, so a grounded window patch always
    arrives with `facts.window` set — and for the model it is the trade `explicit_facts` argues in
    full: a deterministic reader may be overruled by the sentence, and there is nothing left to
    overrule a model that invented a month.
    """
    if previous is None or facts.window is not None:
        return plan
    window = previous.period or previous.date_range
    if window is None:
        return plan
    # As a pair, never one at a time, for the reason `enforce` gives: a plan carries exactly one of
    # the two, so setting a range beside an inherited period builds what the domain refuses.
    return replace(
        plan,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
    )


def _from_what_the_sentence_said(
    planned: PlanningResult, query: AskFinancialQuestionQuery
) -> PlanningResult | None:
    """A plan built from the sentence alone, when the model gave up on one it could have made.

    **The guard owns refusals; the model does not get to invent one.** Nor may it ask for a field
    the sentence already states precisely. `capability_guard` is what
    decides which questions this product declines — ratios and arbitrary partial-range
    comparisons — and
    it runs *before* the model. So an `UNSUPPORTED` naming a family the guard does not have is not a
    refusal this service makes; it is the model failing, dressed as a product limit. Handing it to
    the user is the worst kind of wrong answer: "no puedo" about something that can be, said with
    the authority of a rule.

    Ollama returned exactly that for "¿qué intereses me ha dado la hucha?" — a question whose
    every part is in a closed vocabulary this service reads itself.

    So a refusal or clarification is re-read deterministically, and a plan is built only when the
    sentence states enough on its own: a window, and a category named precisely enough for the
    gate to be sure of it. Both,
    never one — a category with no window would answer about a period nobody chose, and a window
    with no category would total everything under a question about one thing. `None` when it does
    not, and then the decline stands: this rescues a plan the sentence contains, and never guesses
    at one it does not.
    """
    if planned.outcome not in {
        PlanningOutcome.UNSUPPORTED,
        PlanningOutcome.NEEDS_CLARIFICATION,
    }:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    supported_average = planned.reason == "AVERAGE" and facts.metric is Metric.AVERAGE
    supported_comparison = planned.reason == "COMPARISON" and bool(facts.compared_categories)
    if (
        planned.outcome is PlanningOutcome.UNSUPPORTED
        and planned.reason in _MISSING_CAPABILITY
        and not (supported_average or supported_comparison)
    ):
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
            draft=explicit_facts.reshaped(
                FinancialPlanDraft(
                    direction=facts.direction or _direction_of(named),
                    categories=named,
                ),
                group_by=facts.group_by or GroupBy.NONE,
                metric=facts.metric or Metric.SUM,
            ),
            awaiting=Slot.WINDOW,
        )

    plan = explicit_facts.reshaped(
        FinancialPlan(
            direction=facts.direction or _direction_of(named),
            period=facts.window if isinstance(facts.window, RelativePeriod) else None,
            date_range=facts.window if isinstance(facts.window, AbsoluteRange) else None,
            categories=named,
        ),
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


@dataclass(frozen=True)
class _NeverAMerchant:
    """The bare word this turn typed, when B-2(a)'s closed stop-list already knows it is not a
    shop's name -- `_a_bare_shop_name`'s own sentinel for "ask, do not guess"."""

    said: str


def _a_bare_shop_name(
    query: AskFinancialQuestionQuery, was: Conversation
) -> tuple[str, FinancialPlan] | _NeverAMerchant | None:
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
    facts = explicit_facts.read(query.sentence, today=query.today)
    if (
        category_words.named_in(query.sentence)
        or account_words.named_in(query.sentence)
        or facts.window is not None
        or facts.group_by is not None
        or facts.metric is not None
        or facts.direction is not None
        or intent_router.asks_for_something_else(query.sentence)
        or capability_guard.unsupported_reason(query.sentence) is not None
    ):
        # A word another vocabulary already owns. "Gasolina" is a category, "la hucha" an account
        # and "enero" a window, and each of them has a path that answers it properly.
        return None
    stripped = query.sentence.strip(" ¿?¡!.,;:")
    if merchant_words.never_a_merchant(query.sentence):
        # B-2(a) (Wave 3 hardening, R-H1): the closed stop-list -- words this service already
        # knows are a discard, a correction or a pleasantry, never a shop. "Nada" stops being a
        # merchant filter here rather than one query later, at zero cost instead of three.
        return _NeverAMerchant(stripped)
    return stripped, plan


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


def _decline(
    planned: PlanningResult, was: Conversation, said: explicit_facts.ExplicitFacts
) -> AskFinancialQuestionResult:
    """The five outcomes that never reach the ledger.

    Each gets its own sentence. Collapsing them into "no he podido" would tell the user
    nothing about whether to rephrase, wait, or give up.

    `said` is what the declined sentence stated outright. It is carried here rather than re-read
    because only the caller still has the sentence: `_next_state` needs it to keep a stored draft
    honest, and by the next turn the sentence is gone.
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

    left_open = _next_state(planned, said)
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


def _next_state(
    planned: PlanningResult, said: explicit_facts.ExplicitFacts
) -> ActiveWorkflow | None:
    """Where a declined turn leaves the conversation.

    A clarification becomes an open question carrying its draft and its slot, so the reply completes
    it. A missing capability becomes a state that remembers what it was about, so the follow-up is a
    patch. Everything else leaves nothing open: a malformed answer and a dead provider are not
    conversations, they are failures, and inviting the user to continue one would be a lie.

    **The draft is enforced against the sentence that produced it**, the same rule the answerable
    branch states out loud: what the sentence stated outright wins over the model's reading of it.
    Asking a question does not make the model's reading any better. "¿Qué día suelo gastar más?" is
    read here as `Metric.FREQUENCY` and was stored as the model's `AVERAGE`, so «en julio» came back
    «el importe medio por movimiento fue de 67,71 €» — a real figure, and not an answer to the
    question asked.

    Here rather than on the reply, because here is the last turn that still has the sentence. The
    reply is a window and nothing else: `explicit_facts.read("en julio")` states no metric, so by
    then there is nothing left to enforce and the draft is all that remains of what was asked.
    """
    match planned.outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION if planned.draft is not None:
            return FinancialClarification(
                draft=explicit_facts.enforce(planned.draft, said), awaiting=planned.awaiting
            )
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


def _refusal_for(rule: str, sentence: str, plan: FinancialPlan | TopMovements) -> str:
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
    if rule == "BREAKDOWN_ANSWERED_AS_ONE_GROUP" and isinstance(plan, FinancialPlan):
        # The generic message is worse than useless for this one: the person asked for a breakdown,
        # the grouping collapsed because it is filtered to a single value, and saying "díla de otra
        # forma" invites the same sentence and the same collapse. What they can change is the
        # dimension, so that is what this names — and it suggests a month, the one dimension
        # `_ONE_VALUE_COLLAPSES` can never collapse, so the sentence it offers is answerable.
        #
        # Four words duplicated from `answer_writer`, which names the same dimensions for group
        # headings. Sharing them would mean a domain service holding user-facing Spanish; the cost
        # of the copy is that both have to change together, and the cost of the alternative is a
        # layer boundary, so the copy wins and this comment is what pays for it.
        dimension = {
            GroupBy.CATEGORY: "categoría",
            GroupBy.MERCHANT: "comercio",
            GroupBy.COUNTERPARTY: "contraparte",
            GroupBy.ACCOUNT: "cuenta",
        }.get(plan.group_by, "eso")
        return (
            f"Me pides un reparto, pero la consulta que he construido agrupa por {dimension} y "
            f"a la vez la filtra a una sola, así que saldría una única fila y no te diría nada. "
            f"Dime por qué quieres que lo reparta y te lo doy: por ejemplo «por mes»."
        )
    if rule != "CATEGORY_MISREAD":
        return _REPHRASE
    named = category_words.precisely_named_in(sentence)
    words = " y ".join(category_words.name_of(category) for category in named)
    return (
        f"Creo que preguntas por {words}, pero la consulta que he construido filtra por otra cosa, "
        f"así que no te doy una cifra que estaría mal. Prueba a decírmelo nombrando la categoría: "
        f"«¿cuánto gasté en {words} el mes pasado?»"
    )


def _enforced(
    planned: PlannedQuery, checked: PlannedQuery, facts: explicit_facts.ExplicitFacts
) -> None:
    """Record what the sentence stated against what the model produced, one field at a time.

    **The seam this whole design exists for**, and the reason it is per field rather than per turn:
    a turn where the sentence overruled the model on the window and agreed with it on the direction
    is two different facts about two different rules, and one `enforce: overrode` row cannot say
    which. Read back, this is «los últimos 3 meses» answered over five, visible as
    `window: LAST_N_MONTHS:5 → LAST_N_MONTHS:3` beside `direction: EXPENSE → EXPENSE`.

    Only the fields the sentence actually stated. `enforce` has nothing to impose where the
    sentence said nothing, so a decision there would be a row saying a rule that did not run
    agreed — which is how an override rate acquires a denominator of turns it was never asked
    about. Silence is `inherited_window`'s seam, not this one.

    `changed` and the verdict both come from the one comparison, so the invariant `Decision`
    deliberately does not enforce cannot be broken here by miswiring: a field that reads the same
    before and after is `agreed`, and it is the case worth having — a deterministic rule that fires
    on every turn and alters nothing is noise, and only the agreements make that visible.

    Nothing here can carry what the holder wrote. A window is a period name and a count or two
    dates, and the other three are enum values; `field` is one of four literals typed here. What a
    category *is* never reaches this — `enforce` moves categories too, and they are deliberately
    absent, because a category name is one unspaced word and `_own_words` says plainly that it
    cannot tell «abogado» from GROCERIES.
    """
    for name, stated in (
        ("window", facts.window),
        ("direction", facts.direction),
        ("group_by", facts.group_by),
        ("metric", facts.metric),
    ):
        if stated is None:
            continue
        was, now = _field_of(planned, name), _field_of(checked, name)
        record(
            Step.ENFORCE,
            Verdict.OVERRODE if now != was else Verdict.AGREED,
            changed=now != was,
            field=name,
            was=was,
            now=now,
        )


def _field_of(plan: PlannedQuery, name: str) -> str | None:
    """One field of a plan, in this service's own vocabulary, or None where the shape has none.

    A ranking has no direction, grouping or metric — «a qué personas» is not a question about one
    movement — so the sentence can state one and there be nothing on the plan to compare it with.
    That reads as None on both sides, which is `agreed`: the plan did not move.
    """
    if name == "window":
        return _window_of(plan)
    value = getattr(plan, name, None)
    return None if value is None else str(value.value)


async def _decided_visual(
    result: AskFinancialQuestionResult,
    policy: VisualPlanningPolicy,
    presentation: PresentationContext,
    question: str,
) -> AskFinancialQuestionResult:
    """Decide the chart once, in the application, where a model call is able to live.

    Every branch of `_steps` passes through the single line in `stream` that calls this, for the
    same reason the turn record is appended there: forty branches build a result and the decision
    is the same fact in all of them. It used to be taken in `llm_mapper`, *twice* -- once for the
    response DTO and again for the conversational state.

    The eligibility guard is stated here because this is where it belongs. A dataset with one row
    is a KPI and never enters the visual policy at all, which is the precondition that makes
    "every request that gets this far ends in a ChartSpec" a claim worth holding.

    A function rather than a method so a test can drive the decision without standing up five
    ports, and `policy` a parameter rather than a construction so wave 3 can hand it a model
    planner's fallback without this function learning what a model is.

    `question` is passed through to the policy and nowhere else: it reaches the model the way the
    financial planner already sends it, and never the record below, whose every slot is service
    vocabulary.
    """
    dataset = result.visual_dataset
    if dataset is None or len(dataset.rows) < 2:
        return result
    context = result.plan_context
    profile = visual_profile_of(
        dataset,
        total_minor=result.total_minor,
        # Both off the plan, and both the values `llm_mapper._chart_for_dataset` read off this
        # same Result before the decision moved up here: `group_by` says whether "2026-01" is a
        # calendar bucket rather than a label that looks like one, and `group_limit` says the rows
        # were already cut by somebody who asked for their top 25.
        group_by=context.group_by if context is not None else None,
        plan_limited=context is not None and context.group_limit is not None,
        plan_metric=context.metric if context is not None else None,
    )
    decision = await policy.decide(question, profile, presentation)
    if decision is None:
        return result
    record_visual_decision(decision)
    return replace(result, visual_decision=decision)


def _classify_and_record_visual_follow_up(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> VisualFollowUp | None:
    """Twelfth seam. Classifies what the turn wants from the chart it follows, records it, and
    returns the classification -- changes nothing else: the kind and the claimant are service
    vocabulary, the sentence stays in the query. `conversation` is the handler's working
    conversation (`was`), not `query.conversation`.

    Returns what it classified -- `None` when no chart is in context -- so `_steps` can carry it
    onto the result `_remember_visual_context` (Task 4) reads `derived_from` off, through
    `stream`'s own box (`follow_up_seen`).

    Three early returns above this call in `_steps` -- retry control, alias learning, and the
    previous-plan restore -- end the turn before `analysis` exists, so none of them reach here:
    those turns record nothing at all rather than `UNRELATED`. Whoever reads this counter is
    reading it against that denominator, not against every turn that had a chart in context.

    Two of those three still draw a chart of their own: retry control and the previous-plan
    restore both reach `self._answer` -> the module-level `_answered`, which always sets
    `visual_dataset`. Neither ever reaches this line, so that chart's `visual_follow_up` stays
    `None` and `_remember_visual_context` (Task 4) writes `derived_from=None` on it even though a
    chart is already in context -- correct per the brief and 4.1 (no `analysis`, so no
    classification), but as of Task 4 no longer only this counter's denominator: the same gap now
    governs chart lineage on the wire.
    """
    if not conversation.visual_contexts:
        return None
    follow_up = classify_visual_follow_up(query, analysis, conversation)
    record_visual_follow_up(follow_up)
    return follow_up


def record_visual_decision(decision: VisualDecision, *, was: str | None = "deterministic") -> None:
    """One `record()` per turn that entered the policy, in the design's own vocabulary.

    `changed` is whether the model's chart is what got shown -- only an accepted proposal in
    hybrid/promoted does that. Shadow always shows the floor, so it is `AGREED`/`changed=False`
    even on the run where the model's own proposal differed; `detail` is where that comparison
    lives, because `was`/`now` say what was shown, not what was tried.

    `was` is read only by the `patch` branch: a restyle's decision carries no floor to compare
    itself against (`proposed`/`fallback_reason` are never set on one), so the caller -- the only
    one that knows what the chart showed before the patch -- hands it in, rather than this
    function inventing "deterministic" for a chart that a model, or an earlier patch, produced.
    `changed` follows the same invariant every other row does: a `patch` *is* what got shown, so
    `was != "patch"` is `True` whenever the previous chart had a different source, which is
    always (a restyle only exists because something was already on screen, and `now` is always
    "patch") -- `None` included, since `None != "patch"`.

    **`was` is `None` whenever the caller does not know it, and that is most of the time (I2,
    fix wave).** `VisualContext.decision` is turn-local by design (§4): the service keeps no
    conversation store, so a context that crossed the wire and came back has `decision is None`
    even though a decision *did* produce the chart on screen. Before this fix the caller
    substituted `"deterministic"` for that case, which was true only by accident -- while
    `ORCHESTRATOR_VISUAL_PLANNER` stayed `deterministic` for everyone. The day it is promoted
    (Task 9), or a chart is restyled a second time, the substitution becomes a false provenance
    claim: a row saying a model's chart was the floor's. Recording `None` (this module's own
    `Decision.was: str | None`, so nothing downstream has to invent a sentinel) says the honest
    thing -- the source is unrecorded -- until a signed visual context (planned Task 3b) makes it
    recoverable across the wire.

    A `patch` returns before the `logger.info` below: that call is `visual_planner.decision`, the
    promotion-evidence sink Task 9 reads acceptance/abstention/latency rates off, and this
    function's own opening line says what it is for -- one record per turn *that entered the
    policy*. A restyle never does; `record()` above is its whole audit trail.
    """
    if decision.source == "patch":
        record(
            Step.VISUAL_PLANNER,
            Verdict.AGREED,
            changed=(was != "patch"),
            field="chart",
            was=was,
            now="patch",
            detail="accepted",
        )
        return
    if not decision.proposed and decision.fallback_reason is None:
        record(
            Step.VISUAL_PLANNER,
            Verdict.AGREED,
            changed=False,
            field="chart",
            now="deterministic",
            detail="not_run",
        )
    elif decision.accepted:
        record(
            Step.VISUAL_PLANNER,
            Verdict.OVERRODE,
            changed=True,
            field="chart",
            was="deterministic",
            now="llm",
            detail="accepted",
        )
    elif decision.fallback_reason is not None:
        detail: str = decision.fallback_reason
        if decision.rejected_rule is not None:
            detail = f"invalid_proposal:{decision.rejected_rule}"
        elif decision.error_detail is not None:
            # U-8 (Wave 3 hardening): a non-provider exception inside the planner still records
            # `provider_error` (`FallbackReason` stays the four the wire seals), but names itself
            # here so the row is not indistinguishable from the provider actually being down.
            detail = decision.error_detail
        record(
            Step.VISUAL_PLANNER,
            Verdict.FAILED,
            changed=False,
            field="chart",
            now="deterministic",
            detail=detail,
        )
    else:
        record(
            Step.VISUAL_PLANNER,
            Verdict.AGREED,
            changed=False,
            field="chart",
            was="deterministic",
            now="deterministic",
            detail="shadow:changed" if decision.changed_from_baseline else "shadow:same",
        )
    logger.info(
        "visual_planner.decision",
        extra={
            "proposed": decision.proposed,
            "valid": decision.valid,
            "accepted": decision.accepted,
            "changed_from_baseline": decision.changed_from_baseline,
            "selected_source": decision.source,
            "fallback_reason": decision.fallback_reason,
            "latency_ms": decision.latency_ms,
            "proposed_spec": (
                canonical(decision.proposed_spec) if decision.proposed_spec is not None else None
            ),
            "shown_spec": canonical(decision.spec),
        },
    )


def record_visual_decision_refused(context: VisualContext, rejection: Rejection) -> None:
    """A merged patch the verifier refused. One `DECLINED` row, next to `record_visual_decision`
    for the same reason every other seam's rows live together: a rejection rate is read per rule
    across every turn that produced one, `patch` decisions included.

    `changed=False` and `now` names what stayed on screen -- the previous chart, untouched -- not
    what was tried and refused. `context` is the chart already showing, so its own decision is
    what `now` reports when there is one.

    **`now` is `None` whenever the caller does not know it, and that is most of the time (I2,
    fix wave; F3, fix wave 2, applying the same argument here).** `VisualContext.decision` is
    turn-local by design (§4): the service keeps no conversation store, so a context that crossed
    the wire and came back has `decision is None` even though a decision did produce the chart on
    screen. This used to substitute `"deterministic"` for that case, true only by accident -- the
    same false provenance claim `record_visual_decision`'s own docstring already refused for
    `was`. The row never carries the rejected spec at all, only the rule that refused it.
    """
    now = context.decision.source if context.decision is not None else None
    record(
        Step.VISUAL_PLANNER,
        Verdict.DECLINED,
        changed=False,
        field="chart",
        now=now,
        detail=f"invalid_proposal:{rejection.rule}",
    )


def record_context_untrusted(context: VisualContext) -> None:
    """One `DECLINED` row for an echoed visual context whose seal did not check out.

    Task 1 (Wave 3 hardening, A-1). `context.trusted` is the bool everything downstream branches
    on; `context.trust_reason` is the one of five closed reasons `MetricTokenCodec.verify_context`
    found (`"holder"`, `"conversation"`, `"position"`, `"content"`, `"format"`) -- kept off the
    text a holder reads (`llm_controller`'s `VISUAL_UNTRUSTED` sentence collapses the first three
    into one and the last two into the other) but not off the promotion log, the same way a merge
    refusal's rule name (`record_visual_decision_refused`, just above) never reaches the sentence
    either. A rejection rate per reason is read across every turn that produced one, next to every
    other seam's rows, for the same reason `record_visual_decision_refused` sits here rather than
    logging ad hoc from the controller.

    Called once per untrusted context, at verification time (`llm_controller._conversation_of`) --
    not deferred to whichever response branch happens to read `trusted` later, so a context that is
    never actually used for anything this turn (echoed back but not targeted by a restyle or an
    explanation) is still counted.
    """
    record(
        Step.CONTEXT_TRUST,
        Verdict.DECLINED,
        changed=False,
        field="chart",
        detail=f"untrusted:{context.trust_reason}",
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
        excluded_categories=list(plan.excluded_categories),
        merchants=list(plan.merchants),
        merchant_terms=list(plan.merchant_terms),
        excluded_merchants=list(plan.excluded_merchants),
        counterparties=list(plan.counterparties),
        excluded_counterparties=list(plan.excluded_counterparties),
        counterparty_types=[item.value for item in plan.counterparty_types],
        group_by=plan.group_by.value,
        metric=plan.metric.value,
        group_limit=plan.group_limit,
        include_internal=plan.include_internal,
        internal_only=plan.internal_only,
        across_accounts=plan.across_accounts,
        only_account_id=str(plan.only_account_id) if plan.only_account_id else None,
        only_account_ids=[str(account_id) for account_id in plan.only_account_ids],
        transaction_ids=[str(transaction_id) for transaction_id in plan.transaction_ids],
        transaction_types=[item.value for item in plan.transaction_types],
    )


def _recent_movement_count(sentence: str) -> int | None:
    if _RECENT_UNBOUNDED.search(spanish.folded(sentence)):
        return 5
    found = _RECENT_MOVEMENTS.search(spanish.folded(sentence))
    if found is None:
        return None
    written = (
        found.group("count")
        or found.group("count_before")
        or found.group("count_before_bare")
        or found.group("count_first")
    )
    count = int(written) if written.isdigit() else spanish_numbers.integer(written)
    return count if count is not None and 1 <= count <= 50 else None


def _movement_example_count(sentence: str) -> int:
    folded = spanish.folded(sentence)
    if re.search(r"\b(?:un\s+par(?:\s+de)?|dos)\s+ejempl", folded):
        return 2
    if re.search(r"\btres\s+ejempl", folded):
        return 3
    return 1


def _spoken_movement(movement: Movement) -> SpokenMovement:
    return SpokenMovement(
        occurred_at=movement.occurred_at.isoformat(),
        description=movement.description,
        amount_minor=movement.amount_minor,
        direction=movement.direction,
        merchant_name=movement.merchant_name,
        category=movement.category,
        transaction_type=movement.transaction_type.value if movement.transaction_type else None,
        counterparty_name=movement.counterparty_name,
        counterparty_type=movement.counterparty_type.value,
        receipt_category=movement.receipt_category.value if movement.receipt_category else None,
    )


def _evidence_ids(movements: tuple[Movement, ...] | list[Movement]) -> tuple[UUID, ...]:
    """Validated, deduplicated identifiers for the rows actually exposed as evidence."""
    found: list[UUID] = []
    for movement in movements:
        if movement.transaction_id is None:
            continue
        try:
            transaction_id = UUID(movement.transaction_id)
        except ValueError:
            continue
        if transaction_id not in found:
            found.append(transaction_id)
        if len(found) == MAX_SELECTED_TRANSACTIONS:
            break
    return tuple(found)


_WEEKDAY_LABELS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def _visual_metric_value(plan: FinancialPlan, group: Group) -> int:
    """Return the measured value the plan asked for, rounded once at the backend."""
    if plan.metric is Metric.COUNT:
        return group.movement_count
    if plan.metric is Metric.AVERAGE:
        if group.movement_count <= 0:
            return 0
        units, remainder = divmod(abs(group.total_minor), group.movement_count)
        rounded = units + int(remainder * 2 >= group.movement_count)
        return -rounded if group.total_minor < 0 else rounded
    return group.total_minor


def _asks_for_visualization(sentence: str) -> bool:
    folded = spanish.folded(sentence)
    return bool(
        re.search(
            r"\b(?:grafico|grafica|visualiza|representa|en una linea|en barras)\b",
            folded,
        )
    )


def _local_visual_date(movement: Movement, reporting_timezone: str) -> str:
    occurred_at = movement.occurred_at
    if occurred_at.tzinfo is None:
        return occurred_at.date().isoformat()
    return occurred_at.astimezone(ZoneInfo(reporting_timezone)).date().isoformat()


def _answered_window(plan: FinancialPlan, answer: QueryAnswer) -> AbsoluteRange | None:
    """The dates the rows a chart is about to draw actually cover -- never an amount.

    N-2 (fix round 2): the ledger's own resolved window is bounded by the plan's own explicit
    range, never trusted alone. `answer.period_start`/`period_end` are what
    `http_financial_query.py` maps from `periodStart`/`periodEnd` (`:310-311`) -- what banking
    says it ran -- but a lenient ledger, or a fake built to answer whole calendar buckets (the
    corpus harness's own `_resolved_window`), can report a window wider than the plan it was
    handed: a plan asking for 10 June - 20 July grouped by month draws a July bar, and an answer
    of "the whole of June and July" is not what that plan queried. `plan.date_range` is the bound
    the drill must never exceed, so when both are known the window is their intersection
    (`max` of the two starts, `min` of the two ends); an empty intersection -- the ledger answered
    a window disjoint from the plan's own -- is `None`, fail closed, never a guess. The ledger's
    window alone when the plan names a `RelativePeriod` (nothing of the plan's own to bound it
    with); `plan.date_range` alone when the ledger answered no period at all (the corpus harness,
    before Wave 4.3's own fix to it); `None` when neither is set -- the drill's own feasibility
    check (`readers/drill_down.py`) reads that `None` as "refuse, never guess" either way.
    """
    answered = (
        AbsoluteRange(answer.period_start, answer.period_end)
        if answer.period_start is not None and answer.period_end is not None
        else None
    )
    if answered is not None and plan.date_range is not None:
        start = max(answered.start, plan.date_range.start)
        end = min(answered.end, plan.date_range.end)
        return None if end < start else AbsoluteRange(start, end)
    if answered is not None:
        return answered
    return plan.date_range


def _visual_dataset(
    plan: FinancialPlan,
    answer: QueryAnswer,
    visible_groups: tuple[Group, ...],
    *,
    reporting_timezone: str,
) -> VisualDataset | None:
    """Build rows while the typed financial result still owns their semantics."""
    window = _answered_window(plan, answer)
    if plan.metric is Metric.FREQUENCY:
        frequency = frequency_analyzer.analyse(
            answer,
            reporting_timezone=reporting_timezone,
        )
        if not frequency.complete or frequency.count < 2:
            return None
        counts = [0] * len(_WEEKDAY_LABELS)
        for movement in answer.movements:
            occurred_at = movement.occurred_at
            local = (
                occurred_at
                if occurred_at.tzinfo is None
                else occurred_at.astimezone(ZoneInfo(reporting_timezone))
            )
            counts[local.weekday()] += 1
        return VisualDataset(
            rows=tuple(
                VisualRow(dimension=label, value_minor=counts[index])
                for index, label in enumerate(_WEEKDAY_LABELS)
            ),
            metric=Metric.FREQUENCY.value,
            title=answer_writer.describe(plan),
            window=window,
        )

    if plan.metric is Metric.RECURRENCE:
        recurrence = recurrence_analyzer.analyse(
            answer,
            reporting_timezone=reporting_timezone,
        )
        if not recurrence.complete or not recurrence.candidates:
            return None
        candidates = {
            spanish.folded(candidate.counterparty): candidate for candidate in recurrence.candidates
        }
        rows: list[VisualRow] = []
        for movement in answer.movements:
            label = (
                movement.counterparty_name or movement.merchant_name or movement.description
            ).strip()
            candidate = candidates.get(spanish.folded(label))
            if candidate is None:
                continue
            rows.append(
                VisualRow(
                    dimension=candidate.counterparty,
                    value_minor=abs(movement.amount_minor),
                    series=candidate.cadence,
                    occurred_at=_local_visual_date(movement, reporting_timezone),
                )
            )
        return (
            VisualDataset(
                rows=tuple(rows),
                metric=Metric.RECURRENCE.value,
                title=answer_writer.describe(plan),
                window=window,
            )
            if len(rows) >= 2
            else None
        )

    if not visible_groups:
        return None
    return VisualDataset(
        rows=tuple(
            VisualRow(
                dimension=group.key,
                value_minor=_visual_metric_value(plan, group),
            )
            for group in visible_groups
        ),
        metric=plan.metric.value,
        title=answer_writer.describe(plan),
        window=window,
    )


def _answered(
    plan: FinancialPlan,
    answer: QueryAnswer,
    was: Conversation,
    sentence: str,
    *,
    reporting_timezone: str,
    ran_as: FinancialPlan,
) -> AskFinancialQuestionResult:
    """Present one answered plan.

    `plan` is what was asked and becomes the memory a follow-up inherits. `ran_as` is the same
    plan as it was executed, and it differs in one respect: which of the holder's accounts the
    figures came from, which the conversation can settle without this sentence naming it. Both are
    required, because the one consumer that wants the second is easy to serve wrongly with the
    first -- and did, for as long as this took one argument.
    """
    if plan.metric is Metric.CUMULATIVE:
        answer = cumulative_calculator.accumulate(answer, plan.group_by)
    # "¿Cuándo me ingresan la nómina?" ran the right query and was reported as a total. The
    # question decides which sentence the same figures become, so the reading happens here rather
    # than in the writer — a writer that inspected the question would be a writer with a parser
    # in it.
    facts = explicit_facts.read(sentence)
    if plan.metric is Metric.FREQUENCY:
        text = answer_writer.when(answer, plan, reporting_timezone=reporting_timezone)
    elif plan.metric is Metric.RECURRENCE:
        text = answer_writer.recurring_expenses(answer, plan, reporting_timezone=reporting_timezone)
    elif plan.internal_only:
        text = answer_writer.internal_transfers(answer, plan)
    elif facts.asks_top_category:
        text = answer_writer.top_category(answer, plan)
    elif (
        facts.compared_categories or facts.asks_comparison_reference
    ) and plan.metric is Metric.AVERAGE:
        text = answer_writer.compare_averages(answer, plan)
    else:
        text = answer_writer.write(answer, plan)
    # Off the executed plan, never the remembered one. "Consultado en todas tus cuentas" describes
    # where the figures came from, and after the turn that settled the scope no later sentence
    # names it again -- so the remembered plan correctly says `across_accounts=False` while the
    # ledger is still being read across every account. Read from there, the note vanished from the
    # second such turn onward and a figure covering three accounts was presented as one account's.
    # The two ranking paths already pass the plan they ran; this is the same call.
    scope_note = _scope_note(ran_as, sentence)
    if scope_note and "Consultado en " not in text:
        text = f"{text}\n{scope_note}"
    referenced_category: str | None = None
    referenced_group_by: GroupBy | None = None
    referenced_group_key: str | None = None
    if len(plan.categories) == 1 and category_words.words_for(plan.categories[0]) is not None:
        referenced_category = plan.categories[0]
        referenced_group_by = GroupBy.CATEGORY
        referenced_group_key = plan.categories[0]
    if len(plan.merchants) == 1:
        referenced_group_by = GroupBy.MERCHANT
        referenced_group_key = plan.merchants[0]
    if len(plan.counterparties) == 1:
        referenced_group_by = GroupBy.COUNTERPARTY
        referenced_group_key = plan.counterparties[0]
    if plan.group_by in {
        GroupBy.CATEGORY,
        GroupBy.MERCHANT,
        GroupBy.COUNTERPARTY,
        GroupBy.ACCOUNT,
    }:
        highlighted_group = next(
            (group for group in answer.groups if group.movement_count > 0),
            None,
        )
        if highlighted_group is not None:
            referenced_group_by = plan.group_by
            referenced_group_key = highlighted_group.key
            if (
                plan.group_by is GroupBy.CATEGORY
                and category_words.words_for(highlighted_group.key) is not None
            ):
                referenced_category = highlighted_group.key
    if facts.asks_top_category:
        highlighted = next(
            (
                group.key
                for group in answer.groups
                if group.movement_count > 0 and category_words.words_for(group.key) is not None
            ),
            None,
        )
        referenced_category = highlighted
        if highlighted is not None:
            referenced_group_by = GroupBy.CATEGORY
            referenced_group_key = highlighted
    visible_groups = (
        answer.groups[: plan.group_limit] if plan.group_limit is not None else answer.groups
    )
    visible_movements = () if plan.group_limit is not None else answer.movements
    visible_total = (
        sum(group.total_minor for group in visible_groups)
        if plan.group_limit is not None
        else answer.total_minor
    )
    visible_movement_count = (
        sum(group.movement_count for group in visible_groups)
        if plan.group_limit is not None
        else answer.movement_count
    )

    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=text,
        plan=answer_writer.describe(plan),
        plan_in_words=answer_writer.plan_in_words(plan),
        plan_context=_spoken(plan),
        # Answered: nothing is left open, and this plan becomes the memory the next follow-up
        # inherits from.
        conversation=answered_with_result(
            was,
            ResultReference(
                source_query=plan,
                category=referenced_category,
                group_by=referenced_group_by,
                group_key=referenced_group_key,
                transaction_ids=_evidence_ids(visible_movements),
            ),
        ),
        currency=answer.currency,
        total_minor=visible_total,
        movement_count=visible_movement_count,
        groups=[
            SpokenGroup(
                g.key,
                g.total_minor,
                g.movement_count,
                _visual_metric_value(plan, g),
            )
            for g in visible_groups
        ],
        movements=[_spoken_movement(movement) for movement in visible_movements],
        suggestions=query_recovery.empty_suggestions(plan) if answer.is_empty else [],
        explicit_visualization=_asks_for_visualization(sentence),
        # R15 (Wave 4.5, round 3 of D-4.5-6/M-2): this is the one producer that actually HAS the
        # `FinancialPlan` that drew `visual_dataset` -- it is the same `plan` two lines below, in
        # its own local scope, never inherited from `conversation.last_plan`. Stamping it here
        # means `stream()`'s back-fill (`~:630-641`) finds `metric_definition` already set and
        # never runs, so `metric_definition_borrowed` stays False and the presenter may name this
        # plan's own noun. The other four producers in this handler (`_how_many_payees`,
        # `_compare_periods`, the ratio branches, `_compare_salary_history`) genuinely run no
        # single `FinancialPlan` of this shape and are correctly left to the back-fill.
        metric_definition=plan,
        visual_dataset=_visual_dataset(
            plan,
            answer,
            visible_groups,
            reporting_timezone=reporting_timezone,
        ),
    )


def _scope_note(plan: FinancialPlan | TopMovements, sentence: str) -> str | None:
    if account_words.asks_for_all_accounts(sentence) or plan.across_accounts:
        return "Consultado en todas tus cuentas."
    names = account_words.named_in(sentence)
    labels = {"CURRENT": "cuenta corriente", "SAVINGS": "Hucha", "ASSET": "fondo"}
    if len(names) == 1 and names[0] in labels:
        return f"Consultado en {labels[names[0]]}."
    if plan.only_account_id is not None or plan.only_account_ids:
        return "Consultado en la cuenta seleccionada."
    return None


def _is_scope_change_followup(sentence: str, *, today: date) -> bool:
    folded = spanish.folded(sentence).strip()
    if not (folded.startswith("ahora") or folded.startswith("y ahora")):
        return False
    if _starts_new_financial_scope(sentence, today=today):
        return False
    return account_words.asks_for_all_accounts(sentence) or bool(account_words.named_in(sentence))


def _answers_account_scope(sentence: str, names: tuple[str, ...]) -> bool:
    """Whether the short sentence is an account-choice chip, not a new financial question.

    `names` are the display names the question offered, which is all a custom-named chip can be
    matched against — the pending clarification's candidates, or the live accounts the backstop
    listed.
    """
    folded = spanish.folded(sentence).strip(" \t\r\n¿?!.,;:")
    if not folded or any(
        word in folded.split() for word in ("cuanto", "cuánta", "saldo", "tengo", "dinero")
    ):
        return False
    if account_words.asks_for_all_accounts(sentence):
        return len(folded.split()) <= 5
    if account_words.custom_chosen_among(sentence, names):
        return len(folded.split()) <= 5
    return bool(account_words.named_in(sentence)) and len(folded.split()) <= 4


def _chosen_account_scope(
    sentence: str, holdings: tuple[Holding, ...], candidates: set[UUID]
) -> AccountSelection | None:
    """The selection a reply to «¿En qué cuenta…?» makes among the accounts it was offered.

    «Todas mis cuentas» is every account. Otherwise exactly one offered account has to be named,
    by its own display name or, failing that, by its kind; anything else — two of them, none, one
    the question did not offer — selects nothing, and the caller asks again rather than guessing.
    `holdings` is the live directory, so an account that has gone since the question was asked
    cannot be chosen.
    """
    if account_words.asks_for_all_accounts(sentence):
        return AccountSelection(AccountSelectionMode.ALL)
    names = tuple(holding.display_name for holding in holdings if holding.account_id in candidates)
    custom = account_words.custom_chosen_among(sentence, names)
    kinds = account_words.named_in(sentence)
    chosen = [
        holding
        for holding in holdings
        if holding.account_id in candidates
        and (holding.display_name in custom or (not custom and holding.kind in kinds))
    ]
    if len(chosen) != 1:
        return None
    return AccountSelection(AccountSelectionMode.SELECTED, (chosen[0].account_id,))


def _choose_one_of(
    names: tuple[str, ...], conversation: Conversation
) -> AskFinancialQuestionResult:
    """The account question again, as a choice, when a reply named no single offered account.

    The conversation goes back untouched, so whatever made the reply an answer — the open
    clarification, or the backstop's question in the log — is still there for the next one.
    """
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
        text="Elige una de estas cuentas: " + ", ".join(names) + ", o Todas mis cuentas.",
        suggestions=[*names, "Todas mis cuentas"],
        conversation=conversation,
    )


def _asked_which_account_about(was: Conversation, offered: tuple[str, ...]) -> str | None:
    """The sentence the backstop's account question is still waiting on, if it is.

    Three facts have to agree. `last_asked` is the account and nothing else is waiting — a
    clarification, a payment or a transfer with a slot of its own owns the reply instead. And the
    newest turn that asked anything asked `_WHICH_ACCOUNT`: an aside in between («gracias»,
    «¿quién eres?») asks nothing and leaves `last_asked` alone, so the question survives it exactly
    as the resumable clarification does.

    A sentence the log may have cut is not resumed. `Turn` truncates at `MAX_TURN_SENTENCE`, and
    asking the first two hundred characters of a longer question would answer something the holder
    did not ask; those few are read as they were before, and the holder can ask again.

    Nor is an account choice, judged against `offered` — the names the question lists — like any
    reply. One is recorded under the question when a resumed question asks it again on the reply's
    own turn (the directory changed between two reads of that turn), and asked as a question
    «Todas mis cuentas» is the balance this exists to stop; the next reply is read as itself.
    """
    if was.last_asked is not Slot.ACCOUNT or was.awaiting is not None:
        return None
    return _the_sentence_that_asked(was, offered, question=_WHICH_ACCOUNT)


def _the_sentence_that_asked(
    was: Conversation,
    offered: tuple[str, ...] = (),
    *,
    question: str | None = None,
    retrying: PlannedQuery | None = None,
) -> str | None:
    """The sentence on the newest turn that asked anything -- the question a reply now answers.

    Every reply that completes a kept plan leaves the reply's own sentence on the turn that runs
    it: «Todas mis cuentas» after «¿En qué cuenta…?», «este año» after «¿De qué periodo?»,
    «reintenta» after the bank failed. The backstop's account question asks the sentence again;
    the others run the plan, and need the sentence for the readers that narrow a category's
    answer to a shop -- «¿cuánto gasté en el gimnasio?» is the gym on the turn that asked it and
    on the turn that answered it alike.

    `question`, when given, is what that turn must have asked: an aside asks nothing and leaves it
    newest, but a different question in between means this reply answers that one. A turn the bank
    could not answer asked too when `retrying` is given -- «¿Reintento?» is what it offered -- and
    then it must be the turn whose plan line is the plan being retried.

    None for a sentence the log may have cut: half a question is not the question. Nor is an
    account choice, judged against `offered` -- the names the question listed -- like any reply.
    One is recorded under the question when a resumed question asks it again on the reply's own
    turn (the directory changed between two reads of that turn), and asked as a question «Todas
    mis cuentas» is a balance.
    """
    asked = next(
        (
            turn
            for turn in reversed(was.turns)
            if turn.question is not None
            or (retrying is not None and turn.outcome == "BANKING_UNAVAILABLE")
        ),
        None,
    )
    if asked is None:
        return None
    if question is not None and asked.question != question:
        return None
    if retrying is not None and asked.plan != answer_writer.describe(retrying):
        return None
    if not asked.sentence.strip() or len(asked.sentence) >= MAX_TURN_SENTENCE:
        return None
    if _answers_account_scope(asked.sentence, offered):
        return None
    return asked.sentence


def _remember_explicit_scope(
    query: AskFinancialQuestionQuery, plan: FinancialPlan | TopMovements
) -> AskFinancialQuestionQuery:
    """Store an account scope in the conversation only when the sentence stated one.

    Every scope reaching this function is a real one: `_scoped` resolved it against the holder's
    directory and the figures were read from exactly those accounts. What the guard separates is
    *where it came from*. A scope the service derived for itself is execution policy — one account
    because the holder has one, the asset account because "cuánto he invertido" names a purpose
    and this service knows which account that purpose lives in, an account carried in from the
    plan the previous turn happened to build. A scope the holder said out loud is conversational
    data, and losing it makes the very next ellipsis widen back to every account.

    A twin of this function without the guard used to run straight after it, so the guard decided
    nothing and every derived scope was persisted as though it had been chosen. Reproduced twice
    against the live service, `demo-token-laura-pays`, from an empty conversation:

        «¿cuánto he invertido en el último año?»  → 1.650,00 € moved into the fund. Correct, and
                                                    `account_selection = SELECTED(Fondo indexado)`.
        «¿cuánto gasté el mes pasado?»            → «No he encontrado movimientos el mes pasado.
                                                    Puedes ampliar el periodo …»

    She spent 1.895,97 € last month, from an account the second question was no longer allowed to
    see — and the answer sent her to widen the window, which was the one part of the plan that was
    right. `answer_writer.write` argues against exactly that sentence: "no encontré nada" usually
    means the plan was wrong, and here it was.
    """
    if not (
        account_words.asks_for_all_accounts(query.sentence)
        or account_words.named_in(query.sentence)
    ):
        return query
    if plan.across_accounts:
        selection = AccountSelection(AccountSelectionMode.ALL)
    elif plan.only_account_id is not None:
        selection = AccountSelection(AccountSelectionMode.SELECTED, (plan.only_account_id,))
    elif plan.only_account_ids:
        selection = AccountSelection(AccountSelectionMode.SELECTED, plan.only_account_ids)
    else:
        return query
    return replace(query, conversation=replace(query.conversation, account_selection=selection))
