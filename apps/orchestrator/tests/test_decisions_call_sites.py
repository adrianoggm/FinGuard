"""The ten seams, each asserted on the decision it actually produces.

Section 1 built the record and proved it holds a decision. This is the file that proves the
pipeline *puts* one there — which is the half that rots. Six times in the last week this repository
found an instrument that was green because it had stopped executing what it measured, and the shape
of that failure here would be a `record` call behind a branch nothing takes any more: the seam
still compiles, the panel still draws, and the line is zero for a reason nobody notices.

So nothing below asserts that a call site exists. Every test drives the thing the seam is inside —
a real turn through the handler, a real planner reading a real model answer, a real HTTP request
through the streaming route — and then reads the record. A seam that stops being reached fails
here, and it fails naming the step that went quiet.

**`changed=False` gets as much room as `changed=True`.** A rule that fires on every turn and alters
nothing is the denominator, and it is also the case that is easy to record backwards: the obvious
mistake at every one of these seams is to write `changed=True` because the step *ran*. Half the
assertions here are agreements for that reason.

**Nothing here configures an exporter.** The record is a value, and reading it is what this looks
like. See `test_decisions.py` for the privacy control, which drives a real turn and sweeps
everything these seams record.
"""

import json
import pathlib
import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

import finguard_orchestrator
from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_query_bus,
    reporting_today,
    turn_interpreter,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
)
from finguard_orchestrator.domain.errors import BankingUnavailableError, ProviderFailedError
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import planning_context
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import (
    Deterministic,
    Hybrid,
    Promoted,
    Shadowed,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.planning.llm_query_planner import LlmQueryPlanner
from finguard_orchestrator.infrastructure.planning.plan_schema import NO_CATEGORY
from finguard_orchestrator.observability import (
    Step,
    TurnRecord,
    TurnRecorder,
    Verdict,
    account_ref,
    current_turn,
    recording_turn,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)
FIVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=5)
THREE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=3)

# The sentence the whole design is written around, in the one direction this service's reader can
# state. `explicit_facts` only ever reports `INCOME` — an expense verb is the unmarked case and
# reads as no statement at all — so a turn that states its direction *and* its window has to be an
# income question. That is a limit of the reader, not of the seam, and it is why the headline
# assertion below says «cobrado» rather than «gastado».
STATES_WINDOW_AND_DIRECTION = "¿Cuánto he cobrado en los últimos 3 meses?"

# Read deterministically, so the planner is never asked. `_cumulative_plan` claims it.
CLAIMED_BY_A_RECOGNISER = "Muéstrame mi gasto acumulado por mes este año"

CATEGORIES = ("GROCERIES", "TRANSPORT_FUEL", "HOUSING_RENT")

PLANNED_BODY: dict[str, Any] = {
    "outcome": "PLANNED",
    "question": "",
    "period": "LAST_MONTH",
    "n": 0,
    "from": "",
    "to": "",
    "direction": "EXPENSE",
    "categories": [NO_CATEGORY],
    "merchants": [],
    "groupBy": "NONE",
    "metric": "SUM",
}


# --- the doubles ------------------------------------------------------------------------------


class NoCards:
    async def all_of(self, account_id, **kwargs):
        return Wallet(since=date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


class FakePlanner:
    def __init__(self, result: PlanningResult) -> None:
        self._result = result
        self.asked: str | None = None

    async def plan(self, sentence, *, context):
        self.asked = sentence
        return self._result


class FakeLedger:
    """Answers with twelve movements, or refuses, depending on how it was built."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self._unavailable = unavailable
        self.plans: list[Any] = []

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self.plans.append(plan)
        if self._unavailable:
            raise BankingUnavailableError("banking")
        return QueryAnswer(
            currency="EUR",
            total_minor=189_597,
            movement_count=12,
            groups=(Group("Elena", 4_656, 2),),
        )

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        return None


class WatchingLedger(FakeLedger):
    """A ledger that notes which turn was open while it ran.

    `current_turn()` is the only handle a call site has, so watching it from inside the pipeline
    is how a test sees the record the *application* opened without reaching into the module that
    opened it.
    """

    def __init__(self, seen: list) -> None:
        super().__init__()
        self._seen = seen

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self._seen.append(current_turn())
        return await super().run(
            plan, account_id=account_id, today=today, reporting_timezone=reporting_timezone, **kw
        )


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not this path")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


class FixedInterpreter:
    """Reads every turn as one intent. Stands in for a model behind the port."""

    def __init__(self, intent: Intent) -> None:
        self._intent = intent

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        return self._intent


class BrokenInterpreter:
    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        raise RuntimeError("the daemon is not there")


class FakeProvider:
    """Returns consecutive canned answers, so the schema repair can be driven."""

    name = "fake"

    def __init__(self, *answers: str, failure: str | None = None) -> None:
        self._answers = iter(answers)
        self._failure = failure
        self.asked = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.asked += 1
        if self._failure is not None:
            raise ProviderFailedError(self.name, self._failure)
        return next(self._answers)


def planner_for(provider: FakeProvider, *, model: str = "qwen2.5:7b") -> LlmQueryPlanner:
    return LlmQueryPlanner(
        provider, model=model, temperature=0.0, max_output_tokens=256, categories=CATEGORIES
    )


def a_context():
    return planning_context(
        Conversation(),
        today=TODAY,
        reporting_timezone="Europe/Madrid",
        analysis=TurnAnalysis(
            intent=Intent.FINANCIAL_QUERY,
            relation=TurnRelation.NEW_REQUEST,
            focus=TurnFocus.NEW_REQUEST,
        ),
        continuation=None,
    )


def handler_for(plan: FinancialPlan | None, ledger: FakeLedger) -> AskFinancialQuestionHandler:
    planned = PlanningResult.planned(plan) if plan is not None else PlanningResult.unsupported("x")
    return AskFinancialQuestionHandler(
        FakePlanner(planned),
        ledger,
        accounts=FakeAccounts(),
        contacts=FakeContacts(),
        cards=NoCards(),
    )


async def turn_for(
    sentence: str,
    plan: FinancialPlan | None,
    *,
    ledger: FakeLedger | None = None,
    **extra: object,
) -> tuple[AskFinancialQuestionResult, TurnRecord]:
    """One whole turn through the handler, and what it recorded.

    `handle` drains the same generator the stream walks, so what this reads is what the streaming
    route records too — with the one difference the frame test below is about.
    """
    handler = handler_for(plan, ledger or FakeLedger())
    with recording_turn() as turn:
        result = await handler.handle(
            AskFinancialQuestionQuery(
                sentence=sentence,
                account_id=ACCOUNT,
                today=TODAY,
                **extra,  # type: ignore[arg-type]
            )
        )
    return result, turn.sealed()


def only(sealed: TurnRecord, step: Step):
    """The one decision a step made, insisting there is exactly one."""
    decisions = sealed.where(step)
    assert len(decisions) == 1, f"{step} recorded {len(decisions)} decisions, expected one"
    return decisions[0]


# --- the frame, which this section does not own ------------------------------------------------


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_no_call_site_in_this_section_opens_a_turn() -> None:
    """**The one structural guard here, and it is worth more than any of the assertions below.**

    The turn boundary is `RecordingTurns` in `observability/otel.py`, a raw ASGI middleware that
    wraps the whole request. If a seam in these four files also entered `recording_turn`, the two
    would *nest* — `test_decisions.py` pins that behaviour — and nesting is silent: the inner
    recorder would collect every decision of the turn and the outer one, the one that gets
    exported, would seal empty. Counters at zero, traces with no events, nothing raised anywhere.

    That is the exact failure this design was written to stop, so it is checked mechanically rather
    than left to whoever reads the diff next. A seam records; it never decides that a turn exists.
    """
    owned = (
        "application/queries/ask_financial_question.py",
        "domain/services/turn_interpreters.py",
        "infrastructure/planning/llm_query_planner.py",
        "infrastructure/controllers/llm_controller.py",
    )
    root = pathlib.Path(finguard_orchestrator.__file__).parent
    for name in owned:
        source = (root / name).read_text(encoding="utf-8")
        assert "recording_turn" not in source, f"{name} opens a turn; the middleware already did"


def wired(plan: FinancialPlan, *, interpreter: object | None = None) -> tuple[TestClient, list]:
    """The real application, with the pipeline's doubles and the real turn middleware.

    Returns the recorders the *application* opened, seen from inside the pipeline through
    `current_turn()` — which is the only handle a seam ever has, and therefore the right one to
    assert with. Nothing here replaces or wraps `recording_turn`.
    """
    seen: list[TurnRecorder | None] = []
    ledger = WatchingLedger(seen)
    handler = handler_for(plan, ledger)
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, handler)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    app.state.questions_enabled = True
    return TestClient(app), seen


@pytest.mark.parametrize("route", ["/v1/questions", "/v1/questions/stream"])
def test_the_seams_record_into_the_turn_the_application_opened(route: str) -> None:
    """One request, one record, and the seams are in it — on both routes.

    Read from inside the pipeline rather than from the middleware, because that is the only place
    a seam can see: `current_turn()` at the ledger is the same object the middleware opened around
    the whole request, and every decision the turn made is on it once the response is finished.
    """
    client, seen = wired(FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS))

    response = client.post(route, json={"question": STATES_WINDOW_AND_DIRECTION})

    assert response.status_code == 200
    if route.endswith("/stream"):
        response.read()
    assert seen and all(recorder is seen[0] for recorder in seen), "one request, one recorder"
    recorder = seen[0]
    assert recorder is not None, "the application opened no turn at all"
    sealed = recorder.sealed()
    assert sealed.outcome == PlanningOutcome.PLANNED.value
    assert {decision.step for decision in sealed.decisions} >= {
        Step.CAPABILITY_GUARD,
        Step.ENFORCE,
        Step.INHERITED_WINDOW,
        Step.PLAN_VERIFIER,
        Step.LEDGER,
        Step.OUTCOME,
    }


def test_the_turn_is_already_open_when_the_intent_is_read() -> None:
    """Why the boundary has to be outside the route function and not inside the handler.

    Both routes read the turn's intent in `turn_analyzer.analyze`, at the top of the route and
    before the query object exists — so a turn opened in `handle`, in `stream`, or in the SSE
    generator would drop the `intent` seam on every live turn while every unit test that drives an
    interpreter directly stayed green. A middleware wrapping the request is upstream of that call,
    and this is what says so: the intent decision and the ledger read are in one record.
    """
    interpreter = Hybrid(FixedInterpreter(Intent.FINANCIAL_QUERY), Deterministic())
    client, seen = wired(
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS), interpreter=interpreter
    )

    response = client.post("/v1/questions/stream", json={"question": STATES_WINDOW_AND_DIRECTION})
    response.read()

    recorder = seen[0]
    assert recorder is not None
    intent = only(recorder.sealed(), Step.INTENT)
    assert intent.detail == "hybrid", "the arrangement that read the turn, from before the route"
    assert intent.now == Intent.FINANCIAL_QUERY.value
    assert recorder.sealed().where(Step.LEDGER), "and the bank read is in the same record"


# --- 1 · intent -------------------------------------------------------------------------------


async def test_intent_records_the_model_widening_the_routers_reading() -> None:
    """`Hybrid` recovering a catch-all as a balance question. The model moved it, so it counts."""
    hybrid = Hybrid(FixedInterpreter(Intent.ACCOUNT_BALANCE), Deterministic())

    with recording_turn() as turn:
        read = await hybrid.interpret("cuanto dinero tng", conversation=Conversation())

    decision = only(turn.sealed(), Step.INTENT)
    assert read is Intent.ACCOUNT_BALANCE
    assert decision.verdict is Verdict.OVERRODE
    assert decision.changed is True
    assert (decision.field, decision.detail) == ("intent", "hybrid")
    assert decision.now == Intent.ACCOUNT_BALANCE.value


async def test_intent_records_the_agreement_when_the_model_is_not_even_asked() -> None:
    """The denominator. A turn the rules already claimed never reaches the model, and recording it
    as an agreement is what stops the override rate from being a bare count."""
    hybrid = Hybrid(FixedInterpreter(Intent.ACCOUNT_BALANCE), Deterministic())

    with recording_turn() as turn:
        read = await hybrid.interpret("gracias", conversation=Conversation())

    decision = only(turn.sealed(), Step.INTENT)
    assert read is Intent.COURTESY
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is False
    assert decision.was == decision.now == Intent.COURTESY.value


async def test_intent_records_a_model_that_failed_as_the_agreement_it_became() -> None:
    """A dead daemon is a fact about the experiment, not about this turn: the router's reading
    stands, nothing was altered, and `changed` says so. Recording a failure here would put a turn
    in the numerator of "the model disagreed" on the strength of the model saying nothing."""
    promoted = Promoted(BrokenInterpreter(), Deterministic())

    with recording_turn() as turn:
        read = await promoted.interpret("¿cuánto gasté en junio?", conversation=Conversation())

    decision = only(turn.sealed(), Step.INTENT)
    assert read is Intent.FINANCIAL_QUERY
    assert (decision.changed, decision.detail) == (False, "promoted")


async def test_intent_records_a_shadow_as_changing_nothing_because_it_cannot() -> None:
    """Nothing the shadow says reaches the user. That is the safety property of running one against
    real traffic, and a record claiming otherwise would misrepresent it."""
    shadowed = Shadowed(Deterministic(), FixedInterpreter(Intent.PAYMENT))

    with recording_turn() as turn:
        read = await shadowed.interpret("¿cuánto gasté en junio?", conversation=Conversation())

    decision = only(turn.sealed(), Step.INTENT)
    assert read is Intent.FINANCIAL_QUERY
    assert decision.changed is False
    assert decision.now == Intent.FINANCIAL_QUERY.value
    assert decision.detail == "shadowed"


# --- 2 · capability_guard ---------------------------------------------------------------------


async def test_capability_guard_records_the_family_that_refused() -> None:
    """A refusal is a decision, and a wrong one is invisible in every instrument that only counts
    what was answered. The rule's own name goes in `now` — this service's vocabulary, not the
    sentence's."""
    result, sealed = await turn_for(
        "Haz una previsión de mis gastos del año que viene",
        FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS),
    )

    decision = only(sealed, Step.CAPABILITY_GUARD)
    assert result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert decision.verdict is Verdict.DECLINED
    assert decision.changed is True
    assert decision.field == "family"
    assert decision.now is not None and decision.now.isupper()


async def test_capability_guard_records_the_turns_it_let_through() -> None:
    """Without the agreements in the denominator an override rate is just a count."""
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    decision = only(sealed, Step.CAPABILITY_GUARD)
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is False
    assert decision.now is None, "no family refused it"


# --- 3 · recogniser ---------------------------------------------------------------------------


async def test_recogniser_records_which_reader_claimed_the_turn() -> None:
    """The panel this seam exists for: how many turns the model never saw.

    `detail` is the reader's own function name, which is why it survives `_own_words` — one
    unspaced token this service chose, and nothing the holder typed.
    """
    planner = FakePlanner(
        PlanningResult.planned(FinancialPlan(direction=Direction.EXPENSE, period=FIVE_MONTHS))
    )
    handler = AskFinancialQuestionHandler(
        planner,
        FakeLedger(),
        accounts=FakeAccounts(),
        contacts=FakeContacts(),
        cards=NoCards(),
    )

    with recording_turn() as turn:
        await handler.handle(
            AskFinancialQuestionQuery(
                sentence=CLAIMED_BY_A_RECOGNISER, account_id=ACCOUNT, today=TODAY
            )
        )

    decision = only(turn.sealed(), Step.RECOGNISER)
    assert planner.asked is None, "the whole point: the model was never called"
    assert decision.verdict is Verdict.CLAIMED
    assert decision.changed is True
    assert decision.detail == "_cumulative_plan"


async def test_recogniser_records_nothing_when_the_model_ran() -> None:
    """A reader that returned None has not decided anything — it declined to read. Twenty "not
    mine" rows per turn would bury the one row that matters, so the absence is the record."""
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    assert sealed.where(Step.RECOGNISER) == ()
    assert sealed.where(Step.OUTCOME), "and the turn is still counted, once, by its outcome"


# --- 4 · planner ------------------------------------------------------------------------------


async def test_planner_records_the_outcome_and_the_model_that_produced_it() -> None:
    """Two facts, two slots. `now` is what it decided and `detail` is who decided it — the seam
    that made `detail` exist, because those are not a before and an after."""
    provider = FakeProvider(json.dumps(PLANNED_BODY))

    with recording_turn() as turn:
        result = await planner_for(provider).plan("da igual", context=a_context())

    decision = only(turn.sealed(), Step.PLANNER)
    assert result.outcome is PlanningOutcome.PLANNED
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is False, "one attempt, no repair"
    assert (decision.now, decision.detail) == (PlanningOutcome.PLANNED.value, "qwen2.5:7b")


async def test_planner_records_a_schema_repair_as_the_thing_that_changed_the_answer() -> None:
    """Constrained decoding is a property of the runtime, not a guarantee of the pipeline. When it
    stops holding, the repair is what turns an `INVALID_STRUCTURE` turn into an answered one — so
    that, and not the fact the planner ran, is what `changed` means here."""
    provider = FakeProvider("not json at all", json.dumps(PLANNED_BODY))

    with recording_turn() as turn:
        result = await planner_for(provider).plan("da igual", context=a_context())

    decision = only(turn.sealed(), Step.PLANNER)
    assert provider.asked == 2
    assert result.outcome is PlanningOutcome.PLANNED
    assert decision.changed is True
    assert decision.verdict is Verdict.AGREED


async def test_planner_records_a_provider_that_said_nothing_as_failed() -> None:
    """`FAILED` and not `DECLINED`: a daemon that is not there is not this service choosing."""
    with recording_turn() as turn:
        result = await planner_for(FakeProvider(failure="down")).plan(
            "da igual", context=a_context()
        )

    decision = only(turn.sealed(), Step.PLANNER)
    assert result.outcome is PlanningOutcome.PROVIDER_ERROR
    assert decision.verdict is Verdict.FAILED
    assert decision.changed is False


# --- 5 · explicit_facts.enforce ---------------------------------------------------------------


async def test_enforce_records_per_field_which_the_sentence_won_and_which_it_did_not() -> None:
    """**The seam this design exists for, as one assertion.**

    One turn, two fields, two different answers: the sentence overruled the model on the window and
    agreed with it on the direction. A single `enforce: overrode` row for the turn could not say
    that, which is why this seam is per field and not per turn — and it is exactly the defect that
    started all of this, «los últimos 3 meses» answered over five, readable from the record without
    reproducing the conversation.
    """
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS, metric=Metric.SUM),
    )

    by_field = {decision.field: decision for decision in sealed.where(Step.ENFORCE)}
    assert set(by_field) == {"window", "direction"}, "only the fields the sentence stated"

    window = by_field["window"]
    assert window.verdict is Verdict.OVERRODE
    assert (window.was, window.now) == ("LAST_N_MONTHS:5", "LAST_N_MONTHS:3")
    assert window.changed is True

    direction = by_field["direction"]
    assert direction.verdict is Verdict.AGREED
    assert direction.was == direction.now == Direction.INCOME.value
    assert direction.changed is False


async def test_enforce_records_nothing_about_a_field_the_sentence_never_mentioned() -> None:
    """A rule that did not run did not agree. Recording one would give the override rate a
    denominator of turns the rule was never asked about, which flatters it on every panel."""
    _, sealed = await turn_for(
        "¿Cuánto he gastado en los últimos 3 meses?",
        FinancialPlan(direction=Direction.EXPENSE, period=FIVE_MONTHS, group_by=GroupBy.CATEGORY),
    )

    assert {decision.field for decision in sealed.where(Step.ENFORCE)} == {"window"}


async def test_enforce_never_records_what_a_category_is() -> None:
    """`enforce` moves categories too, and they are deliberately not a field here.

    `_own_words` is a shape check and says plainly that it cannot tell «abogado» from GROCERIES —
    both are one unspaced word. So the discipline has to hold at the call site, and this is the
    test that says it does: a plan full of categories records four slots and not one of them is a
    category name.
    """
    _, sealed = await turn_for(
        "¿Cuánto he cobrado en los últimos 3 meses de alquiler?",
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS, categories=("HOUSING_RENT",)),
    )

    recorded = " ".join(
        str(part)
        for decision in sealed.decisions
        for part in (decision.field, decision.was, decision.now, decision.detail)
    )
    assert "HOUSING_RENT" not in recorded
    assert "alquiler" not in recorded


# --- 6 · inherited_window ---------------------------------------------------------------------


async def test_inherited_window_records_a_silence_filled_from_the_previous_turn() -> None:
    """Silence is not a statement, so `enforce` leaves it alone and this is the step that reads it.
    A continuation naming no period runs over the window of the turn it continues, and that
    replacement is exactly the kind of thing that used to happen with nothing recording it."""
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        only_account_id=ACCOUNT,
    )
    # The window the model invented for a sentence that named none: a single day, the reference
    # date. Left standing, the answer is one day of income under a headline about last month —
    # `validation/t_follow_direction`, and the E2E harness reported the same collapse.
    invented = FinancialPlan(
        direction=Direction.INCOME,
        date_range=AbsoluteRange(TODAY, TODAY),
        only_account_id=ACCOUNT,
    )
    handler = handler_for(invented, FakeLedger())

    with recording_turn() as turn:
        await handler.handle(
            AskFinancialQuestionQuery(
                sentence="¿Y lo que cobré?",
                account_id=ACCOUNT,
                today=TODAY,
                conversation=Conversation(last_plan=previous),
            )
        )

    sealed = turn.sealed()
    inherited = only(sealed, Step.INHERITED_WINDOW)
    assert inherited.field == "window"
    assert inherited.verdict is Verdict.OVERRODE
    assert inherited.changed is True
    assert (inherited.was, inherited.now) == ("2026-08-04..2026-08-04", "LAST_MONTH")

    # And the seam next to it agreed, in the same turn. The sentence stated its direction and said
    # nothing about a window, so `enforce` had one field to impose and imposed nothing; the window
    # was not its business at all. Two adjacent rules, two different answers, both recorded.
    direction = only(sealed, Step.ENFORCE)
    assert (direction.field, direction.changed) == ("direction", False)


async def test_inherited_window_agrees_when_the_model_already_had_the_right_window() -> None:
    """The case identity gets wrong. `inherited_window` builds a new plan whenever it fills a
    silence, and the merge has usually already carried the same window down — so `is not` would
    report an inheritance on turns where no figure could have moved, and the `changed` flag would
    then disagree with the `was` and `now` printed beside it."""
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    decision = only(sealed, Step.INHERITED_WINDOW)
    assert decision.was == decision.now == "LAST_N_MONTHS:3"
    assert decision.changed is False
    assert decision.verdict is Verdict.AGREED


# --- 7 · metric_without_a_bucket --------------------------------------------------------------


async def test_a_refusal_becoming_an_answer_is_recorded_as_the_override_it_is() -> None:
    """A running total with no bucket to accumulate over used to be an `InvalidValueError`. It is
    settled now, and settling it changes how one number is presented — so it is an override, and
    counting it is how anybody finds out how often the model asks for the impossible pair."""
    body = {**PLANNED_BODY, "metric": "CUMULATIVE", "groupBy": "NONE"}

    with recording_turn() as turn:
        result = await planner_for(FakeProvider(json.dumps(body))).plan(
            "da igual", context=a_context()
        )

    decision = only(turn.sealed(), Step.METRIC_WITHOUT_A_BUCKET)
    assert result.plan is not None and result.plan.metric is Metric.SUM
    assert decision.verdict is Verdict.OVERRODE
    assert decision.changed is True
    assert (decision.was, decision.now) == (Metric.CUMULATIVE.value, Metric.SUM.value)
    assert decision.detail == GroupBy.NONE.value


async def test_a_pair_that_needed_no_settling_is_recorded_as_agreed() -> None:
    """Every planned turn passes through this step. If only the overrides were recorded the rate
    would be one, which reads as "this always fires" rather than "this almost never has to"."""
    with recording_turn() as turn:
        await planner_for(FakeProvider(json.dumps(PLANNED_BODY))).plan(
            "da igual", context=a_context()
        )

    decision = only(turn.sealed(), Step.METRIC_WITHOUT_A_BUCKET)
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is False
    assert decision.was == decision.now == Metric.SUM.value


# --- 8 · plan_verifier ------------------------------------------------------------------------


async def test_plan_verifier_records_the_rule_that_disagreed() -> None:
    """The plan is well formed, the domain accepts it, and it answers a different question from the
    one asked — banking would return a real figure. `detail` is the rule's own name."""
    _, sealed = await turn_for(
        "¿Cuánto gasté en gasolina en los últimos 3 meses?",
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=THREE_MONTHS,
            categories=("GROCERIES",),
            metric=Metric.SUM,
        ),
    )

    decision = only(sealed, Step.PLAN_VERIFIER)
    assert decision.verdict is Verdict.DECLINED
    assert decision.changed is True
    assert decision.detail is not None and " " not in decision.detail


async def test_plan_verifier_records_its_agreement_on_an_ordinary_turn() -> None:
    """The overwhelming majority, and the reason a disagreement rate means anything."""
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    decision = only(sealed, Step.PLAN_VERIFIER)
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is False
    assert decision.detail is None


# --- 9 · ledger -------------------------------------------------------------------------------


async def test_ledger_records_what_the_bank_returned_and_never_which_account() -> None:
    """Rows, and the account as eight hex characters. `account_ref` exists because pseudonymising
    beat trusting a call site, and the record is where forgetting it would have been a leak."""
    _, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    decision = only(sealed, Step.LEDGER)
    assert decision.verdict is Verdict.AGREED
    assert (decision.field, decision.now) == ("rows", "12")
    assert decision.detail == account_ref(ACCOUNT)
    assert str(ACCOUNT) not in str(decision.detail)


async def test_ledger_records_a_bank_that_did_not_answer_as_failed() -> None:
    """`FAILED` is for a bank that did not respond, and it is the read that has to say so: by the
    time the turn's outcome is written the answer is a sentence apologising, and how many reads
    were attempted behind it is gone."""
    ledger = FakeLedger(unavailable=True)

    result, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
        ledger=ledger,
    )

    reads = sealed.where(Step.LEDGER)
    assert reads, "a read was attempted and it failed; that is a decision"
    assert all(decision.verdict is Verdict.FAILED for decision in reads)
    assert all(decision.now is None for decision in reads)
    assert result.outcome != PlanningOutcome.PLANNED.value


async def test_every_read_of_a_turn_is_recorded_and_not_only_the_first() -> None:
    """The reason this seam is a wrapper round the port and not a call at the reads.

    Sixteen call sites run the ledger and only two of them are on the ordinary answer path; a
    comparison runs two queries, and the ratio operands run two more. A `record` beside one of
    them would report a figure that is right for the common turn and silently short for the ones
    that cost the most.
    """
    ledger = FakeLedger()
    _, sealed = await turn_for(
        "¿Cuánto he cobrado este mes comparado con el mes pasado?",
        FinancialPlan(direction=Direction.INCOME, period=THREE_MONTHS),
        ledger=ledger,
    )

    assert len(ledger.plans) == len(sealed.where(Step.LEDGER)), (
        "one decision per read the bank actually served"
    )


# --- 10 · outcome -----------------------------------------------------------------------------


async def test_outcome_records_the_turn_and_whether_a_figure_was_shown() -> None:
    """`changed` is the fact `outcome` cannot carry. `PLANNED` with no figure is a plan that ran
    and answered nothing, and it counts as a success in every instrument that reads the outcome
    on its own."""
    result, sealed = await turn_for(
        STATES_WINDOW_AND_DIRECTION,
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
    )

    decision = only(sealed, Step.OUTCOME)
    assert result.total_minor == 189_597
    assert decision.now == PlanningOutcome.PLANNED.value
    assert decision.verdict is Verdict.AGREED
    assert decision.changed is True
    assert sealed.outcome == PlanningOutcome.PLANNED.value


async def test_outcome_records_a_refusal_as_a_decision_that_showed_no_figure() -> None:
    """A refusal is a decision. This is the row that keeps a turn nobody answered in the record at
    all — and `changed=False`, because nothing was shown."""
    result, sealed = await turn_for(
        "Haz una previsión de mis gastos del año que viene",
        FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS),
    )

    decision = only(sealed, Step.OUTCOME)
    assert result.outcome == PlanningOutcome.UNSUPPORTED.value
    assert decision.verdict is Verdict.DECLINED
    assert decision.changed is False


async def test_every_turn_records_exactly_one_outcome() -> None:
    """The denominator of every rate in this design. `_steps` ends in a dozen places and a second
    row here would be a turn counted twice on every panel."""
    for sentence, plan in (
        (
            STATES_WINDOW_AND_DIRECTION,
            FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS),
        ),
        (CLAIMED_BY_A_RECOGNISER, FinancialPlan(direction=Direction.EXPENSE, period=FIVE_MONTHS)),
        (
            "Haz una previsión de mis gastos del año que viene",
            FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS),
        ),
        ("gracias", FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS)),
    ):
        _, sealed = await turn_for(sentence, plan)
        assert len(sealed.where(Step.OUTCOME)) == 1, sentence


# --- recording is unconditional ----------------------------------------------------------------


async def test_the_seams_run_with_no_turn_open_at_all() -> None:
    """Nothing here asks whether anybody is listening, and there is no turn to listen with.

    The configuration that would switch recording off is the configuration no test runs under, so
    a seam that only executed inside a turn would be a seam nothing had ever executed on the paths
    that matter. Outside a turn the decision is still built — the vocabulary check still runs —
    and then dropped.
    """
    handler = handler_for(
        FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS), FakeLedger()
    )

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence=STATES_WINDOW_AND_DIRECTION, account_id=ACCOUNT, today=TODAY
        )
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert result.total_minor == 189_597
