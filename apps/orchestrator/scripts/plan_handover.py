"""The plans a model produces for the turns this service hands over.

    uv run python scripts/plan_handover.py --model qwen2.5-coder:7b --out plans.yaml
    uv run python scripts/judge_plans.py --model qwen3.5:9b --plans plans.yaml --group anchor

The step between `handover_cases.py` and `judge_plans.py`, and the reason the judge could not be
run: it takes a mapping of sentence to plan and nothing produced one. `evaluate_planning.py` runs
the same pipeline but only over the datasets somebody wrote by hand, which is precisely the set
these turns are *not* in.

**The pipeline as it ships, not the model alone.** `_planned` is imported rather than reimplemented,
so the guard, the deterministic rescue, the explicit-fact enrichment and the verifier all run — a
plan judged without them would be a judgement about a system nobody deploys.

**Two models, on purpose.** The planner here and the judge there should not be the same one: a model
agreeing with itself measures nothing. Which is also why the judge is validated against the anchor
group first, where a person already wrote what should happen.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_planning import CATEGORIES, _planned, build_provider  # noqa: E402
from handover_cases import cases  # noqa: E402

# The reference day the corpus is written against. Read from the same place the cases are, so a
# window resolved here means what it means there.
from support import episodes  # noqa: E402

from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan  # noqa: E402
from finguard_orchestrator.infrastructure.dto.llm_dto import PlanPayload  # noqa: E402
from finguard_orchestrator.infrastructure.mappers import conversation_mapper  # noqa: E402
from finguard_orchestrator.infrastructure.planning.llm_query_planner import (  # noqa: E402
    LlmQueryPlanner,
)


def _previous_of(case: dict[str, Any]) -> FinancialPlan | None:
    """The query in view when the turn arrived, read the way the API reads it.

    The case carries the payload a client echoes back, so this is `query_to_domain` and not a
    second reader — the handover cases and the endpoint agree about what a plan is because they
    use the same function to say so.
    """
    written = case.get("previous")
    if not written:
        return None
    plan = conversation_mapper.query_to_domain(PlanPayload.model_validate(written))
    return plan if isinstance(plan, FinancialPlan) else None


def _as_written(result: Any) -> dict[str, Any] | None:
    """The plan in the vocabulary the cases already speak, or None when nothing was planned.

    Trimmed of the fields it did not set, for the reason a case is: a plan printed in full is
    twenty keys of `[]` around the three that matter, and the judge reads this.
    """
    if result.plan is None:
        return None
    payload = conversation_mapper.plan_payload(result.plan).model_dump(mode="json")
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


async def run(arguments: argparse.Namespace) -> dict[str, Any]:
    chosen = [case for case in cases() if arguments.group in (case["group"], "all")]
    if arguments.limit:
        chosen = chosen[: arguments.limit]

    planner = LlmQueryPlanner(
        build_provider(arguments.provider, arguments.base_url, arguments.think, None),
        model=arguments.model,
        temperature=0.0,
        max_output_tokens=384,
        categories=CATEGORIES,
    )

    planned: dict[str, Any] = {}
    declined: list[str] = []
    for number, case in enumerate(chosen, start=1):
        if case["say"] in planned:
            # The corpus repeats itself — eight episodes open with "¿cuánto gasté este mes?" — and
            # the plans file is keyed by sentence. Planning it again would cost a call to answer a
            # question already answered, and the second answer would silently replace the first.
            continue
        result = await _planned(
            planner, {"question": case["say"]}, today=episodes.TODAY, previous=_previous_of(case)
        )
        written = _as_written(result)
        if written is None:
            # Written down as `null` rather than left out. A turn the pipeline declined is an
            # outcome and not a gap, and the judge tells the two apart: a sentence missing from
            # this file means nobody ran the planner, which is a different thing to report.
            declined.append(case["say"])
        planned[case["say"]] = written
        if arguments.verbose:
            print(f"  {number}/{len(chosen)} {case['say']!r}")

    print(f"read {len(planned)} distinct sentences of {len(chosen)} cases in {arguments.group}")
    print(f"  declined or refused: {len(declined)}")
    for say in declined[:5]:
        print(f"    {say!r}")
    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan the handed-over turns for a judge.")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--group", default="anchor", choices=["anchor", "extension", "all"])
    parser.add_argument("--limit", type=int, default=0, help="stop after this many cases")
    parser.add_argument("--out", default="plans.yaml")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    planned = asyncio.run(run(arguments))
    Path(arguments.out).write_text(
        "# Generated by scripts/plan_handover.py. Regenerate; do not edit.\n"
        + yaml.safe_dump(planned, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"wrote {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
