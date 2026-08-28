"""Measures the whole path: question over HTTP, figure and sentence back.

    uv run python scripts/evaluate_e2e.py --token demo-token-laura-0001

The plan benchmark stops where the plan is built. The COUNT defect lived past that line —
right plan, wrong kind of figure in the sentence a person reads — and it passed 235 cases
and 163 unit tests, because every layer was correct in isolation and nothing followed a
value to the end.

This drives the deployed service over HTTP rather than the handler in-process, so the
credential, the DTOs, the mappers and the real banking client are all under test. What it
cannot test is what a single seeded holder cannot express: cross-account containment needs
a second holder in the data.

It asserts properties, not figures. `movementCount: 3` is true of one seed on one date;
"a COUNT plan is answered with a count" is true of every seed on every date, and it is
what was broken.

Two things below exist because this harness spent a week reporting zeros it had not earned.
`SETTLED` answers the account question the service asks before any unqualified figure, so the
properties have answers to run on at all; `ANSWERING_FLOOR` makes a run that produced too few of
them a failure rather than a clean sheet. Read both comments before changing either — the defect
they describe was invisible precisely because it looked like a pass.

The rest of that audit is closed here, and it was all one shape: *a check that passes because the
thing it reads is absent.* `corpus_vacuity` gives the conversation path the guard the read sets
already had. `preceding` sends the fifteen cases that declare a preceding turn the turn they
declare, instead of asking them cold. `ANSWER_WITHOUT_A_FIGURE` is the per-case mirror of
`FIGURE_WITHOUT_AN_ANSWER`. `expect_shape` now says `NOT_PLANNED_SO_NO_SHAPE` where it used to say
nothing, like its two siblings. `sliced` stops reading `--limit 0` as *no limit*. And
`_reconciliation` reports a group with no figure in it rather than raising `KeyError` and taking
the run with it.

None of them was fixed by relaxing what is asserted. Where closing one turns a green case red,
that red is the finding.
"""

import argparse
import asyncio
import math
import re
import sys
from pathlib import Path
from typing import Any

import httpx2 as httpx
import yaml

PLANNING = Path(__file__).resolve().parents[1] / "evaluation" / "planning"

# A figure in Spanish notation with a currency marker. Presence in the first line is what
# separates "gastaste 449,62 €" from "8 movimientos".
_MONEY = re.compile(r"\d[\d.]*,\d{2}\s*(€|[A-Z]{3})")

# Outcomes that are an answer and may therefore carry a figure. PLANNED was the only one when the
# rule below was written; BALANCE is one too, and it arrives with the figure it is about. The rule
# is about a *decline* carrying a number, which is the shape where the text says no and the payload
# says 10.370,42.
_ANSWERING = frozenset({"PLANNED", "BALANCE"})


# --- the account question these three sets could not answer ---------------------------------
#
# Laura holds three accounts, so `b928d90` (2026-08-21) made every question that names no account
# ask which one before answering anything. 119 of the 120 dev questions name none. From that day
# this harness measured 235 clarifications and zero answers, and every property above is guarded on
# an answer existing — so the suite went green by testing nothing, for a week, with a tick beside it
# in `docs/next.md`.
#
# The sentences are not ours to change. `evaluate_planning.py` scores these same three files and
# there the sentence *is* the input being scored, so adding «en todas mis cuentas» to 235 questions
# would quietly change what that benchmark measures. The scope is therefore settled in the harness.
#
# This is the same move `tests/support/episodes.py` makes for the conversation corpus — see
# `opening()` and the `scope:` vocabulary there, which this deliberately borrows rather than
# inventing a second idea of "scope already settled". A settled `AccountSelection` is the state the
# service itself stores the moment a holder answers the account question, so a case opening here is
# resuming a real thread rather than inhabiting a synthetic one: every case still reads that
# selection back through the directory-backed branch of `_scoped`, which is the branch that had
# never run.
SETTLED: dict[str, Any] = {"account_selection_mode": "ALL"}

# The chip the service draws, worded the way it draws it. `evaluation/conversations/
# 31-account-scope.yaml` asserts this reply reaches no planner (`no_model: true`) — the account
# vocabulary parses it in `account_words.asks_for_all_accounts`. So the second turn of an unsettled
# case costs a round trip and no GPU, which is what makes keeping a real subset affordable.
ALL_ACCOUNTS = "Todas mis cuentas"

# One in this many plan-expecting cases opens with nothing settled and answers the account question
# itself, instead of opening past it.
#
# Seeding every case would engineer away the commonest thing the service now emits, and a shortcut
# nothing checks is how this benchmark broke in the first place. So a subset takes the production
# path end to end, and its job is to detect the seeded opening drifting away from that path.
#
# The stride is derived from the smallest pool rather than picked. Only `expect: plan` cases can
# reach the account question — an `unsupported` sentence is refused before any account matters, and
# a `clarification` case is already about a different missing slot — and the smallest such pool is
# adversarial's 17. One case is not coverage, because a single flake leaves the path unmeasured in
# that set; two is the minimum that survives one. 17 // 2 = 8.
CLARIFICATION_STRIDE = 8


# --- the vacuity guard ----------------------------------------------------------------------
#
# The reason this file was revisited. On 2026-08-28 it reported 0 violations on all 235 cases across
# three sets and the zero was empty: not one case reached an answering outcome, so not one
# figure-integrity property was ever evaluated. Zero violations and zero answers print identically,
# and nobody noticed for a week.
#
# An instrument that cannot detect its own silence will do this again. So a read benchmark in which
# too few cases answered is not a pass with a low score — it is a run that did not happen, and it
# says so and exits non-zero.
#
# **The denominator is the dataset's own claim**, not the case count: the cases whose `expect` field
# says a plan is the right answer. A set may legitimately be mostly `unsupported` — adversarial
# nearly is — and demanding answers from cases written to be refused would be a bar the data cannot
# meet. Reading `expect` here is also the first time this harness reads it at all; it is used for
# the denominator and deliberately not to score per-case correctness, which is
# `evaluate_planning.py`'s job and a different benchmark.
#
# **The floor is half, and the half is measured rather than round.** It has to sit far below any
# plausible accuracy result, or a vacuity guard becomes an accuracy gate that fires on ordinary
# model variance and gets ignored; and far above zero, or it permits the silence it exists to catch.
# `../evaluation/planning/RESULTS.md` bounds both ends. Its model table is eleven models over these
# same sets, and the *worst outcome accuracy any of them scored on any of these three* is 50.0 % —
# phi4-mini on adversarial, a model that was measured and rejected. (Its `inject` column goes lower,
# but that is a group inside adversarial rather than a set this recipe runs.) The shipped
# `qwen3.5:9b` scores 95.8 / 87.3 / 77.8, and that same file records the run-to-run band as ±1
# case. So a run below half is below the worst
# model anybody has ever pointed at this data: no model swap, no seed, and no ±1 case of
# reproducibility reaches it. What does reach it is a feature landing between the sentences and the
# properties, a credential quietly losing a scope, or a ledger re-seeded out from under the run —
# which is the entire family of failures that produced the vacuous zero.
ANSWERING_FLOOR = 0.5

# The `expect` values a case may declare. The floor above is computed from these, so a mistyped one
# would quietly shrink the denominator and weaken the guard — the same disease in the cure.
# `test_e2e_checks.py` asserts the three datasets against this, the way `TURN_KEYS` is asserted
# against `conversations.yaml`, because a check nobody runs reads exactly like a check that passes.
EXPECTATIONS = frozenset({"plan", "clarification", "unsupported"})


def violations(body: dict[str, Any]) -> list[str]:
    """Everything wrong with one answer, as property names.

    A list rather than a boolean because an answer can break more than one, and which
    one it broke is the whole diagnostic value.
    """
    broken: list[str] = []
    context = (body.get("conversation") or {}).get("last_plan") or {}
    headline = (body.get("text") or "").splitlines()[0] if body.get("text") else ""

    if body.get("outcome") == "PLANNED":
        # The defect. A plan that counted, rendered as an amount.
        counting = context.get("metric") == "COUNT"
        money_shown = bool(_MONEY.search(headline))
        if counting and money_shown:
            broken.append("PLAN_ANSWER_MISMATCH")
        if not counting and not money_shown and body.get("movement_count"):
            broken.append("PLAN_ANSWER_MISMATCH")

        # A figure with no plan beside it is a number to be taken on trust, which is the
        # one thing this pipeline should never ask for.
        if _has_figure(body) and not body.get("plan"):
            broken.append("NO_EVIDENCE")

        broken += _reconciliation(body, context)

    elif body.get("outcome") in ("INVALID_STRUCTURE", "PROVIDER_ERROR"):
        # Under constrained decoding the first is unreachable. Counting it is how we find
        # out if that ever stops being true.
        broken.append(str(body["outcome"]))

    if body.get("outcome") not in _ANSWERING and _has_figure(body):
        # A declined question carrying a figure is the worst shape available: the text
        # says no and the payload says a number.
        broken.append("FIGURE_WITHOUT_AN_ANSWER")

    if body.get("outcome") in _ANSWERING and not _has_figure(body):
        # The mirror of the rule above, and the hole it left open: a figure with no answer was
        # caught, an answer with no figure was not. It matters because every figure-integrity
        # property in this function is guarded on `total_minor` existing — the reconciliation, the
        # money/count agreement, the evidence check — so an answering outcome without one passes
        # all of them for want of anything to check. That is the vacuous zero this file was
        # revisited for, one case at a time instead of one set at a time; `vacuity`'s second prong
        # defends the set and this defends the case.
        #
        # Keyed on `_ANSWERING` rather than on PLANNED alone, because it is that rule's mirror and
        # a BALANCE arrives with the figure it is about. Measured before it was added: 175 of the
        # 175 answers across the three read sets carried a figure, so this closes a hole rather
        # than reporting a live failure.
        #
        # It asks `_has_figure` rather than reading `total_minor` itself, because a comparison's
        # figure is its `difference_minor` and its `total_minor` is `None`. Keyed on `total_minor`
        # this rule would have fired on every comparison the service emits — a hole closed into a
        # false positive.
        broken.append("ANSWER_WITHOUT_A_FIGURE")

    if not (body.get("text") or "").strip():
        broken.append("NOTHING_SAID")

    return broken


def _has_figure(body: dict[str, Any]) -> bool:
    """Whether this answer carries a figure at all, under whichever field names it.

    Not the same question as "has a `total_minor`", and the difference is load-bearing. A comparison
    puts its figure in `difference_minor` and leaves `total_minor` `None`; a net cash flow carries
    both, and there `total_minor` legitimately *is* the difference. So presence of the one does not
    imply absence of the other, and every rule guarded on a figure existing has to ask the question
    the same way or two of them will disagree about the same answer.
    """
    return body.get("total_minor") is not None or body.get("difference_minor") is not None


def _counts_reconcile(body: dict[str, Any], groups: list[dict[str, Any]]) -> list[str]:
    """The half of the reconciliation that holds in every shape.

    `movement_count` is the sum of the rows' counts whether the rows are parts of a whole or the two
    operands of a comparison, so this is what lets the amount check narrow without the property
    going quiet on the answer.
    """
    if sum(g["movement_count"] for g in groups) != body.get("movement_count"):
        return ["GROUP_COUNTS_DO_NOT_SUM"]
    return []


def _is_figure(value: Any) -> bool:
    """Whether this is a number the reconciliation can add up.

    `True` is an `int` in Python and is not a figure, which is why this is not a bare `isinstance`.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def _reconciliation(body: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Do the parts add up to the whole?

    An ungrouped query still comes back with one row keyed "total", which is the figure
    again rather than a breakdown, so it is not reconciled against itself.

    **"The whole" is not one thing, and until now this assumed it was.** Three shapes of breakdown
    reach here and the arithmetic that reconciles them differs, so each branch is keyed on a field
    the service *declares* rather than on an inference this could make from the numbers:
    `difference_minor` says the rows are operands, `metric` says they are running totals.

    The rule every branch obeys: **it narrows, it never goes quiet.** A shape this cannot add up in
    one unit is still reconciled in the other, because a check that stops checking because a field
    is present is the silent pass this file exists to catch.
    """
    groups = body.get("groups") or []
    if not groups or (len(groups) == 1 and groups[0].get("key") == "total"):
        return []

    # A row with no number in it cannot be added up. This used to reach `sum(g["total_minor"] ...)`
    # and raise `KeyError`, which took the whole run down and turned a data problem into a stack
    # trace — the opposite failure to the rest of this file, and still a run that reported nothing.
    #
    # Reported as a property rather than summed around. A breakdown this cannot add up is precisely
    # what gate 1's *Evidence reconciliation* row claims to have checked, so skipping the row
    # quietly would be the silent pass again, and guessing a 0 for the missing figure would invent
    # evidence. `reconcilable` still counts this answer as inspected, because it was: it was
    # inspected and found unaddable.
    if not all(
        _is_figure(g.get("total_minor")) and _is_figure(g.get("movement_count")) for g in groups
    ):
        return ["GROUP_WITHOUT_A_FIGURE"]

    if body.get("difference_minor") is not None:
        # A comparison, and a net cash flow. The rows are the two operands and the headline figure
        # is the signed difference between them, so summing the amounts against it compares a total
        # against a subtraction. That is what fired `GROUPS_DO_NOT_SUM_TO_TOTAL` on
        # `dev/unsupported_comparison` for a payload whose two halves were each coherent: 189597 was
        # the sum of the operands and -189597 was the difference, and both were right.
        #
        # Keyed on `difference_minor` because that field is the service *saying* what the rows are.
        # That is the whole difference between this and the version worth refusing, which would have
        # inferred "comparison" from conversation memory and gone quiet on an answer nobody had
        # declared anything about.
        #
        # Deliberately not keyed on `total_minor is None`: NET_CASH_FLOW carries both fields and
        # there `total_minor` legitimately is the difference, so the two do not travel together.
        #
        # The counts still reconcile, and that is why narrowing here is honest rather than an
        # exemption: `movement_count` is the sum of both operands' counts on every path.
        return _counts_reconcile(body, groups)

    if context.get("metric") == "CUMULATIVE":
        # Running totals. Each row already contains every row before it, so *neither* unit can sum
        # to the whole — `test_cumulative_and_group_rankings.py` pins rows of
        # 10_000, 10_000, 15_000, 15_000 against a `total_minor` of 15_000, and counts of 2, 2, 3, 3
        # against a `movement_count` of 3. Both halves of the old rule fired on that, both falsely,
        # and this is not a comparison special case: the property simply had no notion of shape.
        #
        # The cumulative form of "the parts add up to the whole" is that **the last part is the
        # whole**, so that is what is asserted, in both units. It is a real reconciliation rather
        # than a skip: a running total that stops short of its own figure fails it.
        #
        # Monotonicity is deliberately not asserted. It is true of an expense accumulation and this
        # has no evidence it holds of a signed one, and inventing a property to look thorough is how
        # a benchmark earns the right to be switched off.
        last = groups[-1]
        broken = []
        if last.get("total_minor") != body.get("total_minor"):
            broken.append("CUMULATIVE_DOES_NOT_REACH_TOTAL")
        if last.get("movement_count") != body.get("movement_count"):
            broken.append("CUMULATIVE_COUNTS_DO_NOT_REACH_TOTAL")
        return broken

    broken = []
    if sum(g["total_minor"] for g in groups) != body.get("total_minor"):
        broken.append("GROUPS_DO_NOT_SUM_TO_TOTAL")
    broken += _counts_reconcile(body, groups)
    return broken


def reconcilable(body: dict[str, Any]) -> bool:
    """Whether `_reconciliation` had a real breakdown to add up on this answer.

    Gate 1 in `docs/next.md` carries an *Evidence reconciliation* row, and `_reconciliation`
    returning `[]` reads identically whether the parts added up or there were no parts to add. So
    the row cannot be filled from a violation count alone: 0 failures out of 0 inspected answers is
    the vacuous zero again, one field lower down. Counting the answers it actually inspected is what
    lets that row carry a number that means something.
    """
    groups = body.get("groups") or []
    return bool(groups) and not (len(groups) == 1 and groups[0].get("key") == "total")


def sliced(cases: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """The first `limit` cases, where no limit and a limit of nothing are different requests.

    `if limit` read `--limit 0` as *no limit* and ran the whole corpus. It cost this session a
    contended run — two evaluations against one 8 GB GPU, and the timings of both discarded. The
    honest answer to a request for no cases is no cases, and the vacuity guards then report that
    as a run which did not happen rather than as a clean sheet.
    """
    return cases if limit is None else cases[:limit]


def questions(dataset: str, limit: int | None) -> list[dict[str, Any]]:
    data = yaml.safe_load((PLANNING / f"{dataset}.yaml").read_text(encoding="utf-8"))
    return sliced(list(data["cases"]), limit)


# The keys a `previous:` spec may carry — exactly the ones `evaluate_planning.as_plan` reads.
#
# It reads them with `.get`, so a mistyped key is dropped without a word and the case is asked with
# less context than it declares. That is the same silent pass this file exists to close, one level
# down in the dataset, and it would be invisible in both benchmarks. A test asserts the three read
# sets against this, the way `EXPECTATIONS` and `TURN_KEYS` are asserted against theirs.
PREVIOUS_KEYS = frozenset(
    {
        "period",
        "dateRange",
        "direction",
        "categories",
        "excludedCategories",
        "merchants",
        "excludedMerchants",
        "counterparties",
        "excludedCounterparties",
        "counterpartyTypes",
        "groupBy",
        "metric",
        "groupLimit",
        "includeInternal",
        "internalOnly",
        "acrossAccounts",
        "onlyAccountId",
        "onlyAccountIds",
        "transactionIds",
        "transactionTypes",
    }
)


def _wire_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """The dataset's spelling of a plan, rendered the way the endpoint renders one.

    Two spellings exist and neither is negotiable: the datasets write `groupBy` and a nested
    `period`, the wire carries flat snake_case. So this invents no third one. It borrows the reader
    the other benchmark already uses on this very field — `evaluate_planning.as_plan` — and the
    writer the endpoint itself uses to put a plan on the wire, `conversation_mapper.plan_payload`.

    Two things follow, and both are the reason for going the long way round. A spec this cannot
    build raises here instead of being quietly dropped, which is `as_plan`'s own promise: *a context
    this cannot build is one a client could not send either*. And the two benchmarks cannot come to
    disagree about what a `previous:` block means, because there is one function that decides.

    Imported here rather than at the top because `evaluate_planning` builds `Settings` and the LLM
    providers on import: a run over datasets that declare no preceding turn — `adversarial`, the
    corpus — should not need the planner configured to drive HTTP.
    """
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)

    from evaluate_planning import as_plan

    from finguard_orchestrator.infrastructure.mappers.conversation_mapper import plan_payload

    return dict(plan_payload(as_plan(spec)).model_dump(mode="json"))


def preceding(case: dict[str, Any]) -> dict[str, Any] | None:
    """The plan behind this case's preceding turn, or None if it declares none.

    9 dev cases and 6 validation cases carry a `previous:` plan and this harness asked every one of
    them cold. «¿Y en gasolina?» arrived with no window, «¿Y este mes?» with no category to keep,
    «¿Y cuántas veces?» with nothing to count — so fifteen expectations about follow-up context
    were being evaluated against a service that had never been given any. Whatever they scored, it
    was not what they say they measure.

    `dev.yaml` states the field's meaning outright — *"`previous` is the plan behind the preceding
    answer, for follow-up cases only"* — and the wire has carried it under `conversation.last_plan`
    all along, which is what a client echoes back after an answer. Nothing had to be added to the
    contract or to the datasets; the harness simply was not sending it.
    """
    spec = case.get("previous")
    if spec is None:
        return None
    unknown = set(spec) - PREVIOUS_KEYS
    if unknown:
        raise SystemExit(
            f"{case.get('id')}: `previous` carries {', '.join(sorted(unknown))}, which nothing "
            f"reads. Known: {', '.join(sorted(PREVIOUS_KEYS))}"
        )
    return _wire_plan(spec)


def opening_for(case: dict[str, Any], real_path: bool) -> dict[str, Any] | None:
    """The conversation a case opens with: the account scope, and the turn before it.

    Two independent pieces of memory that travel in one object, and `ConversationPayload` says why
    they compose rather than conflict — `last_plan` is *"memory rather than a workflow, so it
    travels alongside whatever is active"*. A case on the real path opens with no settled scope on
    purpose, and still carries the context it declares: it has to answer the account question
    first, which is the production path, and the plan it is following up on is not what that
    question is about. Two cases are both at once — `dev/follow_category_after_period` and
    `validation/t_follow_period` — and they are the only ones exercising the pair.
    """
    opening: dict[str, Any] = {} if real_path else dict(SETTLED)
    context = preceding(case)
    if context is not None:
        opening["last_plan"] = context
    return opening or None


def expecting_a_plan(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cases whose own `expect` says a plan is the right answer to them."""
    return [case for case in cases if case.get("expect") == "plan"]


def unsettled(cases: list[dict[str, Any]]) -> set[str]:
    """Which cases open with nothing settled and answer the account question themselves.

    A stride over the plan-expecting cases rather than a random sample, so the subset is the same on
    every run: a benchmark whose coverage moves between runs cannot be compared with itself.
    """
    asking = expecting_a_plan(cases)
    return {
        case["id"] for position, case in enumerate(asking) if position % CLARIFICATION_STRIDE == 0
    }


def vacuity(dataset: str, answered: int, figures: int, expected: int) -> list[str]:
    """Why this run does not count as a measurement, or nothing if it does.

    Two prongs, because there are two ways to print zeros. The first is the one that happened: no
    case reached an answering outcome, so the properties never ran. The second is the same shape one
    field lower — an answering outcome carrying no figure passes every property in `violations()`
    for want of anything to check, so a set where *no* answer carried one is equally silent. That
    second prong is an absolute zero rather than a fraction on purpose: no accuracy story explains a
    whole set of answers with no figures in them, whereas the count of answers legitimately moves
    with the model.
    """
    if expected == 0:
        return [
            f"no case in {dataset} declares `expect: plan`, so no answer was ever required and "
            f"nothing here could have been measured"
        ]

    floor = math.ceil(expected * ANSWERING_FLOOR)
    reasons = []
    if answered < floor:
        reasons.append(
            f"{answered} of {expected} plan-expecting cases reached an answering outcome; "
            f"{floor} is the floor ({ANSWERING_FLOOR:.0%} of what {dataset} says should answer)"
        )
    if figures == 0:
        reasons.append(
            "not one answer carried a figure, so every figure-integrity property was skipped "
            "for want of a number to check"
        )
    return reasons


async def _ask(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    question: str,
    conversation: dict[str, Any] | None,
) -> dict[str, Any]:
    response = await client.post(
        f"{base_url}/v1/questions",
        json={"question": question, "conversation": conversation},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return dict(response.json())


async def run(base_url: str, token: str, dataset: str, limit: int | None) -> int:
    cases = questions(dataset, limit)
    walks_the_real_path = unsettled(cases)
    failures: list[tuple[str, list[str], str]] = []
    outcomes: dict[str, int] = {}
    # Two counters and not one. `answered` is every case that reached an answering outcome, which is
    # what the properties had to work with. `earned` is only the plan-expecting ones, and it is what
    # the floor is computed from — numerator and denominator over the same population, or the ratio
    # is not a ratio. An `expect: unsupported` case that answers anyway is a real answer and counts
    # in the first; letting it count in the second would let the guard be satisfied by cases the
    # dataset never asked to answer.
    answered = earned = figures = inspected = 0
    asked_first = 0
    # How many cases were given the preceding turn they declare. Printed for the same reason the
    # counters above it are: fifteen cases spent the harness's whole history being asked cold, and
    # a run cannot say whether it sent the context unless it counts having sent it.
    with_context = 0

    async with httpx.AsyncClient(timeout=180.0) as client:
        for case in cases:
            real_path = case["id"] in walks_the_real_path
            opening = opening_for(case, real_path)
            with_context += "previous" in case
            try:
                body = await _ask(client, base_url, token, case["question"], opening)
                broken = violations(body)

                state = body.get("conversation") or {}
                if real_path and state.get("workflow") == "ACCOUNT_SCOPE":
                    # It asked which account, so answer it the way the client does: echo the whole
                    # conversation back and send the chip. The reply reaches no planner, so this
                    # buys the production path for the price of a round trip.
                    asked_first += 1
                    body = await _ask(client, base_url, token, ALL_ACCOUNTS, state)
                    broken += violations(body)
                    if body.get("outcome") == "NEEDS_CLARIFICATION":
                        # It asked, the reply supplied exactly what it asked for, and it asked
                        # again. The loop detector in the conversation path cannot see this one:
                        # `awaiting` is null on an account-scope clarification, so `asked_slot`
                        # returns None and the run never counts. Hence the explicit check.
                        broken.append("CLARIFICATION_RESTARTED")
            except httpx.HTTPStatusError as exc:
                failures.append(
                    (case["id"], [f"HTTP_{exc.response.status_code}"], exc.response.text[:80])
                )
                continue
            except httpx.HTTPError as exc:
                failures.append((case["id"], ["TRANSPORT"], str(exc)))
                continue

            outcomes[body.get("outcome", "?")] = outcomes.get(body.get("outcome", "?"), 0) + 1
            if body.get("outcome") in _ANSWERING:
                answered += 1
                earned += case.get("expect") == "plan"
                figures += _has_figure(body)
                inspected += reconcilable(body)
            if broken:
                failures.append((case["id"], sorted(set(broken)), (body.get("text") or "")[:70]))

    total = len(cases)
    expected = len(expecting_a_plan(cases))
    print(f"\n=== end to end · {dataset} · {total} questions ===\n")
    print(f"  account scope settled by the harness   {total - len(walks_the_real_path):3}")
    print(f"  answered the account question for real {len(walks_the_real_path):3}", end="")
    print(f"  (asked on {asked_first} of them)\n")
    for name, count in sorted(outcomes.items(), key=lambda item: -item[1]):
        print(f"  {name:22} {count:3}")

    # What the properties actually had to work with. Printed whether or not anything broke, because
    # the failure this file exists to prevent looked exactly like a clean run.
    print(f"\n  answered, of the {expected} that expect a plan   {earned:3}")
    print(f"  answering outcomes, all cases                {answered:3}")
    print(f"  answers with a figure                        {figures:3}")
    print(f"  breakdowns reconciled                        {inspected:3}")
    print(f"  asked with the preceding turn they declare   {with_context:3}")

    print(f"\n  PROPERTY VIOLATIONS   {len(failures):3}/{total}")
    if failures:
        print("\n  what broke")
        for case_id, broken, detail in failures:
            print(f"    {case_id:32} {','.join(broken):28} {detail}")

    empty = vacuity(dataset, earned, figures, expected)
    if empty:
        print("\n  THE BENCHMARK DID NOT RUN")
        for reason in empty:
            print(f"    {reason}")
        print("\n  A violation count is only worth the answers it was computed over. This run")
        print("  measured too few to mean anything, so it is a failure rather than a pass.")
        return 1

    # Not "how accurate". These are properties the code can guarantee, so any failure is
    # a bug rather than a long tail, and the exit code says so.
    return 1 if failures else 0


# --- conversations ------------------------------------------------------------------


def _window_of(context: dict[str, Any]) -> str | None:
    """The window as one string, because a plan carries a period or a range and never both.

    A string rather than a tuple so the dataset can write the expected value the way a person
    would: `LAST_N_MONTHS:3`, `THIS_MONTH`, `2025-07-01..2025-07-31`.
    """
    if context.get("period"):
        named = str(context["period"])
        return f"{named}:{context['n']}" if context.get("n") else named
    if context.get("start") and context.get("end"):
        return f"{context['start']}..{context['end']}"
    return None


def _names(context: dict[str, Any], field: str) -> str:
    """A list field as a stable string. Sorted, because the order a model emits filters in is not
    part of the question."""
    return ",".join(sorted(context.get(field) or []))


# Every field of a plan a case may talk about, as something comparable and printable. The old
# version knew three of them and raised on the rest, which is why the dataset only ever asked about
# three — a check nobody can express is a check nobody writes.
_FIELDS: dict[str, Any] = {
    "window": _window_of,
    "direction": lambda plan: plan.get("direction"),
    "categories": lambda plan: _names(plan, "categories"),
    "merchants": lambda plan: _names(plan, "merchants"),
    "group_by": lambda plan: plan.get("group_by"),
    "metric": lambda plan: plan.get("metric"),
    "action": lambda plan: plan.get("action"),
    "rank": lambda plan: plan.get("rank"),
}


def _value(field: str, plan: dict[str, Any]) -> Any:
    if field not in _FIELDS:
        raise SystemExit(
            f"conversations.yaml asks about a field this cannot check: {field}. "
            f"Known: {', '.join(sorted(_FIELDS))}"
        )
    return _FIELDS[field](plan)


def _expected(field: str, written: Any) -> Any:
    """What the dataset wrote, normalised the same way the answer is."""
    if field in ("categories", "merchants"):
        return ",".join(sorted(written if isinstance(written, list) else [written]))
    return written


def shape_of(body: dict[str, Any]) -> str:
    """What kind of answer this is, as the user sees it.

    The review's point about "¿a qué personas les he hecho Bizum?": the figure was correct and the
    shape was wrong, and no rule about outcomes or windows can see that. A question about *who* has
    to come back as rows with names on them.

    An ungrouped aggregate still returns one row keyed "total", which is the figure again rather
    than a breakdown, so it does not count as GROUPS.
    """
    groups = [g for g in (body.get("groups") or []) if g.get("key") != "total"]
    if groups:
        return "GROUPS"
    if body.get("movements") and len(body["movements"]) == 1:
        return "MOVEMENT"
    if body.get("total_minor") is not None:
        return "TOTAL"
    return "NONE"


def turn_violations(
    turn: dict[str, Any], body: dict[str, Any], asked_before: bool, plan_before: dict[str, Any]
) -> list[str]:
    """What is wrong with this turn *given the ones before it*.

    Everything single-turn is checked by `violations`; this only adds what needs history. Each
    property is an implication, so a model that answers where it could have asked does not fail
    a case — it takes a different branch of the same rule.
    """
    broken: list[str] = []
    outcome = body.get("outcome")
    context = (body.get("conversation") or {}).get("last_plan") or {}

    expected = turn.get("expect")
    if expected and outcome != expected:
        # Only where no model is involved: the intent router, the capability guard, the
        # deterministic completion. Those outcomes are ours to guarantee.
        broken.append(f"EXPECTED_{expected}")

    said = turn.get("says")
    if said and said.lower() not in (body.get("text") or "").lower():
        # For the half of an answer that is not a plan. A correct figure under a sentence that
        # does not say what it is a figure of was the most common complaint about this assistant,
        # and no property about outcomes or windows can see it.
        broken.append("DOES_NOT_SAY")

    refused = turn.get("not_says")
    if refused and refused.lower() in (body.get("text") or "").lower():
        # For a turn where several outcomes are acceptable and one shape is not. "How many"
        # answered with a euro figure is the reported defect, and it is true of every phrasing and
        # every model mood — where demanding a particular outcome would not be.
        broken.append("SAYS_WHAT_IT_MUST_NOT")

    state = body.get("conversation") or {}
    half_built = state.get("draft") or state.get("payment") or state.get("pending_plan")
    if outcome == "NEEDS_CLARIFICATION" and not half_built:
        # Without something on the way out the client has nothing to echo, so the next turn is a
        # fresh question however well it is phrased. This is the defect, at its source.
        #
        # Either draft counts, because either workflow can be the one asking: a query missing its
        # window and a bizum missing its amount are the same failure seen from two sides. The rule
        # was written when only the query path could ask, and payment clarifications then failed it
        # for carrying the right draft under the other name.
        #
        # `pending_plan` is the third name and the same story: account scope asks which of the
        # holder's accounts a bare question was about, and keeps the plan it is holding under a
        # field of its own. It carried 94 of the 115 violations this corpus reported, all of them
        # false — the state was on the wire the whole time. If a fourth workflow learns to ask,
        # this is the line it will fail at, and adding its field is the fix rather than the
        # symptom.
        broken.append("QUESTION_WITHOUT_A_DRAFT")

    if turn.get("completes") and asked_before and outcome == "NEEDS_CLARIFICATION":
        # It asked, the reply supplied exactly what it asked for, and it asked again. This is
        # what the report described and what nothing before this could detect.
        broken.append("CLARIFICATION_RESTARTED")

    # --- what the plan has to be, keep and change ---------------------------------------
    #
    # `expect_plan` is the assertion the dataset could not make before, and it is legitimate now
    # for exactly the fields a deterministic path owns. A window the sentence states outright is
    # no longer the model's decision — `explicit_facts` enforces it after the merge — so demanding
    # a value is asserting our own behaviour rather than betting on the model's mood. Anything
    # still left to judgement belongs in `must_keep` / `must_change`, which are implications.
    for field, written in (turn.get("expect_plan") or {}).items():
        if outcome != "PLANNED":
            broken.append(f"NOT_PLANNED_SO_NO_{field.upper()}")
            continue
        if _value(field, context) != _expected(field, written):
            broken.append(f"WRONG_{field.upper()}")

    for field in turn.get("must_keep") or []:
        if not plan_before:
            continue
        if outcome != "PLANNED":
            # A turn that declined has no plan to compare, and whether it should have declined is
            # the model's judgement rather than a property.
            continue
        if _value(field, plan_before) != _value(field, context):
            broken.append(f"LOST_{field.upper()}")

    # The inverse, and the one the dataset had no way to say. Every rule here asked what survived;
    # none could require that something *stopped* surviving, so "los últimos 3 meses" answered over
    # the previous five passed every check in the file.
    for field in turn.get("must_change") or []:
        if outcome != "PLANNED":
            broken.append(f"NOT_PLANNED_SO_NO_{field.upper()}")
            continue
        if plan_before and _value(field, plan_before) == _value(field, context):
            broken.append(f"INHERITED_{field.upper()}")

    # --- what shape the answer takes ------------------------------------------------------
    expected_shape = turn.get("expect_shape")
    if expected_shape:
        if outcome != "PLANNED":
            # The asymmetry this had with its two siblings, and the hole in it. `expect_plan` and
            # `must_change` both say `NOT_PLANNED_SO_NO_<field>` when there is no plan to look at;
            # this one had `and outcome == "PLANNED"` folded into the condition, so a turn
            # demanding `expect_shape: GROUPS` that came back as a clarification asserted nothing
            # and passed. A demand for a particular shape is a demand for an answer to have a
            # shape, and a clarification does not satisfy it — the same reading `expect_plan`
            # already takes of the same situation.
            broken.append("NOT_PLANNED_SO_NO_SHAPE")
        elif shape_of(body) != expected_shape:
            broken.append(f"SHAPE_IS_{shape_of(body)}")

    for key in turn.get("expect_groups") or []:
        keys = [str(group.get("key", "")).lower() for group in body.get("groups") or []]
        if not any(key.lower() in found for found in keys):
            broken.append(f"NO_GROUP_{key.upper()}")

    return broken


# How many times running the assistant may ask for the same thing. Two is a clarification and one
# retry; the third is the loop from the report, where "¿de qué periodo?" came back about a period
# the user had already written twice. Applied to every thread rather than opted into per case,
# because it is a property of having a conversation rather than of any one conversation.
MAX_REPEATED_CLARIFICATIONS = 2


def asked_slot(body: dict[str, Any]) -> str | None:
    """Which slot this turn asked the user for, or None if it did not ask.

    The slot rather than the wording: the question is written by a model, so two phrasings of
    "which period?" are the same dead end and comparing sentences would miss it.
    """
    if body.get("outcome") != "NEEDS_CLARIFICATION":
        return None
    slot = (body.get("conversation") or {}).get("awaiting")
    return str(slot) if slot else None


def clarification_run(slot: str | None, previous: str | None, run: int) -> int:
    """How many turns running have now asked for this same slot.

    A function rather than three lines in the loop so it can be tested: the interesting cases are
    a run broken by an answer and a run broken by a *different* question, and neither is obvious
    from reading the arithmetic.
    """
    if slot is None:
        return 0
    return run + 1 if slot == previous else 1


# The keys a turn may carry. Checked against the dataset by a test, because a mistyped key is
# silently ignored by every reader here — and a check nobody runs reads exactly like a check that
# passes.
TURN_KEYS = frozenset(
    {
        "say",
        "completes",
        "must_keep",
        "must_change",
        "expect_plan",
        "expect_shape",
        "expect_groups",
        "expect",
        "says",
        "not_says",
        "keeps_payment",
    }
)
CONVERSATION_KEYS = frozenset({"id", "why", "turns", "max_repeated_clarifications"})
SHAPES = frozenset({"TOTAL", "GROUPS", "MOVEMENT", "NONE"})


def corpus_vacuity(threads: int, declared: int, replayed: int) -> list[str]:
    """Why a corpus run does not count as a measurement, or nothing if it does.

    The three read sets got a guard and this path did not, in the same file and on the same day. An
    empty corpus — or one emptied by `--limit 0`, which used to mean *no limit* — printed
    `PROPERTY VIOLATIONS 0` and exited 0. Every property in `turn_violations` needs a turn to have
    happened before it can fail, exactly as every property in `violations` needs an answer, so a
    corpus that replayed nothing breaks nothing and reads as a clean sheet.

    The denominator is the corpus's own claim, the way `ANSWERING_FLOOR`'s is: the turns the
    dataset declares. And `ANSWERING_FLOOR` itself is reused rather than a second number invented,
    because it is answering the same question — *did enough of this happen to be worth a count?* —
    and two thresholds would be two things to justify and one to forget.

    This is deliberately not an accuracy floor. A turn that ran and violated three properties still
    counts as replayed; all this asks is whether the turns reached the service. A thread that breaks
    early already records its own failure, so the fraction and the violation count are separate
    questions and the run prints both.
    """
    if threads == 0:
        return ["not one conversation was replayed, so nothing here could have been measured"]
    if declared == 0:
        return [f"the {threads} threads declare no turns at all, so no property could run"]

    floor = math.ceil(declared * ANSWERING_FLOOR)
    if replayed < floor:
        return [
            f"{replayed} of {declared} declared turns reached the service; {floor} is the floor "
            f"({ANSWERING_FLOOR:.0%} of what the corpus declares)"
        ]
    return []


async def run_conversations(base_url: str, token: str, limit: int | None) -> int:
    """Each conversation replayed the way the client has one: the plan and the draft echoed
    back, nothing kept on the server."""
    data = yaml.safe_load((PLANNING / "conversations.yaml").read_text(encoding="utf-8"))
    cases = sliced(list(data["conversations"]), limit)
    declared = sum(len(case["turns"]) for case in cases)
    failures: list[tuple[str, list[str], str]] = []
    turns_run = 0

    async with httpx.AsyncClient(timeout=180.0) as client:
        for case in cases:
            # One value, exactly as the client holds it. The four separate pieces this replaced
            # were four chances for the harness to combine them differently from the client, which
            # would have made it pass while the browser broke.
            conversation: dict[str, Any] | None = None
            asked = False
            # The slot the previous turn asked about, and how many turns in a row it has been the
            # same one. Kept per thread because a loop is a property of the thread.
            asking_about: str | None = None
            in_a_row = 0
            allowed = case.get("max_repeated_clarifications", MAX_REPEATED_CLARIFICATIONS)

            for index, turn in enumerate(case["turns"], start=1):
                plan_before = (conversation or {}).get("last_plan") or {}
                where = f"{case['id']}#{index}"
                try:
                    response = await client.post(
                        f"{base_url}/v1/questions",
                        json={"question": turn["say"], "conversation": conversation},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except httpx.HTTPError as exc:
                    failures.append((where, ["TRANSPORT"], str(exc)))
                    break

                if response.status_code != 200:
                    failures.append((where, [f"HTTP_{response.status_code}"], response.text[:80]))
                    break

                body = response.json()
                turns_run += 1
                broken = violations(body) + turn_violations(turn, body, asked, plan_before)
                state = body.get("conversation") or {}
                if turn.get("keeps_payment") and state.get("workflow") != "PAYMENT_IN_PROGRESS":
                    # A payment under way that stopped being under way. This is the defect from the
                    # report: the half-built bizum was dropped and the next reply was classified
                    # from scratch, which sent "15 euros" to the financial planner.
                    broken.append("LOST_THE_PAYMENT")

                slot = asked_slot(body)
                in_a_row = clarification_run(slot, asking_about, in_a_row)
                asking_about = slot
                if slot and in_a_row > allowed:
                    # Asked for the same slot this many turns running. A user cannot get out of
                    # that by rephrasing, because rephrasing is what already failed.
                    broken.append(f"ASKED_{slot}_{in_a_row}_TIMES")

                if broken:
                    failures.append((where, broken, (body.get("text") or "")[:60]))

                # Exactly what the client does, and the reason this runs over HTTP: the contract
                # is what the client implements, not what the handler would accept.
                # Stored, not recomputed — the client's whole job now, and the reason this
                # harness matches it: an answer that carries no state (the payment endpoints) leaves
                # the conversation where it was.
                conversation = body.get("conversation") or conversation
                asked = body.get("outcome") == "NEEDS_CLARIFICATION"

    print()
    print(f"=== conversations · {len(cases)} threads · {turns_run} turns ===")
    print()
    # What the properties had to work with, printed whether or not anything broke — for the reason
    # the read path prints its own block: the failure this file exists to prevent looked exactly
    # like a clean run, and a bare violation count cannot tell 0 failures from 0 checks.
    print(f"  turns the corpus declares   {declared:3}")
    print(f"  turns replayed              {turns_run:3}")
    print()
    print(f"  PROPERTY VIOLATIONS   {len(failures):3}")
    if failures:
        print()
        print("  what broke")
        for where, broken, detail in failures:
            print(f"    {where:32} {','.join(broken):28} {detail}")

    empty = corpus_vacuity(len(cases), declared, turns_run)
    if empty:
        print()
        print("  THE BENCHMARK DID NOT RUN")
        for reason in empty:
            print(f"    {reason}")
        print()
        print("  A violation count is only worth the turns it was computed over. This run")
        print("  measured too few to mean anything, so it is a failure rather than a pass.")
        return 1

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="a development token from the token table")
    parser.add_argument(
        "--dataset",
        default="dev",
        choices=("dev", "validation", "adversarial", "conversations"),
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.dataset == "conversations":
        return asyncio.run(run_conversations(args.base_url, args.token, args.limit))
    return asyncio.run(run(args.base_url, args.token, args.dataset, args.limit))


if __name__ == "__main__":
    sys.exit(main())
