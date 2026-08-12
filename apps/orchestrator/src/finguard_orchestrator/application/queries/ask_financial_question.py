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
from typing import Final, Literal
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.constants import (
    MAX_GROUP_RANK,
    MAX_SELECTED_TRANSACTIONS,
    MAX_SUPPORTING_MOVEMENTS,
    RECURRING_EXPENSE_CATEGORIES,
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
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    AccountAlias,
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
    cumulative_calculator,
    entity_alias_words,
    explainer,
    explicit_facts,
    financial_relationships,
    incoming_payment_words,
    intent_router,
    merchant_words,
    payment_words,
    plan_verifier,
    query_recovery,
    ratio_writer,
    spanish,
    spanish_numbers,
    window_words,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
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
from finguard_orchestrator.observability import account_ref

_COUNTERPARTY_LABELS: Final[dict[CounterpartyType, str]] = {
    CounterpartyType.MERCHANT: "comercio",
    CounterpartyType.PERSON: "persona",
    CounterpartyType.EMPLOYER: "empresa pagadora",
    CounterpartyType.BILLER: "emisor de recibos",
    CounterpartyType.ACCOUNT: "cuenta contraparte",
    CounterpartyType.BANK: "banco",
    CounterpartyType.UNKNOWN: "contraparte",
}

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
_COMPARE_WITH_PREVIOUS_PERIOD: Final = re.compile(
    r"^(?:(?:vale|bueno|ok|perfecto)(?:\s+genial)?\s+|pues\s+)?(?:y\s+)?(?:"
    r"(?:me\s+lo\s+)?podrias\s+comparar(?:lo|la)?"
    r"|compara(?:lo|la|melo|mela)?"
    r"|comparame\s+(?:eso|esto|el\s+resultado|la\s+consulta)"
    r"|comprueba(?:lo|la)?"
    r"|revisa(?:lo|la)?"
    r"|hazme\s+una\s+comparacion"
    r")\s+con\s+(?:el\s+|la\s+)?"
    r"(?P<unit>fin\s+de\s+semana|trimestre|semestre|quincena|mes|semana|ano|periodo)\s+"
    r"(?:anterior|pasad[oa])$"
)
_DIRECT_PERIOD_COMPARISON: Final = re.compile(
    r"\b(?:compara\w*|comparacion|frente\s+a(?:l)?|contra|versus|vs"
    r"|mas\b.{0,55}\b(?:que|o)|menos\b.{0,55}\b(?:que|o)"
    r"|(?:cual|que|donde)\b.{0,50}\b(?:mas|menos|mayor|menor)"
    r"|mayor|menor)\b"
)
_SELF_CONTAINED_FINANCIAL_QUESTION: Final = re.compile(
    r"\b(?:cuanto|cuantos|cual|cuales|que)\b.{0,70}"
    r"\b(?:gaste|gastado|pague|pagado|compre|comprado|ingrese|ingresado|cobre|cobrado"
    r"|movimientos|operaciones|categoria)\b"
    r"|\b(?:muestrame|ensename|quiero\s+ver)\b.{0,55}"
    r"\b(?:movimientos|operaciones|gastos|ingresos)\b"
)
_SALARY_HISTORY_COMPARISON: Final = re.compile(
    r"\b(?:cobr|recib|ingres)\w*\b.{0,55}\b(?:mas|menos)\b.{0,55}"
    r"\b(?:anteriores?|otras?)\b"
    r"|\b(?:esa|esta|ultima)\s+nomina\b.{0,55}\b(?:mas|menos)\b.{0,55}"
    r"\b(?:anteriores?|otras?)\b"
)
_NUMBERED_MONTH_FALLBACK: Final = re.compile(
    r"\b(?:el\s+)?mes\s+(?P<first>0?[1-9]|1[0-2])\b.{0,24}"
    r"\b(?:y\s+)?si\s+no\b\s+(?:(?:el\s+)?mes\s+|el\s+)?"
    r"(?P<second>0?[1-9]|1[0-2])\b"
)
_RATIO_WORDS: Final = re.compile(r"\bporcentaje\b|\bque\s+parte\s+de\b|\bque\s+proporcion\s+de\b|%")
_PERCENT_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:cuanto\s+(?:es|fue|supone)\s+eso\s+)?(?:en\s+)?porcentaje$"
    r"|^(?:y\s+)?(?:cual|cuanto)\s+fue\s+(?:la\s+)?(?:diferencia|variacion)\s+porcentual$"
)
_RATIO_WINDOW_REPLY: Final = re.compile(
    r"^(?:(?:vale|bueno|perfecto|pues)\s+)?(?:en\s+|durante\s+)?(?:"
    r"este\s+(?:mes|ano)|esta\s+semana|el\s+(?:mes|ano)\s+(?:pasado|anterior)"
    r"|la\s+semana\s+(?:pasada|anterior)|los\s+ultimos\s+\w+\s+(?:dias|meses|anos)"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre"
    r"|noviembre|diciembre|desde\s+.+|entre\s+.+)\s*$"
)
_INTERNAL_HISTORY_WORDS: Final = re.compile(
    r"\b(?:movimientos?|movimeintos?|traspasos?|transferencias?|movid[oa]s?|traspasad[oa]s?|"
    r"pasad[oa]s?|metid[oa]s?|aportad[oa]s?)\b"
)
_INTERNAL_BETWEEN_ACCOUNTS: Final = re.compile(
    r"\b(?:entre|de\s+una\s+(?:de\s+)?mis)\s+(?:mis|tus|sus|las)?\s*cuentas\b"
    r"|\bde\s+una\s+cuenta\s+a\s+otra\b"
)
_INVESTMENT_HISTORY: Final = re.compile(
    r"\b(?:cuanto|caunto|cuantos|que|cual|dime|dame|muestrame|ensename)\b.{0,55}"
    r"\b(?:(?:invertid|aportad|metid|traspasad)\w*|gast\w*\s+en\s+inversion(?:es)?)\b"
    r"|\b(?:mis\s+)?(?:aportaciones?|inversiones?)\b.{0,40}\b(?:hech|realiz|este|ultimo)\w*\b"
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
_GAMBLING: Final = re.compile(r"\b(?:apuest\w*|bet(?:365|fair)?|casino\w*|codere|bwin|sportium)\b")

logger = logging.getLogger(__name__)


def _clean_turn(sentence: str) -> str:
    """Fold a short conversational turn and remove only punctuation at its edges."""
    return re.sub(r"[^a-z0-9]+", " ", spanish.folded(sentence)).strip()


def _starts_new_financial_scope(sentence: str, *, today: date) -> bool:
    """Whether this turn states a complete new question rather than an ellipsis.

    A concrete window plus a financial question is enough to stand on its own. Inheriting an old
    category there is how "cuÃ¡nto gastÃ© en la cuenta principal este mes" remained filtered by
    restaurants. Short turns without a window ("y cuÃ¡nto gastÃ©?") remain follow-ups.
    """
    folded = spanish.folded(sentence)
    if re.search(r"\b(?:esa|ese|esas|esos)\b|\blo\s+mismo\b", folded):
        return False
    return (
        window_words.window_in(sentence, today=today) is not None
        and _SELF_CONTAINED_FINANCIAL_QUESTION.search(folded) is not None
    )


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
    excluded_categories: list[str] = field(default_factory=list)
    merchants: list[str] = field(default_factory=list)
    merchant_terms: list[str] = field(default_factory=list)
    excluded_merchants: list[str] = field(default_factory=list)
    counterparties: list[str] = field(default_factory=list)
    excluded_counterparties: list[str] = field(default_factory=list)
    counterparty_types: list[str] = field(default_factory=list)
    group_by: str = "NONE"
    metric: str = "SUM"
    group_limit: int | None = None
    include_internal: bool = False
    internal_only: bool = False
    across_accounts: bool = False
    only_account_id: str | None = None
    only_account_ids: list[str] = field(default_factory=list)
    transaction_ids: list[str] = field(default_factory=list)
    transaction_types: list[str] = field(default_factory=list)


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
    transaction_type: str | None = None
    counterparty_name: str | None = None
    counterparty_type: str = "UNKNOWN"
    receipt_category: str | None = None


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
    # Which of the holder's accounts a fully-read transfer is going to, once one is settled.
    #
    # This handler reads and never writes: resolving the account is reading, and staging money is a
    # `payments:prepare` scope the questions route does not have. So the read comes out here and the
    # route above decides whether it may be turned into a preview. Before this, the account was
    # resolved, the amount was resolved, and the answer was a sentence telling the holder to go and
    # do it again on a screen — with everything needed to offer it already in hand.
    transfer_to: str | None = None
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

        The conversation's own record of the turn is appended here for exactly that reason. Forty
        branches build a result and every one of them would have had to remember; the turn is the
        same fact in all of them, and it is already on the result by the time it passes through.

        Nothing the holder wrote is in the *observability* record. See `observability` for the list
        and why — the conversational log below is a different thing, held by the caller and never
        written anywhere this service owns.
        """
        started = time.perf_counter()
        outcome = "INCOMPLETE"
        try:
            async for step in self._steps(query):
                if isinstance(step, AskFinancialQuestionResult):
                    outcome = step.outcome
                    step = replace(
                        step,
                        conversation=step.conversation.with_turn(
                            Turn(
                                sentence=query.sentence,
                                outcome=step.outcome,
                                plan=step.plan,
                                question=step.question,
                            )
                        ),
                    )
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
            yield Stage("planned", answer_writer.describe(target))
            yield Stage("querying")
            restored = (
                await self._ranked(query, target)
                if isinstance(target, TopMovements)
                else await self._answer(query, target)
            )
            yield replace(restored, text=f"He vuelto a la consulta anterior. {restored.text}")
            return

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

        # A complete two-read calculation is a new question even when a previous plan exists.
        # Generic recovery runs very early and used to read "balance entre ingresos y gastos" as
        # several corrections to the previous expense plan, answering "¿con cuál empezamos?".
        # Recognise the closed compound shape first; its branch still executes below, after the
        # conversational asides and transfer workflow have had their normal precedence.
        relationship = _ratio_request(query)
        relaxed = (
            None
            if relationship is not None
            else query_recovery.relaxation(query.sentence, previous)
        )
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

        early_refusal = capability_guard.unsupported_reason(query.sentence, self._policy)
        if early_refusal == "CATEGORY_EXCLUSION":
            yield self._declined(early_refusal, was)
            return

        ratio = relationship
        if isinstance(ratio, RatioDraft):
            scoped = await self._scoped_ratio_draft(ratio, query)
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text="¿De qué periodo quieres calcularlo?",
                question="¿De qué periodo quieres calcularlo?",
                conversation=was.waiting_for_ratio(scoped),
                suggestions=["Este mes", "El mes pasado", "Este año"],
            )
            return
        if isinstance(ratio, RatioPlan):
            yield Stage("querying")
            yield await self._answer_ratio(query, ratio)
            return
        if was.pending_ratio is not None and intent not in _ASIDES:
            # The pending pair asked only for a period. A different financial question abandons
            # that half-built calculation exactly as an ordinary clarification does.
            was = was.without_pending_ratio()
            query = replace(query, conversation=was)

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
            yield Stage("planned", answer_writer.describe(incoming_payment))
            yield Stage("querying")
            yield await self._answer(query, incoming_payment)
            return

        gambling = _gambling_plan(query)
        if isinstance(gambling, FinancialPlanDraft):
            question = (
                "¿De qué periodo quieres que lo compruebe? Buscaré en ocio y en comercios "
                "cuyo nombre contenga apuesta o bet."
            )
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question="¿De qué periodo quieres que lo compruebe?",
                conversation=was.with_active(
                    FinancialClarification(draft=gambling, awaiting=Slot.WINDOW)
                ),
                suggestions=["Este mes", "Los últimos 3 meses", "Este año"],
            )
            return
        if isinstance(gambling, FinancialPlan):
            yield Stage("planned", answer_writer.describe(gambling))
            yield Stage("querying")
            yield await self._answer(query, gambling)
            return

        # Before the breakdown and before the model, because both answer it with the wrong number:
        # the breakdown leads with the money and the planner reads "¿cuántas suscripciones?" as a
        # count of movements. Named categories only, so "¿qué gastos fijos tengo?" still reaches the
        # recurrence discovery further down.
        how_many = _how_many_payees_plan(query)
        if how_many is not None:
            yield Stage("planned", answer_writer.describe(how_many))
            yield Stage("querying")
            yield await self._how_many_payees(query, how_many)
            return

        internal = _internal_movement_request(query)
        if isinstance(internal, FinancialPlanDraft):
            try:
                internal_scoped = await self._scoped_internal_draft(internal, query)
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
        if isinstance(internal, FinancialPlan):
            yield Stage("querying")
            yield await self._answer(query, internal)
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

        salary_history = _salary_history_plans(query)
        if salary_history is not None:
            yield Stage("querying")
            yield await self._compare_salary_history(query, *salary_history)
            return

        period_comparison = _period_comparison_plans(query)
        if period_comparison is None:
            period_comparison = _comparison_with_previous_period(query)
        if period_comparison is None:
            period_comparison = _direct_period_comparison(query)
        if period_comparison is not None:
            # Two complete, already validated questions are safer context than asking a model to
            # reconstruct both from "compáralos". Run them again so the comparison uses current
            # bank data, while the conversation keeps the pair available for another pronoun.
            yield Stage("querying")
            yield await self._compare_periods(query, period_comparison)
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
            yield Stage("planned", answer_writer.describe(account_follow_up))
            yield Stage("querying")
            yield await self._answer(query, account_follow_up)
            return

        narrowing = _a_bare_shop_name(query, was) if intent is Intent.FINANCIAL_QUERY else None
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

        referenced_result = await self._referenced_result_plan(query)
        if referenced_result is not None:
            yield Stage("planned", answer_writer.describe(referenced_result))
            yield Stage("querying")
            yield await self._answer(query, referenced_result)
            return

        contextual = _contextual_follow_up_plan(query)
        if contextual is not None:
            yield Stage("planned", answer_writer.describe(contextual))
            yield Stage("querying")
            yield await self._answer(query, contextual)
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

        movement_ranking = _multiple_movement_ranking(query)
        if movement_ranking is not None:
            multi_ranking, count = movement_ranking
            yield Stage("planned", f"{answer_writer.describe(multi_ranking)}, top {count}")
            yield Stage("querying")
            yield await self._ranked_many(query, multi_ranking, count)
            return

        group_ranking = _group_ranking_plan(query)
        if group_ranking is not None:
            yield Stage("planned", answer_writer.describe(group_ranking))
            yield Stage("querying")
            yield await self._answer(query, group_ranking)
            return

        natural_period = _natural_period_aggregate_plan(query)
        if natural_period is not None:
            yield Stage("planned", answer_writer.describe(natural_period))
            yield Stage("querying")
            yield await self._answer(query, natural_period)
            return

        deterministic = _deterministic_breakdown_plan(query)
        if isinstance(deterministic, FinancialPlanDraft):
            comparing = bool(
                explicit_facts.read(query.sentence, today=query.today).compared_categories
            )
            question = (
                "¿De qué periodo quieres que compare ambos gastos?"
                if comparing
                else (
                    "¿De qué periodo quieres que busque la nómina y la cuenta en la que entró?"
                    if deterministic.group_by is GroupBy.ACCOUNT
                    and deterministic.categories == ("SALARY",)
                    else "¿De qué periodo quieres que haga el desglose?"
                )
            )
            conversation = was.with_active(
                FinancialClarification(draft=deterministic, awaiting=Slot.WINDOW)
            )
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
                text=question,
                question=question,
                conversation=conversation,
                suggestions=["Este mes", "Este año", "Los últimos 4 meses"],
            )
            return
        if deterministic is not None:
            yield Stage("planned", answer_writer.describe(deterministic))
            yield Stage("querying")
            yield await self._answer(query, deterministic)
            return

        ranking = _contextual_ranking_plan(query)
        if ranking is not None:
            yield Stage("planned", answer_writer.describe(ranking))
            yield Stage("querying")
            yield await self._ranked(query, ranking)
            return

        cumulative = _cumulative_plan(query)
        if cumulative is not None:
            yield Stage("planned", answer_writer.describe(cumulative))
            yield Stage("querying")
            yield await self._answer(query, cumulative)
            return

        recurrence = _recurrence_plan(query)
        if recurrence is not None:
            yield Stage("planned", answer_writer.describe(recurrence))
            yield Stage("querying")
            yield await self._answer(query, recurrence)
            return

        frequency = _frequency_plan(query)
        if frequency is not None:
            yield Stage("planned", answer_writer.describe(frequency))
            yield Stage("querying")
            yield await self._answer(query, frequency)
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
                suggestions=[
                    "Este mes",
                    "El mes pasado",
                    "Los últimos 12 meses",
                    f"En {query.today.year}",
                ],
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

        month_fallback = self._pending_month_fallback(query)
        if month_fallback is not None:
            first, second = month_fallback
            yield Stage("planned", answer_writer.describe(first))
            yield Stage("querying")
            yield await self._answer_with_month_fallback(query, first, second)
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

        if not force_follow_up and _starts_new_financial_scope(query.sentence, today=query.today):
            planning_previous = None

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
                    # What was available to inherit, even when a self-contained question correctly
                    # starts a fresh scope and therefore sends no previous plan to the planner.
                    "inherited": _window_of(previous),
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
                direction=Direction.INCOME,
                categories=categories,
                counterparties=(person,),
                counterparty_types=(CounterpartyType.PERSON,),
                transaction_types=transaction_types,
            )
        return FinancialPlan(
            direction=Direction.INCOME,
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
            label = _COUNTERPARTY_LABELS[counterparty_type]
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
                f"como {_COUNTERPARTY_LABELS[counterparty_type]}."
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

    def _pending_patch_base(self, query: AskFinancialQuestionQuery) -> FinancialPlan | None:
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
            movements=[_spoken_movement(found)] if found else [],
            suggestions=query_recovery.empty_suggestions(ranking) if found is None else [],
            # Remembered even when nothing was found: "¿y el segundo?" after "no hay quinto" is a
            # reasonable thing to say, and the question it refers to is this one.
            conversation=query.conversation.answered(
                ranking,
                referenced_transaction_ids=_evidence_ids([found]) if found is not None else (),
            ),
        )

    async def _ranked_many(
        self, query: AskFinancialQuestionQuery, ranking: TopMovements, count: int
    ) -> AskFinancialQuestionResult:
        """Read a bounded top-N while keeping each row grounded in banking's ranking."""
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
                        conversation=query.conversation,
                    )
                except BankingUnavailableError:
                    if attempt + 1 < self._read_attempts:
                        continue
                    return AskFinancialQuestionResult(
                        outcome="BANKING_UNAVAILABLE",
                        text="El banco no ha respondido. Vuelve a intentarlo.",
                        plan=f"{answer_writer.describe(ranking)}, top {count}",
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
            text=answer_writer.ranked_many(tuple(found), ranking, requested=count),
            plan=f"{answer_writer.describe(ranking)}, top {count}",
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
            conversation=query.conversation.answered(
                remembered,
                referenced_transaction_ids=_evidence_ids(found),
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
            conversation=query.conversation.answered(
                remembered, referenced_transaction_ids=selected_ids
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
        previous = query.conversation.last_plan
        folded = spanish.folded(query.sentence)
        facts = explicit_facts.read(query.sentence, today=query.today)

        if _MOVEMENT_SET_REFERENCE.search(folded):
            selected = query.conversation.referenced_transaction_ids
            if not selected and isinstance(previous, FinancialPlan):
                selected = previous.transaction_ids
            if not selected:
                return None
            if isinstance(previous, TopMovements):
                return FinancialPlan(
                    direction=facts.direction or previous.direction,
                    period=previous.period,
                    date_range=previous.date_range,
                    categories=previous.categories,
                    excluded_categories=previous.excluded_categories,
                    merchants=previous.merchants,
                    merchant_terms=previous.merchant_terms,
                    excluded_merchants=previous.excluded_merchants,
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or Metric.SUM,
                    transaction_ids=selected,
                )
            if not isinstance(previous, FinancialPlan):
                return None
            return explicit_facts.enforce(
                replace(
                    previous,
                    transaction_ids=selected,
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )

        if not isinstance(previous, FinancialPlan):
            return None

        dimension = query.conversation.referenced_group_by
        key = query.conversation.referenced_group_key
        if dimension is None or key is None:
            return None
        if dimension is GroupBy.CATEGORY and _CATEGORY_REFERENCE.search(folded):
            if category_words.words_for(key) is None:
                return None
            return explicit_facts.enforce(
                replace(
                    previous,
                    categories=(key,),
                    excluded_categories=tuple(
                        category for category in previous.excluded_categories if category != key
                    ),
                    merchants=(),
                    excluded_merchants=(),
                    transaction_ids=(),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )
        if dimension is GroupBy.MERCHANT and _MERCHANT_REFERENCE.search(folded):
            return explicit_facts.enforce(
                replace(
                    previous,
                    merchants=(key,),
                    excluded_merchants=tuple(
                        merchant
                        for merchant in previous.excluded_merchants
                        if merchant.casefold() != key.casefold()
                    ),
                    transaction_ids=(),
                    group_by=facts.group_by or GroupBy.NONE,
                    metric=facts.metric or previous.metric,
                ),
                facts,
            )
        if dimension is GroupBy.COUNTERPARTY and (
            _COUNTERPARTY_REFERENCE.search(folded) or _MERCHANT_REFERENCE.search(folded)
        ):
            return explicit_facts.enforce(
                replace(
                    previous,
                    counterparties=(key,),
                    excluded_counterparties=tuple(
                        counterparty
                        for counterparty in previous.excluded_counterparties
                        if counterparty.casefold() != key.casefold()
                    ),
                    transaction_ids=(),
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
            replace(
                previous,
                across_accounts=False,
                only_account_id=selected_account,
                only_account_ids=(),
                transaction_ids=(),
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
            plan_context=_spoken(scoped),
            conversation=query.conversation.answered(scoped),
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
        )
        if not difference and len(groups) >= 2:
            result = replace(
                result,
                conversation=result.conversation.with_references(
                    group_by=plan.group_by,
                    group_key=groups[1].key,
                ),
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
        referenced_category = (
            remembered_latest.categories[0]
            if len(remembered_latest.categories) == 1
            and category_words.words_for(remembered_latest.categories[0]) is not None
            else None
        )
        remembered_comparison = ComparisonPlan.between(remembered_earlier, remembered_latest)
        assert remembered_comparison is not None  # execution only adds scope to a validated pair
        comparison_memory = query.conversation.answered_comparison(
            remembered_comparison,
            referenced_category=referenced_category,
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
            # Keep the compared pair as validated query memory. This makes a direct comparison
            # behave like two consecutive questions: a later "compÃ¡ralos otra vez" or a repair can
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
            total_minor=(None if latest.metric is Metric.COUNT else latest_value - earlier_value),
            movement_count=latest_answer.movement_count + earlier_answer.movement_count,
            groups=(
                [
                    *[
                        SpokenGroup(
                            f"{group.key} · {latest_window}",
                            group.total_minor,
                            group.movement_count,
                        )
                        for group in latest_answer.groups
                        if group.key != "total"
                    ],
                    *[
                        SpokenGroup(
                            f"{group.key} · {earlier_window}",
                            group.total_minor,
                            group.movement_count,
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
                    ),
                    SpokenGroup(
                        earlier_window,
                        (
                            earlier_value
                            if earlier.metric is Metric.PERIOD_AVERAGE
                            else earlier_answer.total_minor
                        ),
                        earlier_answer.movement_count,
                    ),
                ]
            ),
            movements=[_spoken_movement(movement) for movement in movements],
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
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.PLANNED.value,
            text=ratio_writer.write(part_answer, whole_answer, executed),
            plan=(
                f"{answer_writer.describe(scoped_part)} respecto a "
                f"{answer_writer.describe(scoped_whole)}"
            ),
            conversation=query.conversation.answered_ratio(executed),
            currency=part_answer.currency,
            total_minor=net_cash_flow,
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
            # This is an answer about the payroll already in view. Keep that plan in view so
            # "compruebalo con el mes anterior" is anchored to its month, not to today's month.
            conversation=query.conversation,
            currency=current_answer.currency,
            total_minor=current_answer.total_minor - highest.total_minor,
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
        if plan.group_by is GroupBy.ACCOUNT and (
            asks_account_breakdown or (not named and not excluded and not explicit_custom)
        ):
            return replace(
                plan,
                across_accounts=True,
                only_account_id=None,
                only_account_ids=(),
            )
        if not named and not excluded and not explicit_custom:
            if plan.only_account_id is not None or plan.only_account_ids:
                return plan
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
            return replace(plan, across_accounts=True)
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
                replace(
                    plan,
                    merchants=(),
                    group_by=GroupBy.MERCHANT,
                    # PERIOD_AVERAGE is valid only over calendar buckets. This auxiliary query is
                    # discovering merchant names, so retaining the temporal divisor here turns a
                    # harmless misspelling into a domain-level 422.
                    metric=Metric.SUM,
                ),
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
        try:
            scoped = await self._scoped(plan, query)
        except AccountScopeNotFoundError:
            return AskFinancialQuestionResult(
                outcome=Spoken.GUIDANCE.value,
                text="No tienes una cuenta de ese tipo en la que buscar traspasos.",
                conversation=query.conversation,
            )
        # The ordinary all-accounts default is execution policy, not conversational data. An
        # explicit account choice is different: losing it makes the very next ellipsis widen back
        # to every account. Keep only scopes the user actually selected (plus ACCOUNT breakdown,
        # whose meaning is intrinsically across accounts).
        remembered = (
            scoped
            if scoped.only_account_id is not None
            or scoped.only_account_ids
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
                    answered = _answered(
                        remembered,
                        answer,
                        query.conversation,
                        query.sentence,
                        reporting_timezone=query.reporting_timezone,
                    )
                    return replace(
                        answered,
                        text=(f"{answered.text}\n¿Querías solo {answer_writer.listed(nearly)}?"),
                        suggestions=list(nearly),
                    )
            if shop is not None:
                tighter = replace(
                    plan,
                    categories=(),
                    merchants=(shop,),
                    excluded_merchants=tuple(
                        merchant
                        for merchant in plan.excluded_merchants
                        if merchant.casefold() != shop.casefold()
                    ),
                )
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

        return _answered(
            remembered,
            answer,
            query.conversation,
            query.sentence,
            reporting_timezone=query.reporting_timezone,
        )


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

_AVERAGE_PERIOD_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:el\s+|la\s+)?(?:"
    r"(?:por|al|cada)\s+dia|(?:por|a\s+la|cada)\s+semana|(?:por|al|cada)\s+mes"
    r"|diari[oa]|semanal|semanl|semnal|mensual(?:mente)?|mensula|mesnual"
    r")$"
)
_NATURAL_PERIOD_AVERAGE_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:eso\s+)?cuanto\s+(?:sale|seria|es)\s+"
    r"(?:por|al|a\s+la|cada)\s+(?P<unit>dia|semana|mes)$"
)
_AVERAGE_MOVEMENT_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:por\s+(?:movimiento|operacion|compra|pago|cargo|ingreso)s?"
    r"|(?:el\s+)?ticket\s+medio)$"
)
_SALARY_ACCOUNT_FOLLOW_UP: Final = re.compile(
    r"^(?:en\s+)?la\s+que\s+(?:ingres\w*|cobr\w*)\s+(?:la\s+)?nomina"
    r"(?:\s+(?:vaya|vamos))?(?:\s+[a-z])?$"
)
_ACCOUNT_SELECTION_FOLLOW_UP: Final = re.compile(
    r"^(?:ahora\s+)?(?:solo|solamente)\b|\b(?:no|menos|excepto|salvo)\s+"
    r"(?:el|la|los|las|mi|mis)?\s*(?:fondo|hucha|cuenta)\b"
    r"|^(?:no[\s,]+)?(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\b"
)
_CATEGORY_FILTER_FOLLOW_UP: Final = re.compile(
    r"^(?:(?:y|ahora)\s+)?(?:solo\s+)?en\s+"
    r"|^(?:ahora\s+)?(?:solo|solamente)\b"
    r"|^perdona\b.{0,35}\b(?:queria|refiero|referia)\b"
    r"|^(?:no[\s,]+)?(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\b"
    r"|^volvamos?\s+a\b"
    r"|^(?:quedate|dejame|deja)\s+solo\s+con\b"
)
_CATEGORY_REFERENCE: Final = re.compile(r"\b(?:esa|esta|aquella|la\s+misma)\s+categoria\b")
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
)
# The nouns that name a charge, from the vocabulary that owns them. They were written out here as
# well and the two copies stayed level only by accident: the rails were missing from both, and each
# was found on its own. "Bizum" and "transferencia" are also categories, which is what makes the
# named-category branch necessary — without it the sentence declines and loses the window rather
# than counting the kind it names.
_COUNT_FOLLOW_UP: Final = re.compile(
    rf"^(?:y\s+)?cuant[oa]s?\s+(?:{explicit_facts.CHARGE_NOUNS})\b"
)
_SUM_EXPENSE_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?cuanto\s+(?:he\s+)?(?:gaste|gastado|pague|pagado|compre|comprado)\b"
)
_SUM_INCOME_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?cuanto\s+(?:he\s+)?(?:ingrese|ingresado|cobre|cobrado|recibi|recibido)\b"
)
_DIRECTION_ONLY_FOLLOW_UP: Final = re.compile(
    r"^(?:y\s+)?(?:ahora\s+)?(?:no[\s,]+)?"
    r"(?:(?:me\s+referia\s+a|queria\s+decir|quise\s+decir|mejor)\s+)?"
    r"(?:solo\s+)?(?:los?\s+)?(?P<direction>ingresos?|gastos?)$"
)
_TOP_MERCHANT_FOLLOW_UP: Final = re.compile(
    r"\ben\s+que\s+(?:comercio|tienda|sitio|establecimiento)\b.{0,45}"
    r"\b(?:gast|pag|compr)\w*\b.{0,18}\bmas\b"
)
_RANKING_REQUEST: Final = re.compile(
    r"\b(?:cual|que)\b.{0,30}\b"
    r"(?:gasto|ingreso|movimiento|compra|pago|cargo|cobro|nomina|sueldo|salario)\b.{0,30}"
    r"\b(?:mas\s+(?:alto|alta|caro|cara|grande)|mayor)\b"
)
_GROUP_RANKING_DIMENSION: Final = re.compile(
    r"\b(?P<dimension>categorias?|comercios?|tiendas?|establecimientos?|cuentas?)\b"
)


def _named_group_dimension(folded: str) -> GroupBy | None:
    """Which breakdown the sentence names outright, if it names one.

    Shared by the two recognisers that need it, because they disagreed. `_group_ranking_plan` read
    "top 2 categorías por ticket medio" as a category ranking and `_average_plan` read the same
    sentence as a merchant one — it inherited the grouping of the two turns before, which were about
    comercios, since `explicit_facts` does not treat "top N categorías" as a breakdown request. Only
    the order of the cascade decided which of the two ran, and a corpus episode whose whole point is
    that "merchant/category must alter only the named dimension" was passing on that.
    """
    match = _GROUP_RANKING_DIMENSION.search(folded)
    if match is None:
        return None
    word = match.group("dimension")
    if word.startswith("categor"):
        return GroupBy.CATEGORY
    if word.startswith(("comerc", "tienda", "establec")):
        return GroupBy.MERCHANT
    if word.startswith("cuenta"):
        return GroupBy.ACCOUNT
    return None


_GROUP_RANKING_SIGNAL: Final = re.compile(
    r"\b(?:top|ranking|mas\s+(?:gasto|importe|operaciones|movimientos|compras|pagos)|"
    r"mas\s+(?:gast|pag|compr)\w*|"
    r"mayor(?:es)?\s+(?:gasto|importe|ticket|media|promedio)|con\s+mas)\b"
)
_GROUP_RANKING_CRITERION: Final = re.compile(
    r"\b(?:ticket\s+medio|importe\s+medio|media|promedio|operaciones|movimientos|compras|pagos|"
    r"importe|gasto)\b"
)
_GROUP_RANKING_COUNT: Final = re.compile(
    r"\b(?:top\s*)?(?P<count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:categorias?|comercios?|tiendas?|establecimientos?|cuentas?)\b"
)
_GROUP_RANKING_NUMBERS: Final = {
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}
_MULTIPLE_MOVEMENT_RANKING: Final = re.compile(
    r"\b(?P<count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:(?:mayores?|principales?)\s+)?"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b.{0,25}"
    r"\b(?:mas\s+(?:altos?|grandes?|caros?)|mayores?)\b"
    r"|\b(?P<leading_count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:mayores?|principales?)\s+"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b"
    r"|\btop\s*(?P<top_count>\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:gastos?|ingresos?|movimientos?|compras?|pagos?|cargos?)\b"
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
_PERIOD_COMPARISON_REFERENCE: Final = re.compile(
    r"^(?:vale\s+|bueno\s+|entonces\s+|y\s+)?(?:"
    r"compara(?:los|las|melos|melas)?"
    r"(?:\s+(?:esos?|esas?|los|las)?\s*dos\s+(?:periodos?|meses?|semanas?))?"
    r"(?:\s+otra\s+vez)?"
    r"|hazme\s+una\s+comparacion(?:\s+de\s+(?:los|esos)\s+dos)?"
    r"|(?:en\s+)?(?:cual|que)\s+(?:de\s+los\s+dos\s+)?"
    r"(?:gaste|he\s+gastado|ingrese|he\s+ingresado|cobre|he\s+cobrado|recibi|he\s+recibido|hubo)\s+mas"
    r"|(?:cual|que)\s+(?:he\s+)?(?:cobrado|recibido|ingresado|gastado)\s+mas"
    r"|(?:del|entre\s+el)\s+anterior\s+(?:a|y)\s+este"
    r")(?:\s+por\s+favor)?$"
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


def _period_comparison_plans(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Resolve a comparison pronoun only when the stored plans differ solely by window."""
    if not _PERIOD_COMPARISON_REFERENCE.fullmatch(_clean_turn(query.sentence)):
        return None
    stored = query.conversation.last_comparison
    if stored is not None:
        return stored
    latest = query.conversation.last_plan
    earlier = query.conversation.prior_plan
    if not isinstance(latest, FinancialPlan) or not isinstance(earlier, FinancialPlan):
        return None
    if (latest.period, latest.date_range) == (earlier.period, earlier.date_range):
        return None

    scope = (
        "direction",
        "categories",
        "excluded_categories",
        "merchants",
        "merchant_terms",
        "excluded_merchants",
        "counterparties",
        "excluded_counterparties",
        "counterparty_types",
        "include_internal",
        "internal_only",
        "across_accounts",
        "only_account_id",
        "only_account_ids",
        "transaction_ids",
        "transaction_types",
        "group_limit",
    )
    if any(getattr(latest, field_name) != getattr(earlier, field_name) for field_name in scope):
        return None
    if latest.transaction_ids:
        return None

    # A metric is part of the question, not presentation. Turning both sides into SUM made
    # "¿en cuál hice más compras?" compare euros and made "¿qué ticket medio fue mayor?" compare
    # totals. Different metrics are two incompatible questions, so they stay on the ordinary
    # interpretation path rather than undergoing a conversion nobody requested.
    if latest.metric is not earlier.metric:
        return None
    if latest.metric is Metric.PERIOD_AVERAGE and latest.group_by is not earlier.group_by:
        return None
    return ComparisonPlan.between(earlier, latest)


def _ratio_request(query: AskFinancialQuestionQuery) -> RatioPlan | RatioDraft | None:
    """A proportion stated completely, or the exact pair waiting for one shared window."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    folded = spanish.folded(query.sentence)
    cleaned = _clean_turn(query.sentence)

    pending = query.conversation.pending_ratio
    if (
        pending is not None
        and facts.window is not None
        and len(cleaned.split()) <= 10
        and _RATIO_WINDOW_REPLY.fullmatch(cleaned) is not None
    ):
        return pending.with_window(facts.window)

    previous_ratio = query.conversation.last_ratio
    if (
        previous_ratio is not None
        and previous_ratio.kind is not RatioKind.PERCENT_CHANGE
        and facts.window is not None
        and len(cleaned.split()) <= 6
        and re.search(r"\b(?:cuanto|cual|que|gast|ingres|cobr|movimientos?)\b", cleaned) is None
    ):
        return previous_ratio.with_window(facts.window)

    if _PERCENT_FOLLOW_UP.fullmatch(cleaned):
        comparison = _stored_percentage_pair(query)
        if comparison is not None:
            return RatioPlan(
                part=comparison.latest,
                whole=comparison.earlier,
                kind=RatioKind.PERCENT_CHANGE,
            )
        return None

    asks_ratio = _RATIO_WORDS.search(folded) is not None
    asks_savings = financial_relationships.asks_savings_rate(query.sentence)
    asks_net_cash_flow = financial_relationships.asks_net_cash_flow(query.sentence)
    if not asks_ratio and not asks_savings and not asks_net_cash_flow:
        return None

    if asks_ratio and _DIRECT_PERIOD_COMPARISON.search(folded):
        comparison = _direct_period_comparison(query)
        if comparison is not None:
            return RatioPlan(
                part=comparison.latest,
                whole=comparison.earlier,
                kind=RatioKind.PERCENT_CHANGE,
            )

    window = facts.window
    if asks_net_cash_flow:
        draft = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.NET_CASH_FLOW,
        )
        return draft.with_window(window) if window is not None else draft
    if asks_savings:
        draft = RatioDraft(
            part=FinancialPlanDraft(direction=Direction.EXPENSE),
            whole=FinancialPlanDraft(direction=Direction.INCOME),
            kind=RatioKind.SAVINGS_RATE,
        )
        return draft.with_window(window) if window is not None else draft

    named = category_words.precisely_named_in(query.sentence)
    part_categories = tuple(category for category in named if category != "SALARY")
    names_salary = bool(re.search(r"\b(?:nomina|sueldo|salario)\b", folded))
    names_income = names_salary or bool(re.search(r"\bingresos?\b", folded))
    names_expenses = bool(re.search(r"\b(?:mis|los|tus)\s+gastos\b|\bgasto\s+total\b", folded))
    if not names_income and not names_expenses:
        return None
    if not part_categories:
        # Without the numerator this would silently divide all expenses by themselves and answer
        # 100 %. An open merchant name also belongs to the semantic path until it can be verified
        # against actual ledger merchants.
        return None
    draft = RatioDraft(
        part=FinancialPlanDraft(
            direction=Direction.EXPENSE,
            categories=part_categories,
        ),
        whole=FinancialPlanDraft(
            direction=Direction.INCOME if names_income else Direction.EXPENSE,
            categories=("SALARY",) if names_salary else (),
        ),
        kind=RatioKind.SHARE,
    )
    return draft.with_window(window) if window is not None else draft


def _frequency_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """A complete cadence question over a grounded category, with a useful default horizon."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.metric is not Metric.FREQUENCY:
        return None
    categories = category_words.precisely_named_in(query.sentence)
    if not categories and re.search(
        r"\b(?:cuando|que\s+dia|cada\s+cuanto)\b.{0,45}"
        r"\b(?:me\s+pagan|pagarme|cobro|cobrar)\b",
        spanish.folded(query.sentence),
    ):
        categories = ("SALARY",)
    if not categories:
        # Free merchant names remain on the grounded planner path. Treating every unknown noun as
        # a shop here would turn "cuándo gasto" into one invented merchant filter.
        return None
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=facts.direction or _direction_of(categories),
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=categories,
        metric=Metric.FREQUENCY,
    )


def _cumulative_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Build a running calendar total without asking the model to reinterpret arithmetic."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    previous = query.conversation.last_plan
    previous_financial = previous if isinstance(previous, FinancialPlan) else None
    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    unit_follow_up = (
        previous_financial is not None
        and previous_financial.metric is Metric.CUMULATIVE
        and facts.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
        and folded.startswith(("y ", "ahora ", "por "))
    )
    if facts.metric is not Metric.CUMULATIVE and not unit_follow_up:
        return None
    contextual = not _starts_new_financial_scope(query.sentence, today=query.today)
    if not contextual and not unit_follow_up:
        previous_financial = None
    named = category_words.precisely_named_in(query.sentence)
    if re.search(r"\bingres|\bcobr|\bnomina", folded):
        direction = Direction.INCOME
    elif re.search(r"\bgast|\bpag|\bcompr|\bcargo", folded):
        direction = Direction.EXPENSE
    elif named:
        direction = _direction_of(named)
    elif previous_financial is not None:
        direction = previous_financial.direction
    else:
        direction = Direction.ALL
    window = facts.window or (
        previous_financial.period or previous_financial.date_range
        if previous_financial is not None
        else RelativePeriod(Period.THIS_YEAR)
    )
    return FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (previous_financial.categories if previous_financial else ()),
        excluded_categories=(previous_financial.excluded_categories if previous_financial else ()),
        merchants=previous_financial.merchants if previous_financial else (),
        merchant_terms=previous_financial.merchant_terms if previous_financial else (),
        excluded_merchants=previous_financial.excluded_merchants if previous_financial else (),
        group_by=facts.group_by or GroupBy.MONTH,
        metric=Metric.CUMULATIVE,
        include_internal=previous_financial.include_internal if previous_financial else False,
        internal_only=previous_financial.internal_only if previous_financial else False,
        across_accounts=previous_financial.across_accounts if previous_financial else False,
        only_account_id=previous_financial.only_account_id if previous_financial else None,
        only_account_ids=previous_financial.only_account_ids if previous_financial else (),
        transaction_types=previous_financial.transaction_types if previous_financial else (),
    )


def _recurrence_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Discover periodic expenses from a conservative, complete category scope."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.metric is not Metric.RECURRENCE:
        return None
    named = category_words.precisely_named_in(query.sentence)
    direct_debits_only = explicit_facts.asks_direct_debits(query.sentence)
    # A bank receipt is already a conservative structured scope. Applying the historical
    # subscription/rent catalogue as well would silently omit utilities, insurance, tax or loan
    # receipts. A category explicitly named by the user still narrows the direct debits.
    categories = named or (() if direct_debits_only else RECURRING_EXPENSE_CATEGORIES)
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=categories,
        metric=Metric.RECURRENCE,
        transaction_types=(TransactionType.DIRECT_DEBIT,) if direct_debits_only else (),
    )


def _how_many_payees_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """How many distinct payees a kind of spending has, and which they are.

    Reported. "¿Qué suscripciones tengo activas?" was answered "36 movimientos en suscripciones en
    los últimos 12 meses", and the holder said what anybody would: "yo pensaba que tenía solo
    netflix altafit spoti y steam". Both figures are true and only one is the answer — thirty-six is
    how many times they were charged, four is how many things they are paying for.

    No new metric and nothing new asked of banking. A breakdown by merchant already returns one
    group per payee, so the count is the number of groups and the names are their keys. What was
    missing was a question that asks for it and a sentence that leads with it.

    Exactly one named category, because the question is "how many of *these*". A sentence naming
    none is either the recurring-expenses discovery below — "¿qué gastos fijos tengo?" — or not
    this at all, and grouping every merchant the holder ever paid is a list dump, not an answer.
    """
    if not explicit_facts.asks_how_many_payees(query.sentence):
        return None
    named = category_words.precisely_named_in(query.sentence)
    if len(named) != 1:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    # A default rather than a question, exactly like the recurring-expenses discovery below.
    # "Activas ahora mismo" is not a window this service can turn into dates, and the reported
    # conversation shows what asking costs: "¿de qué periodo?" was answered "en el último año" and
    # the figure that came back was one the holder did not recognise. The answer names the window,
    # so narrowing it is one short turn.
    window = facts.window or RelativePeriod(Period.LAST_N_MONTHS, n=12)
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named,
        # By merchant rather than by counterparty: a subscription is a shop that charges you, and
        # this is the same dimension the shop rankings already group by.
        group_by=GroupBy.MERCHANT,
        metric=Metric.SUM,
    )


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


def _internal_movement_request(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """A read of transfers that stayed inside the holder's own account set."""
    folded = spanish.folded(query.sentence)
    facts = explicit_facts.read(query.sentence, today=query.today)
    between_accounts = _INTERNAL_BETWEEN_ACCOUNTS.search(folded) is not None
    investment = _INVESTMENT_HISTORY.search(folded) is not None
    named_accounts = account_words.named_in(query.sentence)
    asks = (
        re.search(
            r"\b(?:cuanto|caunto|cuantos|que|cual|cuales|dime|dame|lista|muestrame|ensename)\b",
            folded,
        )
        is not None
    )
    names_transfer_history = (
        re.search(
            # Singular "traspaso" is also the present-tense verb in "cuánto traspaso al fondo".
            # Plural nouns and completed forms are history; the ambiguous verb stays on the
            # established habitual-query path instead of inventing an internal-only request.
            r"\b(?:traspasos|transferencias?|movid|traspasad|metid|aportad)\w*\b",
            folded,
        )
        is not None
        # "Pasado" on its own is usually the calendar adjective in "el mes pasado". Only its
        # completed verb form means money moved to the named account.
        or re.search(r"\b(?:he|has|ha|hemos|han)\s+pasad\w*\b", folded) is not None
    )
    if not investment and not (
        (between_accounts and _INTERNAL_HISTORY_WORDS.search(folded))
        or (named_accounts and asks and names_transfer_history)
    ):
        return None

    destination = account_words.destination_in(query.sentence)
    direction = Direction.INCOME if investment or destination else Direction.EXPENSE
    metric = facts.metric or Metric.SUM
    group_by = (
        facts.group_by
        if metric is Metric.PERIOD_AVERAGE and facts.group_by is not None
        else GroupBy.NONE
    )
    if window := facts.window:
        return FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            group_by=group_by,
            metric=metric,
            include_internal=True,
            internal_only=True,
        )
    return FinancialPlanDraft(
        direction=direction,
        group_by=group_by,
        metric=metric,
        include_internal=True,
        internal_only=True,
    )


def _stored_percentage_pair(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """The two compatible windows behind a bare "en porcentaje"."""
    stored = query.conversation.last_comparison
    if stored is not None:
        return stored
    latest = query.conversation.last_plan
    earlier = query.conversation.prior_plan
    if not isinstance(latest, FinancialPlan) or not isinstance(earlier, FinancialPlan):
        return None
    if _same_window(latest, earlier) or latest.metric is not earlier.metric:
        return None
    scope = (
        "direction",
        "categories",
        "excluded_categories",
        "merchants",
        "merchant_terms",
        "excluded_merchants",
        "counterparties",
        "excluded_counterparties",
        "counterparty_types",
        "include_internal",
        "internal_only",
        "across_accounts",
        "only_account_id",
        "only_account_ids",
        "transaction_ids",
        "transaction_types",
        "group_limit",
    )
    if any(getattr(latest, field) != getattr(earlier, field) for field in scope):
        return None
    return ComparisonPlan.between(earlier, latest)


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


def _salary_history_plans(
    query: AskFinancialQuestionQuery,
) -> tuple[FinancialPlan, FinancialPlan] | None:
    """The payroll in view and the twelve complete calendar months immediately before it."""
    if _SALARY_HISTORY_COMPARISON.search(spanish.folded(query.sentence)) is None:
        return None
    latest = query.conversation.last_plan
    if (
        not isinstance(latest, FinancialPlan)
        or latest.categories != ("SALARY",)
        or latest.transaction_ids
    ):
        return None
    current_window = _calendar_month_window(latest, query.today)
    if current_window is None:
        return None
    history = replace(
        latest,
        period=None,
        date_range=AbsoluteRange(
            start=date(current_window.start.year - 1, current_window.start.month, 1),
            end=current_window.start - timedelta(days=1),
        ),
        group_by=GroupBy.MONTH,
        metric=Metric.SUM,
    )
    current = replace(
        latest,
        period=None,
        date_range=current_window,
        group_by=GroupBy.NONE,
        metric=Metric.SUM,
    )
    return current, history


def _same_window(left: FinancialPlan, right: FinancialPlan) -> bool:
    return (left.period, left.date_range) == (right.period, right.date_range)


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


def _comparison_with_previous_period(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Compare the plan in view with the immediately preceding calendar period.

    The pronoun supplies the query scope and the sentence supplies only the other window. This is
    deliberately narrower than inventing a generic date shift: months, weeks and years have
    calendar boundaries the product already exposes; rolling N-day windows do not have one obvious
    meaning for "the previous period".
    """
    found = _COMPARE_WITH_PREVIOUS_PERIOD.fullmatch(_clean_turn(query.sentence))
    latest = query.conversation.last_plan
    if found is None or not isinstance(latest, FinancialPlan) or latest.transaction_ids:
        return None
    earlier_window = _previous_calendar_window(latest, found.group("unit"), query.today)
    if earlier_window is None:
        return None
    earlier = replace(
        latest,
        period=earlier_window if isinstance(earlier_window, RelativePeriod) else None,
        date_range=earlier_window if isinstance(earlier_window, AbsoluteRange) else None,
    )
    return ComparisonPlan.between(earlier, latest)


def _direct_period_comparison(
    query: AskFinancialQuestionQuery,
) -> ComparisonPlan | None:
    """Build the common two-window comparison stated completely in one sentence.

    Only closed, paired phrases are accepted. A sentence naming arbitrary dates still belongs to
    the planner until the date-range grammar can represent both sides without guessing.
    """
    folded = spanish.folded(query.sentence)
    if _DIRECT_PERIOD_COMPARISON.search(folded) is None:
        return None
    windows: tuple[window_words.Window, window_words.Window] | None = None
    named_ranges = window_words.compared_date_ranges(query.sentence, today=query.today)
    named_months = window_words.compared_calendar_months(query.sentence, today=query.today)
    named_years = window_words.compared_calendar_years(query.sentence, today=query.today)
    natural_periods = window_words.compared_natural_periods(query.sentence, today=query.today)
    if named_ranges is not None:
        windows = (named_ranges[1], named_ranges[0])
    elif named_months is not None:
        # `_compare_periods` writes its `latest` argument first. Reverse mention order here so the
        # answer follows the user's order: "julio con junio" reads July before June.
        windows = (named_months[1], named_months[0])
    elif named_years is not None:
        windows = (named_years[1], named_years[0])
    elif natural_periods is not None:
        windows = (natural_periods[1], natural_periods[0])
    elif re.search(r"\beste\s+mes\b", folded) and re.search(
        r"\b(?:(?:el\s+)?mes\s+(?:pasado|anterior)|el\s+(?:pasado|anterior))\b",
        folded,
    ):
        windows = (
            RelativePeriod(Period.LAST_MONTH),
            RelativePeriod(Period.THIS_MONTH),
        )
    elif re.search(r"\besta\s+semana\b", folded) and re.search(
        r"\b(?:la\s+)?semana\s+(?:pasada|anterior)\b", folded
    ):
        windows = (
            RelativePeriod(Period.LAST_WEEK),
            RelativePeriod(Period.THIS_WEEK),
        )
    elif re.search(r"\beste\s+ano\b|\ben\s+lo\s+que\s+va\s+de\s+ano\b", folded) and re.search(
        r"\b(?:el\s+)?ano\s+(?:pasado|anterior)\b", folded
    ):
        windows = (
            window_words.whole_year(query.today.year - 1),
            RelativePeriod(Period.THIS_YEAR),
        )
    if windows is None:
        return None

    facts = explicit_facts.read(query.sentence, today=query.today)
    named_categories = category_words.precisely_named_in(query.sentence)
    if len(named_categories) > 1:
        # Two categories and two windows describe four possible comparisons. Do not choose one.
        return None
    states_subject = re.search(
        r"\bgast|\bingres|\bincome|\bcobr|\brecib|\bcompr|\bpagu|\bcargo"
        r"|\bnomina|\bsueldo|\bsalario|\bmovimientos?|\boperaciones?"
        r"|\bmedia|\bmedio|\bpromedio|\bticket",
        folded,
    )
    previous = (
        query.conversation.last_plan
        if states_subject is None and isinstance(query.conversation.last_plan, FinancialPlan)
        else None
    )
    categories = named_categories or (previous.categories if previous is not None else ())
    metric = facts.metric or (previous.metric if previous is not None else Metric.SUM)
    group_by = (
        GroupBy.ACCOUNT
        if facts.asks_account_breakdown
        else (
            facts.group_by
            if metric is Metric.PERIOD_AVERAGE and facts.group_by is not None
            else (
                previous.group_by
                if metric is Metric.PERIOD_AVERAGE and previous is not None
                else GroupBy.NONE
            )
        )
    )
    if metric is Metric.PERIOD_AVERAGE and group_by not in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        return None
    direction = facts.direction
    if direction is None:
        if re.search(r"\bgast|\bpagu|\bcompr|\bcargo", folded):
            direction = Direction.EXPENSE
        elif named_categories:
            direction = _direction_of(named_categories)
        elif previous is not None:
            direction = previous.direction
        else:
            direction = Direction.ALL
    base = explicit_facts.enforce_exclusions(
        FinancialPlan(
            direction=direction,
            period=windows[1] if isinstance(windows[1], RelativePeriod) else None,
            date_range=windows[1] if isinstance(windows[1], AbsoluteRange) else None,
            categories=categories,
            excluded_categories=(
                tuple(
                    category
                    for category in previous.excluded_categories
                    if category not in named_categories
                )
                if previous is not None
                else ()
            ),
            merchants=(
                () if named_categories else (previous.merchants if previous is not None else ())
            ),
            excluded_merchants=(
                ()
                if named_categories
                else (previous.excluded_merchants if previous is not None else ())
            ),
            group_by=group_by,
            metric=metric,
            include_internal=previous.include_internal if previous is not None else False,
            internal_only=previous.internal_only if previous is not None else False,
            across_accounts=previous.across_accounts if previous is not None else False,
            only_account_id=previous.only_account_id if previous is not None else None,
            only_account_ids=previous.only_account_ids if previous is not None else (),
        ),
        facts,
    )
    earlier = replace(
        base,
        period=windows[0] if isinstance(windows[0], RelativePeriod) else None,
        date_range=windows[0] if isinstance(windows[0], AbsoluteRange) else None,
    )
    return ComparisonPlan.between(earlier, base)


def _previous_calendar_window(
    plan: FinancialPlan, unit: str, today: date
) -> window_words.Window | None:
    """The calendar window directly before a compatible plan window."""
    if unit == "periodo":
        unit = _calendar_unit_of(plan) or ""
    if unit == "mes":
        if plan.period and plan.period.period is Period.THIS_MONTH:
            return RelativePeriod(Period.LAST_MONTH)
        if plan.period and plan.period.period is Period.LAST_MONTH:
            year, month = _month_before(today.year, today.month)
            year, month = _month_before(year, month)
            return window_words.whole_month(year, month)
        if plan.date_range and _is_whole_month(plan.date_range):
            year, month = _month_before(plan.date_range.start.year, plan.date_range.start.month)
            return window_words.whole_month(year, month)
    if unit == "semana":
        if plan.period and plan.period.period is Period.THIS_WEEK:
            return RelativePeriod(Period.LAST_WEEK)
        if plan.date_range and (plan.date_range.end - plan.date_range.start).days == 6:
            return AbsoluteRange(
                start=plan.date_range.start - timedelta(days=7),
                end=plan.date_range.end - timedelta(days=7),
            )
    if unit == "ano":
        if plan.period and plan.period.period is Period.THIS_YEAR:
            return window_words.whole_year(today.year - 1)
        if plan.date_range and _is_whole_year(plan.date_range):
            return window_words.whole_year(plan.date_range.start.year - 1)
    if unit == "trimestre":
        if plan.period and plan.period.period is Period.THIS_QUARTER:
            return RelativePeriod(Period.LAST_QUARTER)
        if plan.period and plan.period.period is Period.LAST_QUARTER:
            current = window_words.whole_quarter(today.year, (today.month - 1) // 3 + 1)
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_quarter(last_end.year, (last_end.month - 1) // 3 + 1)
            before = last.start - timedelta(days=1)
            return window_words.whole_quarter(before.year, (before.month - 1) // 3 + 1)
        if plan.date_range and _is_whole_quarter(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_quarter(before.year, (before.month - 1) // 3 + 1)
    if unit == "semestre":
        if plan.period and plan.period.period is Period.THIS_SEMESTER:
            return RelativePeriod(Period.LAST_SEMESTER)
        if plan.period and plan.period.period is Period.LAST_SEMESTER:
            current = window_words.whole_semester(today.year, 1 if today.month <= 6 else 2)
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_semester(last_end.year, 1 if last_end.month <= 6 else 2)
            before = last.start - timedelta(days=1)
            return window_words.whole_semester(before.year, 1 if before.month <= 6 else 2)
        if plan.date_range and _is_whole_semester(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_semester(before.year, 1 if before.month <= 6 else 2)
    if unit == "quincena":
        if plan.period and plan.period.period is Period.THIS_FORTNIGHT:
            return RelativePeriod(Period.LAST_FORTNIGHT)
        if plan.period and plan.period.period is Period.LAST_FORTNIGHT:
            current = window_words.whole_fortnight(
                today.year, today.month, 1 if today.day <= 15 else 2
            )
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_fortnight(
                last_end.year, last_end.month, 1 if last_end.day <= 15 else 2
            )
            before = last.start - timedelta(days=1)
            return window_words.whole_fortnight(
                before.year, before.month, 1 if before.day <= 15 else 2
            )
        if plan.date_range and _is_whole_fortnight(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_fortnight(
                before.year, before.month, 1 if before.day <= 15 else 2
            )
    if unit == "fin de semana":
        if plan.period and plan.period.period is Period.THIS_WEEKEND:
            return RelativePeriod(Period.LAST_WEEKEND)
        if plan.period and plan.period.period is Period.LAST_WEEKEND:
            last = _last_completed_weekend(today)
            return AbsoluteRange(
                start=last.start - timedelta(days=7),
                end=last.end - timedelta(days=7),
            )
        if plan.date_range and _is_weekend(plan.date_range):
            return AbsoluteRange(
                start=plan.date_range.start - timedelta(days=7),
                end=plan.date_range.end - timedelta(days=7),
            )
    return None


def _calendar_month_window(plan: FinancialPlan, today: date) -> AbsoluteRange | None:
    """Resolve only month-sized windows, for comparisons whose unit is one payroll."""
    if plan.date_range is not None and _is_whole_month(plan.date_range):
        return plan.date_range
    if plan.period is None:
        return None
    if plan.period.period is Period.THIS_MONTH:
        return window_words.whole_month(today.year, today.month)
    if plan.period.period is Period.LAST_MONTH:
        year, month = _month_before(today.year, today.month)
        return window_words.whole_month(year, month)
    return None


def _comparison_window_label(plan: FinancialPlan) -> str:
    """A compact natural label for the two sides of a comparison."""
    if plan.date_range is not None and _is_whole_month(plan.date_range):
        start = plan.date_range.start
        return f"en {answer_writer.month_name(start.year, start.month)}"
    if plan.date_range is not None and _is_whole_year(plan.date_range):
        return f"en {plan.date_range.start.year}"
    return answer_writer.window_of(plan)


def _calendar_unit_of(plan: FinancialPlan) -> str | None:
    if plan.period is not None:
        return {
            Period.THIS_MONTH: "mes",
            Period.LAST_MONTH: "mes",
            Period.THIS_WEEK: "semana",
            Period.THIS_YEAR: "ano",
            Period.THIS_QUARTER: "trimestre",
            Period.LAST_QUARTER: "trimestre",
            Period.THIS_SEMESTER: "semestre",
            Period.LAST_SEMESTER: "semestre",
            Period.THIS_FORTNIGHT: "quincena",
            Period.LAST_FORTNIGHT: "quincena",
            Period.THIS_WEEKEND: "fin de semana",
            Period.LAST_WEEKEND: "fin de semana",
        }.get(plan.period.period)
    if plan.date_range is not None:
        if _is_whole_month(plan.date_range):
            return "mes"
        if _is_whole_year(plan.date_range):
            return "ano"
        if _is_whole_quarter(plan.date_range):
            return "trimestre"
        if _is_whole_semester(plan.date_range):
            return "semestre"
        if _is_whole_fortnight(plan.date_range):
            return "quincena"
        if _is_weekend(plan.date_range):
            return "fin de semana"
        if (plan.date_range.end - plan.date_range.start).days == 6:
            return "semana"
    return None


def _month_before(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _numbered_month_window(month: int, today: date) -> AbsoluteRange:
    """The most recent occurrence of a numbered month, like the named-month vocabulary."""
    year = today.year if month <= today.month else today.year - 1
    return window_words.whole_month(year, month)


def _is_whole_month(window: AbsoluteRange) -> bool:
    return window == window_words.whole_month(window.start.year, window.start.month)


def _is_whole_year(window: AbsoluteRange) -> bool:
    return window == window_words.whole_year(window.start.year)


def _is_whole_quarter(window: AbsoluteRange) -> bool:
    quarter = (window.start.month - 1) // 3 + 1
    return window == window_words.whole_quarter(window.start.year, quarter)


def _is_whole_semester(window: AbsoluteRange) -> bool:
    semester = 1 if window.start.month <= 6 else 2
    return window == window_words.whole_semester(window.start.year, semester)


def _is_whole_fortnight(window: AbsoluteRange) -> bool:
    fortnight = 1 if window.start.day <= 15 else 2
    return window == window_words.whole_fortnight(window.start.year, window.start.month, fortnight)


def _is_weekend(window: AbsoluteRange) -> bool:
    return (
        window.start.weekday() == 5
        and window.end.weekday() == 6
        and (window.end - window.start).days == 1
    )


def _last_completed_weekend(today: date) -> AbsoluteRange:
    if today.weekday() <= 4:
        sunday = today - timedelta(days=today.weekday() + 1)
    else:
        current_saturday = today - timedelta(days=today.weekday() - 5)
        sunday = current_saturday - timedelta(days=6)
    return AbsoluteRange(start=sunday - timedelta(days=1), end=sunday)


# A name in the slot a filter goes in. "¿Cuántas compras hice en Mercadona?" is a question about a
# shop, and a shop is the one filter the ellipsis branch below cannot represent: it patches the
# previous query and keeps its categories, so the turn came back counting *restaurant* purchases —
# a real figure about a different question, which is the failure this service is built around.
#
# The length guard cannot see it. That sentence is five words, exactly the limit, and length was
# never what tells an ellipsis from a complete question: "¿y en gasolina?" is an ellipsis because
# it introduces nothing this path does not already know, and this one is not because it does.
#
# Capitalised, and that is the whole test. Spanish capitalises proper nouns and almost nothing else
# mid-sentence, so a capital in the "en X" slot is the sentence saying "this is a name" — no
# vocabulary to maintain and no shop list to be wrong about. Somebody typing all in lower case is
# not covered here; they are covered by declining to guess further down the path, and the cost of
# reading the capital as a name when it is not is a turn that reaches the planner, which is the
# harmless side.
_A_NAME_IN_A_FILTER_SLOT: Final = re.compile(
    r"\b(?:en|de|con)\s+(?:el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?([a-z][a-z0-9]{2,})"
)


def _names_a_filter_this_cannot_read(query: AskFinancialQuestionQuery) -> bool:
    """Whether the sentence names something in a filter slot that no vocabulary here claims."""
    for word in _A_NAME_IN_A_FILTER_SLOT.findall(spanish.folded(query.sentence)):
        if category_words.named_in(word) or account_words.named_in(word):
            continue
        if explicit_facts.read(word, today=query.today).window is not None:
            continue
        return True
    return False


def _contextual_follow_up_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Patch explicit filters and calculations onto the last validated financial query."""
    previous = query.conversation.last_plan
    if not isinstance(previous, FinancialPlan):
        return None
    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    folded = re.sub(r"^(?:vale|bueno|ok|perfecto)\W+", "", folded)
    facts = explicit_facts.read(query.sentence, today=query.today)

    if (
        previous.metric is Metric.CUMULATIVE
        and facts.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
        and len(folded.split()) <= 5
        and folded.startswith(("y ", "ahora ", "por "))
    ):
        return explicit_facts.enforce(replace(previous, group_by=facts.group_by), facts)

    if _CATEGORY_REFERENCE.search(folded):
        referenced = query.conversation.referenced_category
        if referenced is None and len(previous.categories) == 1:
            referenced = previous.categories[0]
        if referenced is None or category_words.words_for(referenced) is None:
            return None
        return explicit_facts.enforce(
            replace(
                previous,
                categories=(referenced,),
                excluded_categories=tuple(
                    category for category in previous.excluded_categories if category != referenced
                ),
                merchants=(),
                excluded_merchants=(),
                group_by=facts.group_by or GroupBy.NONE,
                metric=facts.metric or previous.metric,
            ),
            facts,
        )

    if (
        facts.group_by
        in {GroupBy.CATEGORY, GroupBy.MERCHANT, GroupBy.COUNTERPARTY, GroupBy.ACCOUNT}
        and len(folded.split()) <= 7
        and re.search(r"\b(?:separa|separalo|desglosa|desglosalo|agrupa|agrupalo|por)\b", folded)
    ):
        # "Sepáralo por comercio" changes only the presentation dimension of the query in view.
        # Sending that three-word patch to a model made it re-create the window and filters, which
        # is both slower and an opportunity to silently widen the result.
        return explicit_facts.enforce(replace(previous, group_by=facts.group_by), facts)

    if facts.excluded_categories or facts.excluded_merchants:
        # A negative filter patches the validated query in view. No model needs to re-emit the
        # window, direction and calculation merely to understand "sin alquiler".
        return explicit_facts.enforce(previous, facts)

    if (
        re.fullmatch(r"(?:y\s+)?(?:el\s+)?ano\s+(?:anterior|pasado)", folded)
        and (earlier_window := _previous_calendar_window(previous, "ano", query.today)) is not None
    ):
        return replace(
            previous,
            period=earlier_window if isinstance(earlier_window, RelativePeriod) else None,
            date_range=earlier_window if isinstance(earlier_window, AbsoluteRange) else None,
        )

    # A turn containing only a new window patches the query already on screen. Replanning
    # "en lo que llevamos de aÃ±o" from scratch discarded both categories in a comparison.
    if (
        facts.window is not None
        and not facts.names_a_category
        and facts.metric is None
        and facts.direction is None
        and len(folded.split()) <= 8
        and not _starts_new_financial_scope(query.sentence, today=query.today)
        and not (_GROUP_RANKING_DIMENSION.search(folded) and _GROUP_RANKING_SIGNAL.search(folded))
        and not re.search(r"\bano\s+(?:anterior|pasado)\b", folded)
        and capability_guard.unsupported_reason(query.sentence) is None
    ):
        patched = explicit_facts.enforce(previous, facts)
        logger.info(
            "turn.window",
            extra={
                "explicit": _window_of(patched),
                "planned": _window_of(previous),
                "inherited": _window_of(previous),
            },
        )
        return patched

    if facts.asks_comparison_reference and previous.group_by is not GroupBy.NONE:
        return previous

    if _TOP_MERCHANT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(
                previous,
                direction=Direction.EXPENSE,
                merchants=(),
                merchant_terms=(),
                group_by=GroupBy.MERCHANT,
                metric=Metric.SUM,
            ),
            facts,
        )

    named = category_words.precisely_named_in(query.sentence)
    if named and _CATEGORY_FILTER_FOLLOW_UP.search(folded):
        group_by = (
            GroupBy.NONE
            if previous.group_by is GroupBy.CATEGORY and len(named) == 1
            else previous.group_by
        )
        return explicit_facts.enforce(
            replace(
                previous,
                categories=named,
                excluded_categories=(),
                merchants=(),
                merchant_terms=(),
                group_by=group_by,
            ),
            facts,
        )

    # These are deliberately short ellipses. A complete question can introduce a merchant or a
    # category the closed vocabulary does not know, and inheriting the old filters there would
    # answer a different question with a plausible figure.
    #
    # And a complete question is not an ellipsis however short it is, which the window branch above
    # already says and this one did not. "¿Cuánto gasté el mes pasado?" is five words, so it
    # reached the sum-follow-up below and became the previous plan with the direction flipped —
    # after "¿cuánto he invertido en el último año?", that plan is restricted to the fund and to
    # internal movements only, and flipping it to EXPENSE answers "what did you spend" with what
    # went into the fund. Two branches of one function disagreed about what an ellipsis is.
    if (
        _names_a_filter_this_cannot_read(query)
        or len(folded.split()) > 5
        or _starts_new_financial_scope(query.sentence, today=query.today)
    ):
        return None

    # Counting a kind the vocabulary *does* know is the exception to the rule below, and it is the
    # difference between the two reasons a follow-up gets refused. Declining protects the turn that
    # introduces something this path cannot represent; a named category is not that — it is a
    # filter, and replacing one filter is what this whole function does.
    #
    # Without this, "¿cuántos bizums tengo?" declines the way "¿cuántas compras hice en Mercadona?"
    # does, and reaches the planner having dropped the window the conversation was in. Two turns
    # earlier the same question about compras kept it.
    if named and _COUNT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(
                previous,
                categories=named,
                excluded_categories=(),
                merchants=(),
                merchant_terms=(),
                group_by=GroupBy.NONE,
                metric=Metric.COUNT,
            ),
            facts,
        )
    if named:
        return None
    if _COUNT_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(replace(previous, metric=Metric.COUNT), facts)
    if _SUM_EXPENSE_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(previous, direction=Direction.EXPENSE, metric=Metric.SUM), facts
        )
    if _SUM_INCOME_FOLLOW_UP.search(folded):
        return explicit_facts.enforce(
            replace(previous, direction=Direction.INCOME, metric=Metric.SUM), facts
        )
    direction_only = _DIRECTION_ONLY_FOLLOW_UP.fullmatch(folded)
    if direction_only:
        direction = (
            Direction.INCOME
            if direction_only.group("direction").startswith("ingreso")
            else Direction.EXPENSE
        )
        return replace(previous, direction=direction)
    return None


def _contextual_ranking_plan(query: AskFinancialQuestionQuery) -> TopMovements | None:
    """Turn a superlative into a ranking while retaining the prior window and filters."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    if facts.asks_top_category or not _RANKING_REQUEST.search(spanish.folded(query.sentence)):
        return None
    previous = (
        None
        if _starts_new_financial_scope(query.sentence, today=query.today)
        else query.conversation.last_plan
    )
    window = facts.window or (
        previous.period or previous.date_range if previous is not None else None
    )
    if window is None:
        return None
    folded = spanish.folded(query.sentence)
    if re.search(r"\bingres|\bcobr|\bnomina|\bsueldo|\bsalario", folded):
        direction = Direction.INCOME
    elif re.search(r"\bgast|\bpagu|\bcompr|\bcargo", folded):
        direction = Direction.EXPENSE
    elif previous is not None:
        direction = previous.direction
    else:
        direction = Direction.ALL
    named = category_words.precisely_named_in(query.sentence)
    return TopMovements(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (previous.categories if previous is not None else ()),
        excluded_categories=(
            tuple(category for category in previous.excluded_categories if category not in named)
            if previous is not None
            else ()
        ),
        merchants=() if named else (previous.merchants if previous is not None else ()),
        merchant_terms=(() if named else (previous.merchant_terms if previous is not None else ())),
        excluded_merchants=(previous.excluded_merchants if previous is not None else ()),
        rank=1,
    )


def _account_follow_up_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Focus the previous query on an account named as a short conversational reply."""
    previous = (
        None
        if _starts_new_financial_scope(query.sentence, today=query.today)
        else query.conversation.last_plan
    )
    if not isinstance(previous, FinancialPlan) or not account_words.named_in(query.sentence):
        return None
    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    if not _SALARY_ACCOUNT_FOLLOW_UP.fullmatch(folded) and not _ACCOUNT_SELECTION_FOLLOW_UP.search(
        folded
    ):
        if len(folded.split()) > 4:
            return None
        if window_words.window_in(query.sentence, today=query.today) is not None:
            return None
    selected = replace(previous, group_by=GroupBy.NONE)
    window = window_words.window_in(query.sentence, today=query.today)
    if window is None:
        return selected
    return replace(
        selected,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
    )


def _group_ranking_plan(query: AskFinancialQuestionQuery) -> FinancialPlan | None:
    """Rank aggregate groups by the exact metric named in the sentence."""
    folded = spanish.folded(query.sentence)
    dimension_match = _GROUP_RANKING_DIMENSION.search(folded)
    previous = query.conversation.last_plan
    previous_ranking = (
        previous
        if isinstance(previous, FinancialPlan) and previous.group_limit is not None
        else None
    )
    ranking_follow_up = (
        previous_ranking is not None
        and _GROUP_RANKING_CRITERION.search(folded) is not None
        and folded.strip(" ¿?¡!.,").startswith(("y ", "ahora ", "mejor ", "por "))
    )
    if dimension_match is None and not ranking_follow_up:
        return None
    if not ranking_follow_up and _GROUP_RANKING_SIGNAL.search(folded) is None:
        return None

    named_dimension = _named_group_dimension(folded) if dimension_match else None
    if named_dimension is not None:
        group_by = named_dimension
    else:
        assert previous_ranking is not None
        group_by = previous_ranking.group_by

    if re.search(r"\b(?:ticket\s+medio|importe\s+medio|media|promedio)\b", folded):
        metric = Metric.AVERAGE
    elif re.search(r"\b(?:operaciones|movimientos|compras|pagos)\b", folded):
        metric = Metric.COUNT
    elif ranking_follow_up and not re.search(r"\b(?:importe|gasto|total)\b", folded):
        assert previous_ranking is not None
        metric = previous_ranking.metric
    else:
        metric = Metric.SUM

    count_match = _GROUP_RANKING_COUNT.search(folded)
    if count_match is None:
        if ranking_follow_up:
            assert previous_ranking is not None
            group_limit = previous_ranking.group_limit
        else:
            group_limit = 5
    else:
        raw_count = count_match.group("count")
        group_limit = int(raw_count) if raw_count.isdigit() else _GROUP_RANKING_NUMBERS[raw_count]

    facts = explicit_facts.read(query.sentence, today=query.today)
    contextual = ranking_follow_up or not _starts_new_financial_scope(
        query.sentence, today=query.today
    )
    base = previous_ranking if contextual else None
    window = facts.window or (
        base.period or base.date_range if base is not None else RelativePeriod(Period.THIS_YEAR)
    )
    named = category_words.precisely_named_in(query.sentence)
    if re.search(r"\bingres|\bcobr|\bnomina", folded):
        direction = Direction.INCOME
    elif base is not None and ranking_follow_up:
        direction = base.direction
    else:
        direction = Direction.EXPENSE
    return FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named or (base.categories if base is not None else ()),
        excluded_categories=base.excluded_categories if base is not None else (),
        merchants=base.merchants if base is not None and group_by is not GroupBy.MERCHANT else (),
        merchant_terms=(
            base.merchant_terms if base is not None and group_by is not GroupBy.MERCHANT else ()
        ),
        excluded_merchants=base.excluded_merchants if base is not None else (),
        group_by=group_by,
        metric=metric,
        group_limit=group_limit,
        include_internal=base.include_internal if base is not None else False,
        internal_only=base.internal_only if base is not None else False,
        across_accounts=(group_by is GroupBy.ACCOUNT) or (base.across_accounts if base else False),
        only_account_id=None
        if group_by is GroupBy.ACCOUNT
        else (base.only_account_id if base else None),
        only_account_ids=()
        if group_by is GroupBy.ACCOUNT
        else (base.only_account_ids if base else ()),
        transaction_types=base.transaction_types if base is not None else (),
    )


def _gambling_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Expenses at betting merchants, with explicit substring rather than exact-name semantics."""
    folded = spanish.folded(query.sentence)
    if _GAMBLING.search(folded) is None:
        return None
    if not re.search(r"\b(?:gast|pag|movim|operacion|dinero|comercio|casa|ocio)\w*\b", folded):
        return None

    terms: list[str] = []
    if re.search(r"\bapuest\w*\b", folded):
        # The product family, not two exact shops: this covers Casa de Apuestas and Bet365.
        terms.extend(("apuest", "bet"))
    for term in ("bet", "casino", "codere", "bwin", "sportium"):
        if re.search(rf"\b{re.escape(term)}\w*\b", folded) and term not in terms:
            terms.append(term)
    window = window_words.window_in(query.sentence, today=query.today)
    if window is None:
        return FinancialPlanDraft(
            direction=Direction.EXPENSE,
            categories=("LEISURE",),
            merchant_terms=tuple(terms),
        )
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=("LEISURE",),
        merchant_terms=tuple(terms),
    )


def _multiple_movement_ranking(
    query: AskFinancialQuestionQuery,
) -> tuple[TopMovements, int] | None:
    """Several individual movements, distinct from a ranking of aggregate groups."""
    folded = spanish.folded(query.sentence)
    found = _MULTIPLE_MOVEMENT_RANKING.search(folded)
    if found is None:
        return None
    raw_count = found.group("count") or found.group("leading_count") or found.group("top_count")
    count = int(raw_count) if raw_count.isdigit() else _GROUP_RANKING_NUMBERS[raw_count]
    if not 2 <= count <= MAX_GROUP_RANK:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    window = facts.window or RelativePeriod(Period.THIS_YEAR)
    named = category_words.precisely_named_in(query.sentence)
    direction = (
        Direction.INCOME
        if re.search(r"\bingres|\bcobr|\bnomina|\bsalario|\bsueldo", folded)
        else Direction.EXPENSE
    )
    return (
        TopMovements(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=named,
            rank=1,
        ),
        count,
    )


def _natural_period_aggregate_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | None:
    """A simple aggregate whose new calendar window and direction are both explicit."""
    window = window_words.natural_period_in(query.sentence, today=query.today)
    if window is None:
        return None
    facts = explicit_facts.read(query.sentence, today=query.today)
    named = category_words.precisely_named_in(query.sentence)
    direction = facts.direction
    if direction is None and re.search(
        r"\bgast|\bpagu|\bcompr|\bcargos?\b|\bme\s+cobr", spanish.folded(query.sentence)
    ):
        direction = Direction.EXPENSE
    if direction is None and named:
        direction = _direction_of(named)
    if direction is None or facts.metric in {
        Metric.AVERAGE,
        Metric.PERIOD_AVERAGE,
        Metric.CUMULATIVE,
        Metric.FREQUENCY,
        Metric.RECURRENCE,
    }:
        return None
    plan = FinancialPlan(
        direction=direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=named,
        group_by=facts.group_by or GroupBy.NONE,
        metric=facts.metric or Metric.SUM,
        transaction_types=(TransactionType.DIRECT_DEBIT,) if facts.asks_direct_debits else (),
    )
    return explicit_facts.enforce_exclusions(plan, facts)


def _deterministic_breakdown_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Build the two common breakdowns whose meaning is fully stated by the sentence."""
    facts = explicit_facts.read(query.sentence, today=query.today)
    named_categories = category_words.precisely_named_in(query.sentence)
    if not (
        facts.asks_top_category
        or facts.asks_account_breakdown
        or (facts.compared_categories and facts.metric is None)
    ):
        return None

    previous = (
        None
        if _starts_new_financial_scope(query.sentence, today=query.today)
        else query.conversation.last_plan
    )
    previous_financial = previous if isinstance(previous, FinancialPlan) else None
    window = facts.window or (
        previous.period or previous.date_range if previous is not None else None
    )
    direction = (
        Direction.EXPENSE
        if facts.asks_top_category or facts.compared_categories
        else (previous.direction if previous is not None else (facts.direction or Direction.ALL))
    )
    group_by = (
        GroupBy.CATEGORY
        if facts.asks_top_category or facts.compared_categories
        else GroupBy.ACCOUNT
    )
    categories = (
        facts.compared_categories
        if facts.compared_categories
        else (
            ()
            if facts.asks_top_category
            else (named_categories or (previous.categories if previous is not None else ()))
        )
    )
    merchants = (
        ()
        if facts.asks_top_category or named_categories
        else (previous.merchants if previous is not None else ())
    )
    merchant_terms = (
        ()
        if facts.asks_top_category or named_categories
        else (previous.merchant_terms if previous is not None else ())
    )
    excluded_categories = (
        tuple(
            category
            for category in previous.excluded_categories
            if category not in named_categories
        )
        if previous is not None
        else ()
    )
    excluded_merchants = (
        () if named_categories else (previous.excluded_merchants if previous is not None else ())
    )
    include_internal = previous_financial.include_internal if previous_financial else False
    internal_only = previous_financial.internal_only if previous_financial else False
    across_accounts = (
        True
        if facts.asks_account_breakdown
        else (previous_financial.across_accounts if previous_financial else False)
    )
    only_account_id = (
        None
        if facts.asks_account_breakdown
        else (previous_financial.only_account_id if previous_financial else None)
    )
    only_account_ids = (
        ()
        if facts.asks_account_breakdown
        else (previous_financial.only_account_ids if previous_financial else ())
    )
    transaction_ids = previous_financial.transaction_ids if previous_financial else ()
    transaction_types = previous_financial.transaction_types if previous_financial else ()
    if window is None:
        return explicit_facts.enforce_exclusions(
            FinancialPlanDraft(
                direction=direction,
                categories=categories,
                excluded_categories=excluded_categories,
                merchants=merchants,
                merchant_terms=merchant_terms,
                excluded_merchants=excluded_merchants,
                group_by=group_by,
                metric=Metric.SUM,
                include_internal=include_internal,
                internal_only=internal_only,
                across_accounts=across_accounts,
                only_account_id=only_account_id,
                only_account_ids=only_account_ids,
                transaction_ids=transaction_ids,
                transaction_types=transaction_types,
            ),
            facts,
        )
    return explicit_facts.enforce_exclusions(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=excluded_categories,
            merchants=merchants,
            merchant_terms=merchant_terms,
            excluded_merchants=excluded_merchants,
            group_by=group_by,
            metric=Metric.SUM,
            include_internal=include_internal,
            internal_only=internal_only,
            across_accounts=across_accounts,
            only_account_id=only_account_id,
            only_account_ids=only_account_ids,
            transaction_ids=transaction_ids,
            transaction_types=transaction_types,
        ),
        facts,
    )


def _average_plan(
    query: AskFinancialQuestionQuery,
) -> FinancialPlan | FinancialPlanDraft | None:
    """Build either kind of average from explicit words, preserving safe conversation context.

    The distinction is arithmetic, not semantic guesswork: AVERAGE divides by matching movements;
    PERIOD_AVERAGE divides by the DAY/WEEK/MONTH buckets named in the sentence. A missing window is
    kept as a draft so the next turn can answer it instead of being told the capability is absent.
    """
    facts = explicit_facts.read(query.sentence, today=query.today)
    previous = query.conversation.last_plan
    natural_period = _NATURAL_PERIOD_AVERAGE_FOLLOW_UP.fullmatch(_clean_turn(query.sentence))
    if natural_period is not None and isinstance(previous, FinancialPlan):
        groups = {"dia": GroupBy.DAY, "semana": GroupBy.WEEK, "mes": GroupBy.MONTH}
        return replace(
            previous,
            group_by=groups[natural_period.group("unit")],
            metric=Metric.PERIOD_AVERAGE,
        )
    follow_up = _average_follow_up_plan(query, facts, previous)
    if follow_up is not None:
        return follow_up
    if facts.metric not in {Metric.AVERAGE, Metric.PERIOD_AVERAGE}:
        return None

    named = category_words.precisely_named_in(query.sentence)
    previous_categories = previous.categories if previous is not None else ()
    previous_excluded_categories = previous.excluded_categories if previous is not None else ()
    previous_merchants = previous.merchants if previous is not None else ()
    previous_merchant_terms = previous.merchant_terms if previous is not None else ()
    previous_excluded_merchants = previous.excluded_merchants if previous is not None else ()
    asks_to_spend = re.search(
        r"\bgast|\bpagu|\bcompr|\bcargo|\bme\s+cobr", spanish.folded(query.sentence)
    )
    explicit_direction = facts.direction or (Direction.EXPENSE if asks_to_spend else None)
    changes_direction = (
        explicit_direction is not None
        and previous is not None
        and explicit_direction is not previous.direction
    )
    categories = named or (() if changes_direction else previous_categories)
    merchants = () if named or changes_direction else previous_merchants
    merchant_terms = () if named or changes_direction else previous_merchant_terms
    if named:
        previous_excluded_categories = tuple(
            category for category in previous_excluded_categories if category not in named
        )
        previous_excluded_merchants = ()
    direction = explicit_direction
    direction = direction or (previous.direction if previous is not None else None)
    if direction is None:
        direction = _direction_of(categories) if categories else Direction.ALL

    if facts.group_by is not None:
        group_by = facts.group_by
    elif (named_dimension := _named_group_dimension(spanish.folded(query.sentence))) is not None:
        # A dimension the sentence states outright beats anything inherited. "Top 2 categorías por
        # ticket medio" came in after two turns about comercios and was read as a merchant average,
        # because `explicit_facts` does not treat that phrasing as a breakdown request and the
        # grouping was therefore inherited. Only the order of the cascade kept it off the wire.
        group_by = named_dimension
    elif (
        facts.metric is Metric.AVERAGE
        and isinstance(previous, FinancialPlan)
        and previous.metric is Metric.PERIOD_AVERAGE
        and previous.group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}
    ):
        # "Ticket medio" changes the divisor back to movements. A temporal grouping inherited
        # from the previous period-average would otherwise survive and answer a different question.
        group_by = GroupBy.NONE
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
        return explicit_facts.enforce(
            FinancialPlanDraft(
                direction=direction,
                categories=categories,
                excluded_categories=previous_excluded_categories,
                merchants=merchants,
                merchant_terms=merchant_terms,
                excluded_merchants=previous_excluded_merchants,
                group_by=group_by,
                metric=facts.metric,
                include_internal=(
                    previous.include_internal if isinstance(previous, FinancialPlan) else False
                ),
                internal_only=(
                    previous.internal_only if isinstance(previous, FinancialPlan) else False
                ),
                across_accounts=(
                    previous.across_accounts if isinstance(previous, FinancialPlan) else False
                ),
                only_account_id=(
                    previous.only_account_id if isinstance(previous, FinancialPlan) else None
                ),
                only_account_ids=(
                    previous.only_account_ids if isinstance(previous, FinancialPlan) else ()
                ),
            ),
            facts,
        )
    return explicit_facts.enforce(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=previous_excluded_categories,
            merchants=merchants,
            merchant_terms=merchant_terms,
            excluded_merchants=previous_excluded_merchants,
            group_by=group_by,
            metric=facts.metric,
            include_internal=(
                previous.include_internal if isinstance(previous, FinancialPlan) else False
            ),
            internal_only=(
                previous.internal_only if isinstance(previous, FinancialPlan) else False
            ),
            across_accounts=(
                previous.across_accounts if isinstance(previous, FinancialPlan) else False
            ),
            only_account_id=(
                previous.only_account_id if isinstance(previous, FinancialPlan) else None
            ),
            only_account_ids=(
                previous.only_account_ids if isinstance(previous, FinancialPlan) else ()
            ),
        ),
        facts,
    )


def _average_follow_up_plan(
    query: AskFinancialQuestionQuery,
    facts: explicit_facts.ExplicitFacts,
    previous: PlannedQuery | None,
) -> FinancialPlan | None:
    """Apply short, explicit changes to an average without reconstructing it with a model."""
    if not isinstance(previous, FinancialPlan) or previous.metric not in {
        Metric.AVERAGE,
        Metric.PERIOD_AVERAGE,
    }:
        return None
    # A complete average in the new sentence outranks context. This helper only fills the meaning
    # of elliptical turns such as "¿y al mes?"; applying it to "coste semanal medio" would retain
    # the old monthly divisor merely because the sentence also changed category.
    if facts.metric is not None:
        return None

    folded = spanish.folded(query.sentence).strip(" ¿?¡!.,")
    named = category_words.precisely_named_in(query.sentence)
    metric = previous.metric
    group_by = previous.group_by

    if _AVERAGE_PERIOD_FOLLOW_UP.fullmatch(folded) and facts.group_by in {
        GroupBy.DAY,
        GroupBy.WEEK,
        GroupBy.MONTH,
    }:
        metric = Metric.PERIOD_AVERAGE
        group_by = facts.group_by
    elif _AVERAGE_MOVEMENT_FOLLOW_UP.fullmatch(folded):
        metric = Metric.AVERAGE
        if group_by in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
            group_by = GroupBy.NONE
    elif not (folded.startswith("y ") and (facts.window is not None or named)):
        return None

    window = facts.window or previous.period or previous.date_range
    categories = named or previous.categories
    direction = facts.direction
    if direction is None:
        direction = _direction_of(named) if named else previous.direction
    return explicit_facts.enforce(
        FinancialPlan(
            direction=direction,
            period=window if isinstance(window, RelativePeriod) else None,
            date_range=window if isinstance(window, AbsoluteRange) else None,
            categories=categories,
            excluded_categories=tuple(
                category for category in previous.excluded_categories if category not in named
            ),
            merchants=() if named else previous.merchants,
            merchant_terms=() if named else previous.merchant_terms,
            excluded_merchants=() if named else previous.excluded_merchants,
            group_by=group_by,
            metric=metric,
            include_internal=previous.include_internal,
            internal_only=previous.internal_only,
            across_accounts=previous.across_accounts,
            only_account_id=previous.only_account_id,
            only_account_ids=previous.only_account_ids,
        ),
        facts,
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


def _answered(
    plan: FinancialPlan,
    answer: QueryAnswer,
    was: Conversation,
    sentence: str,
    *,
    reporting_timezone: str,
) -> AskFinancialQuestionResult:
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
        # Answered: nothing is left open, and this plan becomes the memory the next follow-up
        # inherits from.
        conversation=was.answered(
            plan,
            referenced_category=referenced_category,
            referenced_group_by=referenced_group_by,
            referenced_group_key=referenced_group_key,
            referenced_transaction_ids=_evidence_ids(visible_movements),
        ),
        currency=answer.currency,
        total_minor=visible_total,
        movement_count=visible_movement_count,
        groups=[SpokenGroup(g.key, g.total_minor, g.movement_count) for g in visible_groups],
        movements=[_spoken_movement(movement) for movement in visible_movements],
        suggestions=query_recovery.empty_suggestions(plan) if answer.is_empty else [],
    )
