from datetime import date

from finguard_simulated_gym.timetable import Timetable


def test_the_timetable_is_deterministic_and_covers_fourteen_days() -> None:
    a = Timetable(seed=0, today=date(2026, 10, 1))
    b = Timetable(seed=0, today=date(2026, 10, 1))
    assert a.sessions == b.sessions
    assert {s.day for s in a.sessions} == {date(2026, 10, d) for d in range(1, 15)}
    assert {s.activity for s in a.sessions} == {
        "spinning",
        "yoga",
        "pilates",
        "pádel",
        "natación",
        "crossfit",
    }


def test_tomorrows_spinning_is_the_fixed_demo_anchor_with_one_full_class() -> None:
    t = Timetable(seed=7, today=date(2026, 10, 1))
    tomorrow = [
        (s.start.strftime("%H:%M"), s.capacity)
        for s in t.sessions
        if s.day == date(2026, 10, 2) and s.activity == "spinning"
    ]
    assert tomorrow == [("18:30", 6), ("19:00", 4), ("20:00", 0), ("21:00", 3)]


def test_refs_are_unique() -> None:
    t = Timetable(seed=0, today=date(2026, 10, 1))
    refs = [s.ref("N") for s in t.sessions]
    assert len(refs) == len(set(refs))
