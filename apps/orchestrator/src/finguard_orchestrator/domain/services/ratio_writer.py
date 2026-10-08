"""Exact arithmetic for relationships between two ledger reads, presented in Spanish."""

from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.services import answer_writer, category_words
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan


def write(part: QueryAnswer, whole: QueryAnswer, plan: RatioPlan) -> str:
    """Relate two banking answers without converting either through a float."""
    if plan.kind is RatioKind.SHARE:
        return _share(part, whole, plan)
    if plan.kind is RatioKind.SAVINGS_RATE:
        return _savings_rate(part, whole, plan)
    if plan.kind is RatioKind.NET_CASH_FLOW:
        return _net_cash_flow(part, whole, plan)
    return _percent_change(part, whole, plan)


def basis_points(numerator: int, denominator: int) -> int | None:
    """Percentage in hundredths of a point, rounded half away from zero."""
    if denominator == 0:
        return None
    sign = -1 if (numerator < 0) != (denominator < 0) else 1
    scaled = abs(numerator) * 10_000
    divisor = abs(denominator)
    units, remainder = divmod(scaled, divisor)
    rounded = units + int(remainder * 2 >= divisor)
    return sign * rounded


def percentage(value: int) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    return f"{sign}{absolute // 100},{absolute % 100:02d} %"


def _share(part: QueryAnswer, whole: QueryAnswer, plan: RatioPlan) -> str:
    ratio = basis_points(part.total_minor, whole.total_minor)
    window = answer_writer.window_of(plan.part)
    denominator = _whole_label(plan.whole)
    if ratio is None:
        return (
            f"No hubo {_whole_plural(plan.whole)} {window}, así que no puedo calcular qué "
            f"porcentaje representa {_part_label(plan.part).lower()}."
        )
    return (
        f"{_part_label(plan.part)} representa el {percentage(ratio)} de {denominator} {window}: "
        f"{answer_writer.money(part.total_minor, part.currency)} de "
        f"{answer_writer.money(whole.total_minor, whole.currency)}."
    )


def _savings_rate(expenses: QueryAnswer, income: QueryAnswer, plan: RatioPlan) -> str:
    ratio = basis_points(income.total_minor - expenses.total_minor, income.total_minor)
    window = answer_writer.window_of(plan.part)
    if ratio is None:
        return f"No hubo ingresos {window}, así que la tasa de ahorro no está definida."
    return (
        f"Tu tasa de ahorro {window} fue de {percentage(ratio)}: ingresaste "
        f"{answer_writer.money(income.total_minor, income.currency)} y gastaste "
        f"{answer_writer.money(expenses.total_minor, expenses.currency)}."
    )


def _net_cash_flow(expenses: QueryAnswer, income: QueryAnswer, plan: RatioPlan) -> str:
    net = income.total_minor - expenses.total_minor
    window = answer_writer.window_of(plan.part)
    figure = answer_writer.money(abs(net), income.currency)
    if net > 0:
        conclusion = f"te quedaron {figure} más de lo que gastaste"
    elif net < 0:
        conclusion = f"gastaste {figure} más de lo que ingresaste"
    else:
        conclusion = "ingresaste exactamente lo mismo que gastaste"
    return (
        f"Tu flujo neto {window} fue de {answer_writer.money(net, income.currency)}: "
        f"ingresaste {answer_writer.money(income.total_minor, income.currency)} y gastaste "
        f"{answer_writer.money(expenses.total_minor, expenses.currency)}; {conclusion}."
    )


def _percent_change(latest: QueryAnswer, earlier: QueryAnswer, plan: RatioPlan) -> str:
    latest_value = answer_writer.comparison_value(latest, plan.part)
    earlier_value = answer_writer.comparison_value(earlier, plan.whole)
    delta = latest_value - earlier_value
    ratio = basis_points(delta, abs(earlier_value))
    latest_window = answer_writer.compared_window(plan.part)
    earlier_window = answer_writer.compared_window(plan.whole)
    concept = f"El {answer_writer.comparison_concept(plan.part)}"
    if ratio is None:
        return (
            f"No hubo importe de referencia {earlier_window}, así que la variación porcentual "
            "no está definida."
        )
    if ratio == 0:
        conclusion = "no cambió"
    elif ratio > 0:
        conclusion = f"aumentó un {percentage(ratio)}"
    else:
        conclusion = f"disminuyó un {percentage(abs(ratio))}"
    # Each figure beside its window, in the words the comparison itself uses (validation fix 3,
    # item 3): «de 102,80 € en junio de 2026 a 159,46 € en julio de 2026». The windows went after a
    # bare «de … a …», which read «de del 2026-07-01 al 2026-07-31» for every range.
    return (
        f"{concept} {conclusion}: de {answer_writer.comparison_figure(earlier, plan.whole)} "
        f"{earlier_window} a {answer_writer.comparison_figure(latest, plan.part)} {latest_window}."
    )


def _part_label(plan: FinancialPlan) -> str:
    if plan.categories:
        names = tuple(category_words.name_of(category) for category in plan.categories)
        return f"El gasto en {answer_writer.listed(names)}"
    if plan.merchants:
        return f"El gasto en {answer_writer.listed(plan.merchants)}"
    return "Tus gastos"


def _whole_label(plan: FinancialPlan) -> str:
    if plan.categories == ("SALARY",):
        return "tu nómina"
    if plan.direction is Direction.INCOME:
        return "tus ingresos"
    return "tus gastos totales"


def _whole_plural(plan: FinancialPlan) -> str:
    if plan.categories == ("SALARY",):
        return "ingresos de nómina"
    return "ingresos" if plan.direction is Direction.INCOME else "gastos"
