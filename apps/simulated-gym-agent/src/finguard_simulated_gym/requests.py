import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
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
# Several days as an exact interval (FinGuard's request since ruling R23): from a day at a time to
# a day at a time, with the daily hours when it asked for any.
_AVAILABILITY_INTERVAL = re.compile(
    r"Busco clases de (?P<activity>.+?) "
    r"desde el \w+ (?P<d1>\d{1,2}) de (?P<m1>\w+) de (?P<y1>\d{4}) a las (?P<t1>\d{2}:\d{2}) "
    r"hasta el \w+ (?P<d2>\d{1,2}) de (?P<m2>\w+) de (?P<y2>\d{4}) a las (?P<t2>\d{2}:\d{2})"
    r"(?:, de (?P<h1>\d{2}:\d{2}) a (?P<h2>\d{2}:\d{2}))?\."
)
# Several days, whole (FinGuard's request under ruling R15, still read), with the daily hours.
_AVAILABILITY_DAYS = re.compile(
    r"Busco clases de (?P<activity>.+?) "
    r"entre el \w+ (?P<d1>\d{1,2}) de (?P<m1>\w+) de (?P<y1>\d{4}) "
    r"y el \w+ (?P<d2>\d{1,2}) de (?P<m2>\w+) de (?P<y2>\d{4})"
    r"(?:, de (?P<h1>\d{2}:\d{2}) a (?P<h2>\d{2}:\d{2}))?\."
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
    # The daily hours asked for, on every day between start and end; None when none were asked.
    band: tuple[time, time] | None = None


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


def _hhmm(value: object) -> time | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    try:
        return time(int(value[:2]), int(value[3:]))
    except ValueError:
        return None


def _band(value: object) -> tuple[time, time] | None:
    """`{"from": "HH:MM", "to": "HH:MM"}` as two times, or None when absent or unreadable."""
    if not isinstance(value, dict):
        return None
    opens, closes = _hhmm(value.get("from")), _hhmm(value.get("to"))
    return (opens, closes) if opens is not None and closes is not None else None


def _from_data(data: dict[str, Any], zone: ZoneInfo) -> Availability | Booking | None:
    kind = data.get("request")
    member = data.get("memberRef")
    if kind == "availability":
        start, end = _iso(zone, data.get("from")), _iso(zone, data.get("to"))
        activity = data.get("activity")
        if not isinstance(activity, str) or start is None or end is None:
            return None
        return Availability(
            activity,
            start,
            end,
            member if isinstance(member, str) else None,
            _band(data.get("band")),
        )
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


def _interval_from_text(text: str, zone: ZoneInfo) -> Availability | None:
    """The exact-interval template: from its first moment to its last, as stated."""
    m = _AVAILABILITY_INTERVAL.search(text)
    if m is None:
        return None
    first = _moment(zone, m["y1"], m["m1"], m["d1"], m["t1"])
    last = _moment(zone, m["y2"], m["m2"], m["d2"], m["t2"])
    opens, closes = _hhmm(m["h1"]), _hhmm(m["h2"])
    band = (opens, closes) if opens is not None and closes is not None else None
    if first is None or last is None:
        return None
    return Availability(m["activity"], first, last, None, band)


def _days_from_text(text: str, zone: ZoneInfo) -> Availability | None:
    """The several-days template: from the first day's opening to the last day's closing, and
    with no hours stated, every hour of both days."""
    m = _AVAILABILITY_DAYS.search(text)
    if m is None:
        return None
    opens, closes = _hhmm(m["h1"]), _hhmm(m["h2"])
    first = _moment(zone, m["y1"], m["m1"], m["d1"], m["h1"] or "00:00")
    band: tuple[time, time] | None = None
    if opens is not None and closes is not None:
        last = _moment(zone, m["y2"], m["m2"], m["d2"], m["h2"])
        band = (opens, closes)
    else:
        midnight = _moment(zone, m["y2"], m["m2"], m["d2"], "00:00")
        last = midnight + timedelta(days=1) if midnight is not None else None
    if first is None or last is None:
        return None
    return Availability(m["activity"], first, last, None, band)


def _from_text(text: str, zone: ZoneInfo, hint: str | None) -> Availability | Booking | None:
    if hint in (None, "timetable", "classes") and (interval := _interval_from_text(text, zone)):
        return interval
    if hint in (None, "timetable", "classes") and (days := _days_from_text(text, zone)):
        return days
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
