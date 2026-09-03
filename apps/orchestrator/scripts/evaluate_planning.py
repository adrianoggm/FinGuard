"""Measures how well a model turns Spanish sentences into plans.

    uv run python scripts/evaluate_planning.py --model qwen3.5:4b
    uv run python scripts/evaluate_planning.py --provider groq \
        --model openai/gpt-oss-120b --dataset metamorphic --tpm 8000
    uv run python scripts/evaluate_planning.py --provider cloudflare \
        --model @cf/qwen/qwen3-30b-a3b-fp8 --dataset metamorphic

Exact match is reported but it is not the headline. A plan with the right window and
the wrong direction is not "one failure": it is one field right and one field wrong,
and a score that cannot tell those apart cannot tell you what to fix. So the per-field
table is what you read.

Two rates matter more than accuracy:

INVENTED — the dataset says ask or refuse, and the model produced a plan. This is the
failure nothing else catches. It is schema-valid, the domain accepts it, banking
returns a real figure, and the user reads a correct answer to a question they did not
ask. Every other number here can look healthy while this one is bad.

MISSED — the dataset says plan, and the model asked or refused. Cheap by comparison:
the user is inconvenienced, not misinformed. Worth watching so a model cannot score
well by refusing everything. It is reported as a **rate** as well as a list, because
`Unsafe execution <= 10 %` in gate 1 falls when the system refuses and something has to
bound that: the two hardest sets have the best safety numbers because they fail by
saying no.

ROBUSTNESS — of the pairs a dataset marks with `paraphrase_of`, how many produced the
same answer on both sides. Deliberately independent of whether that answer was right:
two identically wrong plans are robust, and a paraphrase that flips a correct plan to a
wrong one and a paraphrase that flips a wrong plan to a correct one are both breaks.
Accuracy says how many of the sentences we wrote down are understood; this says whether
understanding survives somebody saying it their own way, and only the second one
predicts a stranger's experience.

The reference date comes from the dataset and never from the clock, so a run today and
a run in March are comparable.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx2 as httpx
import yaml  # type: ignore[import-untyped]

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionQuery,
    _from_what_the_sentence_said,
    inherited_window,
)
from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    PlanningOutcome,
    TransactionType,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import (
    ContinuationBase,
    ContinuationSource,
    planning_context,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.services import capability_guard, explicit_facts, plan_verifier
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.guard_policy import GuardPolicy
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import cloudflare_workers_ai
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)
from finguard_orchestrator.infrastructure.planning import prompt
from finguard_orchestrator.infrastructure.planning.llm_query_planner import LlmQueryPlanner

PLANNING = Path(__file__).resolve().parents[1] / "evaluation" / "planning"

# The answer budget every planning call is made with. It comes from the same deployment setting as
# the service: reasoning models such as gpt-oss need headroom for their hidden chain of thought,
# while the versioned default remains 384 for ordinary local models.
ANSWER_TOKENS = get_settings().planner_max_output_tokens

# The vocabulary that exists in the seeded data. It belongs to banking; until there is
# somewhere legitimate to read it from, it is stated here and the drift shows up as an
# INVALID_DOMAIN in this very report.
CATEGORIES = (
    "GROCERIES",
    "TRANSPORT_FUEL",
    "RESTAURANTS",
    "TRANSPORT_PUBLIC",
    "HEALTH",
    "LEISURE",
    "SUBSCRIPTIONS",
    "HOUSING_RENT",
    "SALARY",
    "TRANSFER_BIZUM",
)

REPOSITORY = Path(__file__).resolve().parents[2]

FIELDS = (
    "window",
    "direction",
    "categories",
    "excludedCategories",
    "merchants",
    "excludedMerchants",
    "counterparties",
    "excludedCounterparties",
    "counterpartyTypes",
    "groupBy",
    "metric",
    "groupLimit",
    "includeInternal",
    "internalOnly",
    "acrossAccounts",
    "onlyAccountId",
    "onlyAccountIds",
    "transactionIds",
    "transactionTypes",
)


class EvaluationProvider(LlmProviderPort, Protocol):
    """A completion provider that can also prove it is reachable before a paid run."""

    async def ping(self) -> bool: ...


def resolve(period: str, n: int | None, today: date) -> tuple[str, str]:
    """Named window -> the days it covers, so equivalent plans compare equal.

    A model that answers TODAY and one that answers the explicit range for today have
    produced the same query. Scoring the first right and the second wrong would be
    measuring how it phrased the window rather than whether it understood it.

    This duplicates banking's `resolve`, which is normally the thing to avoid. Here it
    is deliberate and it stays in the scorer: the day these two disagree, the dataset
    starts failing and someone looks — whereas sharing the implementation under test
    with the thing testing it would hide exactly that.
    """
    first_of_month = today.replace(day=1)
    match period:
        case "TODAY":
            start = end = today
        case "YESTERDAY":
            start = end = today - timedelta(days=1)
        case "THIS_WEEK":
            start, end = today - timedelta(days=today.weekday()), today
        case "LAST_WEEK":
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
        case "THIS_MONTH":
            start, end = first_of_month, today
        case "LAST_MONTH":
            end = first_of_month - timedelta(days=1)
            start = end.replace(day=1)
        case "THIS_YEAR":
            start, end = date(today.year, 1, 1), today
        case "LAST_N_DAYS":
            start, end = today - timedelta(days=(n or 1) - 1), today
        case "LAST_N_MONTHS":
            total = today.year * 12 + today.month - 1 - ((n or 1) - 1)
            start, end = date(total // 12, total % 12 + 1, 1), today
        case _:
            return (period, str(n))
    return (start.isoformat(), end.isoformat())


@dataclass
class GroupScore:
    """One functional capability. A single accuracy number says the assistant is 85%
    right and nothing about what it is wrong at; this says which capability to fix."""

    cases: int = 0
    outcome_right: int = 0
    exact: int = 0
    plans: int = 0


@dataclass
class Score:
    cases: int = 0
    outcome_right: int = 0
    exact: int = 0
    invented: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    # Cases where a figure would have reached the user and would not have answered the
    # question. The accuracy number cannot see this: a plan that never ran and a plan
    # that ran and was wrong are both "not exact", and only one of them puts a real
    # euro amount in front of somebody as the answer to something they did not ask.
    #
    # Three ways in, and they are one failure: the plan executed and disagreed with the
    # sentence — because the sentence named a capability we do not have, because it
    # determined nothing and we guessed, or because the fields came out wrong.
    unsafe: list[tuple[str, str]] = field(default_factory=list)
    field_right: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    field_total: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    structural: int = 0
    latencies: list[float] = field(default_factory=list)
    wrong: list[tuple[str, str]] = field(default_factory=list)
    groups: dict[str, GroupScore] = field(default_factory=dict)
    # A case and its paraphrase say the same thing differently. Getting one right and
    # the other wrong is string matching rather than reading, and the aggregate hides it.
    paraphrase_splits: list[str] = field(default_factory=list)
    right_by_id: dict[str, bool] = field(default_factory=dict)
    # A metamorphic pair deliberately changes one named dimension. It is scored separately from
    # exact accuracy: two individually plausible plans can still reveal that changing "importe"
    # to "cantidad" also lost the account, category or window.
    metamorphic_total: int = 0
    metamorphic_right: int = 0
    metamorphic_violations: list[str] = field(default_factory=list)
    results_by_id: dict[str, PlanningResult] = field(default_factory=dict)
    # The metamorphic property with an empty declared delta: a paraphrase changes no field, so
    # the two plans must be identical. Scored against the *base's own answer* rather than against
    # the dataset's expectation, which is what makes it robustness and not a second accuracy
    # number — a base the system gets wrong is still robust if every rewording of it is wrong the
    # same way, and that distinction is the whole point of the row.
    invariance_total: int = 0
    invariance_held: int = 0
    invariance_breaks: list[str] = field(default_factory=list)
    # Per base as [held, total]. The headline pair rate otherwise gives a base with twelve
    # rewrites six times the influence of one with two; their mean gives every question one vote.
    invariance_by_base: dict[str, list[int]] = field(default_factory=dict)
    # The same property sliced by capability, plus orthogonal diagnostics for a break.  Outcome
    # transitions alone are not safety labels: NEEDS_CLARIFICATION -> PLANNED can be a fix or an
    # unsafe execution depending on the dataset expectation and the field scorer.
    invariance_by_group: dict[str, list[int]] = field(default_factory=dict)
    invariance_break_classes: Counter[str] = field(default_factory=Counter)
    invariance_field_changes: Counter[str] = field(default_factory=Counter)
    invariance_group_by_transitions: Counter[str] = field(default_factory=Counter)
    # The denominator of the refusal rate: cases the dataset says are answerable. `missed` is the
    # numerator and already exists; without this it could only ever be printed as a bare count.
    answerable: int = 0


@dataclass(frozen=True)
class RobustnessGate:
    """Explicit thresholds, supplied only after a stable baseline has been measured."""

    minimum_macro_pct: float
    maximum_over_refusal_pct: float
    maximum_unsafe_pct: float = 10.0
    maximum_invalid_structure: int = 0


def robustness_macro_pct(score: Score) -> float | None:
    measured = [held / total for held, total in score.invariance_by_base.values() if total]
    return 100 * statistics.mean(measured) if measured else None


def robustness_gate_failures(
    score: Score, metadata: dict[str, Any], gate: RobustnessGate
) -> list[str]:
    """Why a robustness run cannot pass; empty means the complete measurement passed."""
    expected_bases = int(metadata["base_count"])
    expected_pairs = int(metadata["paraphrase_count"])
    failures: list[str] = []
    if len(score.results_by_id) != expected_bases + expected_pairs:
        failures.append(
            f"executed {len(score.results_by_id)}/{expected_bases + expected_pairs} cases"
        )
    if score.invariance_total != expected_pairs:
        failures.append(f"measured {score.invariance_total}/{expected_pairs} paraphrase pairs")
    if len(score.invariance_by_base) != expected_bases:
        failures.append(
            f"measured {len(score.invariance_by_base)}/{expected_bases} paraphrase bases"
        )
    macro = robustness_macro_pct(score)
    if macro is None:
        failures.append("robustness has no measured base")
    elif macro < gate.minimum_macro_pct:
        failures.append(f"macro robustness {macro:.1f}% is below {gate.minimum_macro_pct:.1f}%")
    if not score.answerable:
        failures.append("over-refusal has no answerable denominator")
    else:
        over_refusal = 100 * len(score.missed) / score.answerable
        if over_refusal > gate.maximum_over_refusal_pct:
            failures.append(
                f"over-refusal {over_refusal:.1f}% is above {gate.maximum_over_refusal_pct:.1f}%"
            )
    if not score.cases:
        failures.append("unsafe execution has no case denominator")
    else:
        unsafe = 100 * len(score.unsafe) / score.cases
        if unsafe > gate.maximum_unsafe_pct:
            failures.append(
                f"unsafe execution {unsafe:.1f}% is above {gate.maximum_unsafe_pct:.1f}%"
            )
    if score.structural > gate.maximum_invalid_structure:
        failures.append(
            f"invalid structure {score.structural} is above {gate.maximum_invalid_structure}"
        )
    return failures


def robustness_gate_exit_code(score: Score, metadata: dict[str, Any], gate: RobustnessGate) -> int:
    """Print the gate verdict and return the process status used by the CLI."""
    broken = robustness_gate_failures(score, metadata, gate)
    if not broken:
        return 0
    print("\n  ROBUSTNESS GATE FAILED", file=sys.stderr)
    for problem in broken:
        print(f"    {problem}", file=sys.stderr)
    return 1


def _command_output(command: list[str], *, allow_empty: bool = False) -> str | None:
    """Best-effort provenance: measurement must still run on machines without Git/NVIDIA."""
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout.strip()
    return output if output or allow_empty else None


def _gpu_snapshot() -> list[dict[str, Any]] | None:
    raw = _command_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if raw is None:
        return None
    fields = (
        "name",
        "memory_used_mib",
        "memory_total_mib",
        "utilization_pct",
        "temperature_c",
    )
    return [
        dict(zip(fields, (part.strip() for part in line.split(",")), strict=True))
        for line in raw.splitlines()
    ]


async def _ollama_snapshot(provider: EvaluationProvider, model: str) -> dict[str, Any] | None:
    if not isinstance(provider, OllamaLlmProvider):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags_response = await client.get(f"{provider._base_url}/api/tags")
            tags_response.raise_for_status()
            ps_response = await client.get(f"{provider._base_url}/api/ps")
            ps_response.raise_for_status()
        tags = tags_response.json().get("models", [])
        running = ps_response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    installed: dict[str, Any] = next(
        (item for item in tags if item.get("name") == model or item.get("model") == model), {}
    )
    resident: dict[str, Any] = next(
        (item for item in running if item.get("name") == model or item.get("model") == model), {}
    )
    return {
        "digest": installed.get("digest"),
        "size_bytes": installed.get("size"),
        "size_vram_bytes": resident.get("size_vram"),
        "context_length": resident.get("context_length"),
    }


async def _run_manifest(
    dataset_path: Path, provider: EvaluationProvider, model: str, case_count: int
) -> dict[str, Any]:
    commit = _command_output(["git", "rev-parse", "HEAD"])
    dirty = _command_output(["git", "status", "--porcelain"], allow_empty=True)
    return {
        "schema": 1,
        "model": model,
        "provider": provider.name,
        "cases": case_count,
        "commit": commit,
        "dirty": bool(dirty) if dirty is not None else None,
        "corpus_sha256": hashlib.sha256(
            await asyncio.to_thread(dataset_path.read_bytes)
        ).hexdigest(),
        "python": platform.python_version(),
        "provider_settings": {
            "timeout_seconds": getattr(provider, "timeout_seconds", None),
            "context_tokens": getattr(provider, "context_tokens", None),
            "seed": getattr(provider, "seed", None),
            "think": getattr(provider, "think", None),
        },
        "model_runtime": await _ollama_snapshot(provider, model),
        "gpu": _gpu_snapshot(),
    }


def _run_summary(score: Score) -> dict[str, Any]:
    ordered = sorted(score.latencies)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95)) if ordered else 0
    macro = robustness_macro_pct(score)
    return {
        "schema": 1,
        "cases": score.cases,
        "robustness_pair_pct": (
            100 * score.invariance_held / score.invariance_total if score.invariance_total else None
        ),
        "robustness_macro_pct": macro,
        "unsafe_pct": 100 * len(score.unsafe) / score.cases if score.cases else None,
        "over_refusal_pct": (
            100 * len(score.missed) / score.answerable if score.answerable else None
        ),
        "invalid_structure": score.structural,
        "latency_seconds": {
            "p50": statistics.median(ordered) if ordered else None,
            "p95": ordered[p95_index] if ordered else None,
            "max": ordered[-1] if ordered else None,
            "last_ten_mean": statistics.fmean(score.latencies[-10:]) if ordered else None,
        },
        "gpu": _gpu_snapshot(),
    }


def as_plan(spec: dict[str, Any]) -> FinancialPlan:
    """The dataset's plan shape, built as a plan.

    Used for the `previous` of a follow-up, which the planner now takes as a plan rather
    than as its description. The dataset says it in the same shape as an expectation, and
    it goes through the same validation the API does — a context this cannot build is one
    a client could not send either.
    """
    period = spec.get("period")
    window = spec.get("dateRange")
    return FinancialPlan(
        direction=Direction(spec.get("direction", "EXPENSE")),
        period=(
            RelativePeriod(period=Period(period["period"]), n=period.get("n")) if period else None
        ),
        date_range=(
            AbsoluteRange(
                start=date.fromisoformat(str(window["from"])),
                end=date.fromisoformat(str(window["to"])),
            )
            if window
            else None
        ),
        categories=tuple(spec.get("categories", ())),
        excluded_categories=tuple(spec.get("excludedCategories", ())),
        merchants=tuple(spec.get("merchants", ())),
        excluded_merchants=tuple(spec.get("excludedMerchants", ())),
        counterparties=tuple(spec.get("counterparties", ())),
        excluded_counterparties=tuple(spec.get("excludedCounterparties", ())),
        counterparty_types=tuple(
            CounterpartyType(value) for value in spec.get("counterpartyTypes", ())
        ),
        group_by=GroupBy(spec.get("groupBy", "NONE")),
        metric=Metric(spec.get("metric", "SUM")),
        group_limit=spec.get("groupLimit"),
        include_internal=bool(spec.get("includeInternal", False)),
        internal_only=bool(spec.get("internalOnly", False)),
        across_accounts=bool(spec.get("acrossAccounts", False)),
        only_account_id=(UUID(str(spec["onlyAccountId"])) if spec.get("onlyAccountId") else None),
        only_account_ids=tuple(UUID(str(value)) for value in spec.get("onlyAccountIds", ())),
        transaction_ids=tuple(UUID(str(value)) for value in spec.get("transactionIds", ())),
        transaction_types=tuple(
            TransactionType(value) for value in spec.get("transactionTypes", ())
        ),
    )


def _provider(
    name: str,
    base_url: str | None,
    think: bool | str,
    timeout_seconds: float | None,
) -> EvaluationProvider:
    """Build the selected provider from the same settings used by the service.

    A benchmark-specific URL, timeout or reasoning budget would measure a deployment nobody
    runs. Command-line overrides exist for recorded endpoints and unusually slow local models,
    while every omitted value comes from ``Settings``. Missing hosted credentials fail here,
    before a corpus sentence can leave the machine.
    """
    settings = get_settings()
    if name == "groq":
        if settings.groq_api_key is None:
            raise ValueError("ORCHESTRATOR_GROQ_API_KEY is not set - see .env.defaults")
        return OpenAiCompatibleProvider(
            name=name,
            api_key=settings.groq_api_key.get_secret_value(),
            base_url=base_url or settings.groq_base_url,
            timeout_seconds=timeout_seconds or settings.groq_timeout_seconds,
            reasoning_headroom=settings.groq_reasoning_headroom,
        )
    if name == "cloudflare":
        if settings.cloudflare_api_token is None or settings.cloudflare_account_id is None:
            raise ValueError(
                "ORCHESTRATOR_CLOUDFLARE_API_TOKEN and "
                "ORCHESTRATOR_CLOUDFLARE_ACCOUNT_ID must both be set - see .env.defaults"
            )
        return cloudflare_workers_ai(
            api_token=settings.cloudflare_api_token.get_secret_value(),
            account_id=settings.cloudflare_account_id,
            api_base=base_url or settings.cloudflare_api_base,
            timeout_seconds=timeout_seconds or settings.cloudflare_timeout_seconds,
            reasoning_headroom=settings.cloudflare_reasoning_headroom,
        )
    if name != "ollama":
        raise ValueError(f"unknown provider: {name}")
    return OllamaLlmProvider(
        base_url=base_url or settings.ollama_base_url,
        keep_alive=settings.ollama_keep_alive,
        timeout_seconds=timeout_seconds or settings.ollama_timeout_seconds,
        context_tokens=settings.ollama_context_tokens,
        seed=settings.ollama_seed,
        # False is the historical CLI default. Let the deployment choose a model-specific
        # level (gpt-oss needs low/medium/high for structured output) unless the caller
        # explicitly enabled thinking for this run.
        think=settings.ollama_think if not think else think,
    )


def _gated(
    case: dict[str, Any], raw: PlanningResult, policy: GuardPolicy, *, today: date
) -> PlanningResult:
    """The deterministic gates applied to one recorded model answer.

    Split out from the model call because the gates are pure: given the model's raw result,
    every policy is a function of it. That is what makes the ablation nearly free — one
    inference pass, then as many policies as there are rules, replayed offline.

    The order matters and mirrors the handler. The guard sees the sentence before the model
    does, so a guard refusal wins even though the model was asked anyway to record what it
    would have said.
    """
    refused = capability_guard.unsupported_reason(case["question"], policy)
    if refused is not None:
        return PlanningResult.unsupported(refused)
    if not raw.is_answerable:
        rescued = _from_what_the_sentence_said(
            raw,
            AskFinancialQuestionQuery(
                sentence=case["question"],
                account_id=UUID(int=0),
                today=today,
            ),
        )
        if rescued is not None:
            raw = rescued
    if raw.plan is None:
        return raw
    facts = explicit_facts.read(case["question"], today=today)
    checked = explicit_facts.enforce(raw.plan, facts)
    broken = plan_verifier.disagreement(case["question"], checked, policy)
    if broken is None:
        return PlanningResult.planned(checked)
    mended = plan_verifier.repaired(broken, checked)
    if mended is not None and plan_verifier.disagreement(case["question"], mended, policy) is None:
        return PlanningResult.planned(mended)
    return PlanningResult.invalid_domain(broken)


async def _planned(
    planner: LlmQueryPlanner,
    case: dict[str, Any],
    *,
    today: date,
    previous: FinancialPlan | None,
) -> PlanningResult:
    """One case through the pipeline as it ships, not through the model alone.

    The guard, explicit-fact enrichment, deterministic rescue and verifier change the outcome,
    so a score that skipped any of them would measure a system nobody runs. This is especially
    visible for exclusions: they are intentionally absent from the constrained model schema and
    become plan fields only in the enrichment step.

    `previous` arrives built rather than as a mapping to be read here. Two datasets reach this
    pipeline now and they spell a plan differently — this file's `previous` is an expectation and
    the handover cases' is the payload a client echoes back — so each caller uses the reader that
    matches its own file, and there is no third spelling in the middle.
    """
    refused = capability_guard.unsupported_reason(case["question"])
    if refused is not None:
        return PlanningResult.unsupported(refused)

    result = await planner.plan(
        case["question"],
        context=planning_context(
            Conversation(last_plan=previous),
            today=today,
            reporting_timezone="UTC",
            analysis=TurnAnalysis(
                Intent.FINANCIAL_QUERY,
                TurnRelation.FOLLOW_UP if previous is not None else TurnRelation.NEW_REQUEST,
                TurnFocus.LAST_ANSWER if previous is not None else TurnFocus.NEW_REQUEST,
            ),
            continuation=(
                ContinuationBase(previous, ContinuationSource.LAST_ANSWER)
                if previous is not None
                else None
            ),
        ),
    )
    if not result.is_answerable:
        rescued = _from_what_the_sentence_said(
            result,
            AskFinancialQuestionQuery(
                sentence=case["question"],
                account_id=UUID(int=0),
                today=today,
            ),
        )
        if rescued is not None:
            result = rescued
    if result.plan is None:
        return result

    facts = explicit_facts.read(case["question"], today=today)
    checked = explicit_facts.enforce(result.plan, facts)
    # The same step the handler runs beside `enforce`, and here for the reason in this function's
    # own docstring: a score that skipped it would measure a system nobody runs. `previous` is the
    # dataset's declared preceding plan, which is exactly what the handler passes — the
    # continuation base rather than a mirror of it.
    checked = inherited_window(checked, previous, facts)
    if previous is not None and facts.corrects and isinstance(checked, FinancialPlan):
        checked = explicit_facts.restored_filters(checked, previous, facts)

    broken = plan_verifier.disagreement(case["question"], checked)
    if broken is None:
        return PlanningResult.planned(checked)
    mended = plan_verifier.repaired(broken, checked)
    if mended is not None and plan_verifier.disagreement(case["question"], mended) is None:
        return PlanningResult.planned(mended)
    return PlanningResult.invalid_domain(broken)


def expected_fields(plan: dict[str, Any], today: date) -> dict[str, Any]:
    """Only the fields the sentence determines. A field the dataset does not mention is
    not scored: inventing an expectation for it would punish a correct default."""
    out: dict[str, Any] = {}
    if "period" in plan:
        period = plan["period"]
        out["window"] = resolve(period["period"], period.get("n"), today)
    if "dateRange" in plan:
        out["window"] = (str(plan["dateRange"]["from"]), str(plan["dateRange"]["to"]))
    if "direction" in plan:
        out["direction"] = plan["direction"]
    if "categories" in plan:
        out["categories"] = tuple(sorted(plan["categories"]))
    if "excludedCategories" in plan:
        out["excludedCategories"] = tuple(sorted(plan["excludedCategories"]))
    if "merchants" in plan:
        # Compared case-insensitively, like banking matches them: a model that writes
        # "mercadona" found the same movements as one that wrote "Mercadona", and scoring
        # them differently would measure capitalisation.
        out["merchants"] = tuple(sorted(m.lower() for m in plan["merchants"]))
    if "excludedMerchants" in plan:
        out["excludedMerchants"] = tuple(sorted(m.lower() for m in plan["excludedMerchants"]))
    if "counterparties" in plan:
        out["counterparties"] = tuple(sorted(value.lower() for value in plan["counterparties"]))
    if "excludedCounterparties" in plan:
        out["excludedCounterparties"] = tuple(
            sorted(value.lower() for value in plan["excludedCounterparties"])
        )
    if "counterpartyTypes" in plan:
        out["counterpartyTypes"] = tuple(sorted(plan["counterpartyTypes"]))
    if "groupBy" in plan:
        out["groupBy"] = plan["groupBy"]
    if "metric" in plan:
        out["metric"] = plan["metric"]
    if "groupLimit" in plan:
        out["groupLimit"] = plan["groupLimit"]
    for name in ("includeInternal", "internalOnly", "acrossAccounts"):
        if name in plan:
            out[name] = bool(plan[name])
    if "onlyAccountId" in plan:
        out["onlyAccountId"] = str(plan["onlyAccountId"])
    for name in ("onlyAccountIds", "transactionIds"):
        if name in plan:
            out[name] = tuple(sorted(str(value) for value in plan[name]))
    if "transactionTypes" in plan:
        out["transactionTypes"] = tuple(sorted(plan["transactionTypes"]))
    return out


def expected_case_fields(case: dict[str, Any], today: date) -> dict[str, Any]:
    """Expected dimensions, with full semantics for the metamorphic contract.

    The older accuracy corpora intentionally score only fields their handwritten expectation
    names. A metamorphic case has a stronger promise: changing one phrase must preserve every
    other query dimension. Its plan therefore supplies defaults too, so an invented merchant or
    account restriction is a wrong executable query rather than an invisible extra field.
    """
    if str(case.get("group", "")).startswith("metamorphic_"):
        return plan_fields(as_plan(case["plan"]), today)
    return expected_fields(case["plan"], today)


def plan_fields(plan: FinancialPlan, today: date) -> dict[str, Any]:
    """Every scored dimension, including defaults, in a representation fit for deltas."""
    if plan.period is not None:
        window: Any = resolve(plan.period.period.value, plan.period.n, today)
    else:
        assert plan.date_range is not None
        window = (plan.date_range.start.isoformat(), plan.date_range.end.isoformat())
    return {
        "window": window,
        "direction": plan.direction.value,
        "categories": tuple(sorted(plan.categories)),
        "excludedCategories": tuple(sorted(plan.excluded_categories)),
        "merchants": tuple(sorted(m.lower() for m in plan.merchants)),
        "excludedMerchants": tuple(sorted(m.lower() for m in plan.excluded_merchants)),
        "counterparties": tuple(sorted(value.lower() for value in plan.counterparties)),
        "excludedCounterparties": tuple(
            sorted(value.lower() for value in plan.excluded_counterparties)
        ),
        "counterpartyTypes": tuple(sorted(value.value for value in plan.counterparty_types)),
        "groupBy": plan.group_by.value,
        "metric": plan.metric.value,
        "groupLimit": plan.group_limit,
        "includeInternal": plan.include_internal,
        "internalOnly": plan.internal_only,
        "acrossAccounts": plan.across_accounts,
        "onlyAccountId": str(plan.only_account_id) if plan.only_account_id is not None else None,
        "onlyAccountIds": tuple(sorted(str(value) for value in plan.only_account_ids)),
        "transactionIds": tuple(sorted(str(value) for value in plan.transaction_ids)),
        "transactionTypes": tuple(sorted(value.value for value in plan.transaction_types)),
    }


def ranking_fields(plan: TopMovements, today: date) -> dict[str, Any]:
    """A ranking scored on the dimensions it has, and empty on the ones it does not.

    `TopMovements` is a plan with no grouping, no metric and no account scope: "el gasto más caro
    del año" is one row, and asking it which aggregate it computes is asking about a field the
    domain does not give it. Those come back as the neutral value a dataset case that does not
    mention them compares equal to.

    Its `rank` is the field that only exists here, and it is the reason the shape exists at all —
    the recogniser that reads it used to return a boolean, so "¿y el tercero?" answered with the
    second largest.
    """
    aggregate = FinancialPlan(
        direction=plan.direction,
        period=plan.period,
        date_range=plan.date_range,
        categories=plan.categories,
        excluded_categories=plan.excluded_categories,
        merchants=plan.merchants,
        merchant_terms=plan.merchant_terms,
        excluded_merchants=plan.excluded_merchants,
    )
    return {**plan_fields(aggregate, today), "rank": plan.rank}


def actual_fields(result: PlanningResult, today: date) -> dict[str, Any]:
    """What the pipeline produced, in the vocabulary the dataset writes.

    **A ranking used to hit an `assert` here and take the whole run down with it**, which is how
    this script came to be unrunnable without anybody noticing: the scores in
    `evaluation/planning/RESULTS.md` were measured before `TopMovements` existed, and every attempt
    to reproduce them since has died on the first superlative in the dataset. A number nobody can
    reproduce is a number, and it was being quoted.
    """
    plan = result.plan
    if isinstance(plan, TopMovements):
        return ranking_fields(plan, today)
    assert isinstance(plan, FinancialPlan)
    return plan_fields(plan, today)


def judge(case: dict[str, Any], result: PlanningResult, score: Score, today: date) -> None:
    score.results_by_id[case["id"]] = result
    score.cases += 1
    group = score.groups.setdefault(case.get("group", "ungrouped"), GroupScore())
    group.cases += 1
    wanted = case["expect"]
    got = result.outcome
    if wanted == "plan":
        score.answerable += 1

    if got is PlanningOutcome.INVALID_STRUCTURE:
        score.structural += 1

    wanted_outcome = {
        "plan": PlanningOutcome.PLANNED,
        "clarification": PlanningOutcome.NEEDS_CLARIFICATION,
        "unsupported": PlanningOutcome.UNSUPPORTED,
    }[wanted]

    if got is wanted_outcome:
        score.outcome_right += 1
        group.outcome_right += 1
    elif wanted != "plan" and got is PlanningOutcome.PLANNED:
        score.invented.append(case["id"])
        score.unsafe.append((case["id"], f"executed a plan where the dataset says {wanted}"))
    elif wanted == "plan" and got in (
        PlanningOutcome.NEEDS_CLARIFICATION,
        PlanningOutcome.UNSUPPORTED,
    ):
        score.missed.append(case["id"])

    if wanted != "plan":
        return

    # Per-field scoring only where both sides have a plan. When the model produced none,
    # every field the sentence determined counts as wrong — otherwise refusing
    # everything would leave the per-field table looking untouched.
    expected = expected_case_fields(case, today)
    group.plans += 1
    for name in expected:
        score.field_total[name] += 1

    if got is not PlanningOutcome.PLANNED:
        score.wrong.append((case["id"], f"{got.value}: {result.reason or result.question}"))
        return

    actual = actual_fields(result, today)
    mismatches = [n for n, want in expected.items() if actual[n] != want]
    for name in expected:
        if name not in mismatches:
            score.field_right[name] += 1

    score.right_by_id[case["id"]] = not mismatches
    if mismatches:
        detail = ", ".join(f"{n}={actual[n]!r} != {expected[n]!r}" for n in mismatches)
        score.wrong.append((case["id"], detail))
        # It ran. Banking answered it. The figure the user reads is about a window, a
        # direction or a metric they did not ask for, and nothing downstream can tell.
        score.unsafe.append((case["id"], detail))
    else:
        score.exact += 1
        group.exact += 1


def report(model: str, score: Score) -> None:
    total = score.cases
    print(f"\n=== {model} · {total} cases ===\n")
    print(
        f"  outcome correct     {score.outcome_right:3}/{total}  {pct(score.outcome_right, total)}"
    )
    plan_total = score.field_total["direction"] or 1
    print(f"  exact plan match    {score.exact:3}/{plan_total}  {pct(score.exact, plan_total)}")

    # The headline. Everything above says how close the plan was; this says how often a
    # euro figure would have reached somebody as the answer to a question they did not
    # ask. It is the only number here that can be required to be zero.
    print(
        f"\n  UNSAFE EXECUTION    {len(score.unsafe):3}/{total}  "
        f"{pct(len(score.unsafe), total)}   a wrong figure would have been shown"
    )
    print(f"  INVENTED            {len(score.invented):3}      a plan where a question was due")
    # The bound on the other half of the safety trade. `Unsafe execution` falls when the system
    # refuses, so a build that grew more timid would improve gate 1 while answering less; this is
    # the number that stops that being free. Denominator is the cases the dataset says are
    # answerable, because refusing something the dataset itself refuses is correct.
    print(
        f"  OVER-REFUSAL        {len(score.missed):3}/{score.answerable}  "
        f"{pct(len(score.missed), score.answerable)}   a question where a plan was due"
    )
    print(f"  invalid structure   {score.structural:3}      should be 0 under constrained decoding")

    if score.groups:
        # The table worth reading. An aggregate says the assistant is 85% right and
        # nothing about what it is wrong at; this says which capability to go and fix.
        print("\n  per group")
        for name, group in sorted(score.groups.items()):
            plan_score = f"{group.exact}/{group.plans}" if group.plans else "-"
            print(
                f"    {name:20} outcome {group.outcome_right:3}/{group.cases:<3}"
                f" {pct(group.outcome_right, group.cases)}   exact {plan_score:>7}"
            )

    print("\n  per field")
    for name in FIELDS:
        got, want = score.field_right[name], score.field_total[name]
        if want:
            print(f"    {name:12} {got:3}/{want}  {pct(got, want)}")

    if score.latencies:
        ordered = sorted(score.latencies)
        p50 = statistics.median(ordered)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        mean = statistics.fmean(ordered)
        first, last = score.latencies[0], statistics.fmean(score.latencies[-10:])
        print(
            f"\n  latency  P50 {p50:.2f}s   mean {mean:.2f}s   P95 {p95:.2f}s   "
            f"max {ordered[-1]:.2f}s"
        )
        # A laptop GPU under sustained load drops its clock, so a set measured start to
        # finish is not measured under one condition. Printing the first case beside the
        # last ten is what makes that visible instead of hiding inside the mean: a cold
        # load inflates the first, and thermal throttling inflates the last.
        print(f"           first {first:.2f}s   last ten {last:.2f}s")

    if score.invariance_total:
        # The headline of a robustness set, and the figure gate 1 carries. Printed before the
        # splits below because it subsumes them: a split is a break that happened to cross the
        # line between right and wrong, and this counts the ones that did not as well.
        print(
            f"\n  ROBUSTNESS          {score.invariance_held:3}/{score.invariance_total}  "
            f"{pct(score.invariance_held, score.invariance_total)}   "
            "a rewording produced the same answer"
        )
        macro = robustness_macro_pct(score)
        if macro is not None:
            print(
                f"  ROBUSTNESS-MACRO             {macro:5.1f}%   every base question has one vote"
            )
        if score.invariance_by_group:
            print("\n  robustness per group")
            for name, (held, measured) in sorted(score.invariance_by_group.items()):
                print(f"    {name:20} {held:3}/{measured:<3} {pct(held, measured)}")
        if score.invariance_break_classes:
            print("\n  robustness breaks by meaning")
            for name, count in score.invariance_break_classes.most_common():
                print(f"    {name:34} {count:3}")
        if score.invariance_field_changes:
            print("\n  changed fields")
            for name, count in score.invariance_field_changes.most_common():
                print(f"    {name:34} {count:3}")
        if score.invariance_group_by_transitions:
            print("\n  groupBy transitions")
            for name, count in score.invariance_group_by_transitions.most_common():
                print(f"    {name:34} {count:3}")
        for broken in score.invariance_breaks:
            print(f"    {broken}")

    if score.paraphrase_splits:
        print("\n  said differently, answered differently")
        for pair in score.paraphrase_splits:
            print(f"    {pair}")

    if score.metamorphic_total:
        print(
            "\n  metamorphic deltas  "
            f"{score.metamorphic_right:3}/{score.metamorphic_total}  "
            f"{pct(score.metamorphic_right, score.metamorphic_total)}"
        )
        for violation in score.metamorphic_violations:
            print(f"    {violation}")

    if score.unsafe:
        print("\n  unsafe — these would have shown a figure")
        for case_id, why in score.unsafe:
            print(f"    {case_id:34} {why}")

    if score.invented:
        print(f"\n  invented: {', '.join(score.invented)}")
    if score.wrong:
        print("\n  wrong plans")
        for case_id, detail in score.wrong:
            print(f"    {case_id:34} {detail}")


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    -"


async def run(
    model: str,
    limit: int | None,
    provider: EvaluationProvider,
    dataset: str = "dev",
    pace_seconds: float = 0.0,
    collect: list[tuple[int, int]] | None = None,
    gate: RobustnessGate | None = None,
) -> int:
    dataset_path = PLANNING / f"{dataset}.yaml"
    data = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    today = data["reference"]
    all_cases = data["cases"]
    malformed = validate_metamorphic_cases(all_cases, today=today)
    malformed += validate_paraphrase_cases(all_cases)
    if dataset == "robustness":
        malformed += validate_robustness_metadata(data)
    if malformed:
        for issue in malformed:
            print(f"invalid dataset: {issue}", file=sys.stderr)
        return 2
    cases = all_cases[:limit] if limit else all_cases
    manifest = await _run_manifest(dataset_path, provider, model, len(cases))
    print(f"RUN-MANIFEST {json.dumps(manifest, sort_keys=True)}", flush=True)

    planner = LlmQueryPlanner(
        provider,
        model=model,
        temperature=0.0,
        max_output_tokens=ANSWER_TOKENS,
        categories=CATEGORIES,
    )

    score = Score()
    for index, case in enumerate(cases):
        if index and pace_seconds:
            await asyncio.sleep(pace_seconds)
        started = time.perf_counter()
        context = case.get("previous")
        result = await _planned(
            planner, case, today=today, previous=as_plan(context) if context else None
        )
        score.latencies.append(time.perf_counter() - started)

        if result.outcome is PlanningOutcome.PROVIDER_ERROR:
            print(f"provider failed on {case['id']}: {result.reason}", file=sys.stderr)
            return 2

        judge(case, result, score, today)

    _check_paraphrases(cases, score)
    _check_metamorphs(cases, score, today=today)
    _check_invariance(cases, score, today=today)

    if collect is None:
        report(f"{provider.name}/{model}", score)
    else:
        # Repeated runs: the first one prints the detail, the rest only add their totals
        # so the spread can be shown without repeating a page of failures.
        collect.append((score.outcome_right, score.exact))
        if len(collect) == 1:
            report(f"{provider.name}/{model}", score)
    summary = _run_summary(score)
    summary["model_runtime"] = await _ollama_snapshot(provider, model)
    print(f"RUN-SUMMARY {json.dumps(summary, sort_keys=True)}", flush=True)
    if gate is not None:
        return robustness_gate_exit_code(score, data["metadata"], gate)
    return 0


def _check_invariance(cases: list[dict[str, Any]], score: Score, *, today: date) -> None:
    """The metamorphic property over a rewording, whose declared delta is empty.

    `_check_metamorphs` cannot express this: the dataset's own validator requires `changes` to
    name at least one field, and a paraphrase changes none. So the pair is compared here, and the
    comparison is against **the base's produced answer** rather than the dataset's expectation.

    That is the difference between this number and every other one in this file. If the system
    answers «¿cuánto gasté el mes pasado?» with the wrong window, and answers all four rewordings
    of it with the same wrong window, this is 4/4 — the system is wrong, consistently, and a fix
    to that base fixes all five sentences. If it gets the base right and one rewording wrong, this
    is 3/4 and the defect is that the reader is matching strings. The first is a coverage problem
    and the second is the problem this whole design exists for.
    """
    for case in cases:
        twin = case.get("paraphrase_of")
        if twin is None:
            continue
        before = score.results_by_id.get(twin)
        after = score.results_by_id.get(case["id"])
        # A `--limit` run may hold only one side of a pair. It did not measure that pair.
        if before is None or after is None:
            continue
        score.invariance_total += 1
        by_base = score.invariance_by_base.setdefault(str(twin), [0, 0])
        by_base[1] += 1
        by_group = score.invariance_by_group.setdefault(str(case.get("group", "ungrouped")), [0, 0])
        by_group[1] += 1
        if before.outcome is not after.outcome:
            score.invariance_breaks.append(
                f"{case['id']}: base {before.outcome.value}, rewording {after.outcome.value}"
            )
            _classify_invariance_break(case, before, after, score)
            continue
        if before.plan is None or after.plan is None:
            # Both declined, and declined the same way. A refusal that survives rewording is as
            # much a held property as a plan that does; whether it *should* have refused is the
            # accuracy question, and this is not it.
            score.invariance_held += 1
            by_base[0] += 1
            by_group[0] += 1
            continue
        left, right = actual_fields(before, today), actual_fields(after, today)
        if left.keys() != right.keys():
            score.invariance_breaks.append(f"{case['id']}: rewording changed the shape of the plan")
            _classify_invariance_break(case, before, after, score)
            continue
        moved = sorted(name for name in left if left[name] != right[name])
        if moved:
            score.invariance_breaks.append(f"{case['id']}: rewording changed {moved}")
            _classify_invariance_break(
                case, before, after, score, moved=moved, left=left, right=right
            )
        else:
            score.invariance_held += 1
            by_base[0] += 1
            by_group[0] += 1


def _case_was_correct(case: dict[str, Any], result: PlanningResult, score: Score) -> bool:
    """Accuracy of one side of a robustness pair, including plan fields."""
    expected = case["expect"]
    wanted = {
        "plan": PlanningOutcome.PLANNED,
        "clarification": PlanningOutcome.NEEDS_CLARIFICATION,
        "unsupported": PlanningOutcome.UNSUPPORTED,
    }[expected]
    if result.outcome is not wanted:
        return False
    return expected != "plan" or score.right_by_id.get(case["id"], False)


def _classify_invariance_break(
    case: dict[str, Any],
    before: PlanningResult,
    after: PlanningResult,
    score: Score,
    *,
    moved: list[str] | None = None,
    left: dict[str, Any] | None = None,
    right: dict[str, Any] | None = None,
) -> None:
    """Describe a break on accuracy, safety and field axes without conflating them."""
    base_id = str(case["paraphrase_of"])
    unsafe_ids = {case_id for case_id, _ in score.unsafe}
    before_correct = _case_was_correct({**case, "id": base_id}, before, score)
    after_correct = _case_was_correct(case, after, score)
    before_unsafe = base_id in unsafe_ids
    after_unsafe = str(case["id"]) in unsafe_ids

    if before_correct and after_unsafe:
        score.invariance_break_classes["correct -> unsafe"] += 1
    if before_unsafe and after_correct:
        score.invariance_break_classes["unsafe -> correct"] += 1
    if (
        case["expect"] == "plan"
        and before_correct
        and after.outcome in (PlanningOutcome.NEEDS_CLARIFICATION, PlanningOutcome.UNSUPPORTED)
    ):
        score.invariance_break_classes["correct plan -> refusal"] += 1
    if case["expect"] != "plan" and after.outcome is PlanningOutcome.PLANNED:
        score.invariance_break_classes["expected refusal -> plan"] += 1
    if not before_correct and after_correct:
        score.invariance_break_classes["incorrect -> correct"] += 1
    if before_correct and not after_correct and not after_unsafe:
        score.invariance_break_classes["correct -> safe incorrect"] += 1
    if not before_correct and not after_correct:
        score.invariance_break_classes["incorrect -> different incorrect"] += 1

    changed = moved or []
    if changed:
        score.invariance_field_changes[" + ".join(changed)] += 1
    if "groupBy" in changed and left is not None and right is not None:
        transition = f"{left['groupBy']} -> {right['groupBy']}"
        score.invariance_group_by_transitions[transition] += 1
        if "metric" in changed:
            score.invariance_group_by_transitions[f"{transition} + metric"] += 1


def _check_paraphrases(cases: list[dict[str, Any]], score: Score) -> None:
    """Pairs where one side is right and the other wrong, over both kinds of pair.

    **This reads `variant_of` as well, and `_score_invariance` deliberately does not.** The two
    keys are two relations and one name for both is what made `dev` unmeasurable: a strict
    paraphrase differs from its base in the sentence and in nothing else, while a variant is the
    same question shape said differently *with a deliberately different window* — «¿En qué comercio
    gasté más el mes pasado?» beside «¿Cuál es el comercio donde más gasto este mes?».

    An invariance check cannot use a variant, because the two plans are supposed to differ. This
    check can, because it compares nothing but whether each case got its own expectation right, and
    "one of these two was understood and the other was not" is exactly as informative when the
    windows differ. It is also where the value showed up: this report is what surfaced
    `merchant_top_ranking right, merchant_top_ranking_last_month wrong`.
    """
    for case in cases:
        twin = case.get("paraphrase_of") or case.get("variant_of")
        if twin is None:
            continue
        mine, theirs = score.right_by_id.get(case["id"]), score.right_by_id.get(twin)
        if mine is not None and theirs is not None and mine != theirs:
            winner, loser = (twin, case["id"]) if theirs else (case["id"], twin)
            score.paraphrase_splits.append(f"{winner} right, {loser} wrong")


def validate_paraphrase_cases(cases: list[dict[str, Any]]) -> list[str]:
    """A paraphrase must differ from its base in the sentence and in nothing else.

    The robustness corpus is generated, so the one thing that must not be generated is the
    opinion about what the right answer is. `drift.yaml` states the rule in its own header —
    "**Every `plan` here is copied from dev.yaml, never invented.**" — and this is that rule made
    checkable: an expectation that drifted from its base would turn a robustness failure into a
    dataset disagreement, and the report would call it a defect in the planner.

    It also refuses the sentence being the same as the base's. Two identical sentences are 1/1
    robust for free, and a corpus that padded itself that way would be the eighth green
    instrument in this repository that had stopped measuring anything.
    """
    by_id = {case["id"]: case for case in cases}
    issues: list[str] = []
    for case in cases:
        base_id = case.get("paraphrase_of")
        if base_id is None:
            continue
        base = by_id.get(base_id)
        if base is None:
            issues.append(f"{case['id']}: unknown paraphrase_of {base_id}")
            continue
        if case.get("changes") is not None:
            issues.append(f"{case['id']}: a paraphrase declares no changed fields")
        for owned in ("expect", "plan", "previous", "preceding_question"):
            if case.get(owned) != base.get(owned):
                issues.append(f"{case['id']}: {owned} differs from {base_id}, which it may not")
        if str(case["question"]).strip() == str(base["question"]).strip():
            issues.append(f"{case['id']}: the same sentence as {base_id}")

    # A variant carries none of the obligations above -- its plan is *meant* to differ -- so the
    # only thing to check is that it points at a case that exists. A dangling relation is a pair
    # that silently measures nothing, which is the failure this whole validator is here to refuse.
    for case in cases:
        variant_of = case.get("variant_of")
        if variant_of is not None:
            if variant_of not in by_id:
                issues.append(f"{case['id']}: unknown variant_of {variant_of}")
            elif case.get("paraphrase_of") is not None:
                issues.append(f"{case['id']}: both paraphrase_of and variant_of")
    return issues


def validate_robustness_metadata(data: dict[str, Any]) -> list[str]:
    """Keep the committed corpus header honest about the document it describes."""
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return ["robustness: missing metadata (regenerate with generate_paraphrases.py)"]
    cases = data.get("cases", [])
    actual = {
        "base_count": sum("paraphrase_of" not in case for case in cases),
        "paraphrase_count": sum("paraphrase_of" in case for case in cases),
        "rejected_count": len(data.get("rejected", [])),
        "not_admitted_count": len(data.get("bases_not_admitted", [])),
    }
    return [
        f"robustness metadata {name}={metadata.get(name)!r}, content={value}"
        for name, value in actual.items()
        if metadata.get(name) != value
    ]


def validate_metamorphic_cases(cases: list[dict[str, Any]], *, today: date) -> list[str]:
    """Validate that the dataset itself changes exactly the dimensions it declares."""
    by_id = {case["id"]: case for case in cases}
    issues: list[str] = []
    for case in cases:
        base_id = case.get("metamorph_of")
        if base_id is None:
            continue
        base = by_id.get(base_id)
        if base is None:
            issues.append(f"{case['id']}: unknown metamorph_of {base_id}")
            continue
        declared = set(case.get("changes", ()))
        unknown = declared.difference(FIELDS)
        if unknown:
            issues.append(f"{case['id']}: unknown changed fields {sorted(unknown)}")
            continue
        if not declared:
            issues.append(f"{case['id']}: changes must name at least one field")
            continue
        if base.get("expect") != "plan" or case.get("expect") != "plan":
            issues.append(f"{case['id']}: metamorphic pairs must both expect a plan")
            continue
        before = plan_fields(as_plan(base["plan"]), today)
        after = plan_fields(as_plan(case["plan"]), today)
        observed = {name for name in FIELDS if before[name] != after[name]}
        if observed != declared:
            issues.append(
                f"{case['id']}: declares {sorted(declared)}, expected plans change "
                f"{sorted(observed)}"
            )
    return issues


def _check_metamorphs(cases: list[dict[str, Any]], score: Score, *, today: date) -> None:
    """Measure the delta produced by the model, independently of exact-plan accuracy."""
    for case in cases:
        base_id = case.get("metamorph_of")
        if base_id is None:
            continue
        before = score.results_by_id.get(base_id)
        after = score.results_by_id.get(case["id"])
        # A limited run may contain only one side of a pair. It did not measure that pair.
        if before is None or after is None:
            continue
        score.metamorphic_total += 1
        if not isinstance(before.plan, FinancialPlan) or not isinstance(after.plan, FinancialPlan):
            score.metamorphic_violations.append(f"{case['id']}: pair was not planned on both sides")
            continue
        expected = set(case.get("changes", ()))
        left = plan_fields(before.plan, today)
        right = plan_fields(after.plan, today)
        observed = {name for name in FIELDS if left[name] != right[name]}
        if observed == expected:
            score.metamorphic_right += 1
            continue
        missing = sorted(expected - observed)
        leaked = sorted(observed - expected)
        detail = []
        if missing:
            detail.append(f"did not change {missing}")
        if leaked:
            detail.append(f"also changed {leaked}")
        score.metamorphic_violations.append(f"{case['id']}: {', '.join(detail)}")


async def repeated(
    model: str,
    limit: int | None,
    provider: EvaluationProvider,
    dataset: str,
    times: int,
    pace_seconds: float,
    gate: RobustnessGate | None = None,
) -> int:
    """Runs the set `times` over and report the spread as well as the numbers.

    A single run is a point estimate of something noisy. Reporting min-max next to the
    mean is what stops the next person reading a two-point difference as a result.
    """
    scores: list[tuple[int, int]] = []
    for attempt in range(times):
        code = await run(
            model,
            limit,
            provider,
            dataset,
            pace_seconds,
            collect=scores,
            gate=gate,
        )
        if code:
            return code
        if times > 1:
            outcome, exact = scores[-1]
            print(f"  run {attempt + 1}: outcome {outcome}, exact {exact}")

    if times > 1:
        outcomes = [o for o, _ in scores]
        exacts = [e for _, e in scores]
        print(
            f"\n  over {times} runs   outcome {min(outcomes)}-{max(outcomes)}"
            f"   exact {min(exacts)}-{max(exacts)}"
        )
    return 0


async def ablate(
    model: str,
    provider: EvaluationProvider,
    dataset: str,
    pace_seconds: float,
) -> int:
    """What each rule is worth, in unsafe executions prevented and good answers lost.

    One inference pass over the dataset records what the model says with no gate in front
    of it. Every policy is then replayed over those recordings, which is exact rather than
    approximate: the gates are pure functions of the sentence and the plan, so replaying
    them cannot differ from having run them.

    Two columns, because a rule has two effects and only reporting the flattering one is
    how a guard grows until it refuses everything. `prevented` is unsafe executions it
    stops. `cost` is answerable questions it turns into refusals — the expensive kind of
    wrong, since the pipeline had those right.
    """
    data = yaml.safe_load((PLANNING / f"{dataset}.yaml").read_text(encoding="utf-8"))
    today = data["reference"]
    cases = data["cases"]

    planner = LlmQueryPlanner(
        provider,
        model=model,
        temperature=0.0,
        max_output_tokens=ANSWER_TOKENS,
        categories=CATEGORIES,
    )

    print(f"recording {len(cases)} answers with every gate off...", file=sys.stderr)
    recorded: list[tuple[dict[str, Any], PlanningResult]] = []
    for index, case in enumerate(cases):
        if index and pace_seconds:
            await asyncio.sleep(pace_seconds)
        context = case.get("previous")
        previous = as_plan(context) if context else None
        raw = await planner.plan(
            case["question"],
            context=planning_context(
                Conversation(last_plan=previous),
                today=today,
                reporting_timezone="UTC",
                analysis=TurnAnalysis(
                    Intent.FINANCIAL_QUERY,
                    TurnRelation.FOLLOW_UP if previous is not None else TurnRelation.NEW_REQUEST,
                    TurnFocus.LAST_ANSWER if previous is not None else TurnFocus.NEW_REQUEST,
                ),
                continuation=(
                    ContinuationBase(previous, ContinuationSource.LAST_ANSWER)
                    if previous is not None
                    else None
                ),
            ),
        )
        if raw.outcome is PlanningOutcome.PROVIDER_ERROR:
            print(f"provider failed on {case['id']}: {raw.reason}", file=sys.stderr)
            return 2
        recorded.append((case, raw))

    def measure(policy: GuardPolicy) -> Score:
        score = Score()
        for case, raw in recorded:
            judge(case, _gated(case, raw, policy, today=today), score, today)
        return score

    every = capability_guard.RULES | plan_verifier.RULES
    full = measure(GuardPolicy())
    none = measure(GuardPolicy(disabled=frozenset(every)))

    print(f"\n=== ablation · {provider.name}/{model} · {dataset} · {len(cases)} cases ===\n")
    print(f"{'policy':38}{'outcome':>9}{'unsafe':>8}{'prevented':>11}{'cost':>7}")
    print(f"{'every rule on':38}{full.outcome_right:>8}{len(full.unsafe):>8}{'-':>11}{'-':>7}")
    print(f"{'every rule off':38}{none.outcome_right:>8}{len(none.unsafe):>8}{'-':>11}{'-':>7}")
    print()

    for rule in sorted(every):
        # One rule removed and the rest left on. That measures the rule's *marginal*
        # contribution, which is the honest question: a rule whose cases another rule also
        # catches is worth less than measuring it alone would suggest.
        without = measure(GuardPolicy(disabled=frozenset({rule})))
        prevented = len(without.unsafe) - len(full.unsafe)
        cost = len(full.missed) - len(without.missed)
        print(
            f"{'without ' + rule:38}{without.outcome_right:>8}{len(without.unsafe):>8}"
            f"{prevented:>+11}{cost:>+7}"
        )

    print(
        "\n  prevented: unsafe executions this rule stops."
        "\n  cost: answerable questions it turns into refusals."
        "\n  A rule with prevented 0 and cost 0 is dead weight measured on this dataset,"
        "\n  which is not the same as safe to remove — the dataset is not the world."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="a model name the provider serves")
    parser.add_argument(
        "--provider",
        default="ollama",
        choices=("ollama", "groq", "cloudflare"),
        help="ollama runs locally; groq and cloudflare require credentials in .env and send "
        "every evaluated sentence over the internet",
    )
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument(
        "--base-url",
        default=None,
        help="override the selected provider's configured endpoint; for cloudflare, the API root",
    )
    parser.add_argument(
        "--think",
        action="store_true",
        help="let the model reason before answering, on models that support it. Off by "
        "default: the planner emits ~120 tokens of JSON that nobody reads a rationale "
        "for. This flag exists so that claim can be checked rather than repeated.",
    )
    parser.add_argument(
        "--dataset",
        default="dev",
        choices=("dev", "validation", "adversarial", "drift", "metamorphic", "robustness"),
        help="dev is what the prompt was tuned against; validation is what eleven models were "
        "compared against, so it selected a model and is no longer held out. Metamorphic checks "
        "whether a local wording change produces only the declared plan delta. Neither is a "
        "release number — see evaluation/planning/RESULTS.md. drift is the only one nobody "
        "here wrote: ten questions from dev, paraphrased by a model that had never seen the "
        "prompt, and it scores robustness rather than accuracy. robustness is that idea at the "
        "scale it needed: every case dev and adversarial have that drift never reached, "
        "rewritten by a model that is not the planner and filtered by a rule fixed before any "
        "of it ran — see the header of robustness.yaml.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="override the selected provider's configured request timeout",
    )
    parser.add_argument(
        "--tpm",
        type=int,
        default=0,
        help="the provider's tokens-a-minute limit; the pace comes from it. 0 is unpaced. "
        "Use this with hosted free tiers so rate limiting cannot invalidate the run",
    )
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="measure what each deterministic rule is worth. Records the model's answers "
        "once with every gate off, then replays each policy over them — exact rather "
        "than approximate, because the gates are pure functions of their inputs.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the whole dataset N times and report the spread. Local inference is "
        "not bit-reproducible even at temperature 0, so a single run of a 60-case set "
        "carries a few points of noise — enough to invent an improvement.",
    )
    parser.add_argument(
        "--robustness-floor",
        type=float,
        default=None,
        help="minimum macro robustness percentage; requires --over-refusal-ceiling and the "
        "complete robustness dataset",
    )
    parser.add_argument(
        "--over-refusal-ceiling",
        type=float,
        default=None,
        help="maximum percentage of answerable cases refused; requires --robustness-floor",
    )
    args = parser.parse_args()
    if args.tpm < 0:
        parser.error("--tpm cannot be negative")
    if args.think and args.provider != "ollama":
        parser.error("--think is only supported by the Ollama adapter")
    thresholds = (args.robustness_floor, args.over_refusal_ceiling)
    if (thresholds[0] is None) != (thresholds[1] is None):
        parser.error("the robustness floor and over-refusal ceiling must be supplied together")
    if any(value is not None and not 0 <= value <= 100 for value in thresholds):
        parser.error("robustness gate percentages must be between 0 and 100")
    if thresholds[0] is not None and args.dataset != "robustness":
        parser.error("robustness gate thresholds require --dataset robustness")
    return asyncio.run(_evaluate(args))


def _pace(args: argparse.Namespace) -> float:
    """Seconds between calls, derived from the limit the provider publishes.

    **This was `--rpm`, and requests a minute is not what anybody limits.** Groq answers
    `x-ratelimit-limit-tokens: 8000` and `x-ratelimit-limit-requests: 1000` — a minute budget in
    tokens and a *daily* one in requests — so a rate remembered in calls is right only for the
    prompt it was measured against. This prompt is 2,300 tokens of template before the sentence, so
    `--rpm 4` asks for eleven thousand tokens a minute against a wall of eight and gets a 429 on the
    first case. `judge_plans.py` learned this and took the flag in tokens; this file kept the old
    one, which is the same rule written for one member of a class and missing under its sister.

    Four characters to the token, as there, and pessimistically: the cost is the longest prompt in
    the dataset with a previous plan attached, whether or not the case has one. Being twenty per
    cent slow costs seconds; being wrong the other way costs the run.
    """
    data = yaml.safe_load((PLANNING / f"{args.dataset}.yaml").read_text(encoding="utf-8"))
    cases = data["cases"][: args.limit] if args.limit else data["cases"]
    longest = max(
        len(
            prompt.build_prompt(
                case["question"],
                today=data["reference"],
                categories=CATEGORIES,
                previous=FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(period=Period.LAST_MONTH),
                ),
            )
        )
        for case in cases
    )
    cost = ANSWER_TOKENS + longest // 4
    print(f"# pacing {args.tpm / cost:.1f} cases a minute · {cost} tokens each · tpm {args.tpm}")
    return 60.0 * cost / args.tpm


async def _evaluate(args: argparse.Namespace) -> int:
    try:
        provider = _provider(args.provider, args.base_url, args.think, args.timeout)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.provider != "ollama":
        data = yaml.safe_load((PLANNING / f"{args.dataset}.yaml").read_text(encoding="utf-8"))
        cases = data["cases"][: args.limit] if args.limit else data["cases"]
        print(
            f"# {len(cases)} financial questions going to {args.provider}; they leave this machine."
        )
    if not await provider.ping():
        print(f"{args.provider} did not answer", file=sys.stderr)
        return 2

    pace_seconds = _pace(args) if args.tpm else 0.0
    if args.ablate:
        return await ablate(args.model, provider, args.dataset, pace_seconds)
    gate = None
    if args.robustness_floor is not None:
        gate = RobustnessGate(args.robustness_floor, args.over_refusal_ceiling)
    return await repeated(
        args.model,
        args.limit,
        provider,
        args.dataset,
        args.repeat,
        pace_seconds,
        gate,
    )


if __name__ == "__main__":
    raise SystemExit(main())
