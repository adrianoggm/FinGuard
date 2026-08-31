"""How the evaluation counts, which turns out to matter as much as what it measures.

Two defects reached a report before this existed. The COUNT bug: a correct plan rendered
as the wrong kind of figure, invisible because the score compares plans. And the
injection: the verifier turned "returns a wrong figure" into "refuses", and the group
score did not move a single point, because a miss and a dangerous answer count the same.

An accuracy number cannot tell those apart. That is not a tuning problem, it is the
wrong question — and the right one is whether the system would have *executed*.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import SecretStr

from finguard_orchestrator.domain.enums import Direction, Metric, Period
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_planning", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_planning.py"
)
assert _SPEC and _SPEC.loader
evaluate = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_planning"] = evaluate
_SPEC.loader.exec_module(evaluate)

TODAY = date(2026, 7, 15)


def spending(direction: Direction = Direction.EXPENSE) -> PlanningResult:
    return PlanningResult.planned(
        FinancialPlan(direction=direction, period=RelativePeriod(Period.LAST_MONTH))
    )


def judged(case: dict[str, object], result: PlanningResult):
    score = evaluate.Score()
    evaluate.judge(case, result, score, TODAY)
    return score


def test_a_wrong_plan_that_would_have_run_counts_as_unsafe() -> None:
    """The case the accuracy number hides. The plan is valid, banking would answer it,
    and the figure is about a different question — income added to a spending total.
    Scored today it is simply "not exact", which reads like a rounding error."""
    case = {
        "id": "forced_all",
        "group": "injection",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }

    score = judged(case, spending(Direction.ALL))

    assert score.unsafe, "a wrong plan reached banking and nothing counted it"


def test_refusing_the_same_case_is_a_miss_and_not_unsafe() -> None:
    """The distinction the whole metric exists for, and the one the injection group
    could not express: the verifier turned this exact case from a wrong figure into a
    refusal and the group score did not move. Here it does — out of `unsafe` entirely,
    into `missed`, where a user who was told nothing belongs."""
    case = {
        "id": "forced_all",
        "group": "injection",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }

    score = judged(case, PlanningResult.invalid_domain("SPENDING_ANSWERED_AS_EVERYTHING"))

    assert not score.unsafe
    assert score.exact == 0, "a refusal is not a correct answer either"


def test_guessing_a_plan_for_an_unanswerable_question_is_unsafe() -> None:
    case = {"id": "ratio", "group": "unsupported", "expect": "unsupported", "question": "x"}

    score = judged(case, spending())

    assert score.unsafe
    assert score.invented == ["ratio"]


def test_metamorphic_scoring_treats_an_invented_filter_as_unsafe() -> None:
    case = {
        "id": "hidden_filter",
        "group": "metamorphic_exclusion",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }
    result = PlanningResult.planned(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.LAST_MONTH),
            merchants=("supermercados",),
        )
    )

    score = judged(case, result)

    assert score.exact == 0
    assert score.unsafe
    assert "merchants=('supermercados',) != ()" in score.unsafe[0][1]


def metamorphic_cases(*changes: str) -> list[dict[str, object]]:
    return [
        {
            "id": "base",
            "expect": "plan",
            "question": "importe",
            "plan": {
                "period": {"period": "LAST_MONTH"},
                "direction": "EXPENSE",
                "metric": "SUM",
            },
        },
        {
            "id": "variant",
            "expect": "plan",
            "question": "cantidad",
            "metamorph_of": "base",
            "changes": list(changes),
            "plan": {
                "period": {"period": "LAST_MONTH"},
                "direction": "EXPENSE",
                "metric": "COUNT",
            },
        },
    ]


def test_a_local_metric_change_passes_the_metamorphic_score() -> None:
    cases = metamorphic_cases("metric")
    score = evaluate.Score(
        results_by_id={
            "base": spending(),
            "variant": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.LAST_MONTH),
                    metric=Metric.COUNT,
                )
            ),
        }
    )

    evaluate._check_metamorphs(cases, score, today=TODAY)

    assert (score.metamorphic_right, score.metamorphic_total) == (1, 1)
    assert score.metamorphic_violations == []


def test_a_local_window_change_passes_the_metamorphic_score() -> None:
    cases = metamorphic_cases("window")
    score = evaluate.Score(
        results_by_id={
            "base": spending(),
            "variant": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.THIS_MONTH),
                )
            ),
        }
    )

    evaluate._check_metamorphs(cases, score, today=TODAY)

    assert (score.metamorphic_right, score.metamorphic_total) == (1, 1)


def test_an_added_exclusion_passes_without_losing_the_rest_of_the_plan() -> None:
    cases = metamorphic_cases("excludedCategories")
    score = evaluate.Score(
        results_by_id={
            "base": spending(),
            "variant": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(Period.LAST_MONTH),
                    excluded_categories=("HOUSING_RENT",),
                )
            ),
        }
    )

    evaluate._check_metamorphs(cases, score, today=TODAY)

    assert (score.metamorphic_right, score.metamorphic_total) == (1, 1)


def test_a_metric_change_that_also_changes_direction_is_reported_as_a_leak() -> None:
    cases = metamorphic_cases("metric")
    score = evaluate.Score(
        results_by_id={
            "base": spending(),
            "variant": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.ALL,
                    period=RelativePeriod(Period.LAST_MONTH),
                    metric=Metric.COUNT,
                )
            ),
        }
    )

    evaluate._check_metamorphs(cases, score, today=TODAY)

    assert score.metamorphic_right == 0
    assert "also changed ['direction']" in score.metamorphic_violations[0]


def test_a_declared_change_that_did_not_happen_is_reported() -> None:
    cases = metamorphic_cases("metric")
    score = evaluate.Score(results_by_id={"base": spending(), "variant": spending()})

    evaluate._check_metamorphs(cases, score, today=TODAY)

    assert "did not change ['metric']" in score.metamorphic_violations[0]


def test_the_metamorphic_dataset_declares_exactly_its_expected_deltas() -> None:
    path = Path(__file__).resolve().parents[1] / "evaluation" / "planning" / "metamorphic.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    issues = evaluate.validate_metamorphic_cases(data["cases"], today=data["reference"])

    assert len(data["cases"]) >= 15
    assert issues == []


def test_the_dataset_validator_catches_a_false_locality_claim() -> None:
    cases = metamorphic_cases("window")

    issues = evaluate.validate_metamorphic_cases(cases, today=TODAY)

    assert "declares ['window'], expected plans change ['metric']" in issues[0]


def paraphrase_pair() -> list[dict[str, object]]:
    """One base and one rewording of it, saying the same thing."""
    plan = {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"}
    return [
        {"id": "base", "expect": "plan", "question": "¿Cuánto gasté el mes pasado?", "plan": plan},
        {
            "id": "said_differently",
            "expect": "plan",
            "question": "Dime el total de mis gastos del mes anterior",
            "paraphrase_of": "base",
            "plan": plan,
        },
    ]


def invariance(base: PlanningResult, rewording: PlanningResult):
    score = evaluate.Score(results_by_id={"base": base, "said_differently": rewording})
    evaluate._check_invariance(paraphrase_pair(), score, today=TODAY)
    return score


def test_a_rewording_that_produces_the_same_plan_holds_the_property() -> None:
    score = invariance(spending(), spending())

    assert (score.invariance_held, score.invariance_total) == (1, 1)
    assert score.invariance_breaks == []


def test_a_rewording_that_moves_a_field_breaks_the_property() -> None:
    """The defect the whole corpus exists to find: the same question, a different answer."""
    score = invariance(spending(), spending(Direction.ALL))

    assert (score.invariance_held, score.invariance_total) == (0, 1)
    assert "rewording changed ['direction']" in score.invariance_breaks[0]


def test_a_rewording_the_system_declines_breaks_the_property() -> None:
    """A refusal is an answer, and a refusal that only some phrasings trigger is the same bug."""
    score = invariance(spending(), PlanningResult.needs_clarification("¿de qué periodo?"))

    assert score.invariance_held == 0
    assert "base PLANNED, rewording NEEDS_CLARIFICATION" in score.invariance_breaks[0]


def test_two_identically_wrong_plans_are_robust() -> None:
    """The distinction that makes this a second number rather than a second accuracy score.

    Both sides answer a spending question with `ALL`, so both are wrong and the dataset's own
    expectation is missed twice. The property still holds: one fix to the base fixes the rewording
    too, which is exactly what is *not* true of a set that answers the base correctly and the
    rewording wrongly.
    """
    score = invariance(spending(Direction.ALL), spending(Direction.ALL))

    assert (score.invariance_held, score.invariance_total) == (1, 1)


def test_two_refusals_are_robust_even_when_the_dataset_wanted_a_plan() -> None:
    declined = PlanningResult.needs_clarification("¿de qué periodo?")

    score = invariance(declined, declined)

    assert (score.invariance_held, score.invariance_total) == (1, 1)


def test_a_paraphrase_that_edited_its_expectation_is_a_dataset_error() -> None:
    """The rule `drift.yaml` states in prose: the plan is copied from the source, never invented."""
    cases = paraphrase_pair()
    cases[1]["plan"] = {"period": {"period": "THIS_MONTH"}, "direction": "EXPENSE"}

    issues = evaluate.validate_paraphrase_cases(cases)

    assert "plan differs from base, which it may not" in issues[0]


def test_a_paraphrase_that_is_the_same_sentence_is_a_dataset_error() -> None:
    """A pair of identical sentences is 1/1 robust for free and measures nothing."""
    cases = paraphrase_pair()
    cases[1]["question"] = cases[0]["question"]

    issues = evaluate.validate_paraphrase_cases(cases)

    assert issues == ["said_differently: the same sentence as base"]


def test_the_refusal_rate_has_a_denominator_of_answerable_cases() -> None:
    """`missed` was a bare count, and a bare count cannot be a gate bar.

    An unsupported case that is correctly refused must not enter the denominator: refusing what
    the dataset itself refuses is the system working.
    """
    score = evaluate.Score()
    answerable = {
        "id": "answerable",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }
    refusable = {"id": "out_of_scope", "expect": "unsupported", "question": "¿Qué tiempo hace?"}

    evaluate.judge(answerable, PlanningResult.needs_clarification("¿de qué mes?"), score, TODAY)
    evaluate.judge(
        refusable, PlanningResult.unsupported("no es una pregunta de dinero"), score, TODAY
    )

    assert (len(score.missed), score.answerable) == (1, 1)


def complete_robustness_score(rewording: PlanningResult | None = None) -> evaluate.Score:
    score = invariance(spending(), rewording or spending())
    score.answerable = 2
    return score


def test_a_complete_robustness_measurement_can_pass_the_gate() -> None:
    score = complete_robustness_score()
    metadata = {"base_count": 1, "paraphrase_count": 1}
    gate = evaluate.RobustnessGate(100.0, 0.0)

    assert evaluate.robustness_gate_exit_code(score, metadata, gate) == 0


def test_a_deliberately_broken_plan_makes_the_gate_exit_nonzero(capsys) -> None:  # noqa: ANN001
    """Sensitivity control: the gate is red when one produced plan stops matching its twin."""
    score = complete_robustness_score(spending(Direction.ALL))
    metadata = {"base_count": 1, "paraphrase_count": 1}
    gate = evaluate.RobustnessGate(100.0, 0.0)

    assert evaluate.robustness_gate_exit_code(score, metadata, gate) == 1
    assert "macro robustness 0.0% is below 100.0%" in capsys.readouterr().err


def test_a_partial_run_cannot_pass_the_robustness_gate() -> None:
    score = complete_robustness_score()
    metadata = {"base_count": 2, "paraphrase_count": 8}

    broken = evaluate.robustness_gate_failures(score, metadata, evaluate.RobustnessGate(0.0, 100.0))

    assert "executed 2/10 cases" in broken
    assert "measured 1/8 paraphrase pairs" in broken
    assert "measured 1/2 paraphrase bases" in broken


def provider_settings(**changes: object) -> SimpleNamespace:
    values = {
        "ollama_base_url": "http://ollama.test",
        "ollama_keep_alive": "5m",
        "ollama_timeout_seconds": 11.0,
        "ollama_context_tokens": 4096,
        "ollama_seed": 7,
        "groq_api_key": SecretStr("groq-secret"),
        "groq_base_url": "https://groq.test/v1",
        "groq_timeout_seconds": 22.0,
        "groq_reasoning_headroom": 2048,
        "cloudflare_api_token": SecretStr("cloudflare-secret"),
        "cloudflare_account_id": "account-1",
        "cloudflare_api_base": "https://cloudflare.test/client/v4",
        "cloudflare_timeout_seconds": 33.0,
        "cloudflare_reasoning_headroom": 2048,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_the_planning_evaluator_builds_the_selected_hosted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluate, "get_settings", provider_settings)

    groq = evaluate._provider("groq", None, False, None)
    cloudflare = evaluate._provider("cloudflare", None, False, None)

    assert groq.name == "groq"
    assert groq.timeout_seconds == 22.0
    assert cloudflare.name == "cloudflare"
    assert cloudflare.timeout_seconds == 33.0


@pytest.mark.parametrize(
    ("provider", "settings", "message"),
    [
        ("groq", {"groq_api_key": None}, "ORCHESTRATOR_GROQ_API_KEY"),
        (
            "cloudflare",
            {"cloudflare_account_id": None},
            "ORCHESTRATOR_CLOUDFLARE_API_TOKEN",
        ),
    ],
)
def test_a_hosted_evaluation_without_complete_credentials_stops_before_the_run(
    provider: str,
    settings: dict[str, object],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluate, "get_settings", lambda: provider_settings(**settings))

    with pytest.raises(ValueError, match=message):
        evaluate._provider(provider, None, False, None)


class PlannerAnswer:
    def __init__(self, result: PlanningResult) -> None:
        self.result = result

    async def plan(self, *_args: object, **_kwargs: object) -> PlanningResult:
        return self.result


@pytest.mark.asyncio
async def test_the_evaluator_applies_deterministic_exclusions_before_scoring() -> None:
    case = {
        "id": "without_rent",
        "question": "¿Cuánto gasté el mes pasado sin contar el alquiler?",
    }
    raw = PlanningResult.planned(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.LAST_MONTH),
            categories=("HOUSING_RENT",),
        )
    )

    result = await evaluate._planned(PlannerAnswer(raw), case, today=TODAY, previous=None)

    assert isinstance(result.plan, FinancialPlan)
    assert result.plan.categories == ()
    assert result.plan.excluded_categories == ("HOUSING_RENT",)


@pytest.mark.asyncio
async def test_the_evaluator_uses_the_same_deterministic_rescue_as_the_application() -> None:
    case = {
        "id": "without_groceries",
        "question": "¿Cuánto gasté el mes pasado sin supermercados?",
    }

    result = await evaluate._planned(
        PlannerAnswer(PlanningResult.unsupported("the question cannot be expressed as a query")),
        case,
        today=TODAY,
        previous=None,
    )

    assert isinstance(result.plan, FinancialPlan)
    assert result.plan.categories == ()
    assert result.plan.excluded_categories == ("GROCERIES",)


def test_a_precise_merchant_exclusion_is_not_refused_before_planning() -> None:
    sentence = "¿Cuánto gasté en supermercados el mes pasado excepto Carrefour?"

    assert evaluate.capability_guard.unsupported_reason(sentence) is None
    assert evaluate.explicit_facts.excluded_merchants_in(sentence) == ("carrefour",)
