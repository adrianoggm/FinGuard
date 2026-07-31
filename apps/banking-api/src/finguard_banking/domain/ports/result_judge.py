"""Scores a result against a rubric.

Deterministic cases are asserted; heuristic ones are scored. The port exists so that
today's rule-based judge and tomorrow's LLM judge are interchangeable: the harness and
the dataset do not change when the judge does.
"""

from dataclasses import dataclass
from typing import Protocol

from finguard_banking.domain.models.financial_query_result import FinancialQueryResult


@dataclass(frozen=True)
class Requirement:
    """One line of the rubric."""

    id: str
    weight: float = 1.0


@dataclass(frozen=True)
class Verdict:
    """How well one requirement was met, and why."""

    requirement: str
    score: float
    reason: str


@dataclass(frozen=True)
class Assessment:
    verdicts: tuple[Verdict, ...]

    @property
    def score(self) -> float:
        """Weighted score in 0..1. An empty rubric scores 0, never 1: a case with no
        requirements has not been evaluated."""
        if not self.verdicts:
            return 0.0
        return sum(v.score for v in self.verdicts) / len(self.verdicts)

    def failures(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.score < 1.0)


class ResultJudgePort(Protocol):
    def assess(
        self, result: FinancialQueryResult, requirements: tuple[Requirement, ...]
    ) -> Assessment: ...
