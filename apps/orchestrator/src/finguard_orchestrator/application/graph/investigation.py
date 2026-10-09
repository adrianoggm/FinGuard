"""The investigation the graph adds, as pure functions over what banking answered.

The capability (Task 8, Step 1): «¿en qué comercios gasto más que antes, y se debe a más compras o
a importes mayores?». What banking can establish, and therefore all this module says:

- **which merchants rose**: the same window pair the canonical `Compare` validated, each side read
  by merchant (`GroupBy.MERCHANT`), so every merchant has a total and a movement count per window;
- **why, as a closed explanation** (`Explanation`), decided here from those figures and nothing
  else, and rendered by a template with the figures it was decided from;
- **one purchase or many** (the follow-up a model may ask for): the largest movement of that
  merchant in the latest window, a ranking banking answers, set against the rise.

**No model-written text reaches the person** (review ruling R42). The model may only choose the
next allow-listed read or stop; every word of the answer is a template here, every figure banking's.

What must stay unknown: why the person bought more or paid more (a price rise, a move, a habit).
No bank figure says it, so no template says it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

# The rises a reply names, largest first. More is a list nobody reads in a chat.
MAX_NAMED: Final = 5
# How much the mean per purchase must rise, in percent of the earlier mean, to count as «importes
# mayores». Below it the mean is «parecida»: a cent more per purchase is no explanation.
MEAN_RISE_PERCENT: Final = 5
# The groups banking files movements without a merchant under (banking's `UNKNOWN_MERCHANT`) and
# the fakes' one-row `total`: never a shop to name or inspect.
_NO_MERCHANT: Final = frozenset({"total", "UNKNOWN_MERCHANT"})


class Explanation(StrEnum):
    """Why a merchant's spending rose, decided from the two windows' totals and counts.

    With `more` = the latest count is above the earlier one, and `higher` = the latest mean per
    purchase is at least `MEAN_RISE_PERCENT` % above the earlier one (integer cross-multiplication,
    no rounding):
    """

    NEW_MERCHANT = "NEW_MERCHANT"  # no purchases in the earlier window: there is no cause to give
    BOTH = "BOTH"  # more and higher
    MORE_PURCHASES = "MORE_PURCHASES"  # more, not higher
    HIGHER_AMOUNTS = "HIGHER_AMOUNTS"  # higher, not more
    # Neither: the same number of purchases or fewer, and a mean that rose by less than the
    # threshold. The total rose, but by too little to say it was one or the other.
    CANNOT_TELL = "CANNOT_TELL"


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
    explanation: Explanation

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
                explained(total_then, then.movement_count, total_now, now.movement_count),
            )
        )
    changes.sort(key=lambda change: (-change.rise, change.merchant))
    return tuple(changes[:MAX_NAMED])


def _by_merchant(answer: QueryAnswer) -> dict[str, Group]:
    return {group.key: group for group in answer.groups if group.key not in _NO_MERCHANT}


def explained(total_then: int, count_then: int, total_now: int, count_now: int) -> Explanation:
    """The explanation of a rise (see `Explanation` for the rule)."""
    if count_then == 0:
        return Explanation.NEW_MERCHANT
    more = count_now > count_then
    # mean_now >= mean_then * (100 + p) / 100, without dividing.
    higher = count_now > 0 and (
        total_now * count_then * 100 >= total_then * count_now * (100 + MEAN_RISE_PERCENT)
    )
    if more and higher:
        return Explanation.BOTH
    if more:
        return Explanation.MORE_PURCHASES
    if higher:
        return Explanation.HIGHER_AMOUNTS
    return Explanation.CANNOT_TELL


def worth_inspecting(changes: tuple[MerchantChange, ...]) -> tuple[str, ...]:
    """The merchants a single large purchase could explain: those whose mean rose. The allow-list
    of the one follow-up operation the graph runs, decided here and never by the model."""
    return tuple(
        change.merchant
        for change in changes
        if change.explanation in (Explanation.HIGHER_AMOUNTS, Explanation.BOTH)
    )


def inspected(change: MerchantChange, largest: Movement | None) -> Inspection:
    if largest is None:
        return Inspection(change.merchant, None, None)
    alone = abs(largest.amount_minor) >= change.rise
    return Inspection(change.merchant, largest, Finding.ONE_PURCHASE if alone else Finding.SPREAD)


# --- what the person reads: templates over banking's figures ------------------------------------


def answer_text(
    changes: tuple[MerchantChange, ...],
    inspections: tuple[Inspection, ...],
    *,
    earlier: FinancialPlan,
    latest: FinancialPlan,
    earlier_total: int,
    latest_total: int,
    currency: str,
    partial: bool,
) -> str:
    """The whole answer: every figure banking's, through `answer_writer`; every word a template."""
    money = answer_writer.money
    windows = f"{answer_writer.window_of(latest)} frente a {answer_writer.window_of(earlier)}"
    if not changes:
        lines = [
            f"Ningún comercio sube {windows}. En total: {money(earlier_total, currency)} antes y "
            f"{money(latest_total, currency)} ahora."
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
        f"  · {change.merchant}: {money(change.earlier_total, currency)} → "
        f"{money(change.latest_total, currency)}; {_explanation(change, currency)}."
    )


def _explanation(change: MerchantChange, currency: str) -> str:
    """The explanation's template, with the figures it was decided from."""
    counts = f"de {_purchases(change.earlier_count)} a {_purchases(change.latest_count)}"
    if change.explanation is Explanation.NEW_MERCHANT:
        return f"es nuevo: ninguna compra antes, {_purchases(change.latest_count)} ahora"
    means = (
        f"{answer_writer.money(_mean(change.earlier_total, change.earlier_count), currency)} a "
        f"{answer_writer.money(_mean(change.latest_total, change.latest_count), currency)}"
    )
    match change.explanation:
        case Explanation.BOTH:
            return f"por más compras ({counts}) y por importes mayores (media de {means})"
        case Explanation.MORE_PURCHASES:
            return f"por más compras ({counts}; media de {means})"
        case Explanation.HIGHER_AMOUNTS:
            return f"por importes mayores (media de {means}; {counts})"
        case _:
            return (
                f"sube poco para atribuirlo a más compras o a importes mayores "
                f"({counts}; media de {means})"
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


def _mean(total_minor: int, count: int) -> int:
    """Mean in minor units, rounded half up, without a float."""
    if count <= 0:
        return 0
    units, remainder = divmod(total_minor, count)
    return units + int(remainder * 2 >= count)


def _purchases(count: int) -> str:
    return f"{count} {'compra' if count == 1 else 'compras'}"
