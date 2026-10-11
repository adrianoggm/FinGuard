"""The instrument that decides whether a change to the conversation worked.

`scripts/real_conversation.py` drives the running stack with real conversations and re-does every
figure it can in SQL. A wrong rule in the instrument is a wrong verdict on every run after it, and
the baseline would carry it into the comparison, so the rules are pinned here: the SQL it asks,
which answers it checks at all, what counts as a mismatch, the euros as the answers write them, the
one reply it refuses to send, and how a run is counted.

Nothing here reaches the network or the database: where a test needs a turn or a ledger read, it
stands in for it. The smoke run against the live stack is the script's own `preflight` and
`replay`.
"""

import asyncio
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

# By path, as the other script tests do: `scripts/` is not a package and pytest's configuration
# stays as it is.
_SPEC = importlib.util.spec_from_file_location(
    "real_conversation", ROOT / "scripts" / "real_conversation.py"
)
assert _SPEC and _SPEC.loader
real = importlib.util.module_from_spec(_SPEC)
sys.modules["real_conversation"] = real
_SPEC.loader.exec_module(real)

from real_conversation import expected_sql, figure_check, formatted_euros  # noqa: E402

LAURA = "08aba04f-2b30-4639-88a6-eac741153ce0"

PLAN = {
    "direction": "EXPENSE",
    "start": "2026-07-01",
    "end": "2026-07-31",
    "categories": ["RESTAURANTS"],
    "merchants": [],
    "merchant_terms": [],
    "counterparties": [],
    "excluded_categories": [],
    "excluded_merchants": [],
    "across_accounts": True,
    "only_account_id": None,
}


def test_the_sql_sums_the_debits_of_the_window_and_category() -> None:
    sql, params = expected_sql(PLAN, account_id=LAURA, timezone="Europe/Madrid")
    assert "assistant_read.transactions_v1" in sql
    assert "category = ANY" in sql and params["categories"] == ["RESTAURANTS"]
    assert params["start"] == "2026-07-01" and params["end"] == "2026-07-31"


def test_a_figure_that_differs_is_a_mismatch() -> None:
    assert (
        figure_check(
            answer={"total_minor": 13357, "movement_count": 6},
            sql={"total_minor": 13357, "movement_count": 6},
        )
        == "match"
    )
    assert (
        figure_check(
            answer={"total_minor": 13357, "movement_count": 6},
            sql={"total_minor": 13400, "movement_count": 6},
        )
        == "mismatch"
    )
    assert figure_check(answer={"total_minor": None, "movement_count": None}, sql=None) == "n/a"


def test_a_sentence_that_says_another_figure_is_a_mismatch_even_when_the_fields_agree() -> None:
    """The person reads the sentence, not `total_minor`; the two have disagreed before."""
    ledger = {"total_minor": 13357, "movement_count": 6}
    said_right = {"total_minor": 13357, "movement_count": 6, "text": "Has gastado 133,57 €."}
    said_wrong = {"total_minor": 13357, "movement_count": 6, "text": "Has gastado 134,00 €."}
    assert figure_check(answer=said_right, sql=ledger) == "match"
    assert figure_check(answer=said_wrong, sql=ledger) == "mismatch"


def test_euros_are_formatted_as_the_answers_write_them() -> None:
    assert formatted_euros(189597) == "1.895,97 €"


# Read from banking's source (evaluation/real/README.md says why); a checkout without it skips.
@pytest.mark.skipif(
    not real.BANKING_SOURCE.exists(), reason="apps/banking-api/src is not in this checkout"
)
def test_a_named_period_is_resolved_by_bankings_own_calendar() -> None:
    """Banking is where a period becomes days; a second calendar here would be a second answer."""
    last_month = {**PLAN, "start": None, "end": None, "period": "LAST_MONTH"}
    _, params = expected_sql(
        last_month, account_id=LAURA, timezone="Europe/Madrid", today=date(2026, 10, 7)
    )
    assert (params["start"], params["end"]) == ("2026-09-01", "2026-09-30")

    lately = {**PLAN, "start": None, "end": None, "period": "LAST_N_DAYS", "n": 30}
    _, params = expected_sql(
        lately, account_id=LAURA, timezone="Europe/Madrid", today=date(2026, 10, 7)
    )
    assert (params["start"], params["end"]) == ("2026-09-08", "2026-10-07")


def test_no_account_named_is_every_account_of_the_holder() -> None:
    """The plan is remembered as asked: `across_accounts` can be false on figures read from every
    account, so only a named account narrows the check."""
    sql, params = expected_sql(
        {**PLAN, "across_accounts": False}, account_id=LAURA, timezone="Europe/Madrid"
    )
    assert "only_accounts" not in params and "t.account_id" not in sql

    one = "6f1b8a52-0d1c-4c8e-9a35-1d4f0f7a2b11"
    sql, params = expected_sql(
        {**PLAN, "only_account_id": one}, account_id=LAURA, timezone="Europe/Madrid"
    )
    assert params["only_accounts"] == [one] and "t.account_id" in sql


def _answered(remembered: dict[str, object], **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "outcome": "PLANNED",
        "text": "Has gastado 133,57 € en restaurantes.",
        "plan": real.plan_line(remembered),
        "conversation": {"last_plan": remembered},
        "total_minor": 13357,
        "movement_count": 6,
        "difference_minor": None,
        "parts": [],
    }
    return {**body, **over}


def test_only_a_single_sum_or_count_that_ran_this_turn_is_checked() -> None:
    plan = {**PLAN, "metric": "SUM", "group_by": "NONE", "action": "AGGREGATE"}
    assert real.figure_plan(_answered(plan))[0] == plan

    grouped = {**plan, "group_by": "CATEGORY"}
    assert real.figure_plan(_answered(grouped))[0] is None
    # A balance answer carries the previous plan's line and a figure that is not about it.
    assert real.figure_plan(_answered(plan, outcome="BALANCE"))[0] is None
    # A top-N, a comparison or a payroll check names another plan than the one remembered.
    assert real.figure_plan(_answered(plan, plan=f"{real.plan_line(plan)}, top 3"))[0] is None
    assert real.figure_plan(_answered(plan, difference_minor=-4300))[0] is None
    # «ponme un ejemplo»: the last answer's line, one row's amount as the figure.
    example = "Por ejemplo: La Tagliatella, -40,00 €, el 12 de julio."
    assert real.figure_plan(_answered(plan, text=example, total_minor=4000))[0] is None


def test_a_yes_to_an_open_preview_is_never_sent() -> None:
    """The demo's ledger is the person's: an improvised «sí» would move money in it."""
    preview = {"workflow": "PAYMENT_CONFIRMATION"}
    assert real.refused(" Sí ", preview)
    assert not real.refused("no", preview)
    assert not real.refused("sí", {"workflow": None})
    assert not real.refused("sí", None)


def test_a_mismatch_fails_the_turn_whatever_the_judge_said_and_recovery_is_the_next_turn() -> None:
    def turn(name: str, n: int, tag: str, verdict: str, check: str = "n/a") -> dict[str, object]:
        return {
            "conversation": name,
            "turn": n,
            "seconds": float(n),
            "figure_check": check,
            "expect": {"mechanism": tag},
            "judgement": {"verdict": verdict},
        }

    report = real.summarise(
        [
            turn("a", 1, "follow_up", "pass"),
            turn("a", 2, "follow_up", "pass", check="mismatch"),
            turn("a", 3, "reset", "pass"),
            turn("b", 1, "intent", "fail"),
            turn("b", 2, "intent", "fail"),
        ]
    )
    assert report["by_mechanism"]["follow_up"] == {"turns": 2, "failed": 1, "unjudged": 0}
    assert report["by_mechanism"]["intent"] == {"turns": 2, "failed": 2, "unjudged": 0}
    # a/2 failed and a/3 passed: recovered; b/1 failed and b/2 failed: not recovered.
    assert report["recovery_rate"] == 0.5
    assert report["figures"] == {"match": 0, "mismatch": 1, "error": 0, "n/a": 4}


def test_the_replayed_conversations_are_the_persons_own_words() -> None:
    """Typos and chips as they were typed: corrected, they replay a conversation nobody had."""
    replayed = yaml.safe_load(
        (ROOT / "evaluation" / "real" / "conversations.yaml").read_text(encoding="utf-8")
    )["conversations"]
    assert [conversation["name"] for conversation in replayed][:7] == [
        "gimnasio-ultimamente",
        "gimnasio-meses",
        "spotify-llevo",
        "transfer-then-compare",
        "marta-ella-pago",
        "comparacion-gasolina",
        "olvidalo",
    ]
    by_name = {conversation["name"]: conversation for conversation in replayed}
    assert by_name["gimnasio-meses"]["turns"][0]["say"] == (
        "Oye perdona cuanto he gastado en gimansio en los ultimos meses ?"
    )
    assert by_name["olvidalo"]["turns"][0]["say"] == "cuanto gaste en el super el mes pasao"
    for conversation in replayed:
        assert conversation["goal"]
        for turn in conversation["turns"]:
            assert turn["say"]
            assert turn["expect"]["mechanism"] in real.MECHANISMS


def _options() -> Any:
    return real.Options(account=LAURA, timezone="Europe/Madrid", database="unused")


def test_a_figure_that_could_not_be_checked_is_an_error_and_fails_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`n/a` passes. A check that should have run and did not — whatever raised — must not."""

    class WindowRefusedError(Exception):
        pass

    async def refusing(*_: object) -> dict[str, int]:
        raise WindowRefusedError("THIS_WEEKEND has not started yet")

    monkeypatch.setattr(real, "ledger", refusing)
    plan = {**PLAN, "metric": "SUM", "group_by": "NONE", "action": "AGGREGATE"}
    record = asyncio.run(
        real.record_of(
            _options(), name="a", turn=1, said="¿cuánto?", body=_answered(plan), seconds=1.0
        )
    )
    assert record["figure_check"] == "error"
    assert "WindowRefusedError" in record["figure_note"]

    record["expect"] = {"mechanism": "intent"}
    record["judgement"] = {"verdict": "pass"}
    report = real.summarise([record])
    assert report["by_mechanism"]["intent"] == {"turns": 1, "failed": 1, "unjudged": 0}
    assert report["figures"]["error"] == 1


def test_a_rate_limited_turn_is_sent_again_with_the_conversation_from_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A free-tier provider answers 429 in bursts. With retries asked for, the same turn goes again
    — same sentence, same conversation, same request id, so the graph replays one turn rather than
    starting a second — and the record says it took one retry."""
    before = {"workflow": None}
    sent: list[tuple[str, object, str]] = []
    answers = [
        {"outcome": "PROVIDER_ERROR", "text": "No he podido pensar", "conversation": {"x": 1}},
        {"outcome": "COURTESY", "text": "De nada.", "conversation": {"x": 2}},
    ]

    async def ask(
        _client: object, _options: object, said: str, conversation: object, request_id: str
    ) -> Any:
        sent.append((said, conversation, request_id))
        return answers.pop(0), 0.1, None, {}

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(real, "_ask", ask)
    monkeypatch.setattr(real.asyncio, "sleep", no_wait)
    options = real.Options(
        account=LAURA,
        timezone="Europe/Madrid",
        database="unused",
        provider_retries=2,
        provider_wait=0.0,
    )
    record, following = asyncio.run(
        real._turn(None, options, name="a", turn=1, said="gracias", conversation=before)  # type: ignore[arg-type]
    )
    assert [(said, conversation) for said, conversation, _ in sent] == [
        ("gracias", before),
        ("gracias", before),
    ]
    assert sent[0][2] == sent[1][2]
    assert record["outcome"] == "COURTESY" and record["provider_retries"] == 1
    assert following == {"x": 2}


def test_a_replay_that_crashes_keeps_every_turn_before_the_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def turn(
        _client: object, _options: object, *, name: str, turn: int, said: str, **_: object
    ) -> tuple[dict[str, object], None]:
        if turn == 3:
            raise RuntimeError("the network went away")
        return {"conversation": name, "turn": turn, "said": said, "figure_check": "n/a"}, None

    monkeypatch.setattr(real, "_turn", turn)
    run: dict[str, Any] = {"name": "run", "preflight": {}, "goals": {}, "records": []}
    says = [{"say": "uno"}, {"say": "dos"}, {"say": "tres"}]
    with pytest.raises(RuntimeError):
        asyncio.run(
            real._replay(
                _options(), [{"name": "a", "turns": says}], real._keeper(tmp_path / "run", run)
            )
        )
    kept = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert [record["said"] for record in kept["records"]] == ["uno", "dos"]
    assert "«dos»" in (tmp_path / "run.md").read_text(encoding="utf-8")


def _run_with(tmp_path: Path, *files: dict[str, Any]) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, content in enumerate(files):
        path = tmp_path / f"file-{number}.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        paths.append(str(path))
    return paths


def test_summarise_skips_what_is_not_a_conversation_and_refuses_a_name_the_run_has(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replayed = {
        "conversation": "spotify-llevo",
        "turn": 1,
        "said": "¿Cuánto?",
        "figure_check": "n/a",
    }
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps({"name": "run", "records": [replayed]}), encoding="utf-8")

    def summarise(*states: str) -> int:
        options = real.Options(run=str(run_path), states=list(states), previous=None)
        return int(real.summarise_command(options))

    improvised = {**replayed, "conversation": "impro-gym", "said": "y el gym?"}
    capture = {"type": "answer", "outcome": "PLANNED", "text": "76,93 €", "_seconds": 1.2}
    state = {"name": "impro-gym", "goal": "gimnasio", "conversation": None, "turns": [improvised]}
    files = _run_with(tmp_path, capture, state)
    assert summarise(*files) == 0
    assert "skipped" in capsys.readouterr().out
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert [record["conversation"] for record in run["records"]] == ["spotify-llevo", "impro-gym"]

    # Folding the same state again refreshes it rather than duplicating it.
    assert summarise(*files) == 0
    assert len(json.loads(run_path.read_text(encoding="utf-8"))["records"]) == 2

    # A state named like a replayed conversation would replace its turns in silence.
    before = run_path.read_text(encoding="utf-8")
    colliding = {**state, "name": "spotify-llevo"}
    assert summarise(*_run_with(tmp_path / "colliding", colliding)) == 1
    assert "refused" in capsys.readouterr().out
    assert run_path.read_text(encoding="utf-8") == before


def test_improvised_and_web_conversations_are_named_apart(tmp_path: Path) -> None:
    assert real._state(tmp_path / "gym.json", None, real.IMPROVISED)["name"] == "impro-gym"
    assert real._state(tmp_path / "impro-gym.json", None, real.IMPROVISED)["name"] == "impro-gym"
    assert real._state(tmp_path / "gym.json", "g", real.WEB)["name"] == "web-gym"


def test_the_battery_replays_live_with_its_routes_as_the_expectation() -> None:
    battery = {
        "episodes": [
            {
                "name": "01-x",
                "why": "porque",
                "turns": [
                    {"say": "y este mes?", "route": "QUERY", "ran": {"window": "THIS_MONTH"}},
                    {"say": "gracias", "route": "COURTESY"},
                ],
            }
        ]
    }
    (conversation,) = real.ux_conversations(battery)
    assert conversation["name"] == "ux-01-x" and conversation["goal"] == "porque"
    first, second = conversation["turns"]
    assert first["say"] == "y este mes?" and first["expect"]["route"] == "QUERY"
    assert "THIS_MONTH" in first["expect"]["want"] and "mechanism" not in first["expect"]
    assert second["expect"] == {"route": "COURTESY"}

    real_battery = yaml.safe_load(
        (ROOT / "evaluation" / "ux" / "conversations.yaml").read_text(encoding="utf-8")
    )
    assert len(real.ux_conversations(real_battery)) == len(real_battery["episodes"])


def test_the_subscriptions_conversation_is_the_batterys_own_words() -> None:
    replayed = yaml.safe_load(
        (ROOT / "evaluation" / "real" / "conversations.yaml").read_text(encoding="utf-8")
    )["conversations"]
    battery = yaml.safe_load(
        (ROOT / "evaluation" / "ux" / "conversations.yaml").read_text(encoding="utf-8")
    )["episodes"]
    (subscriptions,) = [item for item in replayed if item["name"] == "suscripciones"]
    (episode,) = [item for item in battery if item["name"] == "01-suscripciones"]
    assert [turn["say"] for turn in subscriptions["turns"]] == [
        turn["say"] for turn in episode["turns"]
    ]


class _Headers(dict[str, str]):
    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__({key.lower(): value for key, value in headers.items()})

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key.lower(), default)


class _FakeResponse:
    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
        self.status_code = status
        self._body = body
        # httpx headers are case-insensitive; the fake is too.
        self.headers = _Headers(headers)
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.posted: list[dict[str, Any]] = []

    async def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, str]) -> Any:
        self.posted.append(json)
        return self.response


def _turn_with(response: _FakeResponse, conversation: Any = None) -> tuple[Any, Any, Any]:
    client = _FakeClient(response)
    options = real.Options(
        account=LAURA,
        timezone="Europe/Madrid",
        database="unused",
        base_url="http://x",
        token="t",
    )
    record, following = asyncio.run(
        real._turn(
            client,
            options,
            name="a",
            turn=1,
            said="hola",
            conversation=conversation,
        )
    )
    return record, following, client


def test_the_engine_and_the_model_calls_of_a_turn_land_in_its_record() -> None:
    answer = {"outcome": "COURTESY", "text": "Hola.", "conversation": {"version": 3}}
    headers = {"X-FinGuard-Engine": "model", "X-FinGuard-Model-Calls": "2"}
    record, following, client = _turn_with(_FakeResponse(200, answer, headers), {"version": 2})
    assert record["engine"] == "model" and record["model_calls"] == 2
    # The whole conversation is echoed, its version with it.
    assert following == {"version": 3} and client.posted[0]["conversation"] == {"version": 2}


def test_a_stream_answer_without_the_model_calls_header_is_missing_not_zero() -> None:
    answer = {"outcome": "COURTESY", "text": "Hola.", "conversation": {}}
    record, _, _ = _turn_with(_FakeResponse(200, answer, {"x-finguard-engine": "rules"}))
    assert record["engine"] == "rules" and record["model_calls"] is None


def test_a_stale_conversation_is_an_error_turn_that_keeps_the_conversation_sent() -> None:
    body = {"code": "STALE_CONVERSATION", "conversation": {"version": 5}}
    record, following, _ = _turn_with(_FakeResponse(409, body, {}), {"version": 2})
    assert "STALE_CONVERSATION" in record["error"] and following == {"version": 2}


def _record(name: str, turn: int, verdict: str = "pass", **over: Any) -> dict[str, Any]:
    return {
        "conversation": name,
        "turn": turn,
        "said": "x",
        "figure_check": "n/a",
        "seconds": 1.0,
        "judgement": {"verdict": verdict, "mechanism": "intent"},
        **over,
    }


def test_a_conversation_succeeds_only_when_every_turn_passes() -> None:
    records = [_record("a", 1), _record("a", 2), _record("b", 1), _record("b", 2, "fail")]
    assert real.summarise(records)["conversations"] == {
        "total": 2,
        "succeeded": 1,
        "unjudged": 0,
        "rate": 0.5,
    }


def test_a_session_task_succeeds_only_when_every_turn_in_that_task_passes() -> None:
    records = [
        _record("session", 1, expect={"task": "movements"}),
        _record("session", 2, "fail", expect={"task": "movements"}),
        _record("session", 3, expect={"task": "payment"}),
    ]
    # Task labels are local to their session, so a task is keyed by both.
    tasks = real.summarise(records)["tasks"]
    counted = ("conversation", "name", "turns", "succeeded", "failed", "unjudged")
    assert {key: {field: task[field] for field in counted} for key, task in tasks.items()} == {
        "session::movements": {
            "conversation": "session",
            "name": "movements",
            "turns": 2,
            "succeeded": False,
            "failed": 1,
            "unjudged": 0,
        },
        "session::payment": {
            "conversation": "session",
            "name": "payment",
            "turns": 1,
            "succeeded": True,
            "failed": 0,
            "unjudged": 0,
        },
    }


def test_a_task_fails_when_the_judge_says_its_goal_was_not_met() -> None:
    record = _record(
        "session",
        1,
        expect={"task": "visualization"},
        judgement={"verdict": "pass", "goal_met": False},
    )
    assert real.summarise([record])["tasks"]["session::visualization"]["succeeded"] is False


def test_the_full_banking_acceptance_session_is_ten_turns_and_groups_tasks() -> None:
    corpus = yaml.safe_load(
        (ROOT / "evaluation" / "real" / "conversations.yaml").read_text(encoding="utf-8")
    )
    session = next(
        item
        for item in corpus["conversations"]
        if item["name"] == "sesion-bancaria-completa-paginacion-comparacion-bizum"
    )
    assert len(session["turns"]) == 10
    assert {turn["expect"]["task"] for turn in session["turns"]} == {
        "saldos",
        "movimientos-paginacion",
        "restaurantes-comparacion-visualizacion",
        "capacidad-bizum",
        "bizum-cancelacion",
    }
    assert len({turn["expect"]["task"] for turn in session["turns"]}) == 5
    assert session["turns"][-1]["say"] == "No, mejor déjalo."


def test_a_goal_judged_unmet_fails_a_conversation_whose_turns_all_passed() -> None:
    records = [_record("a", 1), _record("a", 2, judgement={"verdict": "pass", "goal_met": False})]
    records += [_record("b", 1, judgement={"verdict": "pass", "goal_met": True})]
    assert real.summarise(records)["conversations"]["succeeded"] == 1


def test_model_calls_and_latency_are_reported_per_engine() -> None:
    records = [
        _record("a", 1, engine="model", model_calls=1, seconds=2.0),
        _record("a", 2, engine="model", model_calls=3, seconds=4.0),
        _record("b", 1, engine="rules", model_calls=None, seconds=1.0),
    ]
    summary = real.summarise(records)
    assert summary["model_calls"] == {"turns": 2, "mean": 2.0, "p95": 3}
    assert summary["latency_by_engine"]["model"]["median"] == 3.0
    assert summary["latency_by_engine"]["rules"]["turns"] == 1


def test_a_run_nobody_judged_is_not_a_success() -> None:
    records = [_record("a", 1, judgement=None), _record("b", 1, judgement=None)]
    conversations = real.summarise(records)["conversations"]
    assert conversations["unjudged"] == 2 and conversations["rate"] is None


def test_one_dropped_judgement_makes_an_otherwise_passing_conversation_unjudged() -> None:
    records = [_record("a", 1), _record("a", 2, judgement=None), _record("b", 1)]
    conversations = real.summarise(records)["conversations"]
    assert conversations == {"total": 2, "succeeded": 1, "unjudged": 1, "rate": 1.0}


def test_an_errored_turn_fails_its_conversation() -> None:
    records = [_record("a", 1), _record("a", 2, error="HTTP 409: STALE_CONVERSATION")]
    conversations = real.summarise(records)["conversations"]
    assert conversations["succeeded"] == 0 and conversations["rate"] == 0.0


def test_an_errored_turn_is_left_out_of_the_model_calls() -> None:
    records = [
        _record("a", 1, engine="model", model_calls=2),
        _record("a", 2, engine="model", model_calls=9, error="HTTP 409"),
    ]
    assert real.summarise(records)["model_calls"] == {"turns": 1, "mean": 2.0, "p95": 2}


def test_a_malformed_model_calls_header_is_counted() -> None:
    answer = {"outcome": "COURTESY", "text": "Hola.", "conversation": {}}
    record, _, _ = _turn_with(_FakeResponse(200, answer, {"X-FinGuard-Model-Calls": "many"}))
    assert record["model_calls"] is None and record["model_calls_malformed"] is True
    assert (
        real.summarise([_record("a", 1, model_calls_malformed=record["model_calls_malformed"])])[
            "model_calls_malformed"
        ]
        == 1
    )


def test_p95_of_a_small_sample_is_its_largest_value() -> None:
    assert real._latency([2.0])["p95"] == 2.0
    assert real._latency([1.0, 3.0])["p95"] == 3.0
    assert real._p95_index(1) == 0 and real._p95_index(2) == 1


def test_an_unreadable_answer_is_a_failed_turn_not_an_outage() -> None:
    """Since 50592459 an answer unreadable twice is PROVIDER_ERROR too, with every request
    answered. The replay stopped the session as if the provider were down."""
    unreadable = {"outcome": "PROVIDER_ERROR", "provider_requests": 2, "provider_failures": []}
    down = {
        "outcome": "PROVIDER_ERROR",
        "provider_requests": 1,
        "provider_failures": ["gemini:transport"],
    }

    assert real._availability_failure(unreadable) is None
    assert real._availability_failure(down) == "provider"
    assert real._status(unreadable) == "failed"
