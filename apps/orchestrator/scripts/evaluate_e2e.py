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


def _window_of(context: dict[str, Any]) -> tuple[Any, ...]:
    """The window as one value, because a plan carries a period or a range and never both."""
    return (context.get("period"), context.get("n"), context.get("start"), context.get("end"))


def _kept(field: str, before: dict[str, Any], after: dict[str, Any]) -> bool:
    if field == "window":
        return _window_of(before) == _window_of(after)
    if field == "categories":
        return sorted(before.get("categories") or []) == sorted(after.get("categories") or [])
    if field == "metric":
        return before.get("metric") == after.get("metric")
    raise SystemExit(f"conversations.yaml asks about a field this cannot check: {field}")


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

    for field in turn.get("keeps") or []:
        if not plan_before:
            continue
        if outcome != "PLANNED":
            # A turn that declined has no plan to compare, and whether it should have declined is
            # the model's judgement rather than a property.
            continue
        if not _kept(field, plan_before, context):
            broken.append(f"LOST_{field.upper()}")

    return broken


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
