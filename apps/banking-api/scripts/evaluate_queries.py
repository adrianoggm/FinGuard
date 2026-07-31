"""Measures the query engine (vision.md §17).

Two kinds of case, because two kinds of question:

  deterministic  evaluation/queries.yaml — one right answer. The figure either matches
                 what evaluation/expected.sql computed, or it does not.
  heuristic      evaluation/heuristic.yaml — no single exact answer. A judge scores the
                 result against a rubric and the case passes on the score.

Today the judge is rule-based: it verifies the properties a correct result must have
regardless of wording. When an LLM judge lands it fulfils the same port and scores what
a rule cannot.

It exits non-zero if any case fails, so it works both as a report and as a check.

    just evaluate          # needs `just seed` first

Queries go through the read-only pool, the same role the service uses: if a query
needed extra privileges, it would fail here too.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.financial_query_result import FinancialQueryResult
from finguard_banking.domain.ports.result_judge import Requirement
from finguard_banking.infrastructure.dto.banking_dto import FinancialQueryRequest
from finguard_banking.infrastructure.evaluation.rule_based_judge import RuleBasedJudge
from finguard_banking.infrastructure.mappers import http_mapper
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)

EVALUATION = Path(__file__).resolve().parent.parent / "evaluation"
DETERMINISTIC = EVALUATION / "queries.yaml"
HEURISTIC = EVALUATION / "heuristic.yaml"


@dataclass
class Outcome:
    case: str
    passed: bool
    detail: str
    kind: str = "deterministic"


def _compare(case: dict[str, Any], actual: FinancialQueryResult) -> Outcome:
    expected = case["expected"]
    failures: list[str] = []

    if actual.total.amount_minor != expected["totalMinor"]:
        failures.append(f"total {actual.total.amount_minor} != {expected['totalMinor']}")
    if actual.movement_count != expected["movementCount"]:
        failures.append(f"movements {actual.movement_count} != {expected['movementCount']}")

    if "groups" in expected:
        groups = [
            {"key": g.key, "totalMinor": g.total.amount_minor, "movementCount": g.movement_count}
            for g in actual.groups
        ]
        if groups != expected["groups"]:
            failures.append(f"groups {groups} != {expected['groups']}")

    detail = "; ".join(failures) if failures else str(actual.total)
    return Outcome(case=case["id"], passed=not failures, detail=detail)


def _judge(case: dict[str, Any], actual: FinancialQueryResult) -> Outcome:
    requirements = tuple(
        Requirement(id=item["id"], weight=float(item.get("weight", 1)))
        for item in case["requirements"]
    )
    assessment = RuleBasedJudge().assess(actual, requirements)
    threshold = float(case.get("threshold", 1.0))
    failures = "; ".join(f"{v.requirement}: {v.reason}" for v in assessment.failures())
    detail = f"score {assessment.score:.2f}" + (f" — {failures}" if failures else "")
    return Outcome(
        case=case["id"], passed=assessment.score >= threshold, detail=detail, kind="heuristic"
    )


async def evaluate(cases: list[dict[str, Any]], heuristic: list[dict[str, Any]]) -> int:
    settings = get_settings()

    owner = await asyncpg.connect(settings.database_url)
    reader: asyncpg.Pool[Any] = await asyncpg.create_pool(settings.query_database_url)
    try:
        accounts = {
            row["account_reference"]: row["id"]
            for row in await owner.fetch("SELECT id, account_reference FROM accounts")
        }
        if not accounts:
            print("The database is empty: run `just seed` first.", file=sys.stderr)
            return 2

        repository = PostgresFinancialQueryRepository(reader, settings.reporting_timezone)
        outcomes: list[Outcome] = []

        for case in cases:
            plan = dict(case["plan"])
            # A relative period without a reference would give a different result
            # every day.
            if "period" in plan and "reference" not in plan["period"]:
                print(f"{case['id']}: the relative period does not pin reference", file=sys.stderr)
                return 2

            plan["accountId"] = str(accounts[case["account"]])
            request = FinancialQueryRequest.model_validate(plan)
            query = http_mapper.query_from_dto(request, date.today())
            outcomes.append(_compare(case, await repository.run(query)))

        for case in heuristic:
            plan = dict(case["plan"])
            if "period" in plan and "reference" not in plan["period"]:
                print(f"{case['id']}: the relative period does not pin reference", file=sys.stderr)
                return 2
            plan["accountId"] = str(accounts[case["account"]])
            request = FinancialQueryRequest.model_validate(plan)
            query = http_mapper.query_from_dto(request, date.today())
            outcomes.append(_judge(case, await repository.run(query)))
    finally:
        await reader.close()
        await owner.close()

    passed = sum(1 for outcome in outcomes if outcome.passed)
    total = len(outcomes)
    width = max(len(outcome.case) for outcome in outcomes)
    for outcome in outcomes:
        mark = "ok  " if outcome.passed else "FAIL"
        print(f"  {mark} {outcome.case:<{width}}  {outcome.detail}")

    print(f"\nexecution accuracy: {passed}/{total} ({passed / total:.1%})")
    return 0 if passed == total else 1


if __name__ == "__main__":
    # It accepts another case file as an argument, which is how we check that this
    # checker can actually fail.
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DETERMINISTIC
    heuristic_path = HEURISTIC if len(sys.argv) <= 1 else None
    raise SystemExit(
        asyncio.run(
            evaluate(
                yaml.safe_load(path.read_text(encoding="utf-8")),
                yaml.safe_load(heuristic_path.read_text(encoding="utf-8"))
                if heuristic_path is not None
                else [],
            )
        )
    )
