"""Replay adversarial conversations with real local planning and simulated banking.

Run from apps/orchestrator: uv run python scripts/evaluate_conversation_ux.py
No real payment or banking endpoint is called. Reports retain synthetic dialogue only.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault(
    "ORCHESTRATOR_DEV_TOKENS",
    '["ux-test-token-0001:ES-TEST:11111111-1111-1111-1111-111111111111"]',
)

import yaml  # noqa: E402
from support import episodes  # noqa: E402

from finguard_orchestrator.bootstrap.config import get_settings  # noqa: E402
from finguard_orchestrator.domain.services import category_words  # noqa: E402
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import (  # noqa: E402
    OllamaLlmProvider,
)
from finguard_orchestrator.infrastructure.planning.llm_query_planner import (  # noqa: E402
    LlmQueryPlanner,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--case", help="Run only names containing this text")
    parser.add_argument("--stub", action="store_true", help="Routing only; no real model")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/ux/latest.json")
    args = parser.parse_args()
    settings = get_settings()
    provider = OllamaLlmProvider(
        base_url="http://127.0.0.1:11434",
        keep_alive=settings.ollama_keep_alive,
        timeout_seconds=settings.ollama_timeout_seconds,
        context_tokens=settings.ollama_context_tokens,
        seed=settings.ollama_seed,
        think=settings.ollama_think,
    )
    planner = LlmQueryPlanner(
        provider,
        model=args.model,
        temperature=settings.planner_temperature,
        max_output_tokens=settings.planner_max_output_tokens,
        categories=category_words.keys(),
    )

    class LivePlanner:
        def __init__(self):
            self.asked = []

        async def plan(self, sentence, *, context):
            self.asked.append(sentence)
            return await planner.plan(sentence, context=context)

    data = yaml.safe_load((ROOT / "evaluation/ux/conversations.yaml").read_text(encoding="utf-8"))
    selected = [raw for raw in data["episodes"] if not args.case or args.case in raw["name"]]
    if not selected:
        parser.error("No conversations match --case")
    if any(not isinstance(turn["say"], str) for raw in selected for turn in raw["turns"]):
        parser.error('Every say must be text; quote YAML words such as "no"')
    report = {
        "model": None if args.stub else args.model,
        "banking": "simulated",
        "payment_reader": "simulated",
        "reference_date": str(episodes.TODAY),
        "turns": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)
    failures = 0
    with (
        patch.object(episodes, "AnyPlan", episodes.AnyPlan if args.stub else LivePlanner),
        patch.object(episodes, "SHOPS", (*episodes.SHOPS, "Netflix")),
    ):
        for raw in selected:
            episode = episodes.Episode(
                name=raw["name"],
                why=raw["why"],
                scope=raw.get("scope", episodes.SETTLED),
                turns=tuple(episodes.Turn(**turn) for turn in raw["turns"]),
            )
            started = time.perf_counter()
            for number, turn, answer, reached, ran, frames in episodes.replay(episode):
                elapsed = time.perf_counter() - started
                complaints = (
                    list(
                        episodes._complaints(
                            turn,
                            answer,
                            reached_model=reached,
                            ran=ran,
                            frames=frames,
                        )
                    )
                    if answer
                    else ["HTTP error"]
                )
                failures += bool(complaints)
                report["turns"].append(
                    {
                        "episode": episode.name,
                        "turn": number,
                        "user": turn.say,
                        "answer": answer["text"] if answer else None,
                        "outcome": answer["outcome"] if answer else None,
                        "plans": [episodes.executed(plan) for plan in ran],
                        "model_called": reached,
                        "seconds": round(elapsed, 2),
                        "failures": complaints,
                    }
                )
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{episode.name}/{number}: {'FAIL' if complaints else 'PASS'} ({elapsed:.1f}s)",
                    flush=True,
                )
                started = time.perf_counter()
    print(f"{len(report['turns'])} turns; {failures} failed; {args.output}")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
