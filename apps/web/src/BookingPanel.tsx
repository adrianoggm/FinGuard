/**
 * What a booking is about to book, under the sentence that asks for a yes.
 *
 * The payment panel's job for a class: the sentence names it once, and this lays out the four facts
 * a person checks before tapping «Sí, reservar» — the activity, the day and hours, the gym and the
 * places left. Every value was verified by the service against the gym's own reply, and every one
 * is drawn as text. A slot reference is a third party's identifier that may even look like an
 * address; it is shown so it can be quoted to the gym, never followed.
 */

import type { ActivitySlot, ConversationState } from "./api";

const WEEKDAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

export function BookingPanel({ state }: { state: ConversationState | null | undefined }) {
  const slot = state?.workflow === "BOOKING_CONFIRMATION" ? state.booking_slot : null;
  if (!state || !slot) return null;
  return (
    <dl className="preview-detail booking-detail">
      <dt>Actividad</dt>
      <dd>{slot.activity}</dd>
      <dt>Día y hora</dt>
      <dd>{when(slot)}</dd>
      <dt>Gimnasio</dt>
      <dd>{state.booking_agent_name ?? slot.agent_name}</dd>
      <dt>Plazas</dt>
      <dd>{places(slot.free_places)}</dd>
      {slot.slot_ref && (
        <>
          <dt>Referencia</dt>
          <dd className="booking-reference">{slot.slot_ref}</dd>
        </>
      )}
    </dl>
  );
}

/**
 * «jueves 1 de octubre, 19:00–19:45», read off the ISO strings rather than through `Date`'s clock,
 * which would move the class into the browser's zone: a class at 19:00 in Madrid is at 19:00 on a
 * phone set to London too. The weekday is the calendar's, for that date, whatever the zone.
 */
function when(slot: ActivitySlot): string {
  const start = parts(slot.start);
  if (!start) return slot.start;
  const end = slot.end ? parts(slot.end) : null;
  const weekday = WEEKDAYS[new Date(Date.UTC(start.year, start.month - 1, start.day)).getUTCDay()];
  const hours = end ? `${start.clock}–${end.clock}` : start.clock;
  return `${weekday} ${start.day} de ${MONTHS[start.month - 1]}, ${hours}`;
}

function parts(iso: string): { year: number; month: number; day: number; clock: string } | null {
  const found = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(iso);
  if (!found) return null;
  const [, year, month, day, hour, minute] = found;
  return { year: Number(year), month: Number(month), day: Number(day), clock: `${hour}:${minute}` };
}

function places(free: number | null): string {
  if (free === null) return "—";
  return free === 0 ? "completa" : String(free);
}
