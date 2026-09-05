"""The judge and the stability check, against scripted decisions -- no model in tests."""

import importlib.util
import sys
from pathlib import Path

from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec, EncodingSort
from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_visual", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_visual.py"
)
evaluate_visual = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_visual"] = evaluate_visual
_SPEC.loader.exec_module(evaluate_visual)

FAMILY_DATASETS = (
    evaluate_visual.FAMILY_DATASETS
)  # the script imports it from the exporter; one path


def _bar(orientation: str = "y") -> ChartSpec:
    other = "x" if orientation == "y" else "y"
    return ChartSpec(
        "bar",
        (
            ChartEncoding(
                orientation, "dimension", "nominal", sort=EncodingSort("descending", "value_minor")
            ),
            ChartEncoding(other, "value_minor", "quantitative"),
        ),
    )


def _decision(spec, *, valid=True, changed=True, ms=1200, reason=None):
    return VisualDecision(
        spec=_bar(),
        profile=evaluate_visual.PROFILES["breakdown"],
        source="deterministic",
        proposed=True,
        valid=valid,
        changed_from_baseline=changed if valid else None,
        proposed_spec=spec if valid else None,
        latency_ms=ms,
        fallback_reason=reason,
    )


def test_every_case_names_a_known_dataset_and_a_resolvable_twin() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    ids = {c.id for c in cases}
    for case in cases:
        assert case.dataset == "none" or case.dataset in FAMILY_DATASETS
        assert case.paraphrase_of is None or case.paraphrase_of in ids
        assert case.expect_chart is False or case.suitable_marks


def test_the_judge_counts_validity_suitability_and_the_figure_that_matters() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    by_id = {c.id: c for c in cases}
    results = evaluate_visual.Results()
    evaluate_visual.judge(
        by_id["breakdown_where_most"], _decision(_bar("x")), results
    )  # valid, suitable, changed
    evaluate_visual.judge(
        by_id["breakdown_where_most"], _decision(_bar("x"), changed=False), results
    )
    evaluate_visual.judge(
        by_id["breakdown_where_most"],
        _decision(None, valid=False, reason="invalid_proposal"),
        results,
    )
    assert results.entered == 3 and results.valid == 2 and results.suitable == 2
    assert results.valid_suitable_changed == 1
    assert results.fallback_reasons == {"invalid_proposal": 1}


def test_stability_holds_for_paraphrases_and_is_sensitive_to_an_instruction() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    proposals = {
        "breakdown_where_most": _bar("y"),
        "breakdown_where_most_p1": _bar("y"),
        "breakdown_where_most_p2": _bar("x"),  # a break
        "breakdown_as_donut": _bar("y"),  # instruction ignored: insensitive
    }
    stability = evaluate_visual.stability(cases, proposals)
    assert stability.pairs == 2 and stability.held == 1
    assert stability.instructed == 1 and stability.sensitive == 0


def test_a_no_chart_case_is_suitable_when_nothing_was_drawn() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    results = evaluate_visual.Results()
    evaluate_visual.judge(next(c for c in cases if c.id == "no_chart_single_figure"), None, results)
    assert results.no_chart_total == 1 and results.no_chart_held == 1
