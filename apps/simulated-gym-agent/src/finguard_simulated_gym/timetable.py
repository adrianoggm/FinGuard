import random
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

ACTIVITIES = ("spinning", "yoga", "pilates", "pádel", "natación", "crossfit")
_HOURS = (7, 9, 10, 12, 17, 18, 19, 20, 21)
_MINUTES = (0, 30)
_ANCHOR = ((time(18, 30), 6), (time(19, 0), 4), (time(20, 0), 0), (time(21, 0), 3))


def fold(text: str) -> str:
    """Lowercase and strip accents so "Pádel" matches "padel"."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Session:
    activity: str
    day: date
    start: time
    minutes: int
    capacity: int

    def ref(self, prefix: str) -> str:
        return f"{prefix}-{self.day:%m%d}-{self.start:%H%M}"


class Timetable:
    def __init__(self, seed: int, today: date) -> None:
        rng = random.Random(seed)
        slots = [time(h, m) for h in _HOURS for m in _MINUTES]
        sessions: list[Session] = []
        for offset in range(14):
            day = today + timedelta(days=offset)
            fixed: list[Session] = []
            free = list(slots)
            if offset == 1:  # the demo anchor: tomorrow's spinning, one class full
                for start, capacity in _ANCHOR:
                    fixed.append(Session("spinning", day, start, 60, capacity))
                    free.remove(start)
            count = rng.randint(4, 6)
            drawn = sorted(rng.sample(free, count - len(fixed)))
            sessions.extend(fixed)
            for start in drawn:
                sessions.append(
                    Session(
                        rng.choice(ACTIVITIES),
                        day,
                        start,
                        rng.choice((45, 60)),
                        rng.choice(range(1, 12)),
                    )
                )
        # Guarantee every activity appears, whatever the draw.
        seen = {s.activity for s in sessions}
        for activity in ACTIVITIES:
            if activity not in seen:
                index = next(i for i, s in enumerate(sessions) if s.day == today)
                sessions[index] = replace(sessions[index], activity=activity)
        self.sessions: list[Session] = sorted(sessions, key=lambda s: (s.day, s.start))

    def find(self, activity: str, start: datetime, end: datetime) -> list[Session]:
        wanted = fold(activity)
        return [
            s
            for s in self.sessions
            if fold(s.activity) == wanted
            and start.replace(tzinfo=None)
            <= datetime.combine(s.day, s.start)
            < end.replace(tzinfo=None)
        ]

    def by_ref(self, ref: str) -> Session | None:
        for s in self.sessions:
            if ref in (s.ref("N"), s.ref("S")):
                return s
        return None

    def take(self, ref: str) -> bool:
        for i, s in enumerate(self.sessions):
            if ref in (s.ref("N"), s.ref("S")):
                if s.capacity <= 0:
                    return False
                self.sessions[i] = replace(s, capacity=s.capacity - 1)
                return True
        return False

    def next_of(self, activity: str, after: datetime, n: int) -> list[Session]:
        wanted = fold(activity)
        floor = after.replace(tzinfo=None)
        return [
            s
            for s in self.sessions
            if fold(s.activity) == wanted
            and s.capacity > 0
            and datetime.combine(s.day, s.start) > floor
        ][:n]
