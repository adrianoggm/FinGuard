import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from a2a.helpers import get_data_parts, get_text_parts
from a2a.server.agent_execution import RequestContext

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_AVAILABILITY = re.compile(
    r"Busco clases de (?P<activity>.+?) el \w+ (?P<d>\d{1,2}) de (?P<month>\w+) de (?P<y>\d{4}) "
    r"entre las (?P<h1>\d{2}:\d{2}) y las (?P<h2>\d{2}:\d{2})"
)
_BOOKING = re.compile(
    r"Quiero reservar la clase de (?P<activity>.+?) del \w+ (?P<d>\d{1,2}) de (?P<month>\w+) "
    r"de (?P<y>\d{4}) a las (?P<h>\d{2}:\d{2})(?:\. Referencia: (?P<ref>[\w-]+))?\. "
    r"Socio: (?P<member>[\w-]+)"
)


@dataclass(frozen=True)
class Availability:
    activity: str
    start: datetime
    end: datetime
    member_ref: str | None


@dataclass(frozen=True)
class Booking:
    ref: str | None
    activity: str | None
    at: datetime | None
    member_ref: str


def _moment(zone: ZoneInfo, y: str, month: str, d: str, hhmm: str) -> datetime | None:
    number = MONTHS.get(month.casefold())
    if number is None:
        return None
    hour, minute = (int(p) for p in hhmm.split(":"))
    try:
        return datetime(int(y), number, int(d), tzinfo=zone).replace(hour=hour, minute=minute)
    except ValueError:
        return None


def _iso(zone: ZoneInfo, value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(zone) if parsed.tzinfo else parsed.replace(tzinfo=zone)


def _from_data(data: dict[str, Any], zone: ZoneInfo) -> Availability | Booking | None:
    kind = data.get("request")
    member = data.get("memberRef")
    if kind == "availability":
        start, end = _iso(zone, data.get("from")), _iso(zone, data.get("to"))
        activity = data.get("activity")
        if not isinstance(activity, str) or start is None or end is None:
            return None
        return Availability(activity, start, end, member if isinstance(member, str) else None)
    if kind == "booking" and isinstance(member, str):
        ref = data.get("ref")
        activity = data.get("activity")
        return Booking(
            ref if isinstance(ref, str) else None,
            activity if isinstance(activity, str) else None,
            _iso(zone, data.get("at")),
            member,
        )
    return None


def _from_text(text: str, zone: ZoneInfo, hint: str | None) -> Availability | Booking | None:
    if hint in (None, "timetable", "classes") and (m := _AVAILABILITY.search(text)):
        start = _moment(zone, m["y"], m["month"], m["d"], m["h1"])
        end = _moment(zone, m["y"], m["month"], m["d"], m["h2"])
        if start and end:
            return Availability(m["activity"], start, end, None)
    if hint in (None, "reserve", "book") and (m := _BOOKING.search(text)):
        at = _moment(zone, m["y"], m["month"], m["d"], m["h"])
        if at:
            return Booking(m["ref"], m["activity"], at, m["member"])
    return None


def read_request(context: RequestContext, zone: ZoneInfo) -> Availability | Booking | None:
    message = context.message
    if message is None:
        return None
    for data in get_data_parts(list(message.parts)):
        if isinstance(data, dict) and (request := _from_data(data, zone)):
            return request
    # `metadata` is a protobuf Struct: it has item access and `in`, but no `.get`.
    hint = message.metadata["finguardSkill"] if "finguardSkill" in message.metadata else None
    return _from_text("\n".join(get_text_parts(list(message.parts))), zone, hint)
