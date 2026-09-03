"""What stops `robustness.yaml` from being a benchmark that measures the model that wrote it.

The corpus is generated. Nothing about that is safe on its own: a set of sentences a model invented
and nobody checked measures the inventor, and a filter that was loosened until the score looked
good measures the filter. `evaluation/paraphrases/dispatch.yaml` records both hazards in prose —
"every batch here was read start to finish before it was kept" — and this file is the part of that
reading a machine can repeat on every run.

Six properties, and each one is a way the corpus could quietly stop being an instrument:

1. **The rules still reject what they rejected.** Every sentence in the file is re-validated from
   the committed data. An edit that smuggled in a sentence meaning something else fails here, and
   so would a later loosening of `paraphrase_rules` that stopped catching it.
2. **The rules accept the sentences people wrote.** Every question in the five datasets, minus the
   injections that are deliberately shaped like debris, passes the shape rules. This is the
   calibration: the first version of that list contained `total`, which is a Spanish word, and it
   rejected nine good sentences in the first thirty-two. A validator that rejects the known-good
   sentences has no business judging the unknown ones.
3. **The rules discriminate.** An accepted paraphrase of one base is checked against every *other*
   base, and must conflict with almost all of them. A validator that accepts everything against
   everything accepts nothing in particular, and that is measurable rather than assertable.
4. **The opinions are copied, never invented.** Every base is byte-equal to the case of that id in
   `dev.yaml` or `adversarial.yaml`, and every paraphrase carries its base's expectation unchanged.
   The sentences are generated; what the right answer is stays where it already was.
5. **The rejected pile is in the file.** A corpus that loses its inconvenient cases is the same
   bias wearing a different hat, and the ratio of kept to rejected is the credibility of the whole
   set.
6. **It is big enough to be worth a gate row.** `drift.yaml` is ten questions. A floor here is what
   stops this one shrinking back to that by accident — a glob that matches nothing reads exactly
   like a check that passed.

None of this says the corpus is *good Spanish*. That was done by reading it, once, and cannot be
re-done by a test. What these six do is stop it drifting afterwards.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from support.ratchets import Ceiling, Floor

ORCHESTRATOR = Path(__file__).resolve().parents[1]
PLANNING = ORCHESTRATOR / "evaluation" / "planning"


def _script(name: str) -> Any:
    """Load a `scripts/` module the way `test_evaluation_scoring` does: they are not a package."""
    spec = importlib.util.spec_from_file_location(name, ORCHESTRATOR / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rules = _script("paraphrase_rules")
evaluate = _script("evaluate_planning")
generate = _script("generate_paraphrases")


def _dataset(name: str) -> dict[str, Any]:
    return dict(yaml.safe_load((PLANNING / f"{name}.yaml").read_text(encoding="utf-8")))


CORPUS = _dataset("robustness")
CASES: list[dict[str, Any]] = CORPUS["cases"]
BASES = [case for case in CASES if "paraphrase_of" not in case]
PARAPHRASES = [case for case in CASES if "paraphrase_of" in case]
BY_ID = {case["id"]: case for case in CASES}

SOURCES = {"dev": _dataset("dev"), "adversarial": _dataset("adversarial")}
SOURCE_CASES = {case["id"]: case for data in SOURCES.values() for case in data["cases"]}

# `drift.yaml` is 65 paraphrases of ten questions. Anything under this and the set has stopped
# being the answer to "exactly the right idea at one per cent of the right scale".
ENOUGH_PARAPHRASES = Floor(150, "generated sentences that survived the rules")
ENOUGH_BASES = Floor(50, "base cases with at least one surviving rewording")

# A paraphrase that passes the meaning rule against most *other* bases is not evidence about this
# one. Measured over every accepted sentence against every base that is not its own.
CROSS_ACCEPTANCE = Ceiling(3, "accepted sentences that fit more than a tenth of the other bases")


def test_every_sentence_in_the_corpus_still_survives_the_rule_that_admitted_it() -> None:
    """Property 1. Recomputed from the file, so the file cannot drift from the rule."""
    broken = []
    for base in BASES:
        siblings: list[str] = []
        for case in PARAPHRASES:
            if case["paraphrase_of"] != base["id"]:
                continue
            why = rules.rejection(str(base["question"]), str(case["question"]), tuple(siblings))
            if why is not None:
                broken.append(f"{case['id']}: {why}  «{case['question']}»")
            siblings.append(str(case["question"]))
    assert not broken, "the corpus holds sentences its own rule rejects:\n" + "\n".join(broken)


def test_every_base_is_one_the_vocabulary_can_read() -> None:
    """Property 1, the other half: a base the rule cannot read cannot be judged against."""
    unreadable = []
    for base in BASES:
        problem = rules.admission_problem(base, preceding_question=base.get("preceding_question"))
        if problem is not None:
            unreadable.append(f"{base['id']}: {problem}")
    assert not unreadable, "bases in the corpus that were never admissible:\n" + "\n".join(
        unreadable
    )


def test_the_shape_rules_accept_the_sentences_people_wrote() -> None:
    """Property 2, the calibration control, and the one that has already caught a real bug.

    `injection` cases are excluded because they are deliberately shaped like model debris — braces,
    delimiters, quoted commands — and `adv_english` because it is deliberately English. Everything
    else in all five datasets is an ordinary Spanish question and has to pass.
    """
    rejected = []
    checked = 0
    for name in ("dev", "validation", "adversarial", "drift", "metamorphic"):
        for case in _dataset(name)["cases"]:
            if case.get("group") == "injection" or case["id"] == "adv_english":
                continue
            question = str(case["question"])
            if len(question.strip()) < rules.MIN_CHARS:
                continue
            checked += 1
            why = rules.shape_problem(question)
            if why is not None:
                rejected.append(f"{name}/{case['id']}: {why}  «{question}»")
    assert checked > 300, f"only {checked} dataset questions were checked; the glob found nothing"
    assert not rejected, "the rules reject sentences a person wrote:\n" + "\n".join(rejected)


def test_the_meaning_rule_rejects_a_paraphrase_of_the_wrong_question() -> None:
    """Property 3. The rule has to say no to most things, or its yes means nothing.

    For every accepted sentence, how many of the *other* bases would also have accepted it. A
    perfect discriminator scores zero; a rule with no content scores all of them. The bar is a
    tenth, which is generous on purpose — several bases genuinely do share a surface, because
    «¿cuánto gasté el mes pasado?» appears in this corpus under three different groups.
    """
    promiscuous = []
    for case in PARAPHRASES:
        source = BY_ID[case["paraphrase_of"]]
        # Noise and other groups intentionally contain duplicate semantic questions. Accepting a
        # rewrite against an identical expected plan is not accepting the wrong question, so those
        # copies are not negative controls for this property.
        others = [
            base
            for base in BASES
            if base.get("expect") == "plan"
            and base["id"] != case["paraphrase_of"]
            and base.get("plan") != source.get("plan")
        ]
        fits = sum(
            1
            for base in others
            if rules.surface_conflict(str(base["question"]), str(case["question"])) is None
        )
        if fits > len(others) // 10:
            promiscuous.append(f"{case['id']} fits {fits}/{len(others)} other bases")
    assert not CROSS_ACCEPTANCE.exceeded_by(promiscuous), CROSS_ACCEPTANCE.exceeded_by(promiscuous)


def test_the_corpus_copies_its_opinions_and_invents_only_the_sentences() -> None:
    """Property 4. What the right answer is stays in the file that already said it."""
    drifted = []
    for base in BASES:
        source = SOURCE_CASES.get(str(base["id"]))
        if source is None:
            drifted.append(f"{base['id']} is in no source dataset")
            continue
        for owned in ("question", "expect", "plan", "previous", "group"):
            if base.get(owned) != source.get(owned):
                drifted.append(f"{base['id']}: {owned} differs from the dataset it came from")
    assert not drifted, "the corpus edited what it was supposed to copy:\n" + "\n".join(drifted)
    assert not evaluate.validate_paraphrase_cases(CASES)


def test_the_rejected_pile_is_in_the_file_with_its_reasons() -> None:
    """Property 5. The ratio of kept to rejected is the credibility of the set."""
    rejected = CORPUS["rejected"]
    assert rejected, "a generated corpus with no reject pile did not filter anything"
    assert all(entry.get("why") and entry.get("question") for entry in rejected)
    assert all(entry["paraphrase_of"] in BY_ID for entry in rejected)
    # And the bases the vocabulary could not read, for the same reason.
    assert CORPUS["bases_not_admitted"], "no base was dropped, which no admission rule achieves"


def test_the_committed_counts_are_derived_from_the_corpus() -> None:
    """The prose drifted once; executable metadata makes the same mistake a dataset error."""
    assert not evaluate.validate_robustness_metadata(CORPUS)


def test_follow_up_context_is_human_written_and_never_the_previous_plan() -> None:
    """Eight elliptical turns get Spanish context; the context-free control gets none."""
    expected = {
        case["id"]
        for case in _dataset("dev")["cases"]
        if case.get("group") == "follow_up"
        and case.get("previous") is not None
        and str(case["question"]).casefold().startswith("¿y")
    }
    assert set(generate.PRECEDING_QUESTIONS) == expected
    for case_id, question in generate.PRECEDING_QUESTIONS.items():
        assert isinstance(question, str) and question.startswith("¿")
        source = SOURCE_CASES[case_id]
        prompt = generate._prompt(source, 3)
        assert question in prompt
        assert str(source["previous"]) not in prompt
        assert "LAST_MONTH" not in prompt and "EXPENSE" not in prompt
        assert generate.admission_problem(source, preceding_question=question) is None
    without_context = SOURCE_CASES["follow_without_context_must_ask"]
    assert generate.admission_problem(without_context) is not None


def test_the_corpus_is_large_enough_to_be_worth_a_gate_row() -> None:
    """Property 6. `drift.yaml` at one per cent of the right scale is what this is not."""
    with_paraphrases = {case["paraphrase_of"] for case in PARAPHRASES}
    assert not ENOUGH_PARAPHRASES.undercut_by(PARAPHRASES), ENOUGH_PARAPHRASES.undercut_by(
        PARAPHRASES
    )
    assert not ENOUGH_BASES.undercut_by(with_paraphrases), ENOUGH_BASES.undercut_by(
        with_paraphrases
    )


def test_drift_is_checked_by_the_same_rule_the_new_corpus_is() -> None:
    """The property is about `paraphrase_of`, not about one file, so it runs on the older set too.

    `drift.yaml` predates all of this and states the same discipline in its header. If the two
    disagreed, one of them would be wrong about what a paraphrase is.
    """
    assert not evaluate.validate_paraphrase_cases(_dataset("drift")["cases"])
