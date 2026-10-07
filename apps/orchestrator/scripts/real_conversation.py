"""Real conversations, end to end, against the stack as the demo runs it.

    uv run python scripts/real_conversation.py preflight
    uv run python scripts/real_conversation.py replay --file evaluation/real/conversations.yaml \
        [--ux evaluation/ux/conversations.yaml] --out evaluation/real/<YYYY-MM-DD>-<sha>
    uv run python scripts/real_conversation.py say --state <file.json> [--goal "..."] "<frase>"
    uv run python scripts/real_conversation.py observe --state <file.json> --said "<frase>" \
        --response <answer.json>
    uv run python scripts/real_conversation.py summarise <run.json> [<state.json> ...] \
        [--previous <earlier-run.json>]

The battery (`evaluate_conversation_ux.py`) replays sixty turns against a simulated bank on a fixed
date, and a person trying FinGuard in ordinary conversation still found it brittle. This is the
other half: the deployed orchestrator over HTTP, with its configured model, the real development
token and the ledger as loaded, and the person's own conversations sent turn by turn as they were
typed — typos, chips and all (spec §4.1 of
docs/superpowers/specs/2026-10-07-conversation-state-slice-1-design.md).

Two kinds of verdict, kept apart on purpose:

* **Figures are mechanical.** An answer that is a single sum or count of the plan it ran is re-done
  here in SQL, through the read-only role and the holder-scoped views, and a figure that differs is
  a failure whatever else is right. So is `error`: a figure that should have been checked and
  could not be. Everything else is `n/a` here and judged by the rubric.
* **The conversation is judged** by the skill that drives this script
  (`.claude/skills/finguard-real-conversation`), against a rubric. This script leaves those columns
  empty, and `summarise` only counts them once the judge has filled them in.

It never writes to the database, and it never sends a yes to an open payment, transfer or booking:
the ledger is the demo's, and an improvised «sí» would move money in it.
"""

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx2 as httpx
import yaml

from finguard_orchestrator.domain.errors import DomainError
from finguard_orchestrator.domain.services import answer_writer, confirmation
from finguard_orchestrator.infrastructure.dto.llm_dto import PlanPayload
from finguard_orchestrator.infrastructure.mappers.conversation_mapper import query_to_domain

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
BANKING_SOURCE = ROOT.parent / "banking-api" / "src"

ORCHESTRATOR = "http://127.0.0.1:8000"
BANKING = "http://127.0.0.1:8001"
GATEWAY = "http://127.0.0.1:8003"
OLLAMA = "http://127.0.0.1:11434"
# The read-only role, never the owner: it cannot write, cannot read a base table, and every session
# of it is read-only by default (infrastructure/postgres/init/02-query-reader.sql).
DATABASE = (
    "postgresql://finguard_query_reader:finguard_query_reader@127.0.0.1:5442/finguard_banking"
)
# The development token with every scope, and the account it names. The SQL declares that account,
# so the two travel together: a different token needs its own `--account`.
TOKEN = "demo-token-laura-pays"
ACCOUNT = "08aba04f-2b30-4639-88a6-eac741153ce0"
ZONE = "Europe/Madrid"

# The mechanism a turn exercises, so a failure names what broke (spec §4.3). One vocabulary with the
# battery's `mechanism:` tags.
MECHANISMS = frozenset(
    {
        "intent",
        "slot_carryover",
        "pending_resolution",
        "interruption",
        "follow_up",
        "comparison",
        "reference",
        "reset",
        "unnecessary_clarification",
        "execution",
    }
)
# The rubric's columns, filled by the judge in each record's `judgement`.
VERDICT_COLUMNS = (
    "intent",
    "context",
    "pending",
    "clarification",
    "recovery",
    "naturalness",
    "mechanism",
    "notes",
)

# Open states where a yes executes something. A payment or a booking waits for it, and a transfer
# preview reads one as an order.
_PREVIEWS = frozenset({"PAYMENT_CONFIRMATION", "BOOKING_CONFIRMATION", "TRANSFER_IN_PROGRESS"})

# Both settings, as banking declares them (`financial_query_repository.declare`): the account alone
# opens only that account, and a plan with no account named is about every account of the holder.
# `holder_of` is the one question the read-only role may ask of `accounts`.
_DECLARE = (
    "SELECT set_config('app.account_id', $1::text, true)",
    "SELECT set_config('app.customer_id', "
    "COALESCE(assistant_read.holder_of($1::uuid)::text, ''), true)",
)

# Who a movement is with, and what kind of party that is. The rules banking applies, written out
# again here rather than imported: the point of this check is SQL that is not the engine's.
_COUNTERPARTY = "COALESCE(NULLIF(btrim(t.merchant_name), ''), NULLIF(btrim(t.description), ''))"
_COUNTERPARTY_TYPE = """CASE t.transaction_type
            WHEN 'CARD_PAYMENT' THEN 'MERCHANT'
            WHEN 'BIZUM' THEN 'PERSON'
            WHEN 'SALARY' THEN 'EMPLOYER'
            WHEN 'DIRECT_DEBIT' THEN 'BILLER'
            WHEN 'TRANSFER' THEN 'ACCOUNT'
            WHEN 'INTERNAL_TRANSFER' THEN 'ACCOUNT'
            WHEN 'ACCOUNT_OPENING' THEN 'BANK'
            ELSE 'UNKNOWN'
        END"""


# --- figures --------------------------------------------------------------------------------------


def formatted_euros(minor: int) -> str:
    """Minor units as the answers write euros: «1.895,97 €». Written here and not imported, so a
    formatter that drifted would show up as a figure missing from the sentence."""
    sign = "-" if minor < 0 else ""
    units, cents = divmod(abs(minor), 100)
    return f"{sign}{units:,}".replace(",", ".") + f",{cents:02d} €"


def _banking_resolve(period: str, n: int | None, today: date) -> tuple[date, date]:
    """A named period as days, by banking's own `resolve`.

    The orchestrator never does calendar arithmetic — it sends the name and the reference day, and
    banking resolves both into dates (`http_mapper.plan_from_dto`). A second calendar here would be
    a second answer to "what is LAST_MONTH", and a disagreement between the two would read as a
    wrong figure. The module is plain standard library, so it loads from banking's source.
    """
    if str(BANKING_SOURCE) not in sys.path:
        sys.path.append(str(BANKING_SOURCE))
    from finguard_banking.domain.enums import Period
    from finguard_banking.domain.value_objects.relative_period import RelativePeriod, resolve

    window = resolve(RelativePeriod(period=Period(period), n=n), today)
    return window.start, window.end


def window(plan: dict[str, Any], today: date) -> tuple[str, str]:
    """The plan's days, both ends included, as ISO dates."""
    if plan.get("start") and plan.get("end"):
        return str(plan["start"]), str(plan["end"])
    if not plan.get("period"):
        raise ValueError("a plan with neither a period nor a range")
    start, end = _banking_resolve(str(plan["period"]), plan.get("n"), today)
    return start.isoformat(), end.isoformat()


def _like(term: str) -> str:
    """A fragment as a literal ILIKE pattern: a '%' a person typed is a percent sign."""
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _folded(values: list[str]) -> list[str]:
    return [value.strip().lower() for value in values]


def expected_sql(
    plan: dict[str, Any], account_id: str, timezone: str, today: date | None = None
) -> tuple[str, dict[str, Any]]:
    """The plan's figure, asked of the ledger independently of the engine.

    Over `assistant_read.transactions_v1` — the holder-scoped view the read-only role may see —
    with the rules banking applies phrased differently where they can be: a movement's local day
    against the window rather than two instants, the sign of the amount rather than the view's
    direction label, the transaction type rather than the view's `is_internal`. The same rule in
    different SQL, which is what makes agreement mean something.

    Named parameters, `%(name)s`, so the query reads as what it asks; `_positional` numbers them
    for the driver. The transaction that runs it first declares `%(account)s` (see `_DECLARE`).
    """
    today = today or datetime.now(ZoneInfo(timezone)).date()
    start, end = window(plan, today)
    params: dict[str, Any] = {
        "account": account_id,
        "timezone": timezone,
        "start": start,
        "end": end,
    }
    direction = plan.get("direction", "ALL")
    # One direction is a positive amount ("I spent 34,20"); both directions are a signed net.
    amount = "-t.signed_amount_minor" if direction == "EXPENSE" else "t.signed_amount_minor"
    where = [
        "(t.occurred_at AT TIME ZONE %(timezone)s::text)::date "
        "BETWEEN %(start)s::text::date AND %(end)s::text::date"
    ]
    if direction == "EXPENSE":
        where.append("t.signed_amount_minor < 0")
    elif direction == "INCOME":
        where.append("t.signed_amount_minor > 0")

    # Money moved between the holder's own accounts is not spending (ADR-009 §2), unless asked for.
    if plan.get("internal_only"):
        where.append("t.transaction_type = 'INTERNAL_TRANSFER'")
    elif not plan.get("include_internal"):
        where.append("t.transaction_type <> 'INTERNAL_TRANSFER'")

    # The plan is remembered as it was asked, and `across_accounts` can be false on figures read
    # from every account (the scope a conversation settled is execution policy, not part of the
    # question). Only a named account narrows; otherwise the view's holder scope is the scope.
    only = list(plan.get("only_account_ids") or [])
    if plan.get("only_account_id"):
        only = [str(plan["only_account_id"])]
    if only:
        params["only_accounts"] = [str(account) for account in only]
        where.append("t.account_id::text = ANY(%(only_accounts)s::text[])")

    if plan.get("transaction_ids"):
        params["transactions"] = [str(item) for item in plan["transaction_ids"]]
        where.append("t.transaction_id::text = ANY(%(transactions)s::text[])")
    if plan.get("transaction_types"):
        params["transaction_types"] = list(plan["transaction_types"])
        where.append("t.transaction_type = ANY(%(transaction_types)s::text[])")
    if plan.get("categories"):
        params["categories"] = list(plan["categories"])
        where.append("t.category = ANY(%(categories)s::text[])")
    if plan.get("excluded_categories"):
        params["excluded_categories"] = list(plan["excluded_categories"])
        # Uncategorised is not one of the excluded categories.
        where.append(
            "(t.category IS NULL OR NOT t.category = ANY(%(excluded_categories)s::text[]))"
        )
    if plan.get("merchants"):
        params["merchants"] = _folded(plan["merchants"])
        where.append("lower(t.merchant_name) = ANY(%(merchants)s::text[])")
    if plan.get("merchant_terms"):
        params["merchant_patterns"] = [_like(term) for term in plan["merchant_terms"]]
        where.append("t.merchant_name ILIKE ANY(%(merchant_patterns)s::text[])")
    if plan.get("excluded_merchants"):
        params["excluded_merchants"] = _folded(plan["excluded_merchants"])
        where.append(
            "(t.merchant_name IS NULL "
            "OR NOT lower(t.merchant_name) = ANY(%(excluded_merchants)s::text[]))"
        )
    if plan.get("counterparties"):
        params["counterparties"] = _folded(plan["counterparties"])
        where.append(f"lower({_COUNTERPARTY}) = ANY(%(counterparties)s::text[])")
    if plan.get("excluded_counterparties"):
        params["excluded_counterparties"] = _folded(plan["excluded_counterparties"])
        where.append(
            f"({_COUNTERPARTY} IS NULL "
            f"OR NOT lower({_COUNTERPARTY}) = ANY(%(excluded_counterparties)s::text[]))"
        )
    if plan.get("counterparty_types"):
        params["counterparty_types"] = list(plan["counterparty_types"])
        where.append(f"({_COUNTERPARTY_TYPE}) = ANY(%(counterparty_types)s::text[])")

    sql = (
        f"SELECT COALESCE(SUM({amount}), 0)::bigint AS total_minor,\n"
        "       COUNT(*)::bigint AS movement_count\n"
        "FROM assistant_read.transactions_v1 t\n"
        "WHERE " + "\n  AND ".join(where)
    )
    return sql, params


def _positional(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """`%(name)s` as `$n`, the only placeholder asyncpg reads."""
    order: list[str] = []

    def number(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in order:
            order.append(name)
        return f"${order.index(name) + 1}"

    return re.sub(r"%\((\w+)\)s", number, sql), [params[name] for name in order]


async def _read(database: str, account: str, sql: str, params: dict[str, Any]) -> Any:
    """One row, in its own transaction, after declaring whose rows exist.

    A connection per read: a turn waits on a model for seconds at a time, and this role is cut off
    after ten idle seconds inside a transaction. `set_config(..., true)` ends with the transaction.
    """
    statement, values = _positional(sql, params)
    connection = await asyncpg.connect(database)
    try:
        async with connection.transaction():
            for declaration in _DECLARE:
                await connection.execute(declaration, account)
            return await connection.fetchrow(statement, *values)
    finally:
        await connection.close()


async def ledger(
    plan: dict[str, Any], account: str, timezone: str, database: str
) -> dict[str, int]:
    sql, params = expected_sql(plan, account_id=account, timezone=timezone)
    row = await _read(database, account, sql, params)
    return {"total_minor": int(row["total_minor"]), "movement_count": int(row["movement_count"])}


def figure_check(answer: dict[str, Any], sql: dict[str, Any] | None) -> str:
    """`match`, `mismatch` or `n/a`, from the figures the answer carries and the ledger's.

    Only the figures the answer has are compared: a count answers with a count. When the answer
    brings its sentence (`text`), the ledger's total has to be in it as well — the person reads the
    sentence, and a field and a sentence disagreeing has happened here before.
    """
    if sql is None:
        return "n/a"
    compared = [key for key in ("total_minor", "movement_count") if answer.get(key) is not None]
    if not compared:
        return "n/a"
    if any(answer[key] != sql[key] for key in compared):
        return "mismatch"
    text, total = answer.get("text"), sql.get("total_minor")
    if text is not None and total and formatted_euros(abs(total)) not in text:
        return "mismatch"
    return "match"


def plan_line(plan: dict[str, Any]) -> str | None:
    """The plan in the words the answer shows under «Ver la consulta», by the service's own writer.

    Used only to recognise which plan an answer ran, never to compute a figure.
    """
    try:
        return answer_writer.plan_in_words(query_to_domain(PlanPayload.model_validate(plan)))
    except (ValueError, DomainError):
        return None


def figure_plan(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The plan whose figure this answer is, or `None` and why it is not checked.

    `last_plan` is the memory the turn leaves, and it is not always the plan the figure came from:
    a balance carries the previous plan's line beside a balance, a top-N or a payroll check names
    another plan, a comparison leads with a difference. The answer's own line has to be the line of
    the remembered plan, or the two are not the same question.

    One case the line cannot tell apart: «ponme un ejemplo» answers with a row of the last answer,
    under that answer's own line and with that row's amount as its figure. Its sentences are the
    example writer's (`answer_writer.movement_examples`), and that is how it is recognised.
    """
    outcome = body.get("outcome")
    if outcome != "PLANNED":
        return None, f"{outcome}: no es la cifra de una consulta"
    if body.get("parts"):
        return None, "varias preguntas en un turno"
    if body.get("difference_minor") is not None:
        return None, "una comparación: la juzga la rúbrica"
    text = body.get("text") or ""
    if text.startswith("Por ejemplo") or "mostrarte como ejemplo" in text:
        return None, "un ejemplo de la respuesta anterior, no su cifra"
    plan = (body.get("conversation") or {}).get("last_plan")
    if not plan:
        return None, "no queda ninguna consulta recordada"
    kind = (
        plan.get("action", "AGGREGATE"),
        plan.get("metric", "SUM"),
        plan.get("group_by", "NONE"),
    )
    if kind[0] != "AGGREGATE" or kind[1] not in ("SUM", "COUNT") or kind[2] != "NONE":
        return None, f"{'/'.join(kind)}: solo se comprueba una suma o un recuento sin agrupar"
    if body.get("plan") != plan_line(plan):
        return None, "la respuesta es de otra consulta que la recordada"
    if body.get("total_minor") is None and body.get("movement_count") is None:
        return None, "sin cifra"
    return plan, "comprobada"


def answer_figures(body: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """What the answer claims, in the shape `figure_check` compares."""
    if plan.get("metric", "SUM") == "COUNT":
        return {"total_minor": None, "movement_count": body.get("movement_count")}
    figures: dict[str, Any] = {
        "total_minor": body.get("total_minor"),
        "movement_count": body.get("movement_count"),
    }
    # A net over both directions has a sign the sentence may say in words; one direction is a
    # plain amount the sentence must contain.
    if plan.get("direction") in ("EXPENSE", "INCOME"):
        figures["text"] = body.get("text") or ""
    return figures


def refused(sentence: str, conversation: dict[str, Any] | None) -> bool:
    """Whether this is a yes to something that would execute. By the service's own reader, so
    whatever it would take as a confirmation is exactly what is not sent."""
    workflow = (conversation or {}).get("workflow")
    return workflow in _PREVIEWS and confirmation.is_confirmation(sentence)


# --- turns ----------------------------------------------------------------------------------------


class Options(argparse.Namespace):
    base_url: str
    banking_url: str
    gateway_url: str
    ollama_url: str
    token: str
    account: str
    timezone: str
    database: str
    max_age_days: int


async def _ask(
    client: httpx.AsyncClient,
    options: Options,
    sentence: str,
    conversation: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, float, str | None]:
    """One turn, as `evaluate_e2e._ask` sends it: the sentence and the conversation echoed back."""
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{options.base_url}/v1/questions",
            json={"question": sentence, "conversation": conversation},
            headers={"Authorization": f"Bearer {options.token}"},
        )
    except httpx.HTTPError as error:
        return None, round(time.perf_counter() - started, 2), f"{type(error).__name__}: {error}"
    seconds = round(time.perf_counter() - started, 2)
    if response.status_code != 200:
        return None, seconds, f"HTTP {response.status_code}: {response.text[:300]}"
    return dict(response.json()), seconds, None


async def record_of(
    options: Options,
    *,
    name: str,
    turn: int,
    said: str,
    body: dict[str, Any] | None,
    seconds: float | None,
    error: str | None = None,
    expect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The turn as the report keeps it, with its figure checked against the ledger.

    A check that should have run and could not is `error`, never `n/a`: `n/a` passes, and a broken
    instrument would otherwise read as a clean run. Any exception counts — the driver's, banking's
    calendar refusing a window, a connection dropped — because which one it was matters less than
    that the figure went unchecked, and the class and message say which.
    """
    body = body or {}
    state = body.get("conversation") or {}
    sql: dict[str, int] | None = None
    plan, note = figure_plan(body) if not error else (None, "sin respuesta")
    failed = False
    if plan is not None:
        try:
            sql = await ledger(plan, options.account, options.timezone, options.database)
        except Exception as problem:  # every failure is recorded, none ends the run
            failed = True
            note = f"la comprobación en SQL ha fallado: {type(problem).__name__}: {problem}"
    answer = answer_figures(body, plan) if plan is not None else {}
    return {
        "conversation": name,
        "turn": turn,
        "said": said,
        "outcome": body.get("outcome"),
        "text": body.get("text"),
        "plan_in_words": body.get("plan"),
        "last_plan": state.get("last_plan"),
        "total_minor": body.get("total_minor"),
        "movement_count": body.get("movement_count"),
        "difference_minor": body.get("difference_minor"),
        "groups": body.get("groups") or [],
        "suggestions": body.get("suggestions") or [],
        "state": {key: state.get(key) for key in ("workflow", "awaiting", "last_asked")},
        "seconds": seconds,
        "sql": sql,
        "figure_check": "error" if failed else figure_check(answer, sql),
        "figure_note": note,
        "expect": expect,
        "error": error,
    }


async def _turn(
    client: httpx.AsyncClient,
    options: Options,
    *,
    name: str,
    turn: int,
    said: str,
    conversation: dict[str, Any] | None,
    expect: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Send one turn and keep it. Returns the record and the conversation to echo next."""
    if refused(said, conversation):
        return {
            "conversation": name,
            "turn": turn,
            "said": said,
            "skipped": "un «sí» a una operación pendiente no se envía: movería dinero en la demo",
            "expect": expect,
        }, conversation
    body, seconds, error = await _ask(client, options, said, conversation)
    record = await record_of(
        options,
        name=name,
        turn=turn,
        said=said,
        body=body,
        seconds=seconds,
        error=error,
        expect=expect,
    )
    # Echoed as a client echoes it: what came back, or what was sent when nothing came back.
    following = body.get("conversation") if body is not None else conversation
    return record, following


def _line(record: dict[str, Any]) -> str:
    """One turn in a line, for a person watching a run."""
    if record.get("skipped"):
        return f"[{record['conversation']} {record['turn']}] SKIPPED «{record['said']}»"
    seconds = record.get("seconds")
    took = f"{seconds:.1f}s" if seconds is not None else "-"
    return (
        f"[{record['conversation']} {record['turn']}] "
        f"{record.get('outcome') or record.get('error')}"
        f" {took} {record.get('figure_check')} — «{record['said']}»"
    )


# --- preflight ------------------------------------------------------------------------------------


def _command(*arguments: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            list(arguments),
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


async def _services(options: Options) -> dict[str, Any]:
    found: dict[str, Any] = {"services": {}, "version": None, "ollama": None, "newest": None}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, base in (
            ("orchestrator", options.base_url),
            ("banking", options.banking_url),
            ("gateway", options.gateway_url),
        ):
            try:
                response = await client.get(f"{base}/health")
                healthy = response.status_code == 200 and response.json().get("status") == "ok"
                found["services"][name] = "ok" if healthy else f"HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError) as error:
                found["services"][name] = f"unreachable: {type(error).__name__}"
        try:
            found["version"] = (await client.get(f"{options.base_url}/version")).json()
        except (httpx.HTTPError, ValueError) as error:
            found["version"] = {"error": type(error).__name__}
        try:
            models = (await client.get(f"{options.ollama_url}/api/ps")).json().get("models", [])
            found["ollama"] = [
                {key: model.get(key) for key in ("name", "size_vram", "context_length")}
                for model in models
            ]
        except (httpx.HTTPError, ValueError) as error:
            found["ollama"] = {"error": type(error).__name__}
    try:
        row = await _read(
            options.database,
            options.account,
            "SELECT (max(t.occurred_at) AT TIME ZONE %(timezone)s::text)::date AS newest "
            "FROM assistant_read.transactions_v1 t",
            {"timezone": options.timezone},
        )
        found["newest"] = row["newest"].isoformat() if row and row["newest"] else None
    except Exception as error:  # whatever stops the read, the ledger is not reachable: exit 1
        found["newest"] = {"error": f"{type(error).__name__}: {error}"}
    return found


def preflight(options: Options) -> tuple[dict[str, Any], int]:
    """Whether the stack is the demo's, and what it is running on. 0 to go on, 1 if a service or
    the ledger cannot be reached, 2 if the data is not loaded up to yesterday.

    It never loads data: loading truncates the demo's bank, and that is the person's step.
    """
    found = asyncio.run(_services(options))
    today = datetime.now(ZoneInfo(options.timezone)).date()
    found["at"] = datetime.now(ZoneInfo(options.timezone)).isoformat(timespec="seconds")
    found["today"] = today.isoformat()

    gpu = _command("nvidia-smi", "--query-gpu=pstate,clocks.sm", "--format=csv,noheader")
    found["gpu"] = gpu.stdout.strip() if gpu and gpu.returncode == 0 else "unavailable"
    head = _command("git", "rev-parse", "--short=8", "HEAD")
    found["head"] = head.stdout.strip() if head and head.returncode == 0 else None
    # Whether the running service is the code under test: the same product code as HEAD, even if
    # the commits differ by documents and scripts.
    sha = (found.get("version") or {}).get("sha")
    same = (
        _command(
            "git",
            "diff",
            "--quiet",
            sha,
            "HEAD",
            "--",
            "apps/orchestrator/src",
            "apps/banking-api/src",
        )
        if sha
        else None
    )
    found["code_under_test"] = (
        {0: "same as HEAD", 1: "differs from HEAD"}.get(same.returncode, "unknown")
        if same
        else "unknown"
    )

    problems = [f"{name}: {state}" for name, state in found["services"].items() if state != "ok"]
    if isinstance(found["newest"], dict):
        problems.append(f"ledger: {found['newest']['error']}")
    found["problems"] = problems
    if problems:
        return found, 1
    # Yesterday by default: «este mes», «últimamente» and «la semana pasada» are about days a stale
    # ledger does not have, and their answers would be judged on data the person never sees. A
    # wider age is a deliberate choice of whoever runs it, and the report says it was made. An
    # empty ledger is the same case as an old one.
    found["max_age_days"] = options.max_age_days
    oldest_accepted = today - timedelta(days=options.max_age_days)
    if found["newest"] is None or date.fromisoformat(found["newest"]) < oldest_accepted:
        found["problems"] = [
            f"El movimiento más reciente es del {found['newest']} y hoy es {found['today']}. "
            "Carga los datos con `just seed`."
        ]
        return found, 2
    return found, 0


def _print_preflight(found: dict[str, Any]) -> None:
    version = found.get("version") or {}
    print(f"services: {found['services']}")
    print(
        f"orchestrator: {version.get('sha')} ({version.get('ref')}); HEAD {found.get('head')}; "
        f"product code {found.get('code_under_test')}"
    )
    print(
        f"ledger: newest movement {found.get('newest')}, today {found.get('today')}, "
        f"accepted age {found.get('max_age_days')} day(s)"
    )
    print(f"gpu: {found.get('gpu')}; ollama: {found.get('ollama')}")
    for problem in found.get("problems", []):
        print(f"PROBLEM: {problem}")


# --- the report -----------------------------------------------------------------------------------


def _status(record: dict[str, Any]) -> str:
    """failed, passed or unjudged. A mismatch, a figure that could not be checked, or a turn that
    got no answer fails whatever the judge said."""
    if record.get("figure_check") in ("mismatch", "error") or record.get("error"):
        return "failed"
    verdict = (record.get("judgement") or {}).get("verdict")
    return {"fail": "failed", "pass": "passed"}.get(verdict, "unjudged")


def _mechanism(record: dict[str, Any]) -> str:
    return (
        (record.get("judgement") or {}).get("mechanism")
        or (record.get("expect") or {}).get("mechanism")
        or "untagged"
    )


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-mechanism counts, the recovery rate, the figure checks and the latency of a run.

    Recovery is the next turn of the same conversation after a failed one: did it come back? Only
    decided turns count on either side; a skipped turn is not a turn of the product.
    """
    turns = [record for record in records if not record.get("skipped")]
    by_mechanism: dict[str, dict[str, int]] = {}
    for record in turns:
        counts = by_mechanism.setdefault(
            _mechanism(record), {"turns": 0, "failed": 0, "unjudged": 0}
        )
        counts["turns"] += 1
        status = _status(record)
        if status != "passed":
            counts["failed" if status == "failed" else "unjudged"] += 1

    ordered = sorted(turns, key=lambda record: (record["conversation"], record["turn"]))
    opportunities = recovered = 0
    for before, after in zip(ordered, ordered[1:], strict=False):
        if before["conversation"] != after["conversation"] or _status(before) != "failed":
            continue
        if _status(after) == "unjudged":
            continue
        opportunities += 1
        recovered += _status(after) == "passed"

    figures = {"match": 0, "mismatch": 0, "error": 0, "n/a": 0}
    for record in turns:
        figures[record.get("figure_check", "n/a")] += 1

    seconds = sorted(
        record["seconds"]
        for record in turns
        if record.get("seconds") is not None and not record.get("error")
    )
    latency = (
        {
            "median": round(statistics.median(seconds), 2),
            "p95": seconds[max(0, -(-95 * len(seconds) // 100) - 1)],
            "max": seconds[-1],
            "turns": len(seconds),
        }
        if seconds
        else None
    )
    return {
        "by_mechanism": dict(sorted(by_mechanism.items())),
        "recovery_rate": recovered / opportunities if opportunities else None,
        "recovery": {"recovered": recovered, "after_a_failure": opportunities},
        "figures": figures,
        "latency": latency,
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{round(100 * value)} %"


def _rate(counts: dict[str, int]) -> float | None:
    judged = counts["turns"] - counts["unjudged"]
    return (judged - counts["failed"]) / judged if judged else None


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}".replace(".", ",") + " s"


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _figure(total: int | None, count: int | None) -> str:
    parts = [formatted_euros(total)] if total is not None else []
    if count is not None:
        parts.append(f"{count} mov.")
    return " en ".join(parts) if parts else "—"


def report(run: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    """The run as the person reads it: preflight, findings, the summary, every transcript."""
    found = run.get("preflight") or {}
    version = found.get("version") or {}
    records = run.get("records", [])
    summary = summarise(records)
    earlier = summarise(previous.get("records", [])) if previous else None

    lines = [
        f"# Conversaciones reales — {found.get('today', '?')}, `{version.get('sha', '?')}`",
        "",
        f"**Preflight** ({found.get('at', '?')})",
        "",
        "- Servicios: "
        + " · ".join(f"{name} {state}" for name, state in (found.get("services") or {}).items()),
        f"- Orquestador: `{version.get('sha')}` ({version.get('ref')}) · HEAD `{found.get('head')}`"
        f" · código del producto: {_SAME.get(found.get('code_under_test'), 'desconocido')}",
        f"- Libro: último movimiento {found.get('newest')} (hoy {found.get('today')})"
        + (
            f" · antigüedad aceptada a mano: {found['max_age_days']} días"
            if found.get("max_age_days", 1) != 1
            else ""
        ),
        f"- GPU: {found.get('gpu')} · Ollama en memoria: "
        + (", ".join(model["name"] for model in found["ollama"]) or "ninguno")
        if isinstance(found.get("ollama"), list)
        else f"- GPU: {found.get('gpu')} · Ollama: {found.get('ollama')}",
        "",
    ]
    if run.get("findings"):
        lines += ["## Hallazgos", "", *[f"- {item}" for item in run["findings"]], ""]

    lines += [
        "## Resumen",
        "",
        f"Comparado con: `{previous.get('name', 'la ejecución anterior')}`"
        if previous
        else "Sin ejecución anterior con la que comparar.",
        "",
        "| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tag, counts in summary["by_mechanism"].items():
        before = (earlier or {}).get("by_mechanism", {}).get(tag)
        lines.append(
            f"| {tag} | {counts['turns']} | {counts['failed']} | {counts['unjudged']} "
            f"| {_percent(_rate(counts))} | {_percent(_rate(before)) if before else '—'} |"
        )
    recovery = summary["recovery"]
    figures = summary["figures"]
    latency = summary["latency"] or {}
    lines += [
        "",
        "- Recuperación (tras un turno fallido, ¿el siguiente va bien?): "
        f"{recovery['recovered']} de {recovery['after_a_failure']} "
        f"({_percent(summary['recovery_rate'])})"
        + (f" · antes {_percent(earlier['recovery_rate'])}" if earlier else ""),
        f"- Cifras contra el libro: {figures['match']} match · {figures['mismatch']} mismatch · "
        f"{figures['error']} sin poder comprobar (error) · {figures['n/a']} n/a"
        + (
            f" · antes {earlier['figures']['mismatch']} mismatch, "
            f"{earlier['figures'].get('error', 0)} error"
            if earlier
            else ""
        ),
        f"- Latencia por turno: mediana {_seconds(latency.get('median'))} · "
        f"p95 {_seconds(latency.get('p95'))} · máx {_seconds(latency.get('max'))} "
        f"({latency.get('turns', 0)} turnos)",
        "",
    ]

    goals = run.get("goals", {})
    names = list(dict.fromkeys(record["conversation"] for record in records))
    for name in names:
        turns = sorted(
            (record for record in records if record["conversation"] == name),
            key=lambda record: record["turn"],
        )
        channel = "web" if any(record.get("channel") == "web" for record in turns) else "API"
        lines += [f"## {name}", "", f"Objetivo: {goals.get(name, '—')} · canal: {channel}", ""]
        for record in turns:
            lines += _transcript(record)
        lines += [
            "| Turno | Esperado | Cifras | "
            + " | ".join(
                "naturalness 1-5" if column == "naturalness" else column
                for column in VERDICT_COLUMNS
            )
            + " | s |",
            "|---|---|---|" + "---|" * len(VERDICT_COLUMNS) + "---:|",
        ]
        for record in turns:
            judgement = record.get("judgement") or {}
            lines.append(
                f"| {record['turn']} | {_cell((record.get('expect') or {}).get('mechanism'))} "
                f"| {record.get('figure_check', 'skipped')} | "
                + " | ".join(_cell(judgement.get(column)) for column in VERDICT_COLUMNS)
                + f" | {_seconds(record.get('seconds'))} |"
            )
        lines.append("")
    return "\n".join(lines)


_SAME = {"same as HEAD": "igual que HEAD", "differs from HEAD": "distinto de HEAD"}


def _transcript(record: dict[str, Any]) -> list[str]:
    head = f"**{record['turn']}.** «{record['said']}»"
    if record.get("skipped"):
        return [f"{head} — no enviado: {record['skipped']}", ""]
    if record.get("error"):
        return [f"{head} — error: `{_cell(record['error'])}`", ""]
    lines = [f"{head} → `{record.get('outcome')}` · {_seconds(record.get('seconds'))}", ""]
    lines += [f"> {line}" if line else ">" for line in (record.get("text") or "").splitlines()]
    lines.append("")
    facts = []
    if record.get("plan_in_words"):
        facts.append(f"Consulta: {record['plan_in_words']}")
    if record.get("total_minor") is not None or record.get("movement_count") is not None:
        facts.append(f"Cifra: {_figure(record.get('total_minor'), record.get('movement_count'))}")
    if record.get("sql"):
        facts.append(
            f"SQL: {_figure(record['sql']['total_minor'], record['sql']['movement_count'])}"
        )
    facts.append(f"**{record.get('figure_check')}** ({record.get('figure_note')})")
    # One fact per bullet: consecutive plain lines are one paragraph in Markdown.
    lines.append("- " + " · ".join(facts))
    if record.get("groups"):
        shown = record["groups"][:6]
        lines.append(
            "- Grupos: "
            + " · ".join(
                f"{group['key']} {formatted_euros(group['total_minor'])}" for group in shown
            )
            + (" …" if len(record["groups"]) > len(shown) else "")
        )
    if record.get("suggestions"):
        lines.append("- Sugerencias: " + " · ".join(f"«{item}»" for item in record["suggestions"]))
    state = record.get("state") or {}
    if any(state.values()):
        lines.append(
            "- Estado: " + ", ".join(f"{key} {value}" for key, value in state.items() if value)
        )
    expect = record.get("expect") or {}
    if expect:
        lines.append(
            f"- Esperado: `{expect.get('mechanism')}`"
            + (f" {expect['route']}" if expect.get("route") else "")
            + (f" — {expect['want']}" if expect.get("want") else "")
        )
    lines.append("")
    return lines


# --- commands -------------------------------------------------------------------------------------


def _write_run(out: Path, run: dict[str, Any], previous: dict[str, Any] | None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    out.with_suffix(".md").write_text(report(run, previous), encoding="utf-8")


def _keeper(out: Path, run: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    """Keep each turn the moment it exists.

    The run's files are rewritten after every turn, so a crash halfway — the network, the database,
    this script — loses the turn it was on and nothing before it. A run is dozens of model calls;
    writing once at the end made the last one decide whether any of them was kept.
    """

    def keep(record: dict[str, Any]) -> None:
        run["records"].append(record)
        _write_run(out, run, None)

    return keep


async def _replay(
    options: Options,
    conversations: list[dict[str, Any]],
    keep: Callable[[dict[str, Any]], None],
) -> None:
    async with httpx.AsyncClient(timeout=300.0) as client:
        for conversation in conversations:
            state: dict[str, Any] | None = None
            for number, turn in enumerate(conversation["turns"], start=1):
                record, state = await _turn(
                    client,
                    options,
                    name=conversation["name"],
                    turn=number,
                    said=turn["say"],
                    conversation=state,
                    expect=turn.get("expect"),
                )
                keep(record)
                print(_line(record), flush=True)


def ux_conversations(battery: dict[str, Any]) -> list[dict[str, Any]]:
    """The battery's episodes (`evaluation/ux/conversations.yaml`) as conversations to replay live.

    The battery runs them on a simulated bank and a fixed date. Here they meet the real ledger and
    today, so its `route` is the expectation and what it says the turn ran (`ran`, `groups`, `ends`,
    `banking_calls`) travels as a note for the judge — a description of the intent, not figures or
    dates to match. Its `mechanism` tags arrive with the battery's own tagging; a turn without one
    is counted as untagged. Names are prefixed, so an episode cannot collide with a conversation of
    `evaluation/real/conversations.yaml`.
    """
    conversations = []
    for episode in battery["episodes"]:
        turns = []
        for turn in episode["turns"]:
            if not isinstance(turn["say"], str):
                raise ValueError(f"{episode['name']}: {turn['say']!r} is not a sentence; quote it")
            expect: dict[str, Any] = {"route": turn.get("route")}
            if turn.get("mechanism"):
                expect["mechanism"] = turn["mechanism"]
            meant = {
                key: turn[key] for key in ("ran", "groups", "ends", "banking_calls") if key in turn
            }
            if meant:
                expect["want"] = "en la batería: " + json.dumps(meant, ensure_ascii=False)
            turns.append({"say": turn["say"], "expect": expect})
        conversations.append(
            {"name": f"ux-{episode['name']}", "goal": episode.get("why"), "turns": turns}
        )
    return conversations


def replay_command(options: Options) -> int:
    found, code = preflight(options)
    _print_preflight(found)
    if code:
        return code
    path = Path(options.file)
    conversations = yaml.safe_load(path.read_text(encoding="utf-8"))["conversations"]
    if options.ux:
        battery = yaml.safe_load(Path(options.ux).read_text(encoding="utf-8"))
        conversations += ux_conversations(battery)
    names = [item["name"] for item in conversations]
    if len(set(names)) != len(names):
        print(f"refused: two conversations share a name in {names}")
        return 1
    if options.only:
        conversations = [item for item in conversations if item["name"] in options.only]
    sha = (found.get("version") or {}).get("sha") or "unknown"
    out = Path(options.out) if options.out else path.parent / f"{found['today']}-{sha}"
    run: dict[str, Any] = {
        "name": out.name,
        "preflight": found,
        "goals": {item["name"]: item.get("goal") for item in conversations},
        "records": [],
        "findings": [],
    }
    # On disk before the first turn, and again after each one.
    _write_run(out, run, None)
    try:
        asyncio.run(_replay(options, conversations, _keeper(out, run)))
    finally:
        print(f"{len(run['records'])} turns kept in {out.with_suffix('.json')} and .md")
    return 0


# Improvised and web conversations are named apart from the replayed ones, so folding them into a
# run can never take a replayed conversation's place.
IMPROVISED = "impro-"
WEB = "web-"


def _state(path: Path, goal: str | None, prefix: str) -> dict[str, Any]:
    if path.exists():
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    else:
        name = path.stem if path.stem.startswith(prefix) else f"{prefix}{path.stem}"
        state = {"name": name, "goal": None, "conversation": None, "turns": []}
    if goal:
        state["goal"] = goal
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _show(record: dict[str, Any]) -> None:
    """What the improviser reads before writing the next message."""
    print(_line(record))
    if record.get("skipped") or record.get("error"):
        print(record.get("skipped") or record.get("error"))
        return
    print(f"FinGuard: {record.get('text')}")
    if record.get("suggestions"):
        print("Sugerencias: " + " · ".join(f"«{item}»" for item in record["suggestions"]))
    if record.get("plan_in_words"):
        print(f"Consulta: {record['plan_in_words']}")
    if record.get("sql"):
        print(f"SQL: {_figure(record['sql']['total_minor'], record['sql']['movement_count'])}")
    print(f"Estado: {record.get('state')}")


async def _say(options: Options, state: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=300.0) as client:
        record, following = await _turn(
            client,
            options,
            name=state["name"],
            turn=len(state["turns"]) + 1,
            said=options.sentence,
            conversation=state["conversation"],
        )
    state["conversation"] = following
    return record


def say_command(options: Options) -> int:
    path = Path(options.state)
    state = _state(path, options.goal, IMPROVISED)
    record = asyncio.run(_say(options, state))
    state["turns"].append(record)
    _save_state(path, state)
    _show(record)
    return 3 if record.get("skipped") else 0


def observe_command(options: Options) -> int:
    """A turn something else sent — the web client — recorded and checked like a `say` turn.

    The response is the answer as the page received it. The skill's capture hook adds `_seconds`,
    the time the browser waited for it, which stands in when `--seconds` is not given.
    """
    path = Path(options.state)
    state = _state(path, options.goal, WEB)
    body = json.loads(Path(options.response).read_text(encoding="utf-8"))
    # A browser tool may save what the page returned as a JSON string of the answer rather than the
    # answer itself; one more decode reads both.
    if isinstance(body, str):
        body = json.loads(body)
    seconds = options.seconds if options.seconds is not None else body.get("_seconds")
    record = asyncio.run(
        record_of(
            options,
            name=state["name"],
            turn=len(state["turns"]) + 1,
            said=options.said,
            body=body,
            seconds=seconds,
        )
    )
    record["channel"] = "web"
    state["conversation"] = body.get("conversation") or state["conversation"]
    state["turns"].append(record)
    _save_state(path, state)
    _show(record)
    return 0


def _conversation_state(path: Path) -> dict[str, Any] | None:
    """The file as a conversation's state (`say`/`observe`), or None when it is anything else —
    a captured answer, a run, a stray file in the same directory."""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(content, dict)
        and isinstance(content.get("name"), str)
        and isinstance(content.get("turns"), list)
    ):
        return content
    return None


def summarise_command(options: Options) -> int:
    """Fold improvised and web transcripts into the run and write its report again.

    The run's JSON is the record: judgements are written into its records (and the state files'
    turns) by the judge, and the Markdown is always generated from it, so the two cannot disagree.

    A file that is not a conversation's state is skipped and said so: a glob over a scratch
    directory also finds the captured web answers. A state whose name the run already has as a
    *replayed* conversation is refused, because folding it in would replace those turns in silence;
    folding the same state again replaces its own earlier copy, which is how a re-run refreshes it.
    """
    run_path = Path(options.run)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if not isinstance(run, dict) or not isinstance(run.get("records"), list):
        print(f"refused: {run_path} is not a run (no `records`)")
        return 1
    folded = set(run.get("folded", []))
    replayed = {record["conversation"] for record in run["records"]} - folded
    states: list[dict[str, Any]] = []
    for extra in options.states:
        state = _conversation_state(Path(extra))
        if state is None:
            print(f"skipped {extra}: not a conversation's state (a captured answer?)")
            continue
        name = state["name"]
        if name in replayed or name in {item["name"] for item in states}:
            print(
                f"refused: {extra} is the conversation «{name}», which this run already has. "
                f"Rename the state (improvised ones start with {IMPROVISED!r}, web ones with "
                f"{WEB!r}); nothing was written."
            )
            return 1
        states.append(state)
    for state in states:
        run.setdefault("goals", {})[state["name"]] = state.get("goal")
        kept = [record for record in run["records"] if record["conversation"] != state["name"]]
        run["records"] = kept + state["turns"]
        folded.add(state["name"])
    run["folded"] = sorted(folded)
    previous = (
        json.loads(Path(options.previous).read_text(encoding="utf-8")) if options.previous else None
    )
    _write_run(run_path.with_suffix(""), run, previous)
    summary = summarise(run["records"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {run_path} and {run_path.with_suffix('.md')}")
    return 0


def preflight_command(options: Options) -> int:
    found, code = preflight(options)
    _print_preflight(found)
    return code


def main() -> int:
    # The person's words and the answers are Spanish; a Windows console's default code page is not.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=ORCHESTRATOR)
    common.add_argument("--banking-url", default=BANKING)
    common.add_argument("--gateway-url", default=GATEWAY)
    common.add_argument("--ollama-url", default=OLLAMA)
    common.add_argument("--token", default=TOKEN)
    common.add_argument("--account", default=ACCOUNT, help="the account the token names")
    common.add_argument("--timezone", default=ZONE)
    common.add_argument("--database", default=DATABASE, help="the read-only role's URL")
    common.add_argument(
        "--max-age-days",
        type=int,
        default=1,
        help="how old the newest movement may be; more than 1 is recorded in the report",
    )

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", parents=[common]).set_defaults(run_command=preflight_command)

    replay = commands.add_parser("replay", parents=[common])
    replay.add_argument("--file", default=str(ROOT / "evaluation" / "real" / "conversations.yaml"))
    replay.add_argument("--out", help="path without extension; .json and .md are written")
    replay.add_argument("--only", nargs="*", help="replay only these conversations, by name")
    replay.add_argument(
        "--ux",
        help="also replay the battery's episodes (evaluation/ux/conversations.yaml) live",
    )
    replay.set_defaults(run_command=replay_command)

    say = commands.add_parser("say", parents=[common])
    say.add_argument("--state", required=True, help="the conversation so far; created if missing")
    say.add_argument("--goal", help="the holder's goal, kept in the state for the report")
    say.add_argument("sentence")
    say.set_defaults(run_command=say_command)

    observe = commands.add_parser("observe", parents=[common])
    observe.add_argument("--state", required=True)
    observe.add_argument("--goal")
    observe.add_argument("--said", required=True, help="what was typed in the web client")
    observe.add_argument("--response", required=True, help="the answer's JSON, as captured")
    observe.add_argument("--seconds", type=float, default=None)
    observe.set_defaults(run_command=observe_command)

    summary = commands.add_parser("summarise", parents=[common])
    summary.add_argument("run", help="the run's .json")
    summary.add_argument("states", nargs="*", help="improvised or web conversations to fold in")
    summary.add_argument("--previous", help="the previous run's .json, to compare with")
    summary.set_defaults(run_command=summarise_command)

    options = parser.parse_args(namespace=Options())
    return int(options.run_command(options))


if __name__ == "__main__":
    sys.exit(main())
