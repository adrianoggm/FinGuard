"""Write the robustness corpus: every admitted base case, said the way somebody else says it.

    uv run python scripts/generate_paraphrases.py --model granite4.1:8b

**Offline, and the output is committed.** CI cannot depend on a GPU, and a set regenerated per run
is a flaky gate: the number would move because the corpus moved, and nobody could tell that from
the system moving. Running this again produces a *new* corpus with a new baseline, not a reshuffle
of this one — temperature is 0.9 and local inference is not bit-reproducible even without it.

--- the model, and why it is not the planner -------------------------------------------------

The planner is `qwen3.5:9b`. A corpus written by the model under test is a corpus of the sentences
that model already parses, which is the same defect as a corpus written by the person who wrote the
prompt, one layer down.

`drift.yaml` and `paraphrases/dispatch.yaml` were both written by `llama-3.3-70b-versatile` via
Groq. There is no Groq credential in this checkout — `apps/orchestrator/.env` does not exist — so
that generator is not available and this one had to be picked from what Ollama serves. The pick is
argued from a measurement rather than a preference: see the corpus header for the acceptance rates
of the candidates under the rules in `paraphrase_rules.py`, which were fixed before any of them ran.

What matters more than the pick is that **`paraphrase_rules.py` never trusts it.** Every sentence
this script emits survived a rule written before generation started, and every sentence that did
not is written into the corpus file with the reason.

--- what the model is told ---------------------------------------------------------------------

Never the planning prompt, the schema, the enum values, or which phrasings this system already
handles. It is told the task — do not change the period, the subject, or the direction of the money
— because that is the definition of a paraphrase rather than a leak of the vocabulary, and because
the near-duplicate rule stops it discharging the instruction by copying.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from paraphrase_rules import admission_problem, overlap, rejection

PLANNING = Path(__file__).resolve().parents[1] / "evaluation" / "planning"

#: Groups `drift.yaml` already reaches. The selection rule below is "everything it does not", so
#: the two corpora do not both spend their budget on «¿cuánto gasté el mes pasado?».
COVERED_BY_DRIFT = "drift"

#: Deliberately excluded. A paraphrased prompt injection is a *new attack*, not a rewording: the
#: property "the same plan comes out" is not what anybody wants from it, and the thing worth
#: measuring there is whether the refusal survives, which needs an adversarial generator and a
#: threat model rather than this one.
EXCLUDED_GROUPS = ("injection",)

# Human-written context for the eight genuinely elliptical dev cases.  These are deliberately not
# rendered from ``previous``: exposing that plan, directly or through an enum-aware renderer, would
# leak the schema this corpus exists to stay independent from.  The complete fresh-question case
# needs no context; the follow-up with no previous turn remains inadmissible by design.
PRECEDING_QUESTIONS = {
    "follow_category_after_period": "¿Cuánto gasté el mes pasado?",
    "follow_grouping_after_period": "¿Cuánto gasté el mes pasado?",
    "follow_period_change_keeps_category": "¿Cuánto gasté en gasolina el mes pasado?",
    "follow_direction_change": "¿Cuánto gasté el mes pasado?",
    "follow_metric_change": "¿Cuánto gasté en supermercados el mes pasado?",
    "follow_narrow_to_week": "¿Cuánto gasté en restaurantes el mes pasado?",
    "follow_by_merchant": "¿Cuánto he gastado este mes?",
    "follow_ambiguous_stays_ambiguous": "¿Cuánto gasté el mes pasado?",
}

SYSTEM = """Eres una persona cualquiera, cliente de un banco, que le escribe a la aplicación del \
banco desde el móvil. No sabes nada de informática ni de cómo funciona la aplicación por dentro.

Te doy UNA frase que ha escrito otra persona. Escribe {count} maneras distintas de pedir \
EXACTAMENTE lo mismo, como lo dirían otras personas distintas.

Reglas:
- Cambia los verbos y la manera de construir la frase. No repitas la frase original ni la copies \
cambiando una palabra.
- No cambies NADA de lo que se pide. Ni el periodo de tiempo, ni de qué se está hablando, ni si \
es dinero que sale o dinero que entra. Si la frase habla del mes pasado, todas las tuyas hablan \
del mes pasado. Si la frase dice unas fechas concretas, todas las tuyas dicen esas mismas fechas.
- No añadas nada que no esté en la frase original. Si la frase es imprecisa, las tuyas tienen que \
ser igual de imprecisas: no la concretes.
- Habla siempre de tu propio dinero, en primera persona.
- Español de España, y nada de inglés.
- Contesta SOLO con un array JSON de {count} cadenas de texto. Nada más, ni explicaciones ni \
comentarios."""


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))


def bases() -> list[dict[str, Any]]:
    """The selection rule, in code so it cannot drift from the prose that describes it.

    **Every (group, expect) pair `drift.yaml` does not already contain**, from `dev.yaml`, plus
    `adversarial.yaml`'s two refusal groups. Coverage-driven and decided before anything ran: the
    groups nobody outside this repository has ever said out loud are exactly the ones with no
    evidence either way, and picking cases by how they score would be picking the exam.
    """
    covered = {(case.get("group"), case["expect"]) for case in _load(COVERED_BY_DRIFT)["cases"]}
    chosen = [
        case
        for case in _load("dev")["cases"]
        if (case.get("group"), case["expect"]) not in covered
        and case.get("group") not in EXCLUDED_GROUPS
    ]
    chosen += [
        case
        for case in _load("adversarial")["cases"]
        if case.get("group") in ("out_of_domain", "boundary")
    ]
    return chosen


def ask(model: str, base: str, count: int, *, host: str, temperature: float) -> list[str]:
    """One batch of candidate rewrites. A malformed answer is an empty batch, never an exception.

    `dispatch.yaml` records a local model that "leaked its reasoning into the array" — so thinking
    is off, the answer is parsed rather than trusted, and anything that is not a list of strings is
    dropped here before `paraphrase_rules` ever sees it.
    """
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": base},
                {"role": "user", "content": ""},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_predict": 1600},
        }
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/chat", payload, {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            content = str(json.load(response)["message"]["content"])
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  provider failed: {exc}", file=sys.stderr)
        return []
    start, end = content.find("["), content.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()][:count]


def _prompt(case: dict[str, Any], count: int) -> str:
    """Give an elliptical base its human-written context without exposing ``previous``."""
    preceding = PRECEDING_QUESTIONS.get(str(case["id"]))
    if preceding is None:
        return f"{SYSTEM.format(count=count)}\n\nLa frase es:\n{case['question']}"
    return (
        f"{SYSTEM.format(count=count)}\n\n"
        "Esta es la pregunta anterior, sólo para entender el contexto:\n"
        f"{preceding}\n\n"
        "Reformula únicamente esta segunda frase. Debe seguir siendo una continuación y no debe "
        "repetir ni completar la pregunta anterior.\n\n"
        # Prose did not carry this. Told only in words, `granite4.1:8b` resolved the ellipsis in
        # 93 of 96 candidates: «¿Y en gasolina?» came back as «¿Cuánto consumí en combustible el
        # mes pasado?» — good Spanish, the same question, and not a continuation. The meaning rule
        # then rejects it for adding a period the base never stated, and it is right to. What a
        # follow-up case measures is whether an *ellipsis* survives being said differently; the
        # resolved form is already covered by the bases that state their own window.
        #
        # The example is built on a sentence that is none of the eight, so it teaches the shape
        # rather than the answer.
        "Ejemplo. Si la pregunta anterior fuera «¿Cuánto ingresé el año pasado?» y la segunda "
        "frase fuera «¿Y en enero?», valdrían «¿y enero?», «¿y el mes de enero?» o «¿qué tal en "
        "enero?». No valdrían «¿Cuánto ingresé en enero del año pasado?» ni «¿Qué cantidad "
        "recibí en enero?», porque esas dicen por sí solas lo que la primera frase ya decía.\n\n"
        "La segunda frase es:\n"
        f"{case['question']}"
    )


def paraphrase(
    case: dict[str, Any], model: str, count: int, *, host: str, temperature: float
) -> tuple[list[str], list[tuple[str, str]]]:
    """Accepted rewrites and the reject pile, for one base."""
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for candidate in ask(model, _prompt(case, count), count, host=host, temperature=temperature):
        why = rejection(str(case["question"]), candidate, tuple(accepted))
        if why is None:
            accepted.append(candidate)
        else:
            rejected.append((candidate, why))
    return accepted, rejected


HEADER = """\
# Robustness: every question this repository wrote down, said the way somebody else says it.
#
# `drift.yaml` is ten questions, and its header says why it exists: "nobody who wrote these
# sentences had seen the prompt, the vocabulary or the schema". That is the right idea. Ten of
# them is the wrong scale, and this file is the same idea at {bases} base questions and
# {kept} rewordings of them.
#
# --- what it measures, which is not accuracy -----------------------------------------------------
#
# `paraphrase_of` chains each sentence to the one it rewrites, and the evaluator compares the two
# **produced** plans -- not either of them against the expectation. A base the system gets wrong,
# whose every rewording is wrong the same way, is 7/7 robust: that is a coverage defect, one fix
# closes all eight sentences, and it is not what this measures. A base the system gets right whose
# rewording it gets wrong is the defect this exists for, and no accuracy number can see it, because
# both numbers move together.
#
# --- which cases, and why these -------------------------------------------------------------------
#
# Every (group, expect) pair in `dev.yaml` that `drift.yaml` does not already contain, plus
# `adversarial.yaml`'s `out_of_domain` and `boundary`. Coverage-driven and fixed before anything
# ran: those are the capabilities with no evidence either way, and choosing cases by how they score
# would be choosing the exam. `injection` is deliberately left out -- a paraphrased prompt injection
# is a new attack rather than a rewording, and "the same plan comes out" is not the property
# anybody wants from one.
#
# Sixteen `dev.yaml` cases already carry `paraphrase_of` themselves; where one of those is a base
# here, that key is dropped, because its twin lives in `dev.yaml` and a chain with one end missing
# scores as nothing at all.
#
# Eight elliptical `follow_up` bases receive a preceding Spanish question written by hand. The
# generator sees that sentence only as context and rewrites the second turn; it never sees the
# dataset's `previous` plan, the schema or an enum-aware rendering of either. The context-free
# follow-up remains excluded because absence of context is exactly what that case measures.
#
# --- who wrote the sentences ----------------------------------------------------------------------
#
# `{model}`, offline, on {date}. **Not `qwen3.5:9b`**, which is the planner: a corpus written
# by the model under test is a corpus of the sentences that model already parses, which is the same
# defect as a corpus written by whoever wrote the prompt, one layer down. Not a `qwen` at all, for
# the same reason at one remove.
#
# `drift.yaml` and `paraphrases/dispatch.yaml` were both written by `llama-3.3-70b-versatile` via
# Groq. There is no Groq credential in this checkout, so the generator had to come from what Ollama
# serves, and it was picked by measurement rather than preference -- the same twelve bases, the same
# rules, fixed before any of them ran:
#
#     granite4.1:8b     31/88  35.2 %   mean word overlap with the base 0.27   155 s
#     mistral-nemo:12b  36/88  40.9 %                                   0.42   420 s
#     ministral-3:8b    24/80  30.0 %                                   0.28   569 s
#
# `mistral-nemo` accepts more only because it copies more, and a corpus at 0.42 of the base's own
# words is a weaker test than one at 0.27. `ministral-3` returned one batch this script could not
# parse at all. `granite4.1:8b` also declares no `thinking` capability, which is the failure
# `dispatch.yaml` records for local models -- "it leaked its reasoning into the array".
#
# The model was never shown the planning prompt, the schema, the enum values, or which phrasings
# this system already handles. It was told not to change the period, the subject or the direction
# of the money, because that is the definition of a paraphrase; the near-duplicate rule is what
# stops it discharging the instruction by copying. The prompt is in the generator's docstring.
#
# --- what was thrown away, and the rule that threw it --------------------------------------------
#
# **{kept} of {generated} generated sentences were kept ({rate:.1f} %).** The other {refused} are
# below under `rejected`, each with its reason, because a corpus that loses its inconvenient cases
# is the same bias wearing a different hat. That ratio is the credibility of this file: a model
# asked to reword a question changes the question about two times in five, and nothing about being
# generated makes a sentence usable.
#
# The rule is `scripts/paraphrase_rules.py`, fixed before generation and applied before the planner
# had answered anything. It carries its own Spanish vocabulary and never asks this system anything:
# a filter built out of the recognisers would delete exactly the sentences the recognisers cannot
# read, which are the ones worth measuring, and leave a corpus that scores 100 % by construction.
# `tests/test_the_generated_corpus_is_not_the_generator.py` is the part of that a machine re-checks.
#
# **Four of its rules were written after reading a corpus, and none after seeing a score.** The
# shape rules were calibrated against the 321 human-written questions in the five datasets, where
# the first version rejected nine good sentences because its English word list contained `total`.
# Reading the first generated corpus start to finish then added three more: continuations are not
# admissible at all; a sentence naming «el cliente» or «el banco» is about somebody else's money;
# and a balance is a different question from a spending total, which nothing about periods or
# categories could see. Every one of those is *restrictive*, every one closes a false accept found
# by reading, and the planner had answered nothing when they were written.
#
# {dropped} bases were never admitted -- listed under `bases_not_admitted` with the reason. A base
# whose own sentence this vocabulary cannot read is dropped rather than judged, because a validator
# that misreads the known-good sentence will misjudge every rewrite of it.
#
# **Where the rule is weakest, said plainly.** For an `out_of_domain` base there is no period, no
# direction and no category to preserve, so the meaning rule has almost nothing to check and three
# of those bases kept 12 of 12. What survives there is the shape rule and the property itself --
# «¿qué tiempo hace mañana?» reworded is still not a question about money -- and a rewrite that
# became financial would acquire a marker and be rejected. It is a weaker guarantee than the one
# the dated and periodic families get, and it should be read as one.
#
# Mean word overlap with the base is {overlap:.2f}, which is what says these are rewordings rather
# than edits. The plans are YAML anchors into the base's own plan, so a paraphrase cannot hold a
# different opinion about the right answer even by accident.
#
# Generation is not reproducible -- temperature 0.9, and the model is somebody else's. Regenerating
# is allowed and produces a **new** corpus with a new baseline, not a reshuffle of this one.
#
# The reference date is `dev.yaml`'s, for `dev.yaml`'s reason: on the 1st of a month, or a Monday,
# THIS_MONTH and LAST_MONTH and LAST_N_DAYS collapse into each other and half of this file stops
# discriminating.
"""


def emit(
    kept: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    reference: Any,
    account: str,
    provenance: str,
) -> str:
    document = {
        "reference": reference,
        "account": account,
        "metadata": {
            "base_count": len(kept) - sum("paraphrase_of" in case for case in kept),
            "paraphrase_count": sum("paraphrase_of" in case for case in kept),
            "rejected_count": len(rejected),
            "not_admitted_count": len(dropped),
        },
        "cases": kept,
        "bases_not_admitted": dropped,
        "rejected": rejected,
    }
    body = yaml.safe_dump(
        document, allow_unicode=True, sort_keys=False, default_flow_style=None, width=98
    )
    return f"{provenance}\n{body}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="granite4.1:8b",
        help="an Ollama model that is not the planner and not one of its siblings",
    )
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument(
        "--count",
        type=int,
        default=12,
        help="candidates asked for per base. Roughly a third survive the rules, so this is what "
        "makes a group of four rather than a group of one",
    )
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--limit", type=int, default=None, help="only the first N bases")
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated base ids, for regenerating a subset instead of the corpus. "
        "The output is a corpus of just those, to be merged: this writes what it was asked "
        "for and never edits a file it did not produce",
    )
    parser.add_argument("--out", default=str(PLANNING / "robustness.yaml"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report acceptance without writing, which is how the generator itself was chosen",
    )
    args = parser.parse_args()

    dev = _load("dev")
    chosen = bases()
    if args.only:
        wanted = {piece.strip() for piece in args.only.split(",") if piece.strip()}
        chosen = [case for case in chosen if str(case["id"]) in wanted]
        missing = wanted - {str(case["id"]) for case in chosen}
        if missing:
            # Silence here would regenerate fewer bases than asked for and look like success.
            print(f"no such base: {sorted(missing)}", file=sys.stderr)
            return 2
    elif args.limit:
        chosen = chosen[: args.limit]
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    started = time.perf_counter()

    for index, case in enumerate(chosen, start=1):
        preceding = PRECEDING_QUESTIONS.get(str(case["id"]))
        blocked = admission_problem(case, preceding_question=preceding)
        if blocked is not None:
            dropped.append({"id": case["id"], "question": case["question"], "why": blocked})
            print(f"[{index:3}/{len(chosen)}] {case['id']:38} not admitted: {blocked}")
            continue
        accepted, refused = paraphrase(
            case, args.model, args.count, host=args.host, temperature=args.temperature
        )
        # Sixteen `dev.yaml` cases are already somebody's paraphrase — `noise_greeting` is
        # `direction_gaste` with a hello on the front — and four of them are bases here. That key
        # names a twin which is in `dev.yaml` and not in this file, so it is dropped rather than
        # carried: a chain whose other end is absent is scored as nothing at all, silently.
        base = {key: value for key, value in case.items() if key != "paraphrase_of"}
        if preceding is not None:
            base["preceding_question"] = preceding
        kept.append(base)
        for number, said in enumerate(accepted, start=1):
            twin = dict(base)
            twin["id"] = f"{case['id']}_p{number}"
            twin["question"] = said
            twin["paraphrase_of"] = case["id"]
            kept.append(twin)
        rejected += [
            {"paraphrase_of": case["id"], "question": said, "why": why} for said, why in refused
        ]
        print(
            f"[{index:3}/{len(chosen)}] {case['id']:38} "
            f"{len(accepted)} kept, {len(refused)} rejected"
        )

    questions = {case["id"]: str(case["question"]) for case in kept}
    pairs = [case for case in kept if "paraphrase_of" in case]
    generated = len(pairs) + len(rejected)
    closeness = [
        overlap(questions[str(case["paraphrase_of"])], str(case["question"])) for case in pairs
    ]
    summary = (
        f"\n{len(pairs)}/{generated} generated sentences accepted "
        f"({100 * len(pairs) / generated if generated else 0:.1f} %), "
        f"{len(dropped)} bases not admitted, {time.perf_counter() - started:.0f}s"
    )
    print(summary)
    if closeness:
        print(f"mean word overlap with the base: {sum(closeness) / len(closeness):.2f}")
    if args.dry_run:
        return 0

    provenance = HEADER.format(
        bases=len(kept) - len(pairs),
        kept=len(pairs),
        generated=generated,
        refused=len(rejected),
        rate=100 * len(pairs) / generated if generated else 0,
        dropped=len(dropped),
        model=args.model,
        date=date.today().isoformat(),
        overlap=sum(closeness) / len(closeness) if closeness else 0,
    )
    Path(args.out).write_text(
        emit(kept, dropped, rejected, dev["reference"], dev["account"], provenance),
        encoding="utf-8",
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
