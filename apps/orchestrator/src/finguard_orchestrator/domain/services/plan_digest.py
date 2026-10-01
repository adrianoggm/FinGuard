"""A fingerprint of a plan, value-free, for the chart it charted.

Wave 4.3's drill checks that the plan a holder wants narrowed is the one a chart actually drew --
not a later turn's plan that happens to share a `group_by`. A fingerprint is what lets that check
travel on the wire beside `VisualContext.window` (`domain/models/chart.py`) without carrying the
plan itself: sixteen hex characters name the plan; they never describe it.
"""

import hashlib
import json
from dataclasses import asdict

from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


def plan_digest(plan: FinancialPlan) -> str:
    """The first 16 hex characters of a SHA-256 over `plan`'s own fields.

    `dataclasses.asdict` flattens `RelativePeriod`/`AbsoluteRange` into nested dicts; `StrEnum`
    members (`Direction`, `GroupBy`, `Metric`, ...) dump as their own string values, being `str`
    subclasses already; `UUID` and `date` are neither, so `default=str` is what carries them --
    everything else here is a plain string, int, bool, tuple or `None`. `sort_keys=True` is what
    makes the digest a function of the plan and nothing about the order its fields happened to be
    declared in. Deterministic and unsalted on purpose: two equal plans, built in two different
    turns, must produce the same digest, or the drill's own comparison (`newest.plan_digest ==
    plan_digest(plan)`) could never hold for the case it exists to recognise.
    """
    payload = json.dumps(asdict(plan), sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]
