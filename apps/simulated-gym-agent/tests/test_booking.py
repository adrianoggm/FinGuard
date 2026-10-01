import re

from a2a.helpers import get_data_parts, get_message_text
from a2a.types import TaskState
from gym_client import PUBLIC_URL, data_of, open_gym, text_of

_FULL_NORTE = "N-1001-2000"  # the forced full spinning class
_OPEN_NORTE = "N-1001-1900"
_SUR_FULL = (
    "Quiero reservar la clase de spinning del jueves 1 de octubre de 2026 a las 20:00. Socio: m1"
)
_SUR_OPEN = (
    "Quiero reservar la clase de spinning del jueves 1 de octubre de 2026 a las 19:00. Socio: m1"
)


def _booking(ref: str, member: str) -> dict[str, str]:
    return {"request": "booking", "ref": ref, "memberRef": member}


async def test_norte_asks_an_unlinked_member_to_link_first() -> None:
    async with open_gym("norte") as gym:
        task = await gym.ask(text="", skill="reserve", data=_booking(_OPEN_NORTE, "m-unlinked"))
    assert task.status.state == TaskState.TASK_STATE_AUTH_REQUIRED
    assert f"{PUBLIC_URL}/vincular?ref=m-unlinked" in get_message_text(task.status.message)


async def test_a_linked_member_books_and_gets_a_code() -> None:
    async with open_gym("norte") as gym:
        assert (await gym.link("m1")).status_code == 200
        task = await gym.ask(text="", skill="reserve", data=_booking(_OPEN_NORTE, "m1"))
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        reserva = data_of(task)["reserva"]
        assert re.fullmatch(r"NT-\d{4}", reserva["codigo"])
        assert reserva["ref"] == _OPEN_NORTE
        assert reserva["clase"] == "Spinning"
        assert reserva["fecha"] == "2026-10-01"
        assert reserva["hora_inicio"] == "19:00"
    async with open_gym("sur") as gym:
        task = await gym.ask(text=_SUR_OPEN, skill="book")
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert re.search(r"Código SUR-\d{4}", text_of(task))


async def test_a_full_class_offers_the_next_two() -> None:
    async with open_gym("norte") as gym:
        await gym.link("m1")
        task = await gym.ask(text="", skill="reserve", data=_booking(_FULL_NORTE, "m1"))
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        [alternatives] = get_data_parts(list(task.status.message.parts))
        assert len(alternatives["alternativas"]) == 2
        assert all(a["huecos"] > 0 for a in alternatives["alternativas"])
    async with open_gym("sur") as gym:
        task = await gym.ask(text=_SUR_FULL, skill="book")
        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        reply = get_message_text(task.status.message)
        assert "está completa" in reply
        assert len(re.findall(r"a las \d{2}:\d{2}", reply)) == 2


async def test_a_repeated_message_id_returns_the_first_result() -> None:
    async with open_gym("norte") as gym:
        await gym.link("m1")
        first = await gym.ask(
            text="", skill="reserve", data=_booking(_OPEN_NORTE, "m1"), message_id="same"
        )
        second = await gym.ask(
            text="", skill="reserve", data=_booking(_OPEN_NORTE, "m1"), message_id="same"
        )
        assert second.status.state == TaskState.TASK_STATE_COMPLETED
        assert data_of(second)["reserva"]["codigo"] == data_of(first)["reserva"]["codigo"]
        window = {
            "request": "availability",
            "activity": "spinning",
            "from": "2026-10-01T19:00:00+02:00",
            "to": "2026-10-01T19:30:00+02:00",
            "memberRef": "m1",
        }
        after = await gym.ask(text="", skill="timetable", data=window)
        assert data_of(after)["sesiones"][0]["huecos"] == 3  # 4 - one booking, not two
