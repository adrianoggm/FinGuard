"""The instrument that decides whether a change to the conversation worked.

`scripts/real_conversation.py` drives the running stack with real conversations and re-does every
figure it can in SQL. A wrong rule in the instrument is a wrong verdict on every run after it, and
the baseline would carry it into the comparison, so the rules are pinned here: the SQL it asks,
which answers it checks at all, what counts as a mismatch, the euros as the answers write them, the
one reply it refuses to send, and how a run is counted.

Pure functions only. Nothing here reaches the network or the database; the smoke run against the
live stack is the script's own `preflight` and `replay`.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

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
    assert report["figures"] == {"match": 0, "mismatch": 1, "n/a": 4}


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
