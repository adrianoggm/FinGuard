"""What this turn decided, and whether the decision changed the answer.

A latency waterfall answers "why did this request run and how long did it take", and that is not
the question anybody has asked here. Every defect reported in the last week was a figure that was
real and about the wrong question: a window the model invented and nothing overruled, a scope
phrase read as a grouping instruction, a clarification that dropped the metric the sentence had
stated. None of those is slow. All of them are *decisions* that went one way rather than the other.

So the unit recorded here is a decision, and the fact worth recording about it is `changed`. A rule
that fires on every turn and alters nothing is noise; a rule that fires rarely and always changes
the answer is where the risk lives. `changed` is keyword-only and has no default for that reason —
a call site cannot record a decision here without saying whether it mattered.

**This module records. It does not export.** Nothing here imports OpenTelemetry, and nothing here
asks whether anybody is listening. That is deliberate and it is the expensive lesson of the last
week: six times this repository found an instrument that was green because it had stopped executing
the code it measured — `PLANNED` at 0, a corpus with no account directory, an audit assertion
satisfied by a transaction that had already aborted. Instrumentation that only runs when a
collector is configured reproduces that on day one, because the configuration that makes it run is
exactly the configuration no test has. Recording is unconditional; only exporting is optional, and
the adapter that exports is somebody else's module.

The consequence worth having is that the record is a plain value. A unit test can assert "this turn
recorded that `enforce` overrode the window" with no collector, no exporter and no Grafana running.

**Nothing the holder wrote leaves this service.** Not the question, not a Bizum's concept, not a
contact's name, not a movement's description, not a raw account id. `logging` holds that list and
argues it. It applies here without exception, and `_own_words` below is the part of it that is
mechanical rather than hoped for — see the argument there for exactly how far mechanism reaches and
where discipline has to take over.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Final
from uuid import UUID

logger = logging.getLogger(__name__)


class Step(StrEnum):
    """The seams that decide something. Ten, not forty.

    An enum rather than a free string because Section 3 turns this into a metric label, and a
    label whose values are typed by hand at ten call sites is an unbounded-cardinality metric
    waiting for its first typo — `planner` and `planner ` are two time series that will never be
    summed and never be noticed. Bounding it here costs one import at the call site and makes
    `decisions_total{step}` a fixed-width thing a dashboard can enumerate.

    It also makes the list arguable. A seam earns a place here only because a defect has hidden in
    it or plainly could; adding an eleventh should be a diff somebody has to justify, not a string
    that appears one day in a panel nobody built.
    """

    INTENT = "intent"
    """Deterministic router versus model, and any fallback between them."""

    CAPABILITY_GUARD = "capability_guard"
    """Allowed or refused, and which family refused it."""

    RECOGNISER = "recogniser"
    """Which deterministic recogniser claimed the turn — `changed` means the model never ran."""

    PLANNER = "planner"
    """The planning outcome, the model that produced it, and any retry on invalid structure."""

    ENFORCE = "explicit_facts.enforce"
    """Per field: what the sentence stated against what the model produced."""

    INHERITED_WINDOW = "inherited_window"
    """Whether a continuation's silence was filled from the previous turn."""

    METRIC_WITHOUT_A_BUCKET = "metric_without_a_bucket"
    """Whether the pair was settled — `changed` means a refusal became an answer."""

    PLAN_VERIFIER = "plan_verifier"
    """Agreed or disagreed, and which rule disagreed."""

    LEDGER = "ledger"
    """What the bank returned, and whether the run crossed accounts."""

    OUTCOME = "outcome"
    """The turn's `PlanningOutcome`, and whether a figure was actually shown."""


class Verdict(StrEnum):
    """Which way a decision went.

    Bounded for the same reason `Step` is: this is the second label on the headline counter. Five
    words cover the ten seams because the seams all do one of five things — leave a value alone,
    replace it, take the turn, refuse the turn, or fail to produce anything at all.
    """

    AGREED = "agreed"
    """The step looked and left it alone. The common case, and the one that makes a `changed`
    rate meaningful: without the agreements in the denominator, an override rate is just a count."""

    OVERRODE = "overrode"
    """The step replaced what an earlier stage produced. `was` and `now` say with what."""

    CLAIMED = "claimed"
    """The step took the turn and the stages after it never ran."""

    DECLINED = "declined"
    """The step refused the turn. A refusal is a decision, and a wrong one is invisible in every
    instrument that only counts what was answered."""

    FAILED = "failed"
    """The step could not produce anything: no answer from the model, a malformed one, a bank
    that did not respond. Distinct from `declined`, which is this service choosing."""


REDACTED: Final = "<redacted>"
"""What a value becomes when it is not this service's own vocabulary. See `_own_words`."""

_LONGEST: Final = 64
"""The longest a value in this service's vocabulary gets. `LAST_N_MONTHS:3` is fifteen characters,
an `account_ref` is eight, the longest function name in the pipeline is comfortably inside this.
Generous on purpose: this bound exists to catch a sentence, not to police a name."""


def _own_words(value: str | None, *, step: str, slot: str) -> str | None:
    """A value, if it is plausibly this service's own vocabulary, and `REDACTED` if it is not.

    **This is not a filter that reads meaning, and it must not be mistaken for one.** It cannot
    tell a category name from a merchant name — nothing can, which is why the redaction rule in
    `logging` is a discipline at the call sites and why the control that actually holds is a test
    that drives a real turn. What this does is narrower and worth having anyway: it is a *shape*
    check, and the shapes it refuses are the ones that carry the bulk of what must never leave.

    A window is `LAST_N_MONTHS:3`. A grouping is `CATEGORY`. A recogniser is `_cumulative_plan`. An
    outcome is `NEEDS_CLARIFICATION`. Every one of them is one short token with no spaces in it.
    What a holder writes is not: "¿cuánto le he pagado a Marta Solís Vega por el abogado?" is a
    sentence, "regalo de Marta" is a concept, "COMPRA TARJETA MERCADONA 4021" is a description, and
    all three are spaced and long. Whitespace and length separate those two populations completely
    without either one needing to know what the words mean.

    A raw account id is the one item on the list that has neither, so it gets the one rule that is
    exact rather than statistical: a value that parses as a UUID is refused outright, because
    `account_ref` exists precisely so that no record ever carries one. Pseudonymising was judged
    better than trusting a call site once already; this makes forgetting it a redaction rather than
    a leak.

    **What still gets through is a single unspaced word**: "abogado", "Mercadona", "Marta". Nothing
    here would catch those and pretending otherwise would be worse than not trying, because a call
    site that believes it is behind a filter stops thinking. The test that drives a real turn is
    what covers them, and it is the reason this module was written before the call sites were.

    Scrubs rather than raises. `explicit_facts.enforce` carries the note explaining what raising
    from this kind of layer cost — it was total and never raised for exactly this reason — and the
    principle transfers with more force here: a bookkeeping call that can abort a turn has made the
    service less reliable in exchange for watching it, which is the trade this whole design refuses.
    The warning names the step and the slot and never the value, since logging what was too private
    to record would be an unusually direct way to lose the argument.
    """
    if value is None:
        return None
    if not value:
        return value
    offence = None
    if len(value) > _LONGEST:
        offence = "too long to be a name this service chose"
    elif any(character.isspace() for character in value):
        offence = "contains whitespace, so it is prose and not a name"
    else:
        try:
            UUID(value)
        except ValueError:
            pass
        else:
            offence = "is a raw identifier; account_ref() exists for this"
    if offence is None:
        return value
    logger.warning(
        "decision.redacted",
        extra={"step": step, "slot": slot, "reason": offence},
    )
    return REDACTED


@dataclass(frozen=True, slots=True)
class Decision:
    """One choice, in the service's own vocabulary, with the fact that makes it worth counting.

    Frozen because a decision is a thing that happened. A record you can edit after the turn is a
    record that has to be trusted rather than read, and the whole point of putting this in a value
    was that a test could assert on it directly.

    The field order is not the order the design document proposed. `changed` moved ahead of the
    optional half because it is the only way a dataclass can make it mandatory, and mandatory is
    the property that matters: `field`, `was` and `now` are absent for a whole-turn decision, but
    there is no decision anywhere in this pipeline for which "did it matter" has no answer.
    """

    step: Step
    verdict: Verdict
    changed: bool
    """Did this decision alter what the turn answered?

    The headline. Not "did something happen" — a step that ran and agreed did something, and it is
    the denominator, not the finding.

    **Invariant, held by the call sites and asserted in the tests rather than enforced here:** when
    `was` and `now` are both present they settle it, so `was != now` is `changed=True` and
    `was == now` is `changed=False`. Refusing the contradiction in `__post_init__` was the obvious
    move and was rejected: it fires on the data, not on the branch, so a site that hardcoded the
    wrong constant would pass every test whose fixtures happened to agree and raise in production
    on the first turn where they did not. That is a bookkeeping call taking down a bank turn, which
    is the trade this module exists to refuse. A test that sweeps the record catches the same
    mistake with none of the risk, so `tests/test_decisions.py` does that instead.
    """

    field: str | None = None
    """Which field this is about — "window", "metric", "group_by" — or None for a whole-turn
    decision. Also the aspect for a step that has no before and after: `capability_guard` refusing
    the AVERAGE family is `field="family"`, `now="AVERAGE"`."""

    was: str | None = None
    """What the value was before this step, in the service's own vocabulary and never the
    holder's words. None where the step decided about the turn rather than about a value."""

    now: str | None = None
    """What it is after. Same vocabulary, same prohibition."""

    detail: str | None = None
    """Who acted, when that is not the same fact as what changed — the model that planned, the
    family that refused, the recogniser that claimed, the rule that disagreed.

    This slot is the one addition to the proposed shape, and `planner` is the seam that forced it:
    it records an outcome *and* the model that produced it, and those are two facts, not a before
    and an after. Without somewhere to put the second, either `now` stops meaning "the value of
    `field`" and becomes a slot each step fills with whatever it likes, or the seam records two
    decisions and Section 2 stops being one call per seam — and Section 2 names one call per seam
    with no branching as the condition for touching the 7000-line file at all. Three more seams
    read better for the same reason: `capability_guard`, `plan_verifier` and `recogniser` all have
    an actor and no before-and-after, and keeping the actor out of `now` keeps `now` honest."""

    def __post_init__(self) -> None:
        # The three free-text slots, and nothing else, because they are the three a call site
        # fills from values that passed through the sentence. `step` and `verdict` are enums and
        # `field` is a literal the call site typed, so none of them can carry what a holder wrote.
        for slot in ("was", "now", "detail"):
            value = getattr(self, slot)
            checked = _own_words(value, step=self.step, slot=slot)
            if checked is not value:
                object.__setattr__(self, slot, checked)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """What one turn decided, sealed. The value Section 3 exports and a test asserts against."""

    decisions: tuple[Decision, ...] = ()
    outcome: str | None = None
    conversation: str | None = None
    """Which conversation this turn belongs to, as the opaque identifier the client round-trips.

    **It is an identifier and nothing else, and it is deliberately not a `Decision` slot.** A
    conversation id parses as a UUID, and `_own_words` refuses those outright — correctly, because
    the value that rule was written for is a raw *account* id. This one is minted by this service
    with `uuid4`, means nothing to anything outside a trace, and is derived from nothing the holder
    typed, which is the reason it can be a span attribute at all. Routing it through `record()`
    would only get it redacted, and a call site that worked around that redaction would be the
    first crack in the rule. So it lives on the record, `otel` stamps it on the `turn` span, and
    `_own_words` goes on refusing UUIDs everywhere it applies.
    """

    def where(self, step: Step) -> tuple[Decision, ...]:
        """Every decision one step made. A helper because the assertion a test wants to write is
        "what did `enforce` do to the window", and `[d for d in record.decisions if ...]` at forty
        call sites is forty chances to filter on the wrong thing."""
        return tuple(decision for decision in self.decisions if decision.step is step)

    @property
    def changed(self) -> tuple[Decision, ...]:
        """The decisions that altered the answer. The panel this design exists for, as a value."""
        return tuple(decision for decision in self.decisions if decision.changed)


@dataclass(slots=True)
class TurnRecorder:
    """The turn's record while the turn is still running.

    Mutable, and separate from `TurnRecord`, because of how `ContextVar` actually behaves rather
    than because two types are tidier. Measured, on this codebase's own streaming path:

        a value set in a parent context is visible to a child task     yes
        a value set in a child task is visible to the parent           no
        a mutation of an object the parent set is visible to the parent  yes

    The handler is an async generator consumed inside a `StreamingResponse`, so the decisions are
    recorded in a context the turn's owner cannot see writes to. Appending to an object the owner
    already holds is the one shape that crosses that boundary, which is why the ambient thing is a
    recorder that accumulates and not a record that gets replaced.
    """

    _decisions: list[Decision] = dataclass_field(default_factory=list)
    outcome: str | None = None
    conversation: str | None = None

    def append(self, decision: Decision) -> None:
        self._decisions.append(decision)

    def sealed(self) -> TurnRecord:
        """The turn as a value. Copies, so a record handed to an exporter cannot grow afterwards."""
        return TurnRecord(
            decisions=tuple(self._decisions),
            outcome=self.outcome,
            conversation=self.conversation,
        )


# The turn every decision in flight belongs to. A ContextVar rather than a constructor parameter
# for the reason `logging` already argues about the logger and the request id, and with more force:
# a recorder passed as an argument is a dependency every layer has to declare in order to be
# silent, and the layer here is the domain. `explicit_facts.enforce` takes a plan and some facts
# and returns a plan; giving it a telemetry parameter would make the purest function in the
# pipeline depend on the observability package to keep doing nothing.
#
# Default None rather than an empty recorder. A decision recorded outside a turn is dropped, and it
# should be: an empty recorder would silently accumulate whatever fired at import time or in a
# background task and hand it to the next thing that asked, which is how one holder's turn ends up
# carrying another's decisions.
_current: ContextVar[TurnRecorder | None] = ContextVar("turn_recorder", default=None)


def current_turn() -> TurnRecorder | None:
    """The recorder this turn is writing to, or None outside a turn."""
    return _current.get()


@contextmanager
def recording_turn() -> Iterator[TurnRecorder]:
    """Own a turn's record for the duration of the turn, and hand it back.

    **Use the recorder this yields. Never re-read the ContextVar to find out what to export.** That
    is the whole discipline, and the reason is the failure mode below: if the reset does not take,
    the variable can outlive the turn, and an exporter that trusts it would attribute one holder's
    decisions to the next turn that asked. An exporter that holds the reference it was given cannot
    make that mistake no matter what the variable says.

    **Set and reset in the frame that owns the turn, never inside the async generator.** Generators
    have no context of their own — PEP 568 was never implemented — so a `set` inside one leaks into
    whichever frame resumed it and outlives the generator. Worse, and measured: when an async
    generator is abandoned rather than drained, its `finally` runs during `aclose()` in whatever
    context the finaliser happens to be on, and `ContextVar.reset` raises
    `ValueError: Token was created in a different Context`. An SSE client closing the tab mid-answer
    is exactly that case, and it is not rare.

    So the reset below is guarded. Letting that `ValueError` out would replace a clean disconnect
    with an exception during teardown, which is the shape of bug `request_log` already documents
    from the other direction: it resets in a `finally` placed after both log records rather than
    around them, because getting the ordering wrong there left the one record anybody greps with no
    request id on it. Same hazard, opposite symptom — there the reset ran too early, here it can
    run somewhere else entirely.

    Swallowing it is safe only because of the rule in the first paragraph. The stale value is
    stranded in a context that is being torn down, the next turn sets its own recorder, and nothing
    reads the variable to decide what to export.
    """
    recorder = TurnRecorder()
    token: Token[TurnRecorder | None] = _current.set(recorder)
    try:
        yield recorder
    finally:
        try:
            _current.reset(token)
        except ValueError:
            # See above. The context this is unwinding is not the one the token came from, so
            # there is nothing here to restore and nobody left to read it.
            logger.debug("decisions.reset_across_contexts")


def record(
    step: Step,
    verdict: Verdict,
    *,
    changed: bool,
    field: str | None = None,
    was: str | None = None,
    now: str | None = None,
    detail: str | None = None,
) -> Decision:
    """Record one decision against the turn in flight, and return it.

    Unconditional. There is no `if enabled` here and there must never be one: the configuration
    that would switch this off is the configuration no test runs under, and a call site that only
    executes when a collector is listening is a call site nothing has ever executed. Outside a turn
    the decision is still *built* — so the vocabulary check still runs and a call site passing
    something it should not is still caught by a test that never starts a turn at all — and then
    dropped, because there is nowhere honest to put it.

    Returns the decision so a caller can assert on it without reaching for the record, which is
    what makes a focused unit test of one seam a two-line test.
    """
    decision = Decision(
        step=step,
        verdict=verdict,
        changed=changed,
        field=field,
        was=was,
        now=now,
        detail=detail,
    )
    recorder = _current.get()
    if recorder is not None:
        recorder.append(decision)
    return decision


def name_the_conversation(identifier: str) -> None:
    """Tell the turn in flight which conversation it belongs to.

    Called once per turn from the one place a wire payload becomes a `Conversation`. Idempotent by
    construction rather than by a guard: a compound question maps its own rendered payload back
    through the same function, and the identifier it carries is the one that came in.

    Not a `Decision`. See `TurnRecord.conversation` — an identifier is not a choice, it has no
    verdict, and `_own_words` would redact it on the way past anyway.
    """
    recorder = _current.get()
    if recorder is not None:
        recorder.conversation = identifier
