"""Does the model choose a chart a person would have chosen, and does it differ from the floor?

One run is not a number: the planner is not bit-reproducible at temperature 0, and a contended GPU
changes its answers. Run three times with the GPU alone and take the minimum, the way
`calibrate_robustness_gate.py` does for robustness.

    uv run python scripts/evaluate_visual.py --provider ollama --model qwen3.5:9b
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_planning  # noqa: E402
from export_chart_fixtures import FAMILY_DATASETS  # noqa: E402

from finguard_orchestrator.bootstrap.config import get_settings  # noqa: E402
from finguard_orchestrator.domain.models.chart import (  # noqa: E402
    DEFAULT_PRESENTATION,
    ChartSpec,
    VisualDataset,
    VisualProfile,
    VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import canonical  # noqa: E402
from finguard_orchestrator.domain.services.deterministic_visual_planner import (  # noqa: E402
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify  # noqa: E402
from finguard_orchestrator.domain.services.visual_planning_policy import (  # noqa: E402
    VisualPlanningPolicy,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of  # noqa: E402
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import (  # noqa: E402
    LlmVisualPlanner,
)

VISUAL = Path(__file__).resolve().parents[1] / "evaluation" / "visual"
CASES_PATH = VISUAL / "cases.yaml"

PROFILES: dict[str, VisualProfile] = {
    name: visual_profile_of(
        dataset,
        total_minor=kwargs.get("total_minor"),
        group_by=kwargs["group_by"],
        plan_limited=kwargs.get("plan_limited", False),
        plan_metric=kwargs.get("plan_metric"),
    )
    for name, (dataset, kwargs) in FAMILY_DATASETS.items()
}
ONE_ROW = VisualDataset(metric="SUM", rows=(VisualRow("Total", 1),))


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    dataset: str
    question: str
    suitable_marks: tuple[str, ...] = ()
    paraphrase_of: str | None = None
    visual_instruction: bool = False
    expect_chart: bool = True
    plan_limited: bool = False


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Case(**{**item, "suitable_marks": tuple(item.get("suitable_marks", ()))}) for item in raw
    ]


@dataclass
class Results:
    entered: int = 0
    valid: int = 0
    suitable: int = 0
    changed: int = 0
    valid_suitable_changed: int = 0
    latencies: list[int] = field(default_factory=list)
    fallback_reasons: Counter = field(default_factory=Counter)
    rejected_rules: Counter = field(default_factory=Counter)
    no_chart_total: int = 0
    no_chart_held: int = 0
    by_group: dict[str, Counter] = field(default_factory=dict)


def judge(case: Case, decision: VisualDecision | None, results: Results) -> None:
    group = results.by_group.setdefault(case.group, Counter())
    if not case.expect_chart:
        results.no_chart_total += 1
        if decision is None:
            results.no_chart_held += 1
        return
    if decision is None:
        return
    results.entered += 1
    group["entered"] += 1
    if decision.latency_ms is not None:
        results.latencies.append(decision.latency_ms)
    if decision.fallback_reason is not None:
        results.fallback_reasons[decision.fallback_reason] += 1
        if decision.rejected_rule is not None:
            results.rejected_rules[decision.rejected_rule] += 1
    if not decision.valid or decision.proposed_spec is None:
        return
    results.valid += 1
    group["valid"] += 1
    suitable = decision.proposed_spec.mark in case.suitable_marks
    changed = bool(decision.changed_from_baseline)
    results.suitable += suitable
    results.changed += changed
    group["suitable"] += suitable
    if suitable and changed:
        results.valid_suitable_changed += 1
        group["valid_suitable_changed"] += 1


@dataclass
class Stability:
    pairs: int = 0
    held: int = 0
    instructed: int = 0
    sensitive: int = 0
    skipped: int = 0
    """Pairs dropped because one side (or both) had no verified proposal -- neither `pairs` nor
    `instructed` counts these, so without this a run where the model's proposals are mostly
    rejected prints a stability of 0/0 that reads as a result rather than as missing data."""
    breaks: list[str] = field(default_factory=list)


def stability(cases: list[Case], proposals: dict[str, Any]) -> Stability:
    out = Stability()
    for case in cases:
        if case.paraphrase_of is None:
            continue
        mine, twin = proposals.get(case.id), proposals.get(case.paraphrase_of)
        if mine is None or twin is None:
            out.skipped += 1
            continue
        same = canonical(mine) == canonical(twin)
        if case.visual_instruction:
            out.instructed += 1
            out.sensitive += not same
        else:
            out.pairs += 1
            out.held += same
            if not same:
                out.breaks.append(f"{case.id}: differs from {case.paraphrase_of}")
    return out


def _profile_for(case: Case) -> VisualProfile:
    if case.dataset == "none":
        return visual_profile_of(ONE_ROW, total_minor=1, group_by="NONE")
    profile = PROFILES[case.dataset]
    if case.plan_limited:
        # The flag lives on the case, `PROFILES` is per dataset: without this, `ranking_top3`
        # measures nothing -- the verifier's `limit` rule (Task 6) only refuses a limited
        # proposal when the profile itself says the rows are already the answer's top N.
        profile = replace(profile, plan_limited=True)
    return profile


def _percentiles(latencies: list[int]) -> tuple[float | None, float | None]:
    ordered = sorted(latencies)
    if not ordered:
        return None, None
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return statistics.median(ordered), float(ordered[p95_index])


def _report(results: Results, stable: Stability) -> str:
    p50, p95 = _percentiles(results.latencies)
    latency = f"{p50:.0f} / {p95:.0f}" if p50 is not None and p95 is not None else "... / ..."
    latency += "   <- P95 is the budget candidate"
    timeouts = results.fallback_reasons.get("timeout", 0)
    if timeouts:
        # Included in the P50/P95 above, not excluded from it -- `evaluate_planning`'s formula is
        # shared for consistency (finding 8). A censored run otherwise reads as "6s is enough" when
        # the model needed more: this count is what tells a reader the P95 is self-fulfilling.
        latency += f" ({timeouts} at the budget ceiling)"

    held = f"{stable.held} / {stable.pairs}"
    sensitive = f"{stable.sensitive} / {stable.instructed}"
    if stable.skipped:
        # One counter, not split by pair kind: it tells a reader "0/0 is missing data, not a
        # result" on whichever of the two rows would otherwise read as a clean measurement.
        held += f" ({stable.skipped} pairs had no proposal)"
        sensitive += f" ({stable.skipped} instructed had no proposal)"

    rows = [
        ("cases entering the policy", str(results.entered)),
        ("validity", f"{results.valid} / {results.entered}"),
        ("suitability", f"{results.suitable} / {results.valid}"),
        ("changed_from_baseline", f"{results.changed} / {results.valid}"),
        (
            "**valid, suitable and changed**",
            f"{results.valid_suitable_changed} / {results.entered}"
            "   <- the figure that carries the argument",
        ),
        ("stability under equivalent paraphrase", held),
        ("sensitivity to a visual instruction", sensitive),
        ("no-chart cases kept out", f"{results.no_chart_held} / {results.no_chart_total}"),
        ("latency P50 / P95 (ms)", latency),
        (
            "fallback reasons",
            f"{dict(results.fallback_reasons)}; rejected rules {dict(results.rejected_rules)}",
        ),
    ]
    lines = ["| measure | value |", "| --- | --- |"]
    lines += [f"| {measure} | {value} |" for measure, value in rows]
    report = "\n".join(lines) + "\n"
    if stable.breaks:
        # The single most useful diagnostic a paraphrase run produces: which pair broke, not just
        # how many. Listed under the stability row rather than folded into the table, since a
        # table cell is the wrong shape for a variable-length list.
        report += "\nStability breaks:\n" + "\n".join(f"- {line}" for line in stable.breaks) + "\n"
    return report


def _group_table(results: Results) -> str:
    """Per-group counters, so a promote-to-hybrid decision can see which groups the model helps
    or hurts on -- `Results.by_group` was computed and thrown away before this existed."""
    lines = [
        "| group | entered | valid | suitable | valid_suitable_changed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for group in sorted(results.by_group):
        counts = results.by_group[group]
        lines.append(
            f"| {group} | {counts.get('entered', 0)} | {counts.get('valid', 0)} | "
            f"{counts.get('suitable', 0)} | {counts.get('valid_suitable_changed', 0)} |"
        )
    return "\n".join(lines) + "\n"


def _sample_line(cases: list[Case]) -> str:
    """The instrument's own size, computed from the loaded cases and never hard-coded -- so the
    header cannot drift from `cases.yaml` the way a written-out number would."""
    entering = sum(1 for case in cases if case.expect_chart)
    pairs = sum(
        1 for case in cases if case.paraphrase_of is not None and not case.visual_instruction
    )
    instructed = sum(
        1 for case in cases if case.paraphrase_of is not None and case.visual_instruction
    )
    return (
        f"{len(cases)} cases · {entering} enter the policy · "
        f"{pairs} equivalent paraphrase pairs · {instructed} instructed"
    )


async def run(args: argparse.Namespace) -> int:
    cases = load_cases(CASES_PATH)
    try:
        provider = evaluate_planning.build_provider(args.provider, None, False, None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not await provider.ping():
        print(f"{args.provider} did not answer", file=sys.stderr)
        return 2

    # Unbudgeted, on purpose: `VisualPlanningPolicy` wraps every call below in a `budget_seconds`
    # timeout, and on a fresh GPU session Ollama must load a 9B model on its first `complete` --
    # which routinely takes far longer than that budget. Without this, case 1 is recorded as a
    # `timeout` fallback with a censored latency and no proposal, and because it is the twin of
    # two paraphrase pairs, both pairs are then silently dropped by `stability`'s own skip too.
    await provider.complete("ok", model=args.model, temperature=0.0, max_output_tokens=1)

    manifest = await evaluate_planning.run_manifest(CASES_PATH, provider, args.model, len(cases))
    print(f"RUN-MANIFEST {json.dumps(manifest, sort_keys=True)}", flush=True)

    settings = get_settings()
    planner = LlmVisualPlanner(
        provider,
        model=args.model,
        temperature=settings.planner_temperature,
        max_output_tokens=settings.planner_max_output_tokens,
    )
    policy = VisualPlanningPolicy(
        model=planner,
        fallback=DeterministicVisualPlanner(),
        verifier=verify,
        mode="shadow",
        budget_seconds=args.budget,
    )

    results = Results()
    proposals: dict[str, ChartSpec | None] = {}
    records: list[dict[str, Any]] = []
    for case in cases:
        profile = _profile_for(case)
        decision = await policy.decide(case.question, profile, DEFAULT_PRESENTATION)
        judge(case, decision, results)
        proposals[case.id] = decision.proposed_spec if decision is not None else None
        records.append(
            {
                "id": case.id,
                "proposed": (
                    list(canonical(decision.proposed_spec))
                    if decision is not None and decision.proposed_spec is not None
                    else None
                ),
                "shown": list(canonical(decision.spec)) if decision is not None else None,
            }
        )

    stable = stability(cases, proposals)
    report = _report(results, stable)
    group_table = _group_table(results)
    sample = _sample_line(cases)
    print(f"{sample}\n\n{report}\n## Per group\n\n{group_table}")

    VISUAL.mkdir(parents=True, exist_ok=True)
    (VISUAL / "RESULTS.md").write_text(
        "# Visual planner evaluation\n\n"
        f"{sample}\n\n"
        "One run is not a number -- see the script's own docstring. Run three times with the "
        "GPU alone and take the minimum before treating any of these as a release figure.\n\n"
        f"{report}\n"
        "## Per group\n\n"
        f"{group_table}",
        encoding="utf-8",
    )
    (VISUAL / "last-run.json").write_text(
        json.dumps(
            {
                "manifest": manifest,
                "by_group": {group: dict(counts) for group, counts in results.by_group.items()},
                "stability": {
                    "pairs": stable.pairs,
                    "held": stable.held,
                    "instructed": stable.instructed,
                    "sensitive": stable.sensitive,
                    "skipped": stable.skipped,
                    "breaks": stable.breaks,
                },
                "cases": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="a model name the provider serves")
    parser.add_argument(
        "--provider",
        default="ollama",
        choices=("ollama", "groq", "cloudflare"),
        help="ollama runs locally; groq and cloudflare require credentials in .env and send "
        "every evaluated question over the internet",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=6.0,
        help="seconds the policy waits for the model before falling back to the floor",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
