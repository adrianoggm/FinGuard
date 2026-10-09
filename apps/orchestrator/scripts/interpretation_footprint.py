"""How much code reads the person's words: the rules engine against the model engine.

    uv run python scripts/interpretation_footprint.py [--json]

Counts non-blank, non-comment lines of the files in fixed lists, so the comparison between the two
engines is a number that anyone can re-run. Docstrings count: they are lines of the file.

Four groups, kept apart on purpose:

* `rules`: what interprets a sentence in the rules engine. `ask_financial_question.py` is counted
  whole, although only part of it is turn interpretation; the figure is an upper bound.
* `engine`: what interprets a sentence in the model engine (schema, prompt, the model call).
* `non_linguistic`: engine code that never looks at a sentence: grounding is a name lookup,
  transitions and the executor apply operations, the rest is state and plumbing.
* `storage`: the dialogue store, which is persistence and says nothing about interpretation.

Every file counted is named in the output, and a listed file that does not exist is reported under
`missing` instead of being skipped.
"""

import argparse
import json
from pathlib import Path
from typing import Any

PACKAGE = "src/finguard_orchestrator"

SERVICES = "domain/services"
RULES = [
    "application/queries/readers/*.py",
    f"{SERVICES}/turn_arbiter.py",
    f"{SERVICES}/window_words.py",
    f"{SERVICES}/category_words.py",
    f"{SERVICES}/account_words.py",
    f"{SERVICES}/payment_words.py",
    f"{SERVICES}/merchant_words.py",
    f"{SERVICES}/explicit_facts.py",
    f"{SERVICES}/continuation_patch.py",
    f"{SERVICES}/plan_verifier.py",
    f"{SERVICES}/capability_guard.py",
    f"{SERVICES}/intent_lexicon.py",
    f"{SERVICES}/query_recovery.py",
    "infrastructure/interpreting/*.py",
    "infrastructure/planning/prompt.py",
    "infrastructure/planning/plan_schema.py",
    "infrastructure/planning/llm_query_planner.py",
    "application/queries/ask_financial_question.py",
]
ENGINE = [
    "infrastructure/engine/operations_*.py",
    "infrastructure/engine/llm_turn_operations.py",
]
NON_LINGUISTIC = [
    "domain/engine/grounding.py",
    "domain/engine/operations.py",
    "domain/engine/state.py",
    "domain/engine/transitions.py",
    "application/engine/answer_turn.py",
    "application/engine/executor.py",
    "application/engine/presenter.py",
    "infrastructure/controllers/engine_dispatch.py",
]
STORAGE = ["infrastructure/engine/memory_dialogue_store.py"]

GROUPS = {
    "rules": RULES,
    "engine": ENGINE,
    "non_linguistic": NON_LINGUISTIC,
    "storage": STORAGE,
}


def default_root() -> Path:
    return Path(__file__).resolve().parents[1] / PACKAGE


def count_lines(path: Path) -> int:
    """Non-blank lines that do not start with `#`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def measure(root: Path, patterns: list[str]) -> dict[str, Any]:
    files: dict[str, int] = {}
    missing: list[str] = []
    for pattern in patterns:
        found = sorted(path for path in root.glob(pattern) if path.is_file())
        if not found:
            missing.append(pattern)
        for path in found:
            files[path.relative_to(root).as_posix()] = count_lines(path)
    return {"lines": sum(files.values()), "files": files, "missing": missing}


def build(root: Path) -> dict[str, Any]:
    return {group: measure(root, patterns) for group, patterns in GROUPS.items()}


def render(report: dict[str, Any]) -> str:
    out: list[str] = []
    for group, measured in report.items():
        out.append(f"{group}: {measured['lines']} lines in {len(measured['files'])} files")
        out += [f"  {count:5d}  {name}" for name, count in measured["files"].items()]
        out += [f"  MISSING  {pattern}" for pattern in measured["missing"]]
    rules, engine = report["rules"]["lines"], report["engine"]["lines"]
    out.append("")
    out.append(
        f"interpretation: rules {rules} lines, engine {engine} lines"
        + (f" ({engine / rules:.0%} of the rules)" if rules else "")
    )
    out.append(
        "note: ask_financial_question.py is counted whole in `rules`; only part of it is turn "
        "interpretation."
    )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args()
    report = build(default_root())
    print(json.dumps(report, indent=2) if options.json else render(report))
    return 1 if any(group["missing"] for group in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
