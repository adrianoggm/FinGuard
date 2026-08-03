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


def violations(body: dict[str, Any]) -> list[str]:
    """Everything wrong with one answer, as property names.

    A list rather than a boolean because an answer can break more than one, and which
    one it broke is the whole diagnostic value.
    """
    broken: list[str] = []
    context = body.get("plan_context") or {}
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

    if body.get("outcome") != "PLANNED" and body.get("total_minor") is not None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="a development token from the token table")
    parser.add_argument("--dataset", default="dev", choices=("dev", "validation", "adversarial"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    return asyncio.run(run(args.base_url, args.token, args.dataset, args.limit))


if __name__ == "__main__":
    sys.exit(main())
