"""«Ver la consulta» speaks Spanish; the planner's prompt keeps the ledger's identifiers.

Reported from the web, under an answer about subscriptions:

    Ver la consulta → "en el último mes, expense, subscriptions"

Only the window was Spanish. `answer_writer.describe` lowercases the raw enum values — direction,
categories, grouping, metric, counterparty type — because it was written for the planner's prompt,
where a closed set of identifiers is exactly what a model should be shown. The person reads the
same plan through `plan_in_words`, in the words the answers already use: «suscripciones» is what
the answer said two lines above, and «subscriptions» is a column name.

So two tests pin two audiences. What reaches the person — the `planned` stage and the result's
`plan` field — carries no identifier; what reaches the planner is byte-for-byte what its measured
evaluations were run against.
"""

import ast
import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support import episodes

from finguard_orchestrator.application import metrics_executor
from finguard_orchestrator.application.queries import ask_financial_question
from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.services import answer_writer, category_words, explainer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.interpreting.llm_turn_interpreter import (
    LlmTurnInterpreter,
)
from finguard_orchestrator.infrastructure.planning.prompt import build_prompt

LAST_MONTH = RelativePeriod(Period.LAST_MONTH)
REPORTED = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_N_MONTHS, n=1),
    categories=("SUBSCRIPTIONS",),
)

# Every identifier `describe` can put on the line, as it puts it there: lowercased.
IDENTIFIERS = frozenset(
    {direction.value.lower() for direction in Direction}
    | {grouping.value.lower() for grouping in GroupBy if grouping is not GroupBy.NONE}
    | {metric.value.lower() for metric in Metric}
    | {kind.value.lower() for kind in CounterpartyType}
    | {category.lower() for category in category_words.keys()}
)


def identifiers_in(line: str) -> set[str]:
    """The identifiers a line shows as whole words — «persona» is not «person»."""
    return {word for word in IDENTIFIERS if re.search(rf"(?<![a-z_]){word}(?![a-z_])", line)}


def test_the_reported_line_is_spanish() -> None:
    assert answer_writer.plan_in_words(REPORTED) == "en el último mes, gastos, suscripciones"


@pytest.mark.parametrize(
    "plan",
    [
        *(FinancialPlan(direction=direction, period=LAST_MONTH) for direction in Direction),
        *(
            FinancialPlan(direction=Direction.ALL, period=LAST_MONTH, group_by=grouping)
            for grouping in GroupBy
        ),
        *(
            FinancialPlan(direction=Direction.INCOME, period=LAST_MONTH, counterparty_types=(kind,))
            for kind in CounterpartyType
        ),
        *(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=LAST_MONTH,
                metric=metric,
                group_by=(
                    GroupBy.MONTH
                    if metric in {Metric.PERIOD_AVERAGE, Metric.CUMULATIVE}
                    else GroupBy.NONE
                ),
            )
            for metric in Metric
        ),
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=LAST_MONTH,
            categories=tuple(category_words.keys()),
        ),
        TopMovements(
            direction=Direction.EXPENSE, period=LAST_MONTH, categories=("TRANSPORT_FUEL",)
        ),
    ],
    ids=answer_writer.describe,
)
def test_no_identifier_reaches_the_person(plan: FinancialPlan | TopMovements) -> None:
    assert not identifiers_in(answer_writer.plan_in_words(plan))


def test_each_value_is_said_the_way_the_answers_say_it() -> None:
    """The vocabulary is the one already on screen, not a second one: the category names the
    answers use, and the dimension and measure words the answer sentences are written with."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=LAST_MONTH,
        categories=("GROCERIES", "TRANSPORT_FUEL"),
        group_by=GroupBy.MERCHANT,
        metric=Metric.COUNT,
    )

    assert answer_writer.plan_in_words(plan) == (
        "el mes pasado, gastos, supermercado/gasolina, por comercio, número de movimientos"
    )


def test_the_planner_prompt_still_reads_the_identifiers() -> None:
    """The constraint this fix works under. `describe` is quoted into the planner's prompt, and the
    planner's evaluations were measured against that prompt — so the line it quotes is pinned here
    as text, not recomputed: a change to it is a change to a measured prompt, and should fail."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_N_MONTHS, n=1),
        categories=("SUBSCRIPTIONS",),
        group_by=GroupBy.MERCHANT,
        metric=Metric.COUNT,
    )

    text = build_prompt(
        "¿y el mes anterior?",
        today=date(2026, 8, 5),
        categories=("SUBSCRIPTIONS",),
        previous=plan,
    )

    assert (
        "It was answered with this plan: "
        "en el último mes, expense, subscriptions, por merchant, count\n" in text
    )
    assert answer_writer.plan_in_words(plan) not in text


# --- what the person is shown, over the wire --------------------------------------------------


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def streamed(question: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The stage frames and the answer of one turn on the route the browser uses."""
    stack = episodes.a_stack()
    response = stack.client.post(
        "/v1/questions/stream",
        json={"question": question, "conversation": {"account_selection_mode": "ALL"}},
    )
    assert response.status_code == 200, response.text
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    stages = [frame for frame in frames if frame["type"] == "stage"]
    return stages, frames[-1]


@pytest.mark.parametrize(
    ("question", "category"),
    [
        # Claimed by a reader, the path the report came from.
        ("¿Qué suscripciones tengo?", "suscripciones"),
        # And planned, the path every other question takes.
        ("¿Cuánto gasté en el supermercado el mes pasado?", "supermercado"),
    ],
)
def test_the_planned_stage_and_ver_la_consulta_speak_spanish(question: str, category: str) -> None:
    stages, answer = streamed(question)

    planned = [frame["detail"] for frame in stages if frame["name"] == "planned"]
    assert planned, "the stage that puts the query on screen before the figure"
    for shown in (*planned, answer["plan"]):
        assert category in shown
        assert "gastos" in shown
        assert not identifiers_in(shown), shown


# --- what the turn interpreter is shown -------------------------------------------------------


class KeepsThePrompt:
    """The provider behind the real turn interpreter, keeping every prompt it was sent."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        return '{"intent": "FINANCIAL_QUERY"}'


def test_the_turn_interpreter_still_reads_the_identifiers() -> None:
    """The second model the query line reaches. `stream()` records each answer's line on the turn,
    and the turn interpreter's prompt quotes the log back as "you answered: …" — so the line it was
    measured with is the identifier line, and that is what the turn has to keep recording while the
    person is shown the Spanish one. Pinned as text through the real interpreter's prompt, over two
    real turns, for the same reason the planner's is."""
    stack = episodes.a_stack()
    provider = KeepsThePrompt()
    interpreter = LlmTurnInterpreter(provider, model="pinned")  # type: ignore[arg-type]
    app.dependency_overrides[turn_interpreter] = lambda: interpreter

    first = stack.client.post(
        "/v1/questions",
        json={
            "question": "¿Qué suscripciones tengo?",
            "conversation": {"account_selection_mode": "ALL"},
        },
    ).json()
    stack.client.post(
        "/v1/questions",
        json={"question": "¿y el mes pasado?", "conversation": first["conversation"]},
    )

    assert first["plan"] == "en los últimos 12 meses, gastos, suscripciones, por comercio"
    shown = provider.prompts[-1]
    assert "you answered: en los últimos 12 meses, expense, subscriptions, por merchant\n" in shown
    assert first["plan"] not in shown


def calls_in(module: ModuleType) -> list[ast.Call]:
    """Every call in a module's source, read as written — a site no test happens to reach is
    still a site the model or the person is shown."""
    tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def named(call: ast.Call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else ""


def test_every_line_goes_to_its_own_audience() -> None:
    """Per call site, because that is where the two lines are written.

    A result whose `plan` is built from `describe` carries `plan_in_words` beside it, and no result
    writes the Spanish line into `plan` — the field the turn log records for the turn interpreter —
    nor the identifier line into `plan_in_words`. Every `planned` stage, which only the person sees,
    is the Spanish line and never the identifier one."""
    calls = calls_in(ask_financial_question)
    results = [call for call in calls if named(call) == "AskFinancialQuestionResult"]
    planned = [
        call
        for call in calls
        if named(call) == "Stage"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "planned"
    ]
    spanish_for_models, english_for_people = [], []
    for call in results:
        written = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
        plan = written.get("plan", "")
        if "plan_in_words(" in plan:
            spanish_for_models.append(call.lineno)
        if "describe(" in plan and "plan_in_words" not in written:
            english_for_people.append(call.lineno)
        if "describe(" in written.get("plan_in_words", ""):
            english_for_people.append(call.lineno)
    for call in planned:
        shown = " ".join(ast.unparse(argument) for argument in call.args[1:])
        if "describe(" in shown or "plan_in_words(" not in shown:
            english_for_people.append(call.lineno)

    assert results and planned, "the scan found nothing to check, so it checked nothing"
    assert not spanish_for_models, f"`plan` is the model's line, at lines {spanish_for_models}"
    assert not english_for_people, f"the person is shown identifiers at {english_for_people}"


@pytest.mark.parametrize(
    "module", [explainer, metrics_executor], ids=lambda module: module.__name__
)
def test_the_person_only_lines_never_use_the_identifier_one(module: ModuleType) -> None:
    """The explainer's «La consulta que hice fue: …» and a saved metric's «Ver la consulta» reach
    nobody but the person, so neither module has any business with `describe`."""
    used = {
        call.func.attr
        for call in calls_in(module)
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "answer_writer"
    }

    assert "plan_in_words" in used
    assert "describe" not in used
