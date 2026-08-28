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
        if body.get("total_minor") is not None and not body.get("plan"):
            broken.append("NO_EVIDENCE")

        broken += _reconciliation(body)

    elif body.get("outcome") in ("INVALID_STRUCTURE", "PROVIDER_ERROR"):
        # Under constrained decoding the first is unreachable. Counting it is how we find
        # out if that ever stops being true.
        broken.append(str(body["outcome"]))

    if body.get("outcome") not in _ANSWERING and body.get("total_minor") is not None:
        # A declined question carrying a figure is the worst shape available: the text
        # says no and the payload says a number.
        broken.append("FIGURE_WITHOUT_AN_ANSWER")

    if not (body.get("text") or "").strip():
        broken.append("NOTHING_SAID")

    return broken


def _reconciliation(body: dict[str, Any]) -> list[str]:
    """Do the parts add up to the whole?

    An ungrouped query still comes back with one row keyed "total", which is the figure
    again rather than a breakdown, so it is not reconciled against itself.
    """
    groups = body.get("groups") or []
    if not groups or (len(groups) == 1 and groups[0].get("key") == "total"):
        return []

    broken = []
    if sum(g["total_minor"] for g in groups) != body.get("total_minor"):
        broken.append("GROUPS_DO_NOT_SUM_TO_TOTAL")
    if sum(g["movement_count"] for g in groups) != body.get("movement_count"):
        broken.append("GROUP_COUNTS_DO_NOT_SUM")
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


def questions(dataset: str, limit: int | None) -> list[dict[str, Any]]:
    data = yaml.safe_load((PLANNING / f"{dataset}.yaml").read_text(encoding="utf-8"))
    cases = data["cases"]
    return cases[:limit] if limit else cases


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

    async with httpx.AsyncClient(timeout=180.0) as client:
        for case in cases:
            real_path = case["id"] in walks_the_real_path
            try:
                body = await _ask(
                    client, base_url, token, case["question"], None if real_path else SETTLED
                )
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
                figures += body.get("total_minor") is not None
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
    if expected_shape and outcome == "PLANNED" and shape_of(body) != expected_shape:
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


async def run_conversations(base_url: str, token: str, limit: int | None) -> int:
    """Each conversation replayed the way the client has one: the plan and the draft echoed
    back, nothing kept on the server."""
    data = yaml.safe_load((PLANNING / "conversations.yaml").read_text(encoding="utf-8"))
    cases = data["conversations"][:limit] if limit else data["conversations"]
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
    print(f"  PROPERTY VIOLATIONS   {len(failures):3}")
    if failures:
        print()
        print("  what broke")
        for where, broken, detail in failures:
            print(f"    {where:32} {','.join(broken):28} {detail}")
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
