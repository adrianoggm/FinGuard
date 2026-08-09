"""Closed financial relationships that are one request despite naming two concepts."""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish

_NET_CASH_FLOW: Final = re.compile(
    r"\bflujo\s+(?:de\s+caja\s+)?neto\b"
    r"|\bbalance\s+(?:neto\s+)?(?:entre|de)\s+(?:mis\s+)?ingresos\s+y\s+(?:mis\s+)?gastos\b"
    r"|\bdiferencia\s+(?:(?:hubo|hay|existe|fue)\s+)?entre\s+(?:mis\s+)?ingresos\s+y\s+"
    r"(?:mis\s+)?gastos\b"
    r"|\bingresos\s+menos\s+gastos\b"
    r"|\bcuanto\s+(?:dinero\s+)?(?:me\s+)?(?:quedo|queda)\b.{0,45}"
    r"\b(?:despues\s+de\s+(?:mis\s+)?gastos|tras\s+(?:mis\s+)?gastos|"
    r"despues\s+de\s+(?:pagar|gastar))\b"
)


def asks_net_cash_flow(sentence: str) -> bool:
    """Whether income and expenses are the two operands of one requested net figure."""
    return _NET_CASH_FLOW.search(spanish.folded(sentence)) is not None
