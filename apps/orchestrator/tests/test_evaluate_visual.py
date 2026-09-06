"""The judge and the stability check, against scripted decisions -- no model in tests."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec, EncodingSort
from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_visual", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_visual.py"
)
assert _SPEC and _SPEC.loader
evaluate_visual = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_visual"] = evaluate_visual
_SPEC.loader.exec_module(evaluate_visual)

from evaluate_visual import Case, Results, judge, load_cases, stability  # noqa: E402

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


def _decision(
    spec,
    *,
    valid=True,
    changed=True,
    ms=1200,
    reason=None,
    profile=evaluate_visual.PROFILES["breakdown"],
):
    return VisualDecision(
        spec=_bar(),
        profile=profile,
        source="deterministic",
        proposed=True,
        valid=valid,
        changed_from_baseline=changed if valid else None,
        proposed_spec=spec if valid else None,
        latency_ms=ms,
        fallback_reason=reason,
    )


def test_every_case_names_a_known_dataset_and_a_resolvable_twin() -> None:
    cases = evaluate_visual.load_cases(evaluate_visual.CASES_PATH)
    assert len(cases) == 16
    ids = {c.id for c in cases}
    for case in cases:
        assert case.dataset == "none" or case.dataset in FAMILY_DATASETS
        assert case.paraphrase_of is None or case.paraphrase_of in ids
        assert case.expect_chart is False or case.suitable_marks
    # `plan_limited` defaults to False, so if the YAML key were lost, renamed or mis-indented,
    # load_cases would still return a case and every assertion above would still pass -- while
    # `ranking_top3` measures nothing on the GPU. This is the one line that would catch that.
    assert {c.id for c in cases if c.plan_limited} == {"ranking_top3"}


def test_the_limited_case_gets_a_limited_profile() -> None:
    cases = evaluate_visual.load_cases(evaluate_visual.CASES_PATH)
    by_id = {c.id: c for c in cases}
    assert evaluate_visual._profile_for(by_id["ranking_top3"]).plan_limited is True
    assert evaluate_visual._profile_for(by_id["breakdown_where_most"]).plan_limited is False
    assert evaluate_visual._profile_for(by_id["no_chart_balance"]).row_count == 1


def test_the_judge_counts_validity_suitability_and_the_figure_that_matters() -> None:
    cases = evaluate_visual.load_cases(evaluate_visual.CASES_PATH)
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
    """The instructed proposal only turns sideways (`_bar("x")`, the same mark as its `_bar("y")`
    twin): insensitive under the new derivation (`instruction_holds`); under the old one
    (`not same`) this would have read `sensitive == 1` for a canonical difference the instruction
    never asked for."""
    cases = evaluate_visual.load_cases(evaluate_visual.CASES_PATH)
    proposals = {
        "breakdown_where_most": _bar("y"),
        "breakdown_where_most_p1": _bar("y"),
        "breakdown_where_most_p2": _bar("x"),  # a break
        "breakdown_as_donut": _bar("x"),  # orientation differs, mark does not: insensitive
    }
    stability = evaluate_visual.stability(cases, proposals)
    assert stability.pairs == 2 and stability.held == 1
    assert stability.instructed == 1 and stability.sensitive == 0


def test_stability_counts_a_pair_with_no_proposal_as_skipped() -> None:
    cases = [
        evaluate_visual.Case(id="base", group="breakdown", dataset="breakdown", question="q"),
        evaluate_visual.Case(
            id="twin", group="breakdown", dataset="breakdown", question="q2", paraphrase_of="base"
        ),
    ]
    proposals = {"base": _bar("y")}  # "twin"'s proposal is missing
    stability = evaluate_visual.stability(cases, proposals)
    assert stability.skipped_pairs == 1
    assert stability.skipped_instructed == 0
    assert stability.pairs == 0 and stability.held == 0


def test_stability_counts_an_instructed_pair_with_no_proposal_separately() -> None:
    """M6: `skipped_pairs` and `skipped_instructed` used to share one counter printed verbatim
    on both rows -- an instructed pair with no proposal would have made the equivalent-pair row
    claim a pair had no proposal when none did."""
    cases = [
        evaluate_visual.Case(id="base", group="breakdown", dataset="breakdown", question="q"),
        evaluate_visual.Case(
            id="twin",
            group="breakdown",
            dataset="breakdown",
            question="q2",
            paraphrase_of="base",
            visual_instruction=True,
        ),
    ]
    proposals = {"base": _bar("y")}  # "twin"'s proposal is missing
    stability = evaluate_visual.stability(cases, proposals)
    assert stability.skipped_instructed == 1
    assert stability.skipped_pairs == 0
    assert stability.instructed == 0 and stability.sensitive == 0


def test_a_no_chart_case_is_suitable_when_nothing_was_drawn() -> None:
    cases = evaluate_visual.load_cases(evaluate_visual.CASES_PATH)
    results = evaluate_visual.Results()
    evaluate_visual.judge(next(c for c in cases if c.id == "no_chart_single_figure"), None, results)
    assert results.no_chart_total == 1 and results.no_chart_held == 1


def test_a_declined_expect_chart_case_is_counted_not_dropped() -> None:
    """M7: `judge()` returned on `decision is None` for an `expect_chart` case with no counter --
    `entered` could read 12 while the header says 14 enter, with nothing saying which two
    vanished or why."""
    case = evaluate_visual.Case(id="x", group="breakdown", dataset="breakdown", question="q")
    results = evaluate_visual.Results()
    evaluate_visual.judge(case, None, results)
    assert results.declined == 1
    assert results.entered == 0


def test_suitability_requires_the_structure_the_case_names() -> None:
    """A comparison drawn as a bar with no series had the right mark and had lost the comparison."""
    profile = evaluate_visual.PROFILES["categorical_comparison"]
    case = Case(
        id="c",
        group="comparison",
        dataset="categorical_comparison",
        question="q",
        suitable_marks=("bar",),
        requires={"series": True},
    )
    plain = _bar("y")
    grouped = replace(
        plain,
        stack="disabled",
        encodings=plain.encodings
        + (
            ChartEncoding("color", "series", "nominal", sort=EncodingSort(mode="input")),
            ChartEncoding("yOffset", "series", "nominal", sort=EncodingSort(mode="input")),
        ),
    )
    results = Results()
    judge(case, _decision(plain, profile=profile), results)
    judge(case, _decision(grouped, profile=profile), results)
    assert (results.valid, results.suitable) == (2, 1)


def test_a_share_wants_the_normalised_bar_and_a_donut_does_not_stack() -> None:
    profile = evaluate_visual.PROFILES["ratio_share"]
    case = Case(
        id="s",
        group="ratio_share",
        dataset="ratio_share",
        question="q",
        suitable_marks=("bar", "donut"),
        requires={"bar_stack": "normalize"},
    )
    plain = _bar("y")
    normalised = replace(plain, stack="normalize")
    donut = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
    )
    results = Results()
    for spec in (plain, normalised, donut):
        judge(case, _decision(spec, profile=profile), results)
    assert (results.valid, results.suitable) == (3, 2)


def test_a_ranking_must_say_its_order_when_the_dimension_is_on_an_axis() -> None:
    case = Case(
        id="r",
        group="breakdown",
        dataset="breakdown",
        question="q",
        suitable_marks=("bar",),
        requires={"ordered": "descending"},
    )
    ranked = _bar("y")
    unranked = replace(
        ranked, encodings=tuple(replace(item, sort=None) for item in ranked.encodings), sort=None
    )
    results = Results()
    judge(case, _decision(ranked), results)
    judge(case, _decision(unranked), results)
    assert (results.valid, results.suitable) == (2, 1)


def test_sensitivity_is_the_instructions_own_effect_not_any_difference() -> None:
    """A bar that only changed orientation used to count as having obeyed «como un donut»."""
    base = Case(id="a", group="g", dataset="breakdown", question="q", suitable_marks=("bar",))
    asked = Case(
        id="b",
        group="g",
        dataset="breakdown",
        question="q donut",
        suitable_marks=("donut", "bar"),
        paraphrase_of="a",
        visual_instruction=True,
        instruction_effect={"mark": "donut"},
    )
    donut = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
    )
    turned = stability([base, asked], {"a": _bar("y"), "b": _bar("x")})
    obeyed = stability([base, asked], {"a": _bar("y"), "b": donut})
    assert (turned.instructed, turned.sensitive) == (1, 0)
    assert (obeyed.instructed, obeyed.sensitive) == (1, 1)


def test_the_loader_refuses_a_typo_and_an_instruction_without_its_effect(tmp_path: Path) -> None:
    typo = tmp_path / "typo.yaml"
    typo.write_text(
        "- {id: x, group: g, dataset: breakdown, question: q, requires: {orderd: descending}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown requires keys"):
        load_cases(typo)
    silent = tmp_path / "silent.yaml"
    silent.write_text(
        "- {id: y, group: g, dataset: breakdown, question: q, visual_instruction: true}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="instruction_effect"):
        load_cases(silent)


def test_the_cli_defaults_come_from_the_visual_planner_settings(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_MODEL", "bigger:14b")
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_BUDGET_SECONDS", "9.5")
    get_settings.cache_clear()
    try:
        args = evaluate_visual.build_parser(get_settings()).parse_args([])
        assert (args.model, args.budget) == ("bigger:14b", 9.5)
        # unset visual provider -> the financial planner's
        assert args.provider == get_settings().planner_provider
        overridden = evaluate_visual.build_parser(get_settings())
        assert overridden.parse_args(["--model", "other:1b"]).model == "other:1b"
    finally:
        get_settings.cache_clear()
