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
"""

import argparse
import asyncio
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


def questions(dataset: str, limit: int | None) -> list[dict[str, Any]]:
    data = yaml.safe_load((PLANNING / f"{dataset}.yaml").read_text(encoding="utf-8"))
    cases = data["cases"]
    return cases[:limit] if limit else cases


async def run(base_url: str, token: str, dataset: str, limit: int | None) -> int:
    cases = questions(dataset, limit)
    failures: list[tuple[str, list[str], str]] = []
    outcomes: dict[str, int] = {}

    async with httpx.AsyncClient(timeout=180.0) as client:
        for case in cases:
            try:
                response = await client.post(
                    f"{base_url}/v1/questions",
                    json={"question": case["question"]},
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                failures.append((case["id"], ["TRANSPORT"], str(exc)))
                continue

            if response.status_code != 200:
                failures.append((case["id"], [f"HTTP_{response.status_code}"], response.text[:80]))
                continue

            body = response.json()
            outcomes[body.get("outcome", "?")] = outcomes.get(body.get("outcome", "?"), 0) + 1
            broken = violations(body)
            if broken:
                failures.append((case["id"], broken, (body.get("text") or "")[:70]))

    total = len(cases)
    print(f"\n=== end to end · {dataset} · {total} questions ===\n")
    for name, count in sorted(outcomes.items(), key=lambda item: -item[1]):
        print(f"  {name:22} {count:3}")

    print(f"\n  PROPERTY VIOLATIONS   {len(failures):3}/{total}")
    if failures:
        print("\n  what broke")
        for case_id, broken, detail in failures:
            print(f"    {case_id:32} {','.join(broken):28} {detail}")

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
    half_built = state.get("draft") or state.get("payment")
    if outcome == "NEEDS_CLARIFICATION" and not half_built:
        # Without something on the way out the client has nothing to echo, so the next turn is a
        # fresh question however well it is phrased. This is the defect, at its source.
        #
        # Either draft counts, because either workflow can be the one asking: a query missing its
        # window and a bizum missing its amount are the same failure seen from two sides. The rule
        # was written when only the query path could ask, and payment clarifications then failed it
        # for carrying the right draft under the other name.
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
