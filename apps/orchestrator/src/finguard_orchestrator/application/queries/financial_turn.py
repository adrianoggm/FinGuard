"""What an answer looks like: the result the graph's presenter builds and the edge maps to the wire.

It holds nothing that decides anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finguard_orchestrator.domain.models.chart import (
    VisualDataset,
    VisualDecision,
)
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
)
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan


@dataclass(frozen=True)
class SpokenGroup:
    key: str
    total_minor: int
    movement_count: int
    # Backend-derived value for the requested metric (never recomputed by clients).
    value_minor: int | None = None


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
    rank: int | None = None
    result_kind: str | None = None
    query_id: str | None = None
    artifact_id: str | None = None
    view_id: str | None = None


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
    transaction_id: str | None = None
    currency: str | None = None
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

    Two audiences read that line, so it is held twice. `plan` is the ledger's identifiers
    (`answer_writer.describe`): `stream()` records it on the turn, and the turn interpreter's prompt
    quotes the log back to a model whose measurements were taken against exactly that text.
    `plan_in_words` is the same line for the person (`answer_writer.plan_in_words`), and it is what
    the edge sends as the wire's `plan`. A result carrying only `plan` — a line already written in
    Spanish — is shown as it is.
    """

    outcome: str
    text: str
    question: str | None = None
    plan: str | None = None
    plan_in_words: str | None = None
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
    # The figure the answer's own sentence leads with -- `None` when it has no single one, and a
    # client that gets `None` renders the sentence, which was the whole answer either way.
    # `answer_writer.leading_figure` is where that rule is written down and why: the field and the
    # prose are two representations of one decision, and a client showing 2.270,42 € under a
    # sentence saying 7.755,92 € is what happens when they disagree. Never a signed difference --
    # `difference_minor` below carries those.
    total_minor: int | None = None
    # The signed change between two compared figures, `latest - earlier`, in the unit the
    # comparison measured. `None` unless the answer *is* a comparison of two operands, and also on
    # a counted one, where the change is movements rather than money and `_minor` would be a lie
    # about the unit.
    #
    # It exists because `total_minor` was carrying it. «¿He gastado más este mes que el anterior?»
    # answered `total_minor: -189597` beside groups summing to `+189597`, so a client rendering the
    # leading figure showed −1.895,97 € for a question about spending, under a sentence saying
    # "1.895,97 € mayor el mes pasado". Measured over HTTP in
    # `evaluation/planning/RESULTS-E2E.md`, which is where gate 1's *Evidence reconciliation* row
    # took its first real reading and failed 1 of 11 breakdowns.
    #
    # Its presence is also the only thing on the wire that says the rows in `groups` are the two
    # *operands* of a difference rather than *parts* of a total. Nothing else discriminates: the
    # `metric="COMPARISON"` marker stops at `VisualDataset` and never reaches `visualization`,
    # `plan` is prose no client parses, and `outcome` is `PLANNED` either way.
    #
    # Two other shapes were weighed and rejected.
    #
    # **Make `total_minor` the figure the groups add up to.** It would satisfy the reconciliation
    # property by construction, and it puts a number in the field that the sentence never says:
    # 1.000 € this month against 1.895,97 € last month would report 2.895,97 €. That is exactly
    # the failure `leading_figure` exists to prevent, so it trades a wrong sign for a wrong figure.
    #
    # **Take the operands out of `groups`.** That also satisfies the property, while leaving
    # `total_minor` a signed difference under a name that says total -- it silences the measurement
    # and keeps the client bug. And it silences it in the worst available way: `reconcilable()`
    # calls an empty breakdown *not inspectable*, so the answer would leave the denominator too and
    # "breakdowns reconciled" would fall from 11 to 10 with the failure gone from both halves of
    # the ratio. That is the vacuous zero `RESULTS-E2E.md` exists to prevent, one field lower.
    # Keeping the rows is also what a screen needs: the web renders them in `AnswerEvidence`, the
    # only place a person sees the two windows as data.
    #
    # **Teach the property that a comparison is a third shape, and change nothing here.** The
    # cheapest, and it concedes that one field means "a total", "a signed difference" and "a signed
    # net" depending on the answer -- the shape `docs/next.md` lists as a known defect under
    # "Discriminated responses". The property does have to learn: `CUMULATIVE` groups are running
    # totals and cannot sum to their total either, correctly, so its assumption was already wrong
    # on a path nobody disputes. But it had nothing on the wire to key on until this field existed.
    #
    # What it can key on now is a tag, not a hint, and it narrows one check rather than dropping
    # both: with `difference_minor` present the rows are operands and their amounts do not add up
    # to a total, while their *counts* still must -- 28 = 28 held on the measured case and holds on
    # every path here, because `movement_count` is the sum of both operands' counts.
    difference_minor: int | None = None
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
    explicit_visualization: bool = False
    visual_dataset: VisualDataset | None = None
    # Which chart was chosen for `visual_dataset`, and by whom. `None` is "no chart", and it is
    # the answer for a dataset the floor declined as much as for one nobody asked to draw.
    #
    # It is here because the decision used to be taken inside `llm_mapper`, twice per turn -- once
    # for the response DTO and again for the conversational state -- and a mapper is not somewhere
    # a model call can live. The application decides; the mapper renders what it is handed.
    visual_decision: VisualDecision | None = None
    # What this turn wanted from the chart it followed, in service vocabulary (kind + claimant) --
    # never the sentence. `None` when no chart was in context; set in `stream()`'s own convergence,
    # the same one that decides `visual_decision`, for the same reason: forty branches build a
    # result and this is one fact common to all of them, not one any branch is asked to know.
    visual_follow_up: VisualFollowUp | None = None
    # Internal, typed source for a save candidate. Never serialized as financial data.
    metric_definition: FinancialPlan | ComparisonPlan | RatioPlan | None = None
    # R14 (Wave 4.5, closes D-4.5-6/M-2): True when `metric_definition` came from
    # `conversation.last_plan` (`stream()`'s own back-fill, `~:630-633`) rather than from this
    # turn's own producer. A reader that only needs *a* plan (the save candidate, a drill's origin
    # check) keeps reading `metric_definition` regardless -- a borrowed plan is still a real plan,
    # just not necessarily the one that drew THIS chart. A reader that would NAME the plan in
    # prose -- the presenter's spoken noun -- must not when this is True, because naming a plan
    # nobody ran this turn is exactly M-2 (`branch-re-review.md`'s `probe_m2`).
    metric_definition_borrowed: bool = False
    # F-H3 (Wave 3 hardening, fix round): True when `metric_definition` was built by one of the
    # continuation readers (`contextual_follow_up_plan`, or one of the five comparison-continuity
    # readers) -- a reshape, regroup, split or refinement of whatever plan/comparison the
    # conversation already carried, as opposed to a fresh plan the sentence alone produced. This is
    # the causal fact `llm_mapper._remember_visual_context` now reads to decide `derived_from`: the
    # reader that built the plan states the lineage, and the mapper's own structural comparison
    # (`_same_lineage`) stays only as a sanity check on top of it, never the sole test. Two turns
    # that coincidentally ask about the same bare window are not lineage; a chart the reader
    # actually reshaped is, whatever verb the sentence used to ask for it.
    plan_continues_conversation: bool = False
    # A fingerprint of `metric_definition` (back-fill included), taken in `stream()` and fixed for
    # this turn -- the value `_remember_visual_context` seals onto a chart drawn THIS turn. Sealed
    # as data because 4.3's drill binds to it; not read by the presenter (see
    # `metric_definition_borrowed` above -- a digest of the same field cannot tell borrowed from
    # owned, only `stream()` itself can, which is why this stays a fingerprint and that is a flag).
    # `None` when `metric_definition` is not a `FinancialPlan` at all (a comparison, a ratio, or no
    # plan ran this turn).
    visual_plan_digest: str | None = None
