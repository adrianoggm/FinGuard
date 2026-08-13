"""A model scoring a plan against the sentence and the thread behind it, field by field.

    uv run python scripts/judge_plans.py --provider ollama --model qwen2.5-coder:7b \
        --plans /tmp/plans.yaml --group anchor

`--plans` is a mapping of sentence to the plan a planner produced for it, in the vocabulary
`handover_cases.py` writes. Judging is deliberately separate from planning: the judge has to be
believed before it is wired to anything, and keeping the plan as an input is what lets it be
validated against the anchor group — the turns where a person already wrote what should happen.

**A verdict per field, never a score.** An 8 out of 10 does not say which of six fields was wrong,
and more to the point it cannot be compared with the hand-written expectations, which are also per
field (`must_keep`, `must_change`). The comparison is the whole reason the judge exists: a model
scoring plans is an opinion until it agrees with a person where both have an answer.

**Three verdicts, not two.** `wrong` and `right` are not enough for these turns — "¿y el mes
pasado?" after a fund contribution has more than one defensible window, and a judge forced to
choose invents a rule. `unjudgeable` is the honest third, and a field that keeps coming back
unjudgeable is telling us the case needs a person, not a better prompt.

The judge never sees the answer text or the figure. It scores the plan against the question, so a
well-written sentence about the wrong query cannot buy a good verdict.

## What the anchor said, which is that the gate works and two of the three judges failed it

Seventy-two anchor turns, planned once by `qwen2.5-coder:7b` through the shipping pipeline, judged
three times over the identical plans file:

    field                 qwen3.5:9b           mistral-nemo:12b     groq/gpt-oss-120b
    window                right 63 · wrong  1  right 18 · wrong 54  right 63 · wrong  7
    direction             right 35 · wrong 37  right 44 · wrong  1  right 72 · wrong  0
    categories            right 12 · wrong  8  right  7 · wrong 29  right 61 · wrong 11
    merchants             right 25 · wrong  0  right  0 · wrong 33  right 64 · wrong  8
    group_by              right 49 · wrong 22  right 70 · wrong  0  right 71 · wrong  1
    metric                right 62 · wrong 10  right 67 · wrong  5  right 72 · wrong  0
    answers_the_question  right 27 · wrong 39  right 17 · wrong 55  right 51 · wrong 17

**Read the direction column first.** Sixteen of the eighteen distinct sentences are "¿cuánto
gasté…?" and every plan says EXPENSE, so 72/0 is the only defensible answer. One local judge said
35/37 and the other 44/1 with twenty-seven abstentions. The same is true of the window, which this
service does not leave to a model at all — `explicit_facts` enforces it after the merge — and where
the two local judges are sixty-two verdicts apart on identical input.

So the anchor did exactly what it exists for. A model scoring plans is an opinion until it agrees
with a person where both have an answer; these two do not even agree with each other, and neither
may be wired to anything. The hosted judge passes the gate on the five fields with a knowable
answer, which says the prompt and the per-field shape are sound and the local models are not big
enough to hold them.

**What this does not license.** Passing on the anchor is a licence to score the extension group with
*that* judge, not with the idea of a judge — every model has to run this file first. And
`answers_the_question` at 51/17 is the one column the anchor cannot settle, because it is the
judgement the group was chosen to be easy about; the seventeen are worth reading one at a time, and
at least one is a real planner miss rather than a judge error ("¿cuánto paso a la hucha cada mes?"
came back THIS_YEAR with a Bizum category, which is neither the grouping nor the movement asked
for).

Those three columns were measured with `max_output_tokens=800`, which is where it stood before
Cloudflare showed the number was too small. It is a ceiling, so the models that answered inside it
answer the same inside 2000 — but the run behind the table was made at the old number and nobody
should read it as reproduced at the new one.

## Running this against a hosted judge, which is where the time went

    groq        429 at 25 rpm and again at 12. Completes at 6. The limit is tokens a minute and
                not requests, so pacing is the fix and the number is small.
    cloudflare  429 too, and pacing does not help: at 6 rpm it fails, and so does a single call
                once the day's allowance is gone. Workers AI meters a daily budget rather than a
                rate, so a run either fits in what is left or it does not.

Both of Cloudflare's earlier failures were ours rather than theirs, and they hid the quota until it
was fixed: at 800 tokens `@cf/qwen/qwen3-30b-a3b-fp8` spent the budget reasoning and returned first
truncated JSON and then the provider's own "output budget exhausted before final answer". Seven
verdicts with a reason each is about five hundred tokens before a reasoning model thinks at all.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_planning import _provider  # noqa: E402
from handover_cases import cases  # noqa: E402

FIELDS = ("window", "direction", "categories", "merchants", "group_by", "metric")

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [*FIELDS, "answers_the_question"],
    "properties": {
        **{
            field: {
                "type": "object",
                "additionalProperties": False,
                "required": ["verdict", "why"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["right", "wrong", "unjudgeable"]},
                    "why": {"type": "string", "maxLength": 200},
                },
            }
            for field in FIELDS
        },
        "answers_the_question": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "why"],
            "properties": {
                "verdict": {"type": "string", "enum": ["right", "wrong", "unjudgeable"]},
                "why": {"type": "string", "maxLength": 200},
            },
        },
    },
}

PROMPT = """\
Eres un evaluador de planes de consulta financiera. NO respondes al usuario: juzgas si el plan
que se te da corresponde a lo que la persona preguntó.

La conversación venía de esta consulta (null si la pregunta abre tema nuevo):
{previous}

La persona escribió:
{say}

El plan producido fue:
{plan}

Juzga CADA campo por separado. Para cada uno responde:
  right        el campo corresponde a lo que la frase pide, o a lo que hereda del hilo
  wrong        el campo contradice la frase o el hilo
  unjudgeable  la frase admite más de una lectura defendible para ese campo

Reglas:
- Una pregunta que abre tema nuevo no hereda filtros de la consulta anterior.
- Un seguimiento conserva lo que no menciona y cambia sólo lo que menciona.
- "cuánto" es una suma; "cuántos"/"cuántas veces" es un recuento.
- Un comercio nombrado es un filtro de comercio, nunca la categoría a la que pertenece.
- Si dudas entre right y wrong, responde unjudgeable. Inventar una regla es peor que abstenerse.

`answers_the_question` es el veredicto global: ¿este plan, ejecutado, contesta lo que se preguntó?
Devuelve sólo el JSON del esquema."""


def prompt_for(case: dict[str, Any], plan: dict[str, Any]) -> str:
    return PROMPT.format(
        previous=json.dumps(case.get("previous"), ensure_ascii=False),
        say=case["say"],
        plan=json.dumps(plan, ensure_ascii=False),
    )


async def judge(
    provider: Any, model: str, case: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any] | None:
    """One verdict per field for one case, or None when the judge did not produce one.

    Retried once, then given up on. A judge that loops instead of answering is a fact about that
    model, and the run has to survive it: `@cf/qwen/qwen3-30b-a3b-fp8` came back with eight
    kilobytes of repetition on one case of seventy-two, and took the whole run down with it.

    None rather than `unjudgeable`, which already means something else — that the *sentence* admits
    more than one defensible reading. Counting a crash as an abstention would put a defect about
    the judge into a column that reports on the corpus.
    """
    for attempt in range(2):
        answered = await provider.complete(
            prompt_for(case, plan),
            model=model,
            temperature=0.0,
            # Seven verdicts, each with a reason of up to two hundred characters, is about five
            # hundred tokens of answer before a reasoning model has thought about anything. At 800
            # `@cf/qwen/qwen3-30b-a3b-fp8` spent the budget thinking and came back with either
            # truncated JSON or the provider's own "output budget exhausted before final answer" —
            # two symptoms of one number being too small for what this prompt asks for.
            #
            # A ceiling and not a reservation, so the models that answer in five hundred tokens pay
            # nothing for it.
            max_output_tokens=2000,
            schema=VERDICT_SCHEMA,
        )
        try:
            parsed = json.loads(answered)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        del attempt
    return None


def tally(verdicts: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per field, how many of each verdict. The shape a person reads before believing anything."""
    counted: dict[str, dict[str, int]] = {}
    for field in (*FIELDS, "answers_the_question"):
        seen = [verdict.get(field, {}).get("verdict", "unjudgeable") for verdict in verdicts]
        counted[field] = {
            "right": seen.count("right"),
            "wrong": seen.count("wrong"),
            "unjudgeable": seen.count("unjudgeable"),
        }
    return counted


async def run(arguments: argparse.Namespace, plans: dict[str, Any]) -> int:
    chosen = [case for case in cases() if arguments.group in (case["group"], "all")]
    missing = [case["say"] for case in chosen if case["say"] not in plans]
    if missing:
        print(f"{len(missing)} of {len(chosen)} cases have no plan; run the planner first")
        for say in missing[:5]:
            print(f"  {say!r}")
        return 1

    # A sentence the pipeline declined is written into the plans file as `null`, and there is
    # nothing here to judge: "no" and "¿y el segundo?" produced a clarification and a refusal, not
    # a plan with a wrong window. Counted and named rather than dropped — a judge that quietly
    # scored fewer cases than it was given would be reporting on a set nobody chose.
    declined = [case["say"] for case in chosen if plans[case["say"]] is None]
    scored = [case for case in chosen if plans[case["say"]] is not None]

    provider = _provider(arguments.provider, arguments.base_url, arguments.think, None)
    # Paced like the planning evaluator, and for the same reason: a hosted free tier answers the
    # first thirty calls and refuses the rest, which reads as a judge with an opinion about the
    # last forty cases when it is a judge that never saw them.
    pace = 60.0 / arguments.rpm if arguments.rpm else 0.0
    verdicts = []
    unreadable: list[str] = []
    for number, case in enumerate(scored):
        if number and pace:
            await asyncio.sleep(pace)
        verdict = await judge(provider, arguments.model, case, plans[case["say"]])
        if verdict is None:
            unreadable.append(case["say"])
            continue
        verdicts.append(verdict)

    where = f"{provider.name}/{arguments.model} · {arguments.group} · {len(verdicts)} cases"
    print(f"\n=== judge {where} ===\n")
    if unreadable:
        print(f"  {len(unreadable)} of {len(scored)} came back unparseable twice, among them:")
        for say in sorted(set(unreadable))[:5]:
            print(f"    {say!r}")
        print()
    if declined:
        print(f"  {len(declined)} turns declined by the planner and not judged, among them:")
        for say in sorted(set(declined))[:5]:
            print(f"    {say!r}")
        print()
    for field, counts in tally(verdicts).items():
        print(
            f"  {field:20} right {counts['right']:>3}  wrong {counts['wrong']:>3}"
            f"  unjudgeable {counts['unjudgeable']:>3}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge planner output field by field.")
    parser.add_argument("--provider", default="ollama", help="the judge's provider")
    parser.add_argument("--model", required=True, help="the judge's model")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--plans", required=True, help="YAML mapping sentence -> plan")
    parser.add_argument("--group", default="anchor", choices=["anchor", "extension", "all"])
    parser.add_argument("--rpm", type=int, default=0, help="requests a minute; 0 is unpaced")
    arguments = parser.parse_args()
    plans = yaml.safe_load(Path(arguments.plans).read_text(encoding="utf-8")) or {}
    return asyncio.run(run(arguments, plans))


if __name__ == "__main__":
    raise SystemExit(main())
