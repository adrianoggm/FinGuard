from a2a.types import TaskState
from gym_client import data_of, open_gym, text_of

_WINDOW = {"from": "2026-10-01T15:00:00+02:00", "to": "2026-10-01T21:00:00+02:00"}


async def test_norte_answers_availability_as_data_in_its_own_vocabulary() -> None:
    async with open_gym("norte") as gym:
        task = await gym.ask(
            text="",
            skill="timetable",
            data={"request": "availability", "activity": "spinning", **_WINDOW, "memberRef": "m1"},
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    sessions = data_of(task)["sesiones"]
    assert [(s["hora_inicio"], s["huecos"]) for s in sessions] == [
        ("18:30", 6),
        ("19:00", 4),
        ("20:00", 0),
    ]
    assert [s["ref"] for s in sessions] == ["N-1001-1830", "N-1001-1900", "N-1001-2000"]
    for s in sessions:
        assert s["clase"] == "Spinning"
        assert s["fecha"] == "2026-10-01"
        assert s["duracion_min"] in (45, 60)


async def test_sur_answers_availability_as_spanish_text_parsed_from_the_template() -> None:
    async with open_gym("sur") as gym:
        task = await gym.ask(
            text=(
                "Busco clases de spinning el jueves 1 de octubre de 2026 "
                "entre las 15:00 y las 21:00."
            ),
            skill="classes",
        )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert text_of(task) == (
        "Clases de spinning el jueves 1 de octubre: a las 18:30 (quedan 6 plazas), "
        "a las 19:00 (quedan 4 plazas) y a las 20:00 (completa)."
    )


async def test_a_day_with_no_class_says_so() -> None:
    # Nothing is ever scheduled between 15:00 and 16:00 (the hours start at 17:00 after 12:30),
    # so this window is empty whatever the draw.
    window = {"from": "2026-10-01T15:00:00+02:00", "to": "2026-10-01T16:00:00+02:00"}
    async with open_gym("norte") as gym:
        task = await gym.ask(
            text="",
            skill="timetable",
            data={"request": "availability", "activity": "crossfit", **window, "memberRef": "m1"},
        )
        assert data_of(task) == {"sesiones": []}
    async with open_gym("sur") as gym:
        task = await gym.ask(
            text=(
                "Busco clases de crossfit el jueves 1 de octubre de 2026 "
                "entre las 15:00 y las 16:00."
            ),
            skill="classes",
        )
        assert "No hay clases" in text_of(task)
