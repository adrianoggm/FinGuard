"""Canonical, strict v1 codec for saved metric definitions.

The decoder is intentionally hand-written.  A generic dataclass decoder would coerce strings to
numbers/enums and would make adding a field silently widen the persisted authority surface.
"""

from __future__ import annotations

import json
from datetime import date
from enum import Enum
from typing import Any, Literal, cast
from uuid import UUID

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.models.chart import Channel, ChartEncoding, ChartSpec
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

VERSION = 1


class CodecError(ValueError):
    """Invalid or unsupported persisted metric definition."""


def encode_definition(definition: SavedMetricDefinition) -> dict[str, Any]:
    return {
        "version": VERSION,
        "plan": encode_plan(definition.plan),
        # Omitted rather than null when the metric is a figure without a chart. Every definition
        # written before this one carries the key, and `_object` accepts a subset of the keys it
        # allows, so both shapes decode without a version bump.
        **({"chart": encode_chart(definition.chart)} if definition.chart is not None else {}),
    }


def decode_definition(value: object) -> SavedMetricDefinition:
    obj = _object(value, {"version", "plan", "chart"})
    _version(obj)
    plan = decode_plan(obj["plan"])
    raw_chart = obj.get("chart")
    chart = decode_chart(raw_chart) if raw_chart is not None else None
    return SavedMetricDefinition(plan=plan, chart=chart)


def dumps_definition(definition: SavedMetricDefinition) -> str:
    return json.dumps(
        encode_definition(definition), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def loads_definition(value: str) -> SavedMetricDefinition:
    try:
        parsed = json.loads(value, parse_int=_strict_int, parse_float=_reject_float)
    except (TypeError, json.JSONDecodeError, CodecError) as exc:
        raise CodecError("invalid metric JSON") from exc
    return decode_definition(parsed)


def encode_plan(plan: object) -> dict[str, Any]:
    if isinstance(plan, FinancialPlan):
        if plan.transaction_ids or plan.explain:
            raise CodecError("movement-specific and explain plans are not saveable")
        return {"version": VERSION, "kind": "FinancialPlan", "plan": _plan_fields(plan)}
    raise CodecError("plan kind is not saveable")


def decode_plan(value: object) -> FinancialPlan:
    obj = _object(value, {"version", "kind", "plan"})
    _version(obj)
    kind = _str(obj, "kind")
    if kind == "FinancialPlan":
        _exact(obj, {"version", "kind", "plan"})
        try:
            return _decode_plan_fields(obj["plan"])
        except (TypeError, ValueError) as exc:
            raise CodecError("invalid financial plan") from exc
    raise CodecError("unknown plan kind")


def encode_chart(chart: ChartSpec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mark": chart.mark,
        "encodings": [
            {
                "channel": e.channel,
                "field": e.field,
                "type": e.type,
                **({"title": e.title} if e.title is not None else {}),
            }
            for e in chart.encodings
        ],
        "responsive": chart.responsive,
    }
    for name in ("title", "stack", "sort", "sort_field", "limit"):
        value = getattr(chart, name)
        if value is not None:
            result[name] = value
    return {"version": VERSION, **result}


def decode_chart(value: object) -> ChartSpec:
    obj = _object(
        value,
        {
            "version",
            "mark",
            "encodings",
            "title",
            "stack",
            "sort",
            "sort_field",
            "limit",
            "responsive",
        },
    )
    _version(obj)
    _require(obj, {"version", "mark", "encodings", "responsive"})
    encodings = obj["encodings"]
    if not isinstance(encodings, list):
        raise CodecError("encodings must be an array")
    try:
        parsed: list[ChartEncoding] = []
        for item in encodings:
            e = _object(item, {"channel", "field", "type", "title"})
            _require(e, {"channel", "field", "type"})
            parsed.append(
                ChartEncoding(
                    channel=cast(Channel, _str(e, "channel")),
                    field=_str(e, "field"),
                    type=cast(Literal["nominal", "quantitative", "temporal"], _str(e, "type")),
                    title=_optional_str(e, "title"),
                )
            )
        return ChartSpec(
            mark=cast(Literal["line", "area", "bar", "point", "donut"], _str(obj, "mark")),
            encodings=tuple(parsed),
            title=_optional_str(obj, "title"),
            stack=cast(Literal["zero", "normalize"] | None, _optional_str(obj, "stack")),
            sort=cast(
                Literal["ascending", "descending", "input"] | None, _optional_str(obj, "sort")
            ),
            sort_field=_optional_str(obj, "sort_field"),
            limit=_optional_int(obj, "limit"),
            responsive=_bool(obj, "responsive"),
        )
    except (TypeError, ValueError) as exc:
        raise CodecError("invalid chart specification") from exc


def _plan_fields(plan: FinancialPlan) -> dict[str, Any]:
    # Deliberately excludes transaction_ids and explain: saved metrics are aggregate questions.
    value: dict[str, Any] = {
        "direction": plan.direction.value,
        "categories": list(plan.categories),
        "excluded_categories": list(plan.excluded_categories),
        "merchants": list(plan.merchants),
        "merchant_terms": list(plan.merchant_terms),
        "excluded_merchants": list(plan.excluded_merchants),
        "counterparties": list(plan.counterparties),
        "excluded_counterparties": list(plan.excluded_counterparties),
        "counterparty_types": [x.value for x in plan.counterparty_types],
        "group_by": plan.group_by.value,
        "metric": plan.metric.value,
        "include_internal": plan.include_internal,
        "internal_only": plan.internal_only,
        "across_accounts": plan.across_accounts,
        "only_account_id": str(plan.only_account_id) if plan.only_account_id else None,
        "only_account_ids": [str(x) for x in plan.only_account_ids],
        "transaction_types": [x.value for x in plan.transaction_types],
    }
    if plan.group_limit is not None:
        value["group_limit"] = plan.group_limit
    if plan.period is not None:
        value["period"] = {
            "period": plan.period.period.value,
            **({"n": plan.period.n} if plan.period.n is not None else {}),
        }
    else:
        assert plan.date_range is not None
        value["date_range"] = {
            "start": plan.date_range.start.isoformat(),
            "end": plan.date_range.end.isoformat(),
        }
    return value


def _decode_plan_fields(value: object) -> FinancialPlan:
    allowed = {
        "direction",
        "categories",
        "excluded_categories",
        "merchants",
        "merchant_terms",
        "excluded_merchants",
        "counterparties",
        "excluded_counterparties",
        "counterparty_types",
        "group_by",
        "metric",
        "group_limit",
        "include_internal",
        "internal_only",
        "across_accounts",
        "only_account_id",
        "only_account_ids",
        "transaction_types",
        "period",
        "date_range",
    }
    obj = _object(value, allowed)
    _require(
        obj,
        {
            "direction",
            "categories",
            "excluded_categories",
            "merchants",
            "merchant_terms",
            "excluded_merchants",
            "counterparties",
            "excluded_counterparties",
            "counterparty_types",
            "group_by",
            "metric",
            "include_internal",
            "internal_only",
            "across_accounts",
            "only_account_id",
            "only_account_ids",
            "transaction_types",
        },
    )
    period = None
    date_range = None
    if ("period" in obj) == ("date_range" in obj):
        raise CodecError("plan requires exactly one window")
    if "period" in obj:
        p = _object(obj["period"], {"period", "n"})
        _require(p, {"period"})
        period = RelativePeriod(_enum(Period, p["period"]), _optional_int(p, "n"))
    else:
        d = _object(obj["date_range"], {"start", "end"})
        _require(d, {"start", "end"})
        date_range = AbsoluteRange(_date(d, "start"), _date(d, "end"))
    account = obj["only_account_id"]
    return FinancialPlan(
        direction=_enum(Direction, obj["direction"]),
        period=period,
        date_range=date_range,
        categories=_strings(obj, "categories"),
        excluded_categories=_strings(obj, "excluded_categories"),
        merchants=_strings(obj, "merchants"),
        merchant_terms=_strings(obj, "merchant_terms"),
        excluded_merchants=_strings(obj, "excluded_merchants"),
        counterparties=_strings(obj, "counterparties"),
        excluded_counterparties=_strings(obj, "excluded_counterparties"),
        counterparty_types=tuple(
            _enum(CounterpartyType, x) for x in _list(obj, "counterparty_types")
        ),
        group_by=_enum(GroupBy, obj["group_by"]),
        metric=_enum(Metric, obj["metric"]),
        group_limit=_optional_int(obj, "group_limit"),
        include_internal=_bool(obj, "include_internal"),
        internal_only=_bool(obj, "internal_only"),
        across_accounts=_bool(obj, "across_accounts"),
        only_account_id=None if account is None else _uuid(account),
        only_account_ids=tuple(_uuid(x) for x in _list(obj, "only_account_ids")),
        transaction_types=tuple(_enum(TransactionType, x) for x in _list(obj, "transaction_types")),
    )


def _object(value: object, allowed: set[str]) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or any(not isinstance(k, str) for k in value)
        or set(value) - allowed
    ):
        raise CodecError("object has unknown or invalid fields")
    return value


def _require(obj: dict[str, Any], fields: set[str]) -> None:
    if set(obj) != fields and not fields <= set(obj):
        raise CodecError("object has missing fields")


def _exact(obj: dict[str, Any], fields: set[str]) -> None:
    if set(obj) != fields:
        raise CodecError("object has missing or unknown fields")


def _version(obj: dict[str, Any]) -> None:
    if type(obj.get("version")) is not int or obj["version"] != VERSION:
        raise CodecError("unsupported definition version")


def _str(obj: dict[str, Any], key: str) -> str:
    if type(obj.get(key)) is not str:
        raise CodecError(f"{key} must be a string")
    return cast(str, obj[key])


def _optional_str(obj: dict[str, Any], key: str) -> str | None:
    v = obj.get(key)
    return None if v is None else _str(obj, key)


def _optional_int(obj: dict[str, Any], key: str) -> int | None:
    v = obj.get(key)
    return None if v is None else (v if type(v) is int else (_raise("integer required")))


def _bool(obj: dict[str, Any], key: str) -> bool:
    if type(obj.get(key)) is not bool:
        raise CodecError(f"{key} must be boolean")
    return cast(bool, obj[key])


def _list(obj: dict[str, Any], key: str) -> list[Any]:
    if not isinstance(obj.get(key), list):
        raise CodecError(f"{key} must be an array")
    return cast(list[Any], obj[key])


def _strings(obj: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _list(obj, key)
    if any(type(v) is not str for v in values):
        raise CodecError(f"{key} must contain strings")
    return tuple(values)


def _enum(cls: type[Enum], value: object) -> Any:
    if type(value) is not str:
        raise CodecError("enum must be a string")
    try:
        return cls(value)
    except ValueError as exc:
        raise CodecError("unknown enum value") from exc


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise CodecError("UUID must be a string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise CodecError("invalid UUID") from exc


def _date(obj: dict[str, Any], key: str) -> date:
    value = _str(obj, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CodecError("invalid date") from exc


def _raise(message: str) -> Any:
    raise CodecError(message)


def _strict_int(value: str) -> int:
    result = int(value)
    if str(result) != value:
        raise CodecError("non-canonical integer")
    return result


def _reject_float(value: str) -> Any:
    raise CodecError("floating point values are not allowed")
