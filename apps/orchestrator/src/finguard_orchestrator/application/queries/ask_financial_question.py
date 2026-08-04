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
from typing import Final
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.enums import PlanningOutcome, Spoken
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.query_planner import QueryPlannerPort
from finguard_orchestrator.domain.services import (
    answer_writer,
    capability_guard,
    category_words,
    explainer,
    intent_router,
    plan_verifier,
    window_words,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy


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
    # Present with a question back: the reading the question interrupted. The client echoes it
    # as `pending_draft` so the reply completes the plan instead of starting over. Same shape as
    # `plan_context` with no window, because that is exactly what it is.
    pending_context: SpokenPlan | None = None
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
    # The zone `today` was measured in. It travels with the date because neither describes a
    # window alone, and it belongs to the holder rather than the deployment: between
    # midnight and 01:00 peninsular time, today in the Canaries is yesterday in Madrid.
    reporting_timezone: str = "Europe/Madrid"
    # The plan behind the previous answer, echoed back by the caller. Held by whoever
    # is having the conversation, not by this service — and already a plan by the time
    # it arrives here, rather than text that has to be believed.
    previous_plan: FinancialPlan | None = None
    # The half-built plan behind the last question back, echoed by the caller the same way.
    # Separate from `previous_plan` because they mean different things: one is a question that
    # was answered, the other a question that was asked. Both can be present — a follow-up can
    # be the one that gets clarified.
    pending_draft: FinancialPlanDraft | None = None


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
    ) -> None:
        self._planner = planner
        self._queries = queries
        self._policy = policy

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
        intent = intent_router.route(query.sentence, has_previous=query.previous_plan is not None)
        previous = query.previous_plan

        if intent is Intent.IDENTITY_OR_HELP:
            # Answered from here, never from the ledger and never from a model. "¿Quién eres?" used
            # to reach the planner and come back as a refusal about averages and forecasts: true
            # about the product, and not an answer to the question.
            yield AskFinancialQuestionResult(outcome=Spoken.IDENTITY.value, text=explainer.IDENTITY)
            return

        if intent is Intent.EXPLAIN_PREVIOUS:
            # The previous plan and the catalogue know this. Asking a model why *we* answered
            # something would be asking it to guess at a fact we hold.
            yield AskFinancialQuestionResult(
                outcome=Spoken.EXPLANATION.value,
                text=explainer.explain(query.sentence, previous),
                plan=answer_writer.describe(previous) if previous else None,
                plan_context=_spoken(previous) if previous else None,
            )
            return

        if intent is Intent.CORRECTION:
            # The plan being corrected is dropped, which is the whole point of recognising this. A
            # correction treated as an ordinary follow-up patches a window onto a category the user
            # has just said was wrong.
            previous = None

        refused = capability_guard.unsupported_reason(query.sentence, self._policy)
        if refused is not None:
            # Before the model, on purpose. Refusing here is a control; refusing after is
            # whatever disposition the configured model happens to have, and that moved
            # by 60 points between two models we measured.
            yield _decline(PlanningResult.unsupported(refused))
            return

        finished = self._finish_pending(query)
        if finished is not None:
            # No model. The question back was about the window, the reply names one, and a
            # window is a closed vocabulary — so there is nothing here to infer.
            yield Stage("planned", answer_writer.describe(finished))
            yield Stage("querying")
            yield await self._answer(query, finished)
            return

        yield Stage("planning")
        planned = await self._planner.plan(query.sentence, today=query.today, previous=previous)

        if not planned.is_answerable:
            yield _decline(planned)
            return

        assert planned.plan is not None  # is_answerable is exactly this condition

        broken = plan_verifier.disagreement(query.sentence, planned.plan, self._policy)
        if broken is not None:
            # The plan is well formed, the domain accepts it, and it answers a different
            # question from the one asked. Nothing downstream can tell: banking would
            # return a real figure. This is the only place that compares the two.
            yield AskFinancialQuestionResult(
                outcome=PlanningOutcome.INVALID_DOMAIN.value,
                text=_refusal_for(broken, query.sentence),
                plan=answer_writer.describe(planned.plan),
                plan_context=_spoken(planned.plan),
            )
            return

        yield Stage("planned", answer_writer.describe(planned.plan))
        yield Stage("querying")
        yield await self._answer(query, planned.plan)

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

        A reply that goes to the model loses the draft, which is today's behaviour and not yet
        better than it: threading a half-built plan into the prompt is the next step, and it
        needs the prompt to be able to say "these fields are settled" without inviting the
        model to restate them.
        """
        draft = query.pending_draft
        if draft is None or not draft.needs_a_window:
            return None

        window = window_words.window_in(query.sentence)
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

    async def _answer(
        self, query: AskFinancialQuestionQuery, plan: FinancialPlan
    ) -> AskFinancialQuestionResult:
        try:
            answer = await self._queries.run(
                plan,
                account_id=query.account_id,
                today=query.today,
                reporting_timezone=query.reporting_timezone,
            )
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
    # The one where the honest answer has to explain why there is no figure. Everything about the
    # question was understood; the maximum is simply not something a plan can ask for, and a total
    # in its place would be a real number answering something else.
    "LARGEST_MOVEMENT": (
        "Todavía no sé buscar el movimiento más alto de un periodo, y no te doy el total en su "
        "lugar porque no es lo que has preguntado. Puedo darte los movimientos del periodo o el "
        "desglose por categoría."
    ),
}

# For a family with nothing specific to say, which today is none of them. It exists so adding a
# family to the guard cannot silently produce an empty refusal.
_CANNOT_YET = "Eso no puedo calcularlo todavía."


def _decline(planned: PlanningResult) -> AskFinancialQuestionResult:
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
        # Only a clarification carries one, and the client needs it to answer: without the draft
        # on the way out there is nothing to echo back, and the reply is a fresh question again.
        pending_context=_drafted(planned.draft) if planned.draft else None,
    )


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


def _drafted(draft: FinancialPlanDraft) -> SpokenPlan:
    """The draft in the plan's shape. One wire type, with the window empty — which is the
    difference between the two and the only one a client has to notice."""
    return SpokenPlan(
        direction=draft.direction.value,
        period=draft.period.period.value if draft.period else None,
        n=draft.period.n if draft.period else None,
        start=draft.date_range.start.isoformat() if draft.date_range else None,
        end=draft.date_range.end.isoformat() if draft.date_range else None,
        categories=list(draft.categories),
        merchants=list(draft.merchants),
        group_by=draft.group_by.value,
        metric=draft.metric.value,
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
