"""Measures how well a model turns Spanish sentences into plans.

    uv run python scripts/evaluate_planning.py --model qwen3.5:4b

Exact match is reported but it is not the headline. A plan with the right window and
the wrong direction is not "one failure": it is one field right and one field wrong,
and a score that cannot tell those apart cannot tell you what to fix. So the per-field
table is what you read.

Two rates matter more than accuracy:

INVENTED — the dataset says ask or refuse, and the model produced a plan. This is the
failure nothing else catches. It is schema-valid, the domain accepts it, banking
returns a real figure, and the user reads a correct answer to a question they did not
ask. Every other number here can look healthy while this one is bad.

MISSED — the dataset says plan, and the model asked or refused. Cheap by comparison:
the user is inconvenienced, not misinformed. Worth watching so a model cannot score
well by refusing everything.

The reference date comes from the dataset and never from the clock, so a run today and
a run in March are comparable.
"""

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from finguard_assistant.domain.enums import PlanningOutcome
from finguard_assistant.domain.models.planning_result import PlanningResult
from finguard_assistant.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_assistant.infrastructure.planning.llm_query_planner import LlmQueryPlanner

PLANNING = Path(__file__).resolve().parents[1] / "evaluation" / "planning"

# The vocabulary that exists in the seeded data. It belongs to banking; until there is
# somewhere legitimate to read it from, it is stated here and the drift shows up as an
# INVALID_DOMAIN in this very report.
CATEGORIES = (
    "GROCERIES",
    "TRANSPORT_FUEL",
    "RESTAURANTS",
    "TRANSPORT_PUBLIC",
    "HEALTH",
    "LEISURE",
    "SUBSCRIPTIONS",
    "HOUSING_RENT",
    "SALARY",
    "TRANSFER_BIZUM",
)

FIELDS = ("window", "direction", "categories", "groupBy", "metric")


def resolve(period: str, n: int | None, today: date) -> tuple[str, str]:
    """Named window -> the days it covers, so equivalent plans compare equal.

    A model that answers TODAY and one that answers the explicit range for today have
    produced the same query. Scoring the first right and the second wrong would be
    measuring how it phrased the window rather than whether it understood it.

    This duplicates banking's `resolve`, which is normally the thing to avoid. Here it
    is deliberate and it stays in the scorer: the day these two disagree, the dataset
    starts failing and someone looks — whereas sharing the implementation under test
    with the thing testing it would hide exactly that.
    """
    first_of_month = today.replace(day=1)
    match period:
        case "TODAY":
            start = end = today
        case "YESTERDAY":
            start = end = today - timedelta(days=1)
        case "THIS_WEEK":
            start, end = today - timedelta(days=today.weekday()), today
        case "LAST_WEEK":
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
        case "THIS_MONTH":
            start, end = first_of_month, today
        case "LAST_MONTH":
            end = first_of_month - timedelta(days=1)
            start = end.replace(day=1)
        case "THIS_YEAR":
            start, end = date(today.year, 1, 1), today
        case "LAST_N_DAYS":
            start, end = today - timedelta(days=(n or 1) - 1), today
        case "LAST_N_MONTHS":
            total = today.year * 12 + today.month - 1 - ((n or 1) - 1)
            start, end = date(total // 12, total % 12 + 1, 1), today
        case _:
            return (period, str(n))
    return (start.isoformat(), end.isoformat())


@dataclass
class GroupScore:
    """One functional capability. A single accuracy number says the assistant is 85%
    right and nothing about what it is wrong at; this says which capability to fix."""

    cases: int = 0
    outcome_right: int = 0
    exact: int = 0
    plans: int = 0


@dataclass
class Score:
    cases: int = 0
    outcome_right: int = 0
    exact: int = 0
    invented: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    field_right: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    field_total: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    structural: int = 0
    latencies: list[float] = field(default_factory=list)
    wrong: list[tuple[str, str]] = field(default_factory=list)
    groups: dict[str, GroupScore] = field(default_factory=dict)
    # A case and its paraphrase say the same thing differently. Getting one right and
    # the other wrong is string matching rather than reading, and the aggregate hides it.
    paraphrase_splits: list[str] = field(default_factory=list)
    right_by_id: dict[str, bool] = field(default_factory=dict)


def expected_fields(plan: dict[str, Any], today: date) -> dict[str, Any]:
    """Only the fields the sentence determines. A field the dataset does not mention is
    not scored: inventing an expectation for it would punish a correct default."""
    out: dict[str, Any] = {}
    if "period" in plan:
        period = plan["period"]
        out["window"] = resolve(period["period"], period.get("n"), today)
    if "dateRange" in plan:
        out["window"] = (str(plan["dateRange"]["from"]), str(plan["dateRange"]["to"]))
    if "direction" in plan:
        out["direction"] = plan["direction"]
    if "categories" in plan:
        out["categories"] = tuple(sorted(plan["categories"]))
    if "groupBy" in plan:
        out["groupBy"] = plan["groupBy"]
    if "metric" in plan:
        out["metric"] = plan["metric"]
    return out


def actual_fields(result: PlanningResult, today: date) -> dict[str, Any]:
    plan = result.plan
    assert plan is not None
    if plan.period is not None:
        window: Any = resolve(plan.period.period.value, plan.period.n, today)
    else:
        assert plan.date_range is not None
        window = (plan.date_range.start.isoformat(), plan.date_range.end.isoformat())
    return {
        "window": window,
        "direction": plan.direction.value,
        "categories": tuple(sorted(plan.categories)),
        "groupBy": plan.group_by.value,
        "metric": plan.metric.value,
    }


def judge(case: dict[str, Any], result: PlanningResult, score: Score, today: date) -> None:
    score.cases += 1
    group = score.groups.setdefault(case.get("group", "ungrouped"), GroupScore())
    group.cases += 1
    wanted = case["expect"]
    got = result.outcome

    if got is PlanningOutcome.INVALID_STRUCTURE:
        score.structural += 1

    wanted_outcome = {
        "plan": PlanningOutcome.PLANNED,
        "clarification": PlanningOutcome.NEEDS_CLARIFICATION,
        "unsupported": PlanningOutcome.UNSUPPORTED,
    }[wanted]

    if got is wanted_outcome:
        score.outcome_right += 1
        group.outcome_right += 1
    elif wanted != "plan" and got is PlanningOutcome.PLANNED:
        score.invented.append(case["id"])
    elif wanted == "plan" and got in (
        PlanningOutcome.NEEDS_CLARIFICATION,
        PlanningOutcome.UNSUPPORTED,
    ):
        score.missed.append(case["id"])

    if wanted != "plan":
        return

    # Per-field scoring only where both sides have a plan. When the model produced none,
    # every field the sentence determined counts as wrong — otherwise refusing
    # everything would leave the per-field table looking untouched.
    expected = expected_fields(case["plan"], today)
    group.plans += 1
    for name in expected:
        score.field_total[name] += 1

    if got is not PlanningOutcome.PLANNED:
        score.wrong.append((case["id"], f"{got.value}: {result.reason or result.question}"))
        return

    actual = actual_fields(result, today)
    mismatches = [n for n, want in expected.items() if actual[n] != want]
    for name in expected:
        if name not in mismatches:
            score.field_right[name] += 1

    score.right_by_id[case["id"]] = not mismatches
    if mismatches:
        detail = ", ".join(f"{n}={actual[n]!r} != {expected[n]!r}" for n in mismatches)
        score.wrong.append((case["id"], detail))
    else:
        score.exact += 1
        group.exact += 1


def report(model: str, score: Score) -> None:
    total = score.cases
    print(f"\n=== {model} · {total} cases ===\n")
    print(
        f"  outcome correct     {score.outcome_right:3}/{total}  {pct(score.outcome_right, total)}"
    )
    plans = score.field_total["direction"] or 1
    print(f"  exact plan match    {score.exact:3}/{plans}  {pct(score.exact, plans)}")
    print(f"  INVENTED            {len(score.invented):3}      a plan where a question was due")
    print(f"  missed              {len(score.missed):3}      a question where a plan was due")
    print(f"  invalid structure   {score.structural:3}      should be 0 under constrained decoding")

    if score.groups:
        # The table worth reading. An aggregate says the assistant is 85% right and
        # nothing about what it is wrong at; this says which capability to go and fix.
        print("\n  per group")
        for name, group in sorted(score.groups.items()):
            plans = f"{group.exact}/{group.plans}" if group.plans else "-"
            print(
                f"    {name:20} outcome {group.outcome_right:3}/{group.cases:<3}"
                f" {pct(group.outcome_right, group.cases)}   exact {plans:>7}"
            )

    print("\n  per field")
    for name in FIELDS:
        got, want = score.field_right[name], score.field_total[name]
        if want:
            print(f"    {name:12} {got:3}/{want}  {pct(got, want)}")

    if score.latencies:
        ordered = sorted(score.latencies)
        p50 = statistics.median(ordered)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(f"\n  latency  P50 {p50:.2f}s   P95 {p95:.2f}s   max {ordered[-1]:.2f}s")

    if score.paraphrase_splits:
        print("\n  said differently, answered differently")
        for pair in score.paraphrase_splits:
            print(f"    {pair}")

    if score.invented:
        print(f"\n  invented: {', '.join(score.invented)}")
    if score.wrong:
        print("\n  wrong plans")
        for case_id, detail in score.wrong:
            print(f"    {case_id:34} {detail}")


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    -"


async def run(
    model: str,
    limit: int | None,
    base_url: str,
    dataset: str = "dev",
    collect: list[tuple[int, int]] | None = None,
) -> int:
    data = yaml.safe_load((PLANNING / f"{dataset}.yaml").read_text(encoding="utf-8"))
    today = data["reference"]
    cases = data["cases"][:limit] if limit else data["cases"]

    planner = LlmQueryPlanner(
        OllamaLlmProvider(base_url=base_url),
        model=model,
        temperature=0.0,
        max_output_tokens=384,
        categories=CATEGORIES,
    )

    score = Score()
    for case in cases:
        started = time.perf_counter()
        result = await planner.plan(case["question"], today=today, previous=case.get("previous"))
        score.latencies.append(time.perf_counter() - started)

        if result.outcome is PlanningOutcome.PROVIDER_ERROR:
            print(f"provider failed on {case['id']}: {result.reason}", file=sys.stderr)
            return 2

        judge(case, result, score, today)

    _check_paraphrases(cases, score)

    if collect is None:
        report(model, score)
    else:
        # Repeated runs: the first one prints the detail, the rest only add their totals
        # so the spread can be shown without repeating a page of failures.
        collect.append((score.outcome_right, score.exact))
        if len(collect) == 1:
            report(model, score)
    return 0


def _check_paraphrases(cases: list[dict[str, Any]], score: Score) -> None:
    for case in cases:
        twin = case.get("paraphrase_of")
        if twin is None:
            continue
        mine, theirs = score.right_by_id.get(case["id"]), score.right_by_id.get(twin)
        if mine is not None and theirs is not None and mine != theirs:
            winner, loser = (twin, case["id"]) if theirs else (case["id"], twin)
            score.paraphrase_splits.append(f"{winner} right, {loser} wrong")


async def repeated(model: str, limit: int | None, base_url: str, dataset: str, times: int) -> int:
    """Runs the set `times` over and report the spread as well as the numbers.

    A single run is a point estimate of something noisy. Reporting min-max next to the
    mean is what stops the next person reading a two-point difference as a result.
    """
    scores: list[tuple[int, int]] = []
    for attempt in range(times):
        code = await run(model, limit, base_url, dataset, collect=scores)
        if code:
            return code
        if times > 1:
            outcome, exact = scores[-1]
            print(f"  run {attempt + 1}: outcome {outcome}, exact {exact}")

    if times > 1:
        outcomes = [o for o, _ in scores]
        exacts = [e for _, e in scores]
        print(
            f"\n  over {times} runs   outcome {min(outcomes)}-{max(outcomes)}"
            f"   exact {min(exacts)}-{max(exacts)}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Ollama model tag, e.g. qwen3.5:4b")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--dataset",
        default="dev",
        choices=("dev", "test", "adversarial"),
        help="dev is what the prompt was tuned against and reads optimistically; test "
        "has never been seen by the prompt and is the number worth quoting.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the whole dataset N times and report the spread. Local inference is "
        "not bit-reproducible even at temperature 0, so a single run of a 60-case set "
        "carries a few points of noise — enough to invent an improvement.",
    )
    args = parser.parse_args()
    return asyncio.run(repeated(args.model, args.limit, args.base_url, args.dataset, args.repeat))


if __name__ == "__main__":
    raise SystemExit(main())
