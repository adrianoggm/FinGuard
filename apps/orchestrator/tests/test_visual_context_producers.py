"""The two places a chart is remembered fill in how it came to be."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from support.visual_planners import BREAKDOWN_PROFILE, default_policy
from test_decisions_call_sites import turn_for
from test_visual_follow_up import CHARTED, FOLLOW_UP, PREVIOUS

from finguard_orchestrator.application.queries import ask_financial_question
from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
    _decided_visual,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_interpreter import interpret_visual
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.controllers.llm_controller import (
    _derived_context,
    _visual_only_response,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import VisualContextPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import _visualization_to_dto
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def _evidence_of(plan: object) -> tuple[UUID, ...]:
    return llm_mapper._evidence_of(plan)


async def _charted_result() -> AskFinancialQuestionResult:
    """A real turn, built the way `test_visualization_stage1._result` builds its own: an
    `AskFinancialQuestionResult` run through `_decided_visual` so it carries a real
    `visual_decision`. The one addition is `metric_definition` -- a `FinancialPlan` naming the two
    movements behind the figures -- which that helper never sets, because nothing before Task 4
    read it back off a chart's context."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
    )
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Resultado",
        plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
        groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
        visual_dataset=VisualDataset(
            rows=(VisualRow("Comida", 100), VisualRow("Casa", 50)), metric="SUM"
        ),
        metric_definition=plan,
    )
    return await _decided_visual(result, default_policy(), DEFAULT_PRESENTATION, "")


@pytest.fixture
async def charted_result() -> AskFinancialQuestionResult:
    return await _charted_result()


@pytest.fixture
def chart_response(charted_result: AskFinancialQuestionResult) -> object:
    """The `ChartVisualizationResponse` the mapper built for `charted_result`, the way
    `test_visualization_stage1` pairs its own real-turn result with `_visualization_to_dto`."""
    return _visualization_to_dto(charted_result)


@pytest.fixture
def restyle_turn() -> object:
    """Drives the restyle endpoint the way `test_visualization_stage2_core` does: a domain
    `Conversation` holding one `VisualContext`, a sentence `interpret_visual` turns into a patch,
    and `_visual_only_response` applying it.

    Two departures from that file's own pattern, both required by what this test checks.

    First, the sentence: `test_visualization_stage2_core` uses "ponlo en barras" and similar --
    the brief's "ponlo en horizontal" names no keyword `interpret_visual` recognises (its style
    vocabulary is barra/linea/area/apil.../ordena.../circular/donut/anillo), so it would never
    produce a patch. "ordénalo de menor a mayor" is a proven ascending-sort sentence, kept here so
    the fixture actually exercises a restyle instead of asserting on `None`. Fix round 1 is what
    lets a restyle over this spec verify at all: `SPEC` (the floor's own plan for
    `BREAKDOWN_PROFILE`) ranks its dimension with a per-encoding sort, and before the wire carried
    a field's `order`, a descending restyle over this exact spec was wrongly refused (`rule=
    "order"`) the moment the context had crossed the wire once -- `test_a_round_tripped_ranked_
    bar_still_verifies_the_descending_sort` (`test_visualization_stage2_core.py`) pins that a
    restyle now verifies. The direction asked for here is ascending rather than descending
    specifically because fix wave 2's F2 answers a *descending* restyle over this same
    already-descending `SPEC` with `VISUAL_UNCHANGED` and no derived context -- exactly what this
    fixture exists to produce one of.

    Second, the round trip: the conversation is serialised through `conversation_mapper.to_payload`
    and back through `to_domain` before it reaches `_visual_only_response`, because that is what
    the real endpoint receives -- a conversation from the wire, where `source_plan`/`evidence_ids`
    were never serialised (only `derived_from`/`turn_id` are, per Task 3). The response's own
    conversation is converted back to a domain `Conversation` the same way (rather than left as the
    wire `ConversationPayload`, whose `VisualContextPayload` never carried `decision`/`source_plan`/
    `evidence_ids` to begin with) so the test can read those fields at all.
    """
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(),),
    )
    initial_context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, source_plan=plan, evidence_ids=(uuid4(),)
    )
    conversation = IDLE.with_visual_context(initial_context)
    from_wire = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    intent = interpret_visual("ordénalo de menor a mayor", from_wire.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, from_wire)
    assert response.conversation is not None
    return SimpleNamespace(conversation=conversation_mapper.to_domain(response.conversation))


def test_a_fresh_chart_remembers_its_decision_plan_evidence_and_turn(
    charted_result, chart_response
) -> None:
    """`charted_result` is a fixture: an AskFinancialQuestionResult with a visual_decision and a
    FinancialPlan metric_definition whose transaction_ids are two UUIDs; `chart_response` is the
    ChartVisualizationResponse the mapper built for it. Build both the way test_visualization_stage1
    builds its real-turn fixture."""
    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.decision is charted_result.visual_decision
    assert context.source_plan is charted_result.metric_definition
    assert context.evidence_ids == charted_result.metric_definition.transaction_ids
    assert isinstance(context.turn_id, UUID)
    assert context.derived_from is None


def test_a_reshape_names_the_chart_it_came_from(charted_result, chart_response) -> None:
    """Task 4: a follow-up classified `ANALYTICAL_RESHAPE` while a chart was in context produces a
    context that names the chart it came from -- the newest one on the conversation BEFORE this
    turn's is appended."""
    previous = VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE)
    reshaped = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan"),
    )
    remembered = llm_mapper._remember_visual_context(reshaped, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from == "chart-0"


def test_a_reshape_over_an_untrusted_previous_names_no_origin(
    charted_result, chart_response
) -> None:
    """m9 (fix wave): a new, sealed chart used to be able to name an *untrusted* chart as its
    `derived_from` -- only the id crossed over (this turn's own dataset still built the new
    chart's spec and profile), but `derived_from` is what «el gráfico anterior» resolves through,
    so a legitimate chart's provenance could point at one the server never vouched for. Naming no
    origin when the previous chart is untrusted is the same refusal `UNRELATED` already gets
    below, applied to the one case that used to slip past it.
    """
    previous = replace(VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE), trusted=False)
    reshaped = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan"),
    )
    remembered = llm_mapper._remember_visual_context(reshaped, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


def test_an_unrelated_new_chart_names_no_origin(charted_result, chart_response) -> None:
    """The same chart-in-context setup as the reshape above, but classified `UNRELATED`: the new
    chart names no origin even though a previous chart exists."""
    previous = VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE)
    unrelated = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("UNRELATED", "none"),
    )
    remembered = llm_mapper._remember_visual_context(unrelated, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


async def test_the_handler_and_the_mapper_together_prove_derived_from(monkeypatch) -> None:
    """Fix round 1, Important 2. The two mapper tests above hand-set `visual_follow_up` with
    `replace(...)` and never run the handler; `test_visual_follow_up.py`'s handler tests read
    `result.visual_follow_up` and never run the mapper. Nothing before this test drove `stream()`'s
    convergence (`ask_financial_question.py`, the box read alongside `_decided_visual`) and
    `_remember_visual_context`'s `derived_from` read together -- the one seam this commit exists
    to join, and the one thing left unguarded: moving the convergence's `replace(...)` under a
    `visual_dataset is not None` guard, or attaching the box's value anywhere else, would leave
    every other test in this module green while the feature stayed dead.

    One real turn -- `"Solo restaurantes"` over `CHARTED` (which already holds `chart-1`) with
    `analysis=FOLLOW_UP` injected, exactly as `test_visual_follow_up.py`'s own seam tests inject it
    -- run three times with only the classification varied, monkeypatched the way
    `test_every_kind_is_a_record_row` (`test_visual_follow_up.py`) varies it: every case draws the
    identical real chart, and only `visual_follow_up.kind`'s effect on `derived_from` differs.
    `question_to_dto` is driven directly -- the composition `_visualization_to_dto` then
    `_remember_visual_context` that actually builds a `QuestionResponse` -- rather than chaining the
    two mapper functions by hand.
    """
    for kind, claimant, expected in (
        ("ANALYTICAL_RESHAPE", "follow_up_plan", "chart-1"),
        ("UNRELATED", "none", None),
        ("PRESENTATION_ONLY", "interpret_visual", None),
    ):
        monkeypatch.setattr(
            ask_financial_question,
            "classify_visual_follow_up",
            lambda query, analysis, conversation, kind=kind, claimant=claimant: VisualFollowUp(
                kind, claimant
            ),
        )
        result, _ = await turn_for(
            "Solo restaurantes", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP
        )
        response = llm_mapper.question_to_dto(result)
        assert response.conversation.visual_contexts[-1].derived_from == expected, kind


def test_two_charts_in_one_process_get_two_turn_ids(charted_result, chart_response) -> None:
    first = llm_mapper._remember_visual_context(charted_result, chart_response)
    second = llm_mapper._remember_visual_context(charted_result, chart_response)
    first_turn_id = first.conversation.visual_contexts[-1].turn_id
    second_turn_id = second.conversation.visual_contexts[-1].turn_id
    assert first_turn_id != second_turn_id


def test_a_plan_without_evidence_yields_no_ids() -> None:
    assert _evidence_of(object()) == ()
    assert _evidence_of(None) == ()


def test_a_restyled_chart_names_its_origin_and_keeps_its_plan(restyle_turn) -> None:
    """`restyle_turn` drives the restyle endpoint (llm_controller) with a conversation holding
    `VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, source_plan=<a FinancialPlan>,
    evidence_ids=(uuid4(),))` and a restyle sentence; it returns the QuestionResponse's
    conversation, converted back to a domain `Conversation` (see the fixture's docstring for why).

    `derived.source_plan is None` and `derived.evidence_ids == ()` look like a broken copy, but they
    are the correct outcome: the restyle endpoint never sees the single-turn context above -- it
    sees `from_wire`, already round-tripped through the wire once, where those two fields do not
    travel. Within a single turn they would be copied faithfully; across a turn boundary, on this
    wire, they cannot be. `derived.decision is None` for the same reason: `VisualContextPayload`
    carries no `decision` field at all, so the response read back through `conversation_mapper.
    to_domain` (this fixture's own last line) never carries one either -- Task 3's restyle chart
    IS a `VisualDecision(source="patch")` in the process that built it (`_derived_context`'s
    direct, in-process test in `test_visualization_stage2_core.py` proves that construction), it
    is only unobservable here because nothing about a decision travels this wire."""
    response = restyle_turn
    contexts = response.conversation.visual_contexts
    derived = contexts[-1]
    assert derived.derived_from == "chart-1"
    assert derived.turn_id is not None and derived.turn_id != contexts[0].turn_id
    assert derived.decision is None  # decision never crosses the wire -- see the docstring above
    assert derived.source_plan is None
    assert derived.evidence_ids == ()


def test_the_restyle_helper_copies_a_trusted_contexts_provenance_forward() -> None:
    """`restyle_turn` above proves the wire's own emptying of `source_plan`/`evidence_ids`, which
    means it cannot also prove the copy that runs before the wire ever sees them: in that fixture,
    `trusted_context.source_plan`/`.evidence_ids` are already `None`/`()` by the time
    `_derived_context` reads them, so deleting the copy and falling through to the dataclass
    defaults would leave every assertion there unchanged.

    `_derived_context` is `_visual_only_response`'s own copy step, pulled into a module-level
    function precisely so it can be driven directly with an in-process `VisualContext` that still
    carries real provenance -- no wire, no round trip, nothing to empty it first. This is the test
    that fails if `source_plan=trusted.source_plan, evidence_ids=trusted.evidence_ids` is deleted
    from its construction."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
    )
    trusted = VisualContext(
        "chart-1",
        SPEC,
        BREAKDOWN_PROFILE,
        source_plan=plan,
        evidence_ids=(uuid4(), uuid4()),
        turn_id=uuid4(),
    )
    derived = _derived_context(trusted, SPEC, "chart-2")
    assert derived.source_plan is trusted.source_plan
    assert derived.evidence_ids == trusted.evidence_ids
    assert derived.derived_from == trusted.chart_id
    assert isinstance(derived.turn_id, UUID) and derived.turn_id != trusted.turn_id
    assert derived.decision is None


# --- Task 3: sealed where it is born -----------------------------------------------------------


def test_a_fresh_chart_is_sealed_for_its_holder_and_unsealed_without_one(
    charted_result, chart_response
) -> None:
    """`question_to_dto` builds `seal` itself from the two keyword arguments it already takes and
    hands it to `_remember_visual_context`, so the context it mints is sealed before `to_payload`
    ever sees it -- signed at birth, not by a second pass over the wire afterward.

    With no holder to seal for -- every existing caller in this codebase's tests, and every turn
    on a channel/feature-flag combination the routes gate to `None` -- the context stays exactly
    as it always has: unsealed, and `trusted=True` in-process, which is `VisualContext`'s own
    default rather than anything this task has to set.
    """
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")

    sealed = llm_mapper.question_to_dto(charted_result, holder_reference="h", metric_tokens=tokens)
    (payload_context,) = sealed.conversation.visual_contexts[-1:]
    assert payload_context.token is not None
    assert tokens.verify_context(
        payload_context.token, "h", conversation_mapper.context_semantics(payload_context)
    )

    unsealed = llm_mapper.question_to_dto(charted_result)
    (plain_context,) = unsealed.conversation.visual_contexts[-1:]
    assert plain_context.token is None

    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    assert remembered.conversation.visual_contexts[-1].trusted is True


def test_chart_suggestions_offers_nothing_for_an_untrusted_context() -> None:
    """A chip offered over a context nobody can vouch for is a button that will answer
    `VISUAL_UNTRUSTED` the moment it is tapped -- `_chart_suggestions` says so up front rather than
    relying on `_visual_only_response`'s own refusal to happen to produce the same empty list."""
    untrusted = replace(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE), trusted=False)
    assert llm_mapper._chart_suggestions(untrusted) == []


def test_to_domain_is_called_only_where_a_caller_can_verify_it() -> None:
    """`to_domain` builds `VisualContext.trusted` from its `verify` callable, so a call with no
    `verify` trusts whatever the caller echoed with no check at all -- `to_domain`'s own default.
    Two call sites do this in `src/` today, and this pins both rather than either alone.

    `_conversation_of` is the one HTTP boundary: `request.conversation` is a caller's own claim
    about where a conversation was, and it is the one call bound to `verify=...`, closing over
    `who.holder_reference` and the app's own `metric_tokens` codec.

    `_compound_read_reply`'s own call is not a second boundary. Its argument
    (`rendered.conversation`) is `to_payload(result.conversation)`, built two lines earlier in the
    same function from a `Conversation` the query bus just returned inside *this* request --
    nothing a caller sent reaches it. The round trip exists to recover the richer domain types
    (`ChartSpec` and friends) `to_payload` just flattened, not to admit new data; every context it
    builds carries `token=None`, so binding a `verify` there would refuse it regardless of content
    and turn an internal implementation detail into a bug in the compound-question path.

    A third call site appearing anywhere else in `src/` -- verified or not -- fails this test until
    someone decides which of the two shapes it is, the way `test_otel.py`'s own import sweep pins
    where `opentelemetry` may be imported from.
    """
    import ast
    from pathlib import Path

    source_root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"

    class _Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []
            self.calls: list[tuple[str, int, str, bool]] = []

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name == "to_domain":
                enclosing = self.stack[-1] if self.stack else "<module>"
                verified = any(keyword.arg == "verify" for keyword in node.keywords)
                self.calls.append((self.relative, node.lineno, enclosing, verified))
            self.generic_visit(node)

    calls: list[tuple[str, int, str, bool]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        visitor = _Visitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        calls.extend(visitor.calls)

    verified_calls = [call for call in calls if call[3]]
    unverified_calls = [call for call in calls if not call[3]]

    assert [call[2] for call in verified_calls] == ["_conversation_of"], (
        f"to_domain called with verify= outside _conversation_of, or not at all: {verified_calls}"
    )
    assert [(call[0], call[2]) for call in unverified_calls] == [
        ("infrastructure/controllers/llm_controller.py", "_compound_read_reply")
    ], f"an unverified to_domain call site appeared where none is accounted for: {unverified_calls}"


def test_the_seal_is_never_silently_skipped() -> None:
    """Fix round 2 (I1): the verify side had an allowlist (the test above); the seal side did not,
    and that is exactly where C1 shipped -- `_compound_read_reply` called `question_to_dto(result)`
    with no `holder_reference`/`metric_tokens`, so a compound turn's own charts were born unsealed
    and refused their own restyle one turn later. This is the seal-side mirror.

    Every production call of `question_to_dto(` passes both `holder_reference=` and
    `metric_tokens=`, with one allowlisted exception: `_as_own_transfer`'s no-payments fallback.
    Both of its own callers (the two routes) reach it only when `result.outcome ==
    Spoken.TRANSFER_READY.value`, and a `TRANSFER_READY` result carries no `visual_dataset`, so
    `_remember_visual_context` returns before minting anything regardless of what this call passes
    -- sealing there would be a no-op, not a fix.

    Every `_visual_only_response(` call passes `seal=` -- no exceptions, because every restyle can
    mint a derived context that needs one.
    """
    import ast
    from pathlib import Path

    source_root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"
    allowed_unsealed_question_to_dto = {
        ("infrastructure/controllers/llm_controller.py", "_as_own_transfer"),
    }

    class _Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []
            self.question_to_dto_calls: list[tuple[str, str, frozenset[str]]] = []
            self.visual_only_response_calls: list[tuple[str, str, frozenset[str]]] = []

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            enclosing = self.stack[-1] if self.stack else "<module>"
            # m5 (fix wave): a keyword bound to a literal `None` does not count as passed -- both
            # callers' own guards (`question_to_dto`'s `if holder_reference is not None and
            # metric_tokens is not None:`, `llm_controller.py`'s save-candidate branch) already
            # treat it as absent, so `metric_tokens=None` written at the call site must fail this
            # allowlist the same way omitting the keyword entirely would.
            keywords = frozenset(
                keyword.arg
                for keyword in node.keywords
                if keyword.arg
                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
            )
            if name == "question_to_dto":
                self.question_to_dto_calls.append((self.relative, enclosing, keywords))
            elif name == "_visual_only_response":
                self.visual_only_response_calls.append((self.relative, enclosing, keywords))
            self.generic_visit(node)

    # m5: pinned directly against the visitor, since no live call site writes this shape --
    # `metric_tokens=None` (a literal), not the `metric_tokens if source is not None else None`
    # conditional D-3b-6 just removed. A keyword present only by name and bound to `None` must
    # still read as unsealed.
    synthetic = _Visitor("synthetic.py")
    synthetic.visit(
        ast.parse("def f():\n    question_to_dto(r, holder_reference=h, metric_tokens=None)\n")
    )
    (_, _, synthetic_keywords) = synthetic.question_to_dto_calls[0]
    assert "metric_tokens" not in synthetic_keywords, "a literal metric_tokens=None must not seal"

    question_to_dto_calls: list[tuple[str, str, frozenset[str]]] = []
    visual_only_response_calls: list[tuple[str, str, frozenset[str]]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        visitor = _Visitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        question_to_dto_calls.extend(visitor.question_to_dto_calls)
        visual_only_response_calls.extend(visitor.visual_only_response_calls)

    unsealed = {
        (relative, enclosing)
        for relative, enclosing, keywords in question_to_dto_calls
        if not {"holder_reference", "metric_tokens"} <= keywords
    }
    assert unsealed == allowed_unsealed_question_to_dto, (
        f"a question_to_dto( call site skips holder_reference=/metric_tokens= and is not on the "
        f"allowlist (or the allowlist names a site that now seals): {unsealed}"
    )
    assert visual_only_response_calls, "no _visual_only_response( call sites found -- test rotted"
    unsealed_restyles = [
        (relative, enclosing)
        for relative, enclosing, keywords in visual_only_response_calls
        if "seal" not in keywords
    ]
    assert not unsealed_restyles, (
        f"a _visual_only_response( call site passes no seal=: {unsealed_restyles}"
    )


def test_trusted_never_crosses_the_wire() -> None:
    """Fix round 2 (I2): `_compound_read_reply`'s own `to_domain(rendered.conversation)` call has
    no `verify=`, so every context it round-trips comes back `trusted=True`
    (`conversation_mapper.py`'s own default), including one that arrived untrusted. That
    relaundering is harmless only because nothing on the wire ever carries `trusted` in the first
    place -- pinned here directly rather than left as an inference from reading
    `_context_payload`.
    """
    assert "trusted" not in VisualContextPayload.model_fields
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, trusted=False)
    payload = conversation_mapper.to_payload(IDLE.with_visual_context(context))
    (item,) = payload.visual_contexts
    assert "trusted" not in item.model_dump()
