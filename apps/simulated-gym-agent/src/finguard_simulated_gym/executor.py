import random
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from a2a.helpers import new_data_part, new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState

from finguard_simulated_gym.config import Settings
from finguard_simulated_gym.requests import MONTHS, Availability, Booking, read_request
from finguard_simulated_gym.timetable import Session, Timetable

_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTH_NAMES = {number: name for name, number in MONTHS.items() if name != "setiembre"}


@dataclass(frozen=True)
class _Reply:
    """What one request answered; kept so a repeated message id gets the same answer."""

    state: TaskState
    artifact: list[Part] | None = None
    message: list[Part] | None = None


def _long_day(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.day} de {_MONTH_NAMES[day.month]}"


def _plazas(n: int) -> str:
    return "queda 1 plaza" if n == 1 else f"quedan {n} plazas"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _slots(sessions: list[Session]) -> list[str]:
    return [
        f"a las {s.start:%H:%M} ({'completa' if s.capacity == 0 else _plazas(s.capacity)})"
        for s in sessions
    ]


class GymExecutor(AgentExecutor):
    def __init__(self, timetable: Timetable, settings: Settings) -> None:
        self._timetable = timetable
        self._norte = settings.style == "norte"
        self._public_url = settings.public_url
        self._zone = ZoneInfo(settings.timezone)
        self.linked: dict[str, str] = {}
        self._results_by_message_id: dict[str, _Reply] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        assert message is not None and context.task_id and context.context_id
        if context.current_task is None:
            # Not new_task_from_user_message: it refuses an empty text part, and a data-only
            # request may legitimately carry one.
            await event_queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message],
                )
            )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        replay = self._results_by_message_id.get(message.message_id)
        if replay is not None:  # the SDK does not deduplicate; a booking must never happen twice
            return await self._send(updater, replay)
        request = read_request(context, self._zone)
        if request is None:
            return await updater.reject(
                message=updater.new_agent_message([new_text_part("No entiendo la petición.")])
            )
        reply = (
            self._availability(request)
            if isinstance(request, Availability)
            else self._booking(request, message.message_id)
        )
        self._results_by_message_id[message.message_id] = reply
        await self._send(updater, reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    async def _send(self, updater: TaskUpdater, reply: _Reply) -> None:
        await updater.start_work()
        if reply.artifact is not None:
            await updater.add_artifact(reply.artifact, name="respuesta")
        status = updater.new_agent_message(reply.message) if reply.message else None
        await updater.update_status(reply.state, status)

    # -- availability -------------------------------------------------------------------------

    def _availability(self, request: Availability) -> _Reply:
        found = self._timetable.find(request.activity, request.start, request.end, request.band)
        if self._norte:
            return _Reply(
                TaskState.TASK_STATE_COMPLETED,
                artifact=[new_data_part({"sesiones": [self._norte_session(s) for s in found]})],
            )
        first = request.start.date()
        last = (request.end - timedelta(microseconds=1)).date()
        if first == last:
            day = _long_day(first)
            if not found:
                text = f"No hay clases de {request.activity} el {day} en ese horario."
            else:
                text = f"Clases de {request.activity} el {day}: {_join(_slots(found))}."
        elif not found:
            text = (
                f"No hay clases de {request.activity} entre el {_long_day(first)} y el "
                f"{_long_day(last)} en ese horario."
            )
        else:
            # Several days: each day named, so a session is never read as another day's.
            days = sorted({s.day for s in found})
            listed = [
                f"el {_long_day(d)}: {_join(_slots([s for s in found if s.day == d]))}"
                for d in days
            ]
            text = f"Clases de {request.activity} {'; '.join(listed)}."
        return _Reply(TaskState.TASK_STATE_COMPLETED, artifact=[new_text_part(text)])

    def _norte_session(self, s: Session) -> dict[str, Any]:
        return {
            "clase": s.activity.capitalize(),
            "fecha": s.day.isoformat(),
            "hora_inicio": f"{s.start:%H:%M}",
            "duracion_min": s.minutes,
            "huecos": s.capacity,
            "ref": s.ref("N"),
        }

    # -- booking ------------------------------------------------------------------------------

    def _booking(self, request: Booking, message_id: str) -> _Reply:
        session = self._resolve(request)
        if session is None:
            return _Reply(
                TaskState.TASK_STATE_FAILED, message=[new_text_part("No encuentro esa clase.")]
            )
        if self._norte and request.member_ref not in self.linked:
            link = f"{self._public_url}/vincular?ref={quote(request.member_ref, safe='')}"
            text = f"Para reservar necesito vincular tu cuenta de socio: {link}"
            return _Reply(TaskState.TASK_STATE_AUTH_REQUIRED, message=[new_text_part(text)])
        if session.capacity == 0:
            return self._alternatives(session)
        self._timetable.take(session.ref("N"))
        prefix = "NT" if self._norte else "SUR"
        code = f"{prefix}-{random.Random(zlib.crc32(message_id.encode())).randrange(10000):04d}"
        if self._norte:
            reserva = {
                "codigo": code,
                "ref": session.ref("N"),
                "clase": session.activity.capitalize(),
                "fecha": session.day.isoformat(),
                "hora_inicio": f"{session.start:%H:%M}",
            }
            return _Reply(
                TaskState.TASK_STATE_COMPLETED, artifact=[new_data_part({"reserva": reserva})]
            )
        text = (
            f"Listo: te he apuntado a la clase de {session.activity} del {_long_day(session.day)} "
            f"a las {session.start:%H:%M}. Código {code}."
        )
        return _Reply(TaskState.TASK_STATE_COMPLETED, artifact=[new_text_part(text)])

    def _resolve(self, request: Booking) -> Session | None:
        if request.ref:
            return self._timetable.by_ref(request.ref)
        if request.activity and request.at:
            exact = self._timetable.find(request.activity, request.at, request.at.replace(second=1))
            return exact[0] if exact else None
        return None

    def _alternatives(self, full: Session) -> _Reply:
        after = datetime.combine(full.day, full.start)
        options = self._timetable.next_of(full.activity, after, 2)
        if self._norte:
            return _Reply(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=[
                    new_data_part({"alternativas": [self._norte_session(s) for s in options]})
                ],
            )
        offers = [f"el {_long_day(s.day)} a las {s.start:%H:%M}" for s in options]
        text = f"La clase de {full.activity} de las {full.start:%H:%M} está completa."
        if offers:
            text += f" Te puedo ofrecer {' o '.join(offers)}."
        return _Reply(TaskState.TASK_STATE_INPUT_REQUIRED, message=[new_text_part(text)])
