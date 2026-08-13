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

## What the anchor said, and how little of it the anchor is entitled to say

Seventy-two anchor turns, planned once by `qwen2.5-coder:7b` through the shipping pipeline, judged
three times over the identical plans file — with the always-right baseline beside them, and the
number of distinct values the group actually puts in front of the judge:

    field                 spread  baseline  qwen3.5:9b  mistral-nemo  groq/gpt-oss-120b
    window                     4      72/0        63/1         18/54               63/7
    direction                  1      72/0       35/37          44/1               72/0
    categories                 5      72/0        12/8          7/29              61/11
    merchants                  1      72/0        25/0          0/33               64/8
    group_by                   3      72/0       49/22          70/0               71/1
    metric                     1      72/0       62/10          67/5               72/0
    answers_the_question       —      72/0       27/39         17/55              51/17

**Three of the six fields take one value across the whole group.** Every plan is EXPENSE, every plan
is SUM, and every plan has an empty merchant list — because the anchor's seventy-four rows are
eighteen sentences and sixteen of them are "¿cuánto gasté…?". On those rows a judge that never says
`wrong` scores full marks, so the hosted 72/0 on direction and on metric is exactly the baseline and
buys nothing. On merchants it is *below* the baseline: eight wrongs about a field that is empty
everywhere. An earlier version of this note told the reader to check direction first. That was the
worst advice in it — it is the column that can least distinguish anything.

**Where the group varies is where the numbers mean something**: categories at five values, window at
four, group_by at three. There the hosted judge is 61/11, 63/7 and 71/1, and that is a real result
rather than a free one.

**And the local models did not fail the way this note first claimed.** On the window — four distinct
values, the field with real spread and the one this service does not leave to a model at all —
`qwen3.5:9b` is 63/1 against the hosted judge's 63/7, which is not worse. Where it collapses is
direction, the constant column, which reads as a model not understanding the task rather than one
too small to hold it. The conclusion that neither local judge may be wired to anything probably
stands; the evidence for it is thinner than three columns of green suggested.

**What none of this licenses.** The field the extension group needs scored is
`answers_the_question`, and it is precisely the one the anchor cannot settle — 51/17, on a group
chosen to be easy. The five fields the hosted judge does pass on are the five nobody needs a judge
for: `explicit_facts` enforces the window after the merge, and `plan_verifier` already checks the
direction and the metric against the sentence. So today's licence reads *this judge is right about
what we can already check without it*, which is a necessary step and not the one that was wanted.

## The anchor after the first thing was done about it

Six sentences were added to `planning/conversations.yaml` for exactly this — money arriving, a
count, a superlative, a list of movements, one account — all of them handed-over turns, none of
them "¿cuánto gasté…?". The group went from 74 rows to 80, and what matters is the spread:

    field         before  after
    window             4      5
    direction          1      3
    categories         5      6
    merchants          1      1
    group_by           3      3
    metric             1      2

Direction and metric stop being constants, which retires two of the three free columns. **Merchants
did not move, and the reason is worth keeping:** the one anchor sentence that names a shop —
"¿cuántas compras hice en Mercadona?" — was *declined* by the planner, so there is no plan carrying
a merchant for a judge to be right or wrong about. That is a fact about `qwen2.5-coder:7b` and not
about the group, and it will change the day a planner that answers it produces this file.

**The three model columns are now from a different group.** They were measured on the 74-row anchor
at `max_output_tokens=800`; the spread above comes from the 80-row one at 2000. Nobody should read
them as one run.

## Reading the disagreements, which is what `--explain` is for

The hosted judge on the 80-row anchor: window 70/6, direction 77/0, categories 67/10, merchants
70/7, group_by 76/1, metric 75/2, and `answers_the_question` 56 right · 19 wrong · 2 unjudgeable.
Nineteen rows, read one at a time, and they are not one thing:

**Eleven are the judge, and six of those are a single misreading.** "¿Cuánto gasté el mes pasado en
el supermercado?" was rejected six times for "missing merchant filter and incorrect category" — the
judge taking a generic noun for a shop, which is its own rule about named merchants applied to a
word that names none. That one cluster also explains the seven `wrong` merchants on a group where
every merchant list is empty. Two more are the judge saying a plan lacks a window while the plan in
front of it carries `LAST_MONTH`, and three are calls about an inherited window that its own
instruction to abstain should have made `unjudgeable`.

**Eight are the planner, and they are real defects of the kind this corpus keeps surfacing:**

    "¿cuánto paso a la hucha cada mes?"         THIS_YEAR + TRANSFER_BIZUM for a question about
                                                money moved between the holder's own accounts
    "Caunto he gastado este mes ?"              group_by CATEGORY invented for a plain total
    "Enséñame los movimientos de los ultimos 4 meses"   metric SUM, so a request for a list
                                                        comes back as one figure
    "Cuanto gaste en la cuenta principal este mes?"     the account scope dropped entirely
    "¿cuál es el bizum más alto que he hecho este año?" a rank query carrying metric SUM

Four of those five are the sentences added to widen the anchor, which is the argument for widening
it: a group of one question shape cannot report a defect that only appears in another shape.

**The prompt has been sharpened and the sharpening is unmeasured.** Two rules were added — that a
merchant is a proper noun and "el supermercado" is a category, and that a field present in the plan
cannot be missing — and Groq's budget was spent before they could be run. The prediction, written
before the measurement: the six supermarket rows and the two invented missing windows disappear,
`merchants` returns to 77/0 on a group where nothing varies, and `answers_the_question` lands near
64/11. If it does not, the prompt was not the cause and this paragraph is the evidence that it was
a guess.

## The ceiling of this judge, and where the thread ends

**This judge is validated over spending questions and nothing else, and that is not an accident of
how the anchor was written.** Counting what is left to cede settles it: of the forty-eight distinct
sentences this service still hands over, seventeen are "¿cuánto gasté…?" and about twenty are not
questions at all — "sí", "a ver", "ajá", "déjalo", "1000". Eight are anything else. The
deterministic readers already answer the incomes, the counts, the groupings and the balances, so
what reaches a planner is a spending question with an awkward window plus a pile of non-questions.

Which means the group cannot be diversified out of what is already ceded. Widening it from those
eight is worth doing and was done, and it moved direction from one distinct value to three and
metric from one to two — but for a judge to be scored on money arriving, on a count or on a
grouping at any depth, the *corpus* has to grow conversations whose questions this service is
obliged to hand over in those shapes. That turns "the corpus should grow" from general advice into
a dependency of this file.

So there are two honest answers and this is the one taken: **the header says what the judge is
validated for.** The other — grow the corpus with the explicit aim of ceding other question shapes —
stays open, and whoever takes it should change this paragraph rather than add to it.

**The thread ends when the extension group has a score somebody believes.** Not when the judge runs,
not when it agrees with the anchor on the fields `explicit_facts` and `plan_verifier` already check.
When `answers_the_question` on the turns nobody measures is a number that survives being read one
case at a time.

## Running this against a hosted judge, which is where the time went

    groq        429 at 25 requests a minute and again at 12; completed at 6, when a call cost
                about 1,400 tokens. So the wall is somewhere between 8k and 17k tokens a minute,
                and `--tpm 8000` is the setting that reproduces the run that worked.
    cloudflare  429 as well, and pacing does not reach it: at the same 6 a minute it fails, and so
                does a single call once the day is spent. Workers AI meters a daily allowance
                rather than a rate, so a run either fits in what is left of the day or it does not.

**Groq is the judge's provider, and Cloudflare is not one.** A daily allowance means no judge lives
there: the run that matters is the one somebody repeats after changing the prompt, and a provider
that answers on Tuesday and refuses on Wednesday cannot hold a measurement anybody compares. That is
written here, beside the table, because the alternative is the next person spending an afternoon
rediscovering it — as this one did, through a truncated JSON and an exhausted output budget before
the quota ever showed itself.

**The flag is `--tpm` and it used to be `--rpm`, which measured the thing neither provider limits.**
Requests a minute is a number somebody remembers; it stops being right the moment the answer budget
changes, and the next person to run this gets the 429 back. Given the budget the pace is arithmetic,
so the flag now takes the limit the provider publishes and derives the rest.

Both of Cloudflare's earlier failures were ours rather than theirs, and they hid the quota until it
was fixed: at 800 tokens `@cf/qwen/qwen3-30b-a3b-fp8` spent the budget reasoning and returned first
truncated JSON and then the provider's own "output budget exhausted before final answer".
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Final

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_planning import _provider  # noqa: E402
from handover_cases import cases  # noqa: E402

FIELDS = ("window", "direction", "categories", "merchants", "group_by", "metric")
ANSWERS: Final = "answers_the_question"

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
- Un comercio es un NOMBRE PROPIO: Mercadona, Repsol, Netflix. "El supermercado", "el súper",
  "la gasolinera" y "restaurantes" son CATEGORÍAS, no comercios — para ésas el plan debe llevar
  categoría y NO debe llevar filtro de comercio. Echar en falta un comercio ahí es un error.
- Juzga sólo lo que ves: un campo que está en el plan no puede faltar. Si el plan trae `period`
  o `date_range`, tiene ventana.
- Si dudas entre right y wrong, responde unjudgeable. Inventar una regla es peor que abstenerse.

`answers_the_question` es el veredicto global: ¿este plan, ejecutado, contesta lo que se preguntó?
Devuelve sólo el JSON del esquema."""


def prompt_for(case: dict[str, Any], plan: dict[str, Any]) -> str:
    return PROMPT.format(
        previous=json.dumps(case.get("previous"), ensure_ascii=False),
        say=case["say"],
        plan=json.dumps(plan, ensure_ascii=False),
    )


# Seven verdicts, each with a reason of up to two hundred characters, is about five hundred tokens
# of answer before a reasoning model has thought about anything. A ceiling and not a reservation,
# so the models that answer inside it pay nothing for the room.
#
# At 800 `@cf/qwen/qwen3-30b-a3b-fp8` spent the budget thinking and came back with either truncated
# JSON or the provider's own "output budget exhausted before final answer" — two symptoms of one
# number being too small for what this prompt asks for.
MAX_ANSWER_TOKENS: Final = 2000


def _tokens_in(case: dict[str, Any], plan: dict[str, Any]) -> int:
    """What one judgement costs a tokens-a-minute budget, near enough to pace by.

    Four characters to the token is the usual rule for Spanish and it does not need to be better
    than that: what this feeds is a sleep, and being twenty per cent pessimistic costs seconds
    while being wrong in the other direction costs the run.
    """
    return MAX_ANSWER_TOKENS + len(prompt_for(case, plan)) // 4


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
            max_output_tokens=MAX_ANSWER_TOKENS,
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
    for field in (*FIELDS, ANSWERS):
        seen = [verdict.get(field, {}).get("verdict", "unjudgeable") for verdict in verdicts]
        counted[field] = {
            "right": seen.count("right"),
            "wrong": seen.count("wrong"),
            "unjudgeable": seen.count("unjudgeable"),
        }
    return counted


# The plan key each judged field is carried by, so "how many different values does this group put
# in front of the judge" is asked of the plans rather than of a list written here.
_PLAN_KEY: Final = {
    "window": ("period", "date_range"),
    "direction": ("direction",),
    "categories": ("categories",),
    "merchants": ("merchants",),
    "group_by": ("group_by",),
    "metric": ("metric",),
}


def spread(plans: list[dict[str, Any]]) -> dict[str, int]:
    """How many distinct values each field takes across the plans about to be judged.

    **The number that says what a column can be worth.** A field with one value everywhere cannot
    separate a judge from a coin: if every plan in the group says EXPENSE, then "always right" and
    "always EXPENSE is right" score identically, and 72/0 buys nothing. The anchor is 74 rows of 18
    sentences and three of its fields are constant, which is why the baseline below is printed
    beside every run instead of being worked out once and forgotten.

    Derived from the plans, so it stays true as the group changes — a note in a docstring saying
    "direction is constant here" would be right on the day it was written and wrong afterwards.
    """
    return {
        field: len({json.dumps([plan.get(key) for key in keys], sort_keys=True) for plan in plans})
        for field, keys in _PLAN_KEY.items()
    }


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

    verdicts: list[dict[str, Any]] = []
    judged: list[dict[str, Any]] = []
    unreadable: list[str] = []
    if arguments.baseline:
        # A judge that says `right` to everything, which needs no provider and is the number every
        # real judge has to beat. On a field whose value never varies it is not a floor, it is the
        # ceiling too: "always right" and "always EXPENSE is right" score the same 72/0, so a model
        # matching it there has demonstrated nothing at all.
        verdicts = [
            {field: {"verdict": "right", "why": "baseline"} for field in (*FIELDS, ANSWERS)}
            for _ in scored
        ]
        where = f"BASELINE always-right · {arguments.group} · {len(verdicts)} cases"
    else:
        provider = _provider(arguments.provider, arguments.base_url, arguments.think, None)
        # Paced from the limit the provider actually enforces. This was `--rpm`, and requests a
        # minute is not what Groq counts — it counts tokens, so a number that worked at one answer
        # budget returns 429 at another and the next person to run this rediscovers that. Given the
        # budget, the pace is arithmetic rather than a remembered number.
        #
        # Cloudflare is not paceable at all: its Workers AI free tier meters a *daily* allowance,
        # so a run either fits in what is left of the day or a single call fails.
        cost = max((_tokens_in(case, plans[case["say"]]) for case in scored), default=0)
        pace = 60.0 * cost / arguments.tpm if arguments.tpm else 0.0
        if pace:
            print(f"pacing {60.0 / pace:.1f} calls a minute · {cost} tokens · tpm {arguments.tpm}")

        for number, case in enumerate(scored):
            if number and pace:
                await asyncio.sleep(pace)
            verdict = await judge(provider, arguments.model, case, plans[case["say"]])
            if verdict is None:
                unreadable.append(case["say"])
                continue
            verdicts.append(verdict)
            judged.append({"case": case, "verdict": verdict})
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
    # How many different answers this group actually puts in front of the judge, printed beside the
    # verdicts rather than left to be worked out. A `1` is the whole story of that row: the column
    # is free, the baseline scores full marks on it, and a model matching the baseline there has
    # been asked nothing.
    varies = spread([plans[case["say"]] for case in scored])
    for field, counts in tally(verdicts).items():
        distinct = varies.get(field)
        says = "" if distinct is None else f"  · {distinct} distinct value{'s'[: distinct ^ 1]}"
        print(
            f"  {field:20} right {counts['right']:>3}  wrong {counts['wrong']:>3}"
            f"  unjudgeable {counts['unjudgeable']:>3}{says}"
        )

    if arguments.explain:
        # The disagreements, one at a time, which is the only way this stops being a scoreboard.
        # A `wrong` is either a planner defect or a judge defect and the count cannot say which —
        # "¿cuánto paso a la hucha cada mes?" came back with a Bizum category and the judge was
        # right to reject it, while eight `wrong` merchants on a group where every merchant list is
        # empty were the judge inventing a disagreement. Both are one row in the same column.
        print(f"\n--- every wrong verdict on {arguments.explain} ---\n")
        for entry in judged:
            said = entry["verdict"].get(arguments.explain, {})
            if said.get("verdict") != "wrong":
                continue
            print(f"  {entry['case']['say']!r}")
            print(f"      plan  {json.dumps(plans[entry['case']['say']], ensure_ascii=False)}")
            print(f"      judge {said.get('why', '')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge planner output field by field.")
    parser.add_argument("--provider", default="ollama", help="the judge's provider")
    parser.add_argument("--model", default="none", help="the judge's model")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="score with a judge that says right to everything; needs no provider",
    )
    parser.add_argument(
        "--explain",
        default=None,
        choices=[*FIELDS, ANSWERS],
        help="print every wrong verdict on this field, with the plan and the judge's reason",
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--plans", required=True, help="YAML mapping sentence -> plan")
    parser.add_argument("--group", default="anchor", choices=["anchor", "extension", "all"])
    parser.add_argument(
        "--tpm",
        type=int,
        default=0,
        help="the provider's tokens-a-minute limit; the pace comes from it. 0 is unpaced",
    )
    arguments = parser.parse_args()
    plans = yaml.safe_load(Path(arguments.plans).read_text(encoding="utf-8")) or {}
    return asyncio.run(run(arguments, plans))


if __name__ == "__main__":
    raise SystemExit(main())
