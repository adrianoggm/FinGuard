"""The instructions that go with the schema.

The schema decides what shapes are emittable; this decides which one is right. They do
different jobs and neither replaces the other — a constrained decoder will happily emit
a perfectly-shaped wrong answer, which is the failure this whole layer is built around.

Written in English, like the rest of the code, with the examples in Spanish because the
examples are product data: they are the sentences users actually say.

The rules are not general advice. Each one is a specific confusion that costs a real
wrong number, taken from the same four pairs the evaluation dataset is built to
separate — the reference date is mid-month and mid-week precisely so those pairs cannot
accidentally agree.
"""

from datetime import date
from typing import Final

from finguard_assistant.infrastructure.planning.plan_schema import ABSOLUTE, NO_CATEGORY

_TEMPLATE: Final = """\
You convert one Spanish sentence about a bank account into a query plan.

Today is {today}. Every relative expression is resolved against that date.

## Choosing the outcome

PLANNED — the sentence determines a query. Fill the plan fields.
NEEDS_CLARIFICATION — the sentence does not determine one. Put the question to ask the \
user, in Spanish, in "question". Prefer asking over guessing: a guess returns a real \
figure for a question nobody asked, which is worse than a question.
UNSUPPORTED — the query cannot be expressed at all. There is no average, no comparison \
between two periods, and no forecast: only what already happened.

## The window: exactly one

Either "period" names one of the windows, or "period" is "{absolute}" and "from"/"to" \
carry an explicit range as YYYY-MM-DD. Never both.

These four distinctions each cost a wrong number when missed:

- THIS_MONTH runs from the 1st to TODAY. It is a partial month, not a whole one.
- LAST_MONTH is the whole previous calendar month. It is NOT LAST_N_DAYS with n=30 — \
today those two share only about half their days.
- LAST_N_DAYS includes today: "the last 7 days" is today plus the 6 before it.
- THIS_WEEK is Monday to today. LAST_WEEK is a full Monday to Sunday.

A named month or year ("durante enero", "en 2025") is not a window name: use \
"{absolute}" with an explicit range.
"n" is only for LAST_N_DAYS and LAST_N_MONTHS. Otherwise it is 0.

## Direction, from the account holder's point of view

EXPENSE — money leaving: gasté, pagué, me cobraron, me he dejado.
INCOME — money arriving: cobré, recibí, me ingresaron, me devolvieron.
ALL — only when the sentence asks for both sides.

"How much did I spend" answered with ALL adds income to the total. That is a wrong \
number, not a broader answer.

## Categories

Choose only from the list in the schema; there are no others. Use ["{no_category}"] \
when the question is not about a particular kind of spending.

The list has categories, not shops. A question about one shop ("¿cuánto gasté en \
Mercadona?") cannot be answered by its category, because that category also covers the \
other shops in it — ask for clarification instead.

## Grouping and metric

groupBy is NONE unless the sentence asks for a breakdown. A breakdown nobody asked for \
is as wrong as a missing one.
metric is SUM for "cuánto" and COUNT for "cuántas veces" or "cuántos movimientos".

## Examples

"¿Cuánto gasté el mes pasado?"
{{"outcome": "PLANNED", "question": "", "period": "LAST_MONTH", "n": 0, "from": "", \
"to": "", "direction": "EXPENSE", "categories": ["{no_category}"], "groupBy": "NONE", \
"metric": "SUM"}}

"¿Cuánto he gastado en los últimos 30 días?"
{{"outcome": "PLANNED", "question": "", "period": "LAST_N_DAYS", "n": 30, "from": "", \
"to": "", "direction": "EXPENSE", "categories": ["{no_category}"], "groupBy": "NONE", \
"metric": "SUM"}}

"¿En qué me he gastado el dinero este mes?"
{{"outcome": "PLANNED", "question": "", "period": "THIS_MONTH", "n": 0, "from": "", \
"to": "", "direction": "EXPENSE", "categories": ["{no_category}"], "groupBy": \
"CATEGORY", "metric": "SUM"}}

"¿Cuánto he gastado últimamente?"
{{"outcome": "NEEDS_CLARIFICATION", "question": "¿De qué periodo? Por ejemplo este mes, \
el mes pasado o los últimos 30 días.", "period": "", "n": 0, "from": "", "to": "", \
"direction": "EXPENSE", "categories": ["{no_category}"], "groupBy": "NONE", "metric": \
"SUM"}}

"¿Cuál es mi gasto medio mensual?"
{{"outcome": "UNSUPPORTED", "question": "", "period": "", "n": 0, "from": "", "to": "", \
"direction": "EXPENSE", "categories": ["{no_category}"], "groupBy": "NONE", "metric": \
"SUM"}}

## The sentence

{sentence}
"""


def build_prompt(sentence: str, *, today: date) -> str:
    return _TEMPLATE.format(
        today=today.isoformat(),
        absolute=ABSOLUTE,
        no_category=NO_CATEGORY,
        sentence=sentence,
    )
