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
) -> dict[str, Any]:
    """One verdict per field for one case. Raises whatever the provider raises."""
    answered = await provider.complete(
        prompt_for(case, plan),
        model=model,
        temperature=0.0,
        max_output_tokens=800,
        schema=VERDICT_SCHEMA,
    )
    parsed = json.loads(answered)
    if not isinstance(parsed, dict):
        raise ValueError(f"the judge did not return an object: {answered[:120]}")
    return parsed


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

    provider = _provider(arguments.provider, arguments.base_url, arguments.think, None)
    verdicts = [await judge(provider, arguments.model, case, plans[case["say"]]) for case in chosen]

    where = f"{provider.name}/{arguments.model} · {arguments.group} · {len(chosen)} cases"
    print(f"\n=== judge {where} ===\n")
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
    arguments = parser.parse_args()
    plans = yaml.safe_load(Path(arguments.plans).read_text(encoding="utf-8")) or {}
    return asyncio.run(run(arguments, plans))


if __name__ == "__main__":
    raise SystemExit(main())
