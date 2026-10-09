"""The investigation the graph adds, as pure functions over what banking answered.

The capability (Task 8, Step 1): «¿en qué comercios gasto más que antes, y se debe a más compras o
a importes mayores?». What banking can establish, and therefore all this module says:

- **which merchants rose**: the same window pair the canonical `Compare` validated, each side read
  by merchant (`GroupBy.MERCHANT`), so every merchant has a total and a movement count per window;
- **more purchases or bigger amounts**: a rise in the movement count is «más compras», a rise in
  the mean per movement is «importes mayores». Both are integer arithmetic over banking's own
  totals and counts, the mean compared by cross-multiplication so no rounding decides it;
- **one purchase or many** (the follow-up a model may ask for): the largest movement of that
  merchant in the latest window, a ranking banking answers, set against the rise.

What must stay unknown: why the person bought more or paid more (a price rise, a move, a habit).
No bank figure says it, so no template here says it, and a narrative that tries is checked only
for its numbers, not its reasons (see `unsupported_numbers` and the report's limitations).
A merchant absent from the earlier window is new, with no cause; a currency mismatch is refused by
the executor before anything here runs.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

# The rises a reply names, largest first. More is a list nobody reads in a chat.
MAX_NAMED: Final = 5
# What a narrative may run to, in characters.
MAX_NARRATIVE: Final = 400
# The groups banking files movements without a merchant under (banking's `UNKNOWN_MERCHANT`) and
# the fakes' one-row `total`: never a shop to name or inspect.
_NO_MERCHANT: Final = frozenset({"total", "UNKNOWN_MERCHANT"})


class Cause(StrEnum):
    MORE_PURCHASES = "MORE_PURCHASES"
    BIGGER_AMOUNTS = "BIGGER_AMOUNTS"
    BOTH = "BOTH"
    # Absent before: there is nothing to have more of, so no cause is given.
    NEW = "NEW"


class Finding(StrEnum):
    """What the largest purchase says about a rise."""

    # Without the largest movement the latest window would not be above the earlier one.
    ONE_PURCHASE = "ONE_PURCHASE"
    # Even without it the latest window is above: the rise is spread over several movements.
    SPREAD = "SPREAD"


@dataclass(frozen=True)
class MerchantChange:
    merchant: str
    earlier_total: int
    earlier_count: int
    latest_total: int
    latest_count: int
    cause: Cause

    @property
    def rise(self) -> int:
        return self.latest_total - self.earlier_total


@dataclass(frozen=True)
class Inspection:
    merchant: str
    largest: Movement | None
    finding: Finding | None


def risen(earlier: QueryAnswer, latest: QueryAnswer) -> tuple[MerchantChange, ...]:
    """The merchants whose spending rose from `earlier` to `latest`, largest rise first."""
    before = _by_merchant(earlier)
    changes = []
    for merchant, now in _by_merchant(latest).items():
        then = before.get(merchant, Group(merchant, 0, 0))
        total_then, total_now = abs(then.total_minor), abs(now.total_minor)
        if total_now <= total_then:
            continue
        changes.append(
            MerchantChange(
                merchant,
                total_then,
                then.movement_count,
                total_now,
                now.movement_count,
                _cause(total_then, then.movement_count, total_now, now.movement_count),
            )
        )
    changes.sort(key=lambda change: (-change.rise, change.merchant))
    return tuple(changes[:MAX_NAMED])


def _by_merchant(answer: QueryAnswer) -> dict[str, Group]:
    return {group.key: group for group in answer.groups if group.key not in _NO_MERCHANT}


def _cause(total_then: int, count_then: int, total_now: int, count_now: int) -> Cause:
    if count_then == 0:
        return Cause.NEW
    more = count_now > count_then
    # mean_now > mean_then, without dividing: total_now / count_now > total_then / count_then.
    bigger = count_now > 0 and total_now * count_then > total_then * count_now
    if more and bigger:
        return Cause.BOTH
    # The total rose, so at least one of the two did.
    return Cause.MORE_PURCHASES if more else Cause.BIGGER_AMOUNTS


def worth_inspecting(changes: tuple[MerchantChange, ...]) -> tuple[str, ...]:
    """The merchants a single large purchase could explain: those whose mean rose. The allow-list
    of the one follow-up operation the graph runs, decided here and never by the model."""
    return tuple(
        change.merchant for change in changes if change.cause in (Cause.BIGGER_AMOUNTS, Cause.BOTH)
    )


def inspected(change: MerchantChange, largest: Movement | None) -> Inspection:
    if largest is None:
        return Inspection(change.merchant, None, None)
    alone = abs(largest.amount_minor) >= change.rise
    return Inspection(change.merchant, largest, Finding.ONE_PURCHASE if alone else Finding.SPREAD)


# --- what the person reads ----------------------------------------------------------------------

_CAUSE_SAID: Final = {
    Cause.MORE_PURCHASES: "por más compras",
    Cause.BIGGER_AMOUNTS: "por importes mayores",
    Cause.BOTH: "por más compras y por importes mayores",
    Cause.NEW: "es nuevo: no tenía compras en el periodo anterior",
}


def answer_text(
    changes: tuple[MerchantChange, ...],
    inspections: tuple[Inspection, ...],
    *,
    earlier: FinancialPlan,
    latest: FinancialPlan,
    earlier_answer: QueryAnswer,
    latest_answer: QueryAnswer,
    partial: bool,
) -> str:
    """The grounded answer: every figure in it is banking's, through `answer_writer`."""
    currency = latest_answer.currency
    windows = f"{answer_writer.window_of(latest)} frente a {answer_writer.window_of(earlier)}"
    if not changes:
        lines = [
            f"Ningún comercio sube {windows}. En total: "
            f"{answer_writer.money(abs(earlier_answer.total_minor), currency)} antes y "
            f"{answer_writer.money(abs(latest_answer.total_minor), currency)} ahora."
        ]
    else:
        lines = [f"Comercios en los que gastas más {windows}:"]
        lines += [_change_line(change, currency) for change in changes]
        lines += [_inspection_line(inspection, currency) for inspection in inspections]
    if partial:
        lines.append("No he investigado más: he llegado al límite de consultas de esta pregunta.")
    return "\n".join(lines)


def _change_line(change: MerchantChange, currency: str) -> str:
    money = answer_writer.money
    return (
        f"  · {change.merchant}: {money(change.earlier_total, currency)} "
        f"({_purchases(change.earlier_count)}) → {money(change.latest_total, currency)} "
        f"({_purchases(change.latest_count)}); {_CAUSE_SAID[change.cause]}."
    )


def _inspection_line(inspection: Inspection, currency: str) -> str:
    if inspection.largest is None or inspection.finding is None:
        return f"  En {inspection.merchant} no he encontrado la compra más grande."
    amount = answer_writer.money(abs(inspection.largest.amount_minor), currency)
    day = inspection.largest.occurred_at.date().isoformat()
    if inspection.finding is Finding.ONE_PURCHASE:
        return (
            f"  En {inspection.merchant}, la subida se debe a una compra: sin la de {amount} "
            f"del {day} no habrías gastado más que antes."
        )
    return (
        f"  En {inspection.merchant}, la compra más grande ({amount} el {day}) no explica sola "
        "la subida: aun sin ella gastas más que antes."
    )


def _purchases(count: int) -> str:
    return f"{count} {'compra' if count == 1 else 'compras'}"


# --- the narrative's numbers --------------------------------------------------------------------

_NUMBER: Final = re.compile(r"\d+(?:[.,]\d+)*")


def unsupported_numbers(narrative: str, grounded: str) -> tuple[str, ...]:
    """The numbers in `narrative` that the grounded answer does not contain, in order.

    The grounded answer is built from banking's figures only, so a number it does not hold is one
    a model wrote. Compared as written: «120,00» and «120» are different claims.
    """
    allowed = set(_NUMBER.findall(grounded))
    return tuple(number for number in _NUMBER.findall(narrative) if number not in allowed)


def checked_narrative(narrative: str, grounded: str) -> str | None:
    """`narrative` when every number in it is grounded and it fits, else None (refused)."""
    narrative = narrative.strip()
    if not narrative or len(narrative) > MAX_NARRATIVE:
        return None
    if unsupported_numbers(narrative, grounded):
        return None
    return narrative
