"""A judge that checks what can be checked mechanically.

It is not a stand-in for reading an answer: it verifies the properties a correct result
must have regardless of wording — the figure reconciles with its own evidence, the
grouping actually groups, nothing is dated in the future. Those are exactly the
properties an LLM judge is worst at and a rule is best at.

When an LLM judge arrives it fulfils the same port and scores the requirements this one
cannot: whether the answer reads as an answer, whether it covers the question asked.
"""

from datetime import UTC, datetime

from finguard_banking.domain.models.financial_query_result import FinancialQueryResult
from finguard_banking.domain.ports.result_judge import Assessment, Requirement, Verdict


def _met(requirement: str, ok: bool, reason: str) -> Verdict:
    return Verdict(requirement=requirement, score=1.0 if ok else 0.0, reason=reason)


class RuleBasedJudge:
    """Scores the mechanical requirements. Unknown ones score 0, loudly."""

    def assess(
        self, result: FinancialQueryResult, requirements: tuple[Requirement, ...]
    ) -> Assessment:
        return Assessment(tuple(self._score(result, r.id) for r in requirements))

    def _score(self, result: FinancialQueryResult, requirement: str) -> Verdict:
        match requirement:
            case "total_matches_groups":
                total = sum(group.total.amount_minor for group in result.groups)
                return _met(
                    requirement,
                    total == result.total.amount_minor,
                    f"groups add up to {total}, total says {result.total.amount_minor}",
                )

            case "total_matches_evidence":
                # Only checkable when every movement came back; with a limit the
                # evidence is a sample and cannot reconcile.
                shown = sum(m.amount.amount_minor for m in result.supporting_movements)
                if len(result.supporting_movements) < result.movement_count:
                    return _met(
                        requirement,
                        True,
                        f"{len(result.supporting_movements)} of {result.movement_count} shown, "
                        "reconciliation not applicable",
                    )
                return _met(
                    requirement,
                    shown == result.total.amount_minor,
                    f"evidence adds up to {shown}, total says {result.total.amount_minor}",
                )

            case "is_grouped":
                # The property is that grouping happened, not that the data happened to
                # span several groups: a month with one merchant has one group, and that
                # is the right answer. "More than one" was a badly specified rubric.
                grouped = bool(result.groups) and all(g.key != "total" for g in result.groups)
                return _met(
                    requirement,
                    grouped,
                    f"{len(result.groups)} groups, keys {[g.key for g in result.groups][:3]}",
                )

            case "groups_are_ordered":
                keys = [group.key for group in result.groups]
                return _met(requirement, keys == sorted(keys), f"order: {keys}")

            case "every_movement_has_a_merchant":
                missing = [m for m in result.supporting_movements if not m.merchant_name]
                return _met(requirement, not missing, f"{len(missing)} without a merchant")

            case "every_movement_has_a_category":
                missing = [m for m in result.supporting_movements if not m.category]
                return _met(requirement, not missing, f"{len(missing)} without a category")

            case "no_future_movements":
                now = datetime.now(UTC)
                ahead = [m for m in result.supporting_movements if m.occurred_at > now]
                return _met(requirement, not ahead, f"{len(ahead)} dated in the future")

            case _:
                # A requirement this judge cannot score must not silently pass: that is
                # how a rubric quietly stops measuring anything.
                return Verdict(
                    requirement=requirement,
                    score=0.0,
                    reason="this judge cannot score it; an LLM judge has to",
                )
