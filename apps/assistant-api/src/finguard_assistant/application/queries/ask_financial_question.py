"""AskFinancialQuestionQuery · Result · Handler — the whole phase 3 path.

Sentence in, plan, figures from banking, sentence out. The handler's only real job is
deciding what each planning outcome means, and five of the six never reach the ledger:
the cheapest wrong plan is the one that was never run.

`today` arrives on the query rather than being read here, for the same reason it does
in banking: a handler that reads the clock cannot be replayed, and this one has to be —
the evaluation depends on it.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_assistant.application.bus.query_bus import Query
from finguard_assistant.domain.enums import PlanningOutcome
from finguard_assistant.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_assistant.domain.models.planning_result import PlanningResult
from finguard_assistant.domain.models.query_answer import QueryAnswer
from finguard_assistant.domain.ports.financial_query import FinancialQueryPort
from finguard_assistant.domain.ports.query_planner import QueryPlannerPort
from finguard_assistant.domain.services import answer_writer
from finguard_assistant.domain.value_objects.financial_plan import FinancialPlan


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
    group_by: str = "NONE"
    metric: str = "SUM"


@dataclass(frozen=True)
class SpokenMovement:
    occurred_at: str
    description: str
    amount_minor: int
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
    currency: str | None = None
    total_minor: int | None = None
    movement_count: int | None = None
    groups: list[SpokenGroup] = field(default_factory=list)
    movements: list[SpokenMovement] = field(default_factory=list)


@dataclass(frozen=True)
class AskFinancialQuestionQuery(Query[AskFinancialQuestionResult]):
    sentence: str
    account_id: UUID
    today: date
    # The plan behind the previous answer, echoed back by the caller. Held by whoever
    # is having the conversation, not by this service — and already a plan by the time
    # it arrives here, rather than text that has to be believed.
    previous_plan: FinancialPlan | None = None


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
    def __init__(self, planner: QueryPlannerPort, queries: FinancialQueryPort) -> None:
        self._planner = planner
        self._queries = queries

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
        yield Stage("planning")
        planned = await self._planner.plan(
            query.sentence, today=query.today, previous=query.previous_plan
        )

        if not planned.is_answerable:
            yield _decline(planned)
            return

        assert planned.plan is not None  # is_answerable is exactly this condition
        yield Stage("planned", answer_writer.describe(planned.plan))
        yield Stage("querying")
        yield await self._answer(query, planned.plan)

    async def _answer(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan
    ) -> AskFinancialQuestionResult:
        try:
            answer = await self._queries.run(plan, account_id=query.account_id, today=query.today)
        except QueryRejectedError:
            # Our plan, banking's rules. Ours to fix, so it is reported as such rather
            # than dressed up as the user having asked something impossible.
            return AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text="He entendido la pregunta, pero la consulta que he construido no es válida.",
                plan=answer_writer.describe(plan),
            )
        except BankingUnavailableError:
            return AskFinancialQuestionResult(
                outcome="BANKING_UNAVAILABLE",
                text="No puedo consultar tus movimientos ahora mismo. Vuelve a intentarlo.",
            )

        return _answered(plan, answer)


def _decline(planned: PlanningResult) -> AskFinancialQuestionResult:
    """The five outcomes that never reach the ledger.

    Each gets its own sentence. Collapsing them into "no he podido" would tell the user
    nothing about whether to rephrase, wait, or give up.
    """
    match planned.outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            text = planned.question or ""
        case PlanningOutcome.UNSUPPORTED:
            text = (
                "Eso no puedo calcularlo todavía: solo consulto lo que ya ha pasado, "
                "sin medias, comparaciones entre periodos ni previsiones."
            )
        case PlanningOutcome.PROVIDER_ERROR:
            text = "No he podido pensar la respuesta ahora mismo. Vuelve a intentarlo."
        case _:
            text = "No he entendido la pregunta. ¿Puedes decirla de otra forma?"

    return AskFinancialQuestionResult(
        outcome=planned.outcome.value,
        text=text,
        question=planned.question,
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
        group_by=plan.group_by.value,
        metric=plan.metric.value,
    )


def _answered(plan: FinancialPlan, answer: QueryAnswer) -> AskFinancialQuestionResult:
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=answer_writer.write(answer, plan),
        plan=answer_writer.describe(plan),
        plan_context=_spoken(plan),
        currency=answer.currency,
        total_minor=answer.total_minor,
        movement_count=answer.movement_count,
        groups=[SpokenGroup(g.key, g.total_minor, g.movement_count) for g in answer.groups],
        movements=[
            SpokenMovement(
                occurred_at=m.occurred_at.isoformat(),
                description=m.description,
                amount_minor=m.amount_minor,
                merchant_name=m.merchant_name,
                category=m.category,
            )
            for m in answer.movements
        ],
    )
