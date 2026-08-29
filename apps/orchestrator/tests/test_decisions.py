"""What a turn records, what it can never record, and what holds when nothing is listening.

Three things are being defended here and they are not the same thing.

**Privacy.** `test_logging.py` drives a real turn with a sentence full of things that must not be
logged and reads everything emitted. This is that control extended to the decision record, with one
addition: it does not wait for Section 2 to wire the call sites before it means anything. It drives
the real turn and then records, from inside it, the worst decisions a call site could record — the
holder's sentence in every free slot at every seam — and asserts the record comes out clean anyway.
Written before the sites that would violate it exist, which is the only time writing it is cheap.

**Unconditionality.** Six times in the last week this repository found an instrument that was green
because it had stopped executing the code it measured. Nothing here configures an exporter, and the
tests that matter most are the ones that would fail if recording ever learned to ask whether
anybody was listening.

**The ambient mechanism.** The handler is an async generator inside a `StreamingResponse`, and
`ContextVar` does not behave across that boundary the way a reading of `request_log` suggests. Those
behaviours are measured here rather than assumed, because the design rests on them.
"""

import ast
import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.observability import (
    REDACTED,
    Decision,
    Step,
    TurnRecord,
    TurnRecorder,
    Verdict,
    account_ref,
    current_turn,
    record,
    recording_turn,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)
THREE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=3)

# The same sentence `test_logging.py` uses, and for the same reason: what somebody typed, who they
# pay, and the note they wrote about it, in one line. Kept identical on purpose — the two records
# this service keeps have one prohibition between them, and a second vocabulary for it would be a
# second thing to keep in step.
PRIVATE = "¿cuánto le he pagado a Marta Solís Vega por el abogado en los últimos 3 meses?"

# The fragments, separately. A sweep for the whole sentence passes trivially the moment anything
# truncates, so the assertion is per-word.
FRAGMENTS = ("cuánto le he pagado", "Marta", "Solís", "Vega", "abogado", PRIVATE)

# The subset a call site could realistically hand to `record`, and that the shape check refuses on
# its own: prose and raw identifiers. The bare words above are deliberately not here — nothing
# mechanical separates "abogado" from "GROCERIES", and injecting them would be testing a claim this
# module explicitly does not make. See `test_a_bare_word_still_gets_through...` for that boundary.
PROSE = (
    PRIVATE,
    "cuánto le he pagado",
    "Marta Solís Vega",
    "regalo de Marta",
    "COMPRA TARJETA MERCADONA 4021 12/07",
    str(ACCOUNT),
)


class NoCards:
    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


class FakePlanner:
    def __init__(self, result: PlanningResult) -> None:
        self._result = result

    async def plan(self, sentence: str, *, context: PlanningContext):
        return self._result


class FakeLedger:
    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        return QueryAnswer(currency="EUR", total_minor=189_597, movement_count=12)

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        return None


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not this path")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def wired(planned: FinancialPlan) -> TestClient:
    handler = AskFinancialQuestionHandler(
        FakePlanner(PlanningResult.planned(planned)),
        FakeLedger(),
        accounts=FakeAccounts(),
        contacts=FakeContacts(),
        cards=NoCards(),  # type: ignore[arg-type]
    )
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
    app.state.questions_enabled = True
    return TestClient(app)


def everything_in(sealed: TurnRecord) -> str:
    """Every free-text slot of every decision, as one string to sweep."""
    return (
        "\n".join(
            "\t".join(
                str(part)
                for part in (
                    decision.step,
                    decision.verdict,
                    decision.field,
                    decision.was,
                    decision.now,
                    decision.detail,
                )
            )
            for decision in sealed.decisions
        )
        + f"\n{sealed.outcome}"
    )


# --- what never reaches a decision ---------------------------------------------------------


def test_nothing_the_holder_wrote_reaches_a_decision() -> None:
    """The control the whole design depends on, driven against a real turn.

    A sweep of an empty record proves nothing, and the record is empty until Section 2 lands. So
    this turn also plays the part of the call sites that do not exist yet, and plays them as badly
    as possible: the sentence, the contact, the concept, the movement description and the raw
    account id, pushed into `was`, `now` and `detail` at every one of the ten seams while a real
    request is in flight. The record still comes out with none of it, and it comes out that way
    because each value was *refused* — not because nothing ran.

    The sweep afterwards is over every fragment including the bare words, and it covers whatever
    the real pipeline recorded as well. Today that is nothing; the moment Section 2 wires a seam
    that puts a merchant name in `now`, this is the test that goes red.
    """
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    with recording_turn() as turn:
        response = client.post("/v1/questions", json={"question": PRIVATE})
        # The adversarial call sites. `was`, `now` and `detail` are the only slots a call site
        # fills from anything that passed near the sentence, so all three get the worst value.
        for step in Step:
            for leak in PROSE:
                record(step, Verdict.OVERRODE, changed=True, field="window", was=leak, now=leak)
                record(step, Verdict.AGREED, changed=False, detail=leak)
        turn.outcome = PlanningOutcome.PLANNED.value

    assert response.status_code == 200, "the turn has to have really run for this to mean anything"
    sealed = turn.sealed()
    assert sealed.decisions, "the sweep would pass an empty record without noticing"

    swept = everything_in(sealed)
    for secret in FRAGMENTS:
        assert secret not in swept, f"{secret!r} reached a decision"
    assert REDACTED in swept, "the sentence did not merely vanish, it was refused"


def test_a_raw_account_id_is_refused_rather_than_recorded() -> None:
    """The one item on the prohibited list with neither whitespace nor length to give it away.

    `account_ref` exists because pseudonymising beat trusting a call site. This makes forgetting
    to call it a redaction rather than a leak — the one rule here that is exact, not statistical.
    """
    leaked = record(Step.LEDGER, Verdict.AGREED, changed=False, field="account", now=str(ACCOUNT))
    ref = account_ref(ACCOUNT)
    proper = record(Step.LEDGER, Verdict.AGREED, changed=False, field="account", now=ref)

    assert leaked.now == REDACTED
    assert proper.now == account_ref(ACCOUNT)
    assert len(str(proper.now)) == 8


@pytest.mark.parametrize(
    "value",
    [
        PRIVATE,
        "regalo de Marta",
        "COMPRA TARJETA MERCADONA 4021 12/07",
        "Marta Solís Vega",
        "x" * 65,
        str(ACCOUNT),
    ],
)
def test_prose_and_identifiers_are_redacted(value: str) -> None:
    """A sentence, a concept, a movement description, a full name, an overlong token, an account."""
    assert record(Step.ENFORCE, Verdict.OVERRODE, changed=True, was=value).was == REDACTED


@pytest.mark.parametrize(
    "value",
    ["LAST_N_MONTHS:3", "CATEGORY", "NEEDS_CLARIFICATION", "_cumulative_plan", "qwen2.5:7b", "12"],
)
def test_the_services_own_vocabulary_survives(value: str) -> None:
    """The check has to let through everything the ten seams actually record, or it is a filter
    that makes the record useless rather than private."""
    assert record(Step.PLANNER, Verdict.AGREED, changed=False, now=value).now == value


def test_a_bare_word_still_gets_through_and_that_is_the_honest_limit() -> None:
    """**This test documents a gap on purpose.**

    Nothing mechanical can tell "abogado" the concept from "GROCERIES" the category — they are one
    unspaced word each. The shape check catches sentences, descriptions, full names and raw ids,
    and it stops exactly there. Asserting the gap keeps a later reader from believing the call
    sites are behind a filter and relaxing the discipline that actually covers this case.
    """
    assert record(Step.ENFORCE, Verdict.OVERRODE, changed=True, now="abogado").now == "abogado"


def test_the_step_and_the_verdict_cannot_carry_anything() -> None:
    """Two of the six slots are structurally incapable of leaking, which is half the reason they
    are enums. The other half is that Section 3 makes them metric labels."""
    assert len(Step) == 10, "the design argues ten seams; an eleventh should be a diff to justify"
    assert len({step.value for step in Step}) == 10, "two seams sharing a label share a time series"
    assert set(Verdict) == {
        Verdict.AGREED,
        Verdict.OVERRODE,
        Verdict.CLAIMED,
        Verdict.DECLINED,
        Verdict.FAILED,
    }


# --- `changed` is the point ------------------------------------------------------------------


def test_a_decision_cannot_be_recorded_without_saying_whether_it_mattered() -> None:
    """Keyword-only and no default. A rule that fires on every turn and alters nothing is noise,
    and a record that does not distinguish the two cannot tell you which one you have."""
    with pytest.raises(TypeError):
        record(Step.ENFORCE, Verdict.OVERRODE)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        Decision(step=Step.ENFORCE, verdict=Verdict.OVERRODE)  # type: ignore[call-arg]


def test_the_changed_flag_agrees_with_the_values_it_reports() -> None:
    """The invariant `Decision` deliberately does not enforce in `__post_init__`.

    Raising there fires on the data rather than the branch, so a miswired call site would pass
    every test whose fixtures happened to agree and take down a turn on the first one that did not.
    Swept here instead, where the same mistake is caught and nothing is at risk.
    """
    with recording_turn() as turn:
        record(
            Step.ENFORCE,
            Verdict.OVERRODE,
            changed=True,
            field="window",
            was="LAST_N_MONTHS:5",
            now="LAST_N_MONTHS:3",
        )
        record(Step.ENFORCE, Verdict.AGREED, changed=False, field="metric", was="SUM", now="SUM")
        record(Step.CAPABILITY_GUARD, Verdict.DECLINED, changed=True, field="family", now="AVERAGE")

    for decision in turn.sealed().decisions:
        if decision.was is not None and decision.now is not None:
            assert decision.changed is (decision.was != decision.now), (
                f"{decision.step} reports changed={decision.changed} "
                f"while moving {decision.was!r} to {decision.now!r}"
            )


def test_the_changed_decisions_are_readable_as_a_group() -> None:
    """The panel this design exists for, as a value: which rules altered the answer."""
    with recording_turn() as turn:
        record(Step.INTENT, Verdict.AGREED, changed=False)
        record(
            Step.ENFORCE,
            Verdict.OVERRODE,
            changed=True,
            field="window",
            was="LAST_N_MONTHS:5",
            now="LAST_N_MONTHS:3",
        )
        record(Step.RECOGNISER, Verdict.CLAIMED, changed=True, detail="_cumulative_plan")

    sealed = turn.sealed()
    assert len(sealed.decisions) == 3
    assert {d.step for d in sealed.changed} == {Step.ENFORCE, Step.RECOGNISER}


# --- assertable with nothing running ---------------------------------------------------------


def test_a_turn_records_that_enforce_overrode_the_window() -> None:
    """The sentence from the design document, as an executable assertion. No collector, no
    exporter, no Grafana — the record is a value and this is what reading it looks like."""
    with recording_turn() as turn:
        record(
            Step.ENFORCE,
            Verdict.OVERRODE,
            changed=True,
            field="window",
            was="LAST_N_MONTHS:5",
            now="LAST_N_MONTHS:3",
        )
        turn.outcome = PlanningOutcome.PLANNED.value

    (window,) = turn.sealed().where(Step.ENFORCE)
    assert window.verdict is Verdict.OVERRODE
    assert window.field == "window"
    assert (window.was, window.now) == ("LAST_N_MONTHS:5", "LAST_N_MONTHS:3")
    assert window.changed is True
    assert turn.sealed().outcome == PlanningOutcome.PLANNED.value


def test_recording_needs_nothing_configured() -> None:
    """No exporter, no settings, no import of anything that talks to a collector. If this ever
    needs setup, the call sites become code that only runs when somebody is watching."""
    import finguard_orchestrator.observability.decisions as module

    source = module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    # The imports, not the prose. The module argues about OpenTelemetry at length and must be free
    # to; what it may not do is import it. Matching on the word would make the comment the test.
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith("opentelemetry") for name in imported), (
        f"Section 3 owns the only module that imports OTel; this one imports {imported}"
    )

    with recording_turn() as turn:
        record(Step.OUTCOME, Verdict.AGREED, changed=False, now=PlanningOutcome.PLANNED.value)
    assert len(turn.sealed().decisions) == 1


def test_a_decision_outside_a_turn_is_dropped_and_still_built() -> None:
    """Dropped, because there is nowhere honest to put it. Still built, so the vocabulary check
    still runs — a call site passing something it should not is caught by a test that never
    starts a turn at all."""
    assert current_turn() is None

    dropped = record(Step.INTENT, Verdict.AGREED, changed=False, now=PRIVATE)

    assert dropped.now == REDACTED
    assert current_turn() is None


def test_a_sealed_record_cannot_grow_afterwards() -> None:
    """An exporter holding a record must be holding what the turn decided, not a live view of a
    recorder somebody else is still writing to."""
    with recording_turn() as turn:
        record(Step.INTENT, Verdict.AGREED, changed=False)
        sealed = turn.sealed()
        record(Step.OUTCOME, Verdict.AGREED, changed=False)

    assert len(sealed.decisions) == 1
    assert len(turn.sealed().decisions) == 2


def test_a_decision_is_frozen() -> None:
    """A decision is a thing that happened."""
    decision = record(Step.INTENT, Verdict.AGREED, changed=False)
    with pytest.raises(FrozenInstanceError):
        decision.changed = True  # type: ignore[misc]


# --- the ambient mechanism, measured rather than assumed ---------------------------------------


def test_the_recorder_is_ambient_and_restored() -> None:
    """Set and reset, the way `request_id` is. Nested because a turn inside a turn must not leave
    the outer one holding the inner one's decisions."""
    assert current_turn() is None
    with recording_turn() as outer:
        assert current_turn() is outer
        with recording_turn() as inner:
            assert current_turn() is inner
            record(Step.INTENT, Verdict.AGREED, changed=False)
        assert current_turn() is outer
        assert outer.sealed().decisions == ()
        assert len(inner.sealed().decisions) == 1
    assert current_turn() is None


def test_the_recorder_survives_being_reset_in_the_wrong_context() -> None:
    """The hazard `request_log` documents from the other side, and the reason the reset is guarded.

    Generators have no context of their own, so a `with recording_turn()` inside an async generator
    runs its `finally` during `aclose()` — and when the generator is abandoned rather than drained,
    that happens on whatever context the finaliser is on. `ContextVar.reset` raises there. An SSE
    client closing the tab mid-answer is exactly this, and it is not rare enough to leave to luck.

    Measured, not assumed: without the guard this raises `ValueError: Token was created in a
    different Context` and a clean disconnect becomes an exception during teardown.
    """

    held: list[TurnRecorder] = []

    async def turn() -> AsyncIterator[int]:
        with recording_turn() as recorder:
            held.append(recorder)
            record(Step.INTENT, Verdict.AGREED, changed=False)
            yield 1
            yield 2

    async def abandon() -> None:
        generator = turn()
        await generator.__anext__()
        # Closed from a different task, which is what a finaliser does.
        await asyncio.create_task(generator.aclose())

    asyncio.run(abandon())

    # The turn was abandoned, not lost: what it decided before the client went away is still
    # readable through the reference `recording_turn` handed out, which is the whole reason an
    # exporter is told to hold that and never to re-read the variable.
    (recorder,) = held
    assert len(recorder.sealed().decisions) == 1
    assert current_turn() is None, "and nothing leaked into the context that ran the test"


def test_decisions_recorded_in_a_child_task_reach_the_turn_that_owns_it() -> None:
    """The measurement the whole shape rests on.

    A `ContextVar` set in a child task is invisible to the parent, so a recorder that gets
    *replaced* would lose every decision the streaming path makes. A recorder that gets *appended
    to* does not, because the child inherits the parent's reference to the same object. That is why
    the ambient value is a mutable `TurnRecorder` and the frozen `TurnRecord` is a snapshot of it.
    """

    async def child() -> None:
        record(Step.PLANNER, Verdict.AGREED, changed=False, detail="qwen2.5:7b")

    async def parent() -> TurnRecord:
        with recording_turn() as turn:
            await asyncio.create_task(child())
        return turn.sealed()

    sealed = asyncio.run(parent())
    (planner,) = sealed.where(Step.PLANNER)
    assert planner.detail == "qwen2.5:7b"


def test_the_record_is_reached_by_reference_not_by_reading_the_variable() -> None:
    """The discipline that makes the guarded reset safe. An exporter that re-reads the ContextVar
    could attribute one holder's decisions to the next turn; one that holds what `recording_turn`
    handed it cannot, whatever the variable says."""
    with recording_turn() as turn:
        record(Step.INTENT, Verdict.AGREED, changed=False)
        held = turn
    assert current_turn() is None
    assert len(held.sealed().decisions) == 1


# --- the ten seams fit ------------------------------------------------------------------------


def test_every_seam_in_the_design_can_be_recorded_in_one_call() -> None:
    """Section 2's condition for touching the 7000-line file is one call per seam with no
    branching of its own. This is that condition, checked against the shape before the file is
    opened: each of the ten, with the facts the design's table says it carries.

    `planner` is the seam that added `detail`. It records an outcome *and* the model that produced
    it, and those are not a before and an after — without a slot for the actor either `now` becomes
    a free-for-all or planner needs two calls.
    """
    with recording_turn() as turn:
        record(
            Step.INTENT,
            Verdict.OVERRODE,
            changed=True,
            field="intent",
            was="FINANCIAL_QUERY",
            now="ACCOUNT_BALANCE",
        )
        record(Step.CAPABILITY_GUARD, Verdict.DECLINED, changed=True, field="family", now="AVERAGE")
        record(Step.RECOGNISER, Verdict.CLAIMED, changed=True, detail="_cumulative_plan")
        record(
            Step.PLANNER,
            Verdict.AGREED,
            changed=False,
            now=PlanningOutcome.PLANNED.value,
            detail="qwen2.5:7b",
        )
        record(
            Step.ENFORCE,
            Verdict.OVERRODE,
            changed=True,
            field="window",
            was="LAST_N_MONTHS:5",
            now="LAST_N_MONTHS:3",
        )
        record(
            Step.INHERITED_WINDOW,
            Verdict.OVERRODE,
            changed=True,
            field="window",
            now="LAST_N_MONTHS:5",
        )
        record(
            Step.METRIC_WITHOUT_A_BUCKET,
            Verdict.OVERRODE,
            changed=True,
            field="group_by",
            was="NONE",
            now="MONTH",
        )
        record(Step.PLAN_VERIFIER, Verdict.DECLINED, changed=True, detail="_category_misread")
        record(
            Step.LEDGER,
            Verdict.AGREED,
            changed=False,
            field="rows",
            now="12",
            detail=account_ref(ACCOUNT),
        )
        record(
            Step.OUTCOME,
            Verdict.AGREED,
            changed=True,
            now=PlanningOutcome.PLANNED.value,
            detail="figure",
        )
        turn.outcome = PlanningOutcome.PLANNED.value

    sealed = turn.sealed()
    assert {decision.step for decision in sealed.decisions} == set(Step), "all ten fit"
    assert all(REDACTED not in everything_in(TurnRecord((d,))) for d in sealed.decisions)
    assert len(sealed.changed) == 8, "eight of the ten altered the answer in this example"


# --- the package move -------------------------------------------------------------------------


def test_every_existing_import_still_works() -> None:
    """`observability.py` became `observability/`. The four call sites outside this package import
    `account_ref`, `configure_logging`, `request_id` and `JsonLines`, and none of them moved."""
    from finguard_orchestrator.observability import (  # noqa: F401
        JsonLines,
        account_ref,
        configure_logging,
        request_id,
    )

    assert request_id.get() == "-"
    assert len(account_ref(ACCOUNT)) == 8
    assert isinstance(JsonLines(), logging.Formatter)


def test_the_two_records_are_separate_modules() -> None:
    """`logging` is what a person greps, `decisions` is what a dashboard counts. Kept apart so the
    decision record could be added without reopening the module four other files import."""
    from finguard_orchestrator.observability import decisions as decisions_module
    from finguard_orchestrator.observability import logging as logging_module

    assert "record" not in dir(logging_module), "logging did not learn about decisions"
    assert isinstance(TurnRecorder(), TurnRecorder)
    assert decisions_module.REDACTED == REDACTED
