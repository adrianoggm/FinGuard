"""Which product capability a unit test is about, derived rather than declared.

`evaluation/planning/GROUPS.md` names sixteen capability groups and the planning harness
already reports per-group accuracy. The unit suite carries no such label, so `3070
passed` and `follow_up 62%` describe the same capabilities without either knowing it.
This module supplies the missing half by *deriving* the family from what a test already
says about itself. Nothing is annotated: no marker, no decorator, no edit to a test file.

Two rules, tried in order, and a bucket for what neither reaches:

1.  **The dataset's own label.** Several tests are parametrised over the cases in
    `evaluation/planning/*.yaml`, and every one of those cases already declares a
    `group`. Where a test id carries a case id, that case's group *is* the answer --
    authoritative, and by construction identical to the vocabulary the planning report
    uses. This is the only rule that can label a single test inside a large file.

2.  **The file it lives in.** `_BY_PATH` below maps a path fragment to a family. It is
    deliberately a flat table a reviewer can read in a minute and disagree with in two.

3.  **`unmapped`.** Everything else. Most of this suite is payments, accounts, HTTP
    wiring, providers and conversation plumbing, and GROUPS.md has no word for any of
    it, because it describes what the *planner* can be asked, not what the product does.
    Stretching a group to swallow those files would make the number a lie, so they are
    counted honestly instead and the size of the bucket is left visible.

A file this module has never seen lands in `unmapped`. That is the designed outcome for
new tests, not a failure, and nothing here may raise: a taxonomy that can break a test
run is worse than no taxonomy.
"""

from __future__ import annotations

import re
from pathlib import Path

#: The sixteen groups of GROUPS.md, in the order that file argues them.
FAMILIES: tuple[str, ...] = (
    "period_named",
    "period_confusable",
    "period_absolute",
    "direction",
    "category_single",
    "category_multiple",
    "metric",
    "grouping",
    "merchant",
    "follow_up",
    "ambiguity",
    "unsupported",
    "noise",
    "injection",
    "out_of_domain",
    "boundary",
)

#: Where tests that describe no capability group go. Named, counted, never hidden.
UNMAPPED = "unmapped"

# Path fragment -> family. First match wins, so the order is part of the meaning.
#
# A file earns a row here only if the capability is what the file is *about*. Files that
# cover several groups at once (`test_explicit_facts`, `test_intent_router`,
# `test_e2e_checks`) get no row: picking one of their groups would be a guess, and the
# dataset rule above already labels the parametrised tests inside them case by case.
#
# `test_explainer` had a row here and lost it. It reads as an `out_of_domain` file -- the
# assistant answering about itself -- but over half of it asserts on the *category*
# vocabulary, so a category regression was reported as `out_of_domain`. That was found by
# breaking `category_words` on purpose and watching the wrong family light up.
_BY_PATH: tuple[tuple[str, str], ...] = (
    # -- windows ----------------------------------------------------------------------
    # `window_words` reads months, years and explicit ranges -- "a month of a year",
    # "a named year", "an explicit range" -- which is period_absolute's definition, not
    # the nine names. `natural_periods` adds quarter/semester/fortnight/weekend, which
    # are named windows. The timezone files decide which day TODAY is.
    ("test_window_words", "period_absolute"),
    ("test_natural_period", "period_named"),
    ("test_holder_timezone", "period_named"),
    ("uses_the_holder_timezone", "period_named"),
    # -- what is being asked ----------------------------------------------------------
    ("test_category_words", "category_single"),
    ("test_category_misread", "category_single"),
    ("test_frequency", "metric"),
    ("test_how_many_subscriptions", "metric"),
    # -- the refusal boundary ---------------------------------------------------------
    # GROUPS.md names averages, comparisons and ratios as what `unsupported` is for, so
    # the guard files and the two capabilities carved out of that boundary sit here.
    ("test_capability_guard", "unsupported"),
    ("test_the_guard_owns_refusals", "unsupported"),
    ("test_guard_policy", "unsupported"),
    ("test_percentages", "unsupported"),
    ("test_comparison_plan", "unsupported"),
    # -- a sentence that needs the one before it --------------------------------------
    ("test_continuation_patch", "follow_up"),
    ("test_planning_focus_readers", "follow_up"),
    ("test_plan_merger", "follow_up"),
    ("test_plan_patch", "follow_up"),
    ("test_result_reference", "follow_up"),
    ("test_a_follow_up_said_differently", "follow_up"),
    # -- a sentence that determines no plan -------------------------------------------
    ("test_plan_draft", "ambiguity"),
    ("test_clarification_round_trip", "ambiguity"),
    ("test_a_question_is_not_asked_twice", "ambiguity"),
    # -- surface that must not change the plan ----------------------------------------
    ("test_a_pleasantry_is_not_a_query", "noise"),
    ("test_accents_do_not_hide_a_rule", "noise"),
    ("test_the_same_question_said_differently", "noise"),
    # -- the question may not rewrite the rules ---------------------------------------
    ("test_prompt", "injection"),
)

_DATASETS = Path(__file__).resolve().parents[1] / "evaluation" / "planning"
_ID = re.compile(r"^\s*-?\s*id:\s*(\S+)\s*$")
_GROUP = re.compile(r"^\s*group:\s*(\S+)\s*$")

_case_groups: dict[str, str] | None = None


def _dataset_groups() -> dict[str, str]:
    """Case id -> group, read once from the planning datasets.

    Deliberately a line scanner rather than a YAML parse: this runs inside a test
    reporting hook, wants no import of PyYAML, and needs only two keys. Anything
    unreadable yields an empty table, which degrades to rule 2 rather than failing.
    """
    global _case_groups
    if _case_groups is not None:
        return _case_groups
    found: dict[str, str] = {}
    try:
        for dataset in sorted(_DATASETS.glob("*.yaml")):
            case = None
            for line in dataset.read_text(encoding="utf-8").splitlines():
                matched_id = _ID.match(line)
                if matched_id:
                    case = matched_id.group(1)
                    continue
                matched_group = _GROUP.match(line)
                if matched_group and case and matched_group.group(1) in FAMILIES:
                    found[case] = matched_group.group(1)
    except Exception:  # noqa: BLE001 - an unreadable dataset degrades to rule 2, once
        found = {}
    _case_groups = found
    return found


def family_of(nodeid: str) -> str:
    """The capability family a test node belongs to, or `UNMAPPED`.

    Never raises: an unrecognised or malformed node id is `UNMAPPED`, which is also
    where a test file nobody has mapped yet lands.
    """
    try:
        path, _, rest = nodeid.partition("::")
        if "[" in rest:
            params = rest[rest.index("[") + 1 : rest.rindex("]") if "]" in rest else len(rest)]
            cases = _dataset_groups()
            for piece in params.split("-"):
                group = cases.get(piece.strip())
                if group is not None:
                    return group
        stem = path.replace("\\", "/")
        for fragment, family in _BY_PATH:
            if fragment in stem:
                return family
    except Exception:  # noqa: BLE001 - a taxonomy may not break a test run
        return UNMAPPED
    return UNMAPPED
