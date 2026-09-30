/**
 * The classes an activity search offered, and a line for each gym that could not answer.
 *
 * Every value here was verified by the service against the gym's own reply, and every one is
 * drawn as text. The activity and the gym's name are a third party's words, and a slot reference
 * may even look like an address -- none of it becomes markup or a link. The one link is the
 * linking address the service checked against the gym's own hosts, shown only when the gym asked
 * for linking, and only when it is a web address.
 */

import type { ActivitySlot, AgentNotice } from "./api";

export function ActivityList({ slots, notices }: { slots: ActivitySlot[]; notices: AgentNotice[] }) {
  if (slots.length === 0 && notices.length === 0) return null;
  return (
    <div className="activities">
      {slots.length > 0 && (
        <ul className="activity-list">
          {slots.map((slot, index) => (
            <li key={`${slot.agent_id}-${slot.start}-${index}`}>{row(slot)}</li>
          ))}
        </ul>
      )}
      {notices.map((notice, index) => (
        <p className="activity-notice" role="status" key={`${notice.agent_name}-${index}`}>
          {sentence(notice)}
          {notice.kind === "link_required" && isWebAddress(notice.link) && (
            <>
              {" "}
              <a href={notice.link} target="_blank" rel="noopener noreferrer">
                Vincular mi cuenta
              </a>
            </>
          )}
        </p>
      ))}
    </div>
  );
}

/** «18:30 · Spinning · Gimnasio Sur · 6 plazas», as one string so it is one text node. */
function row(slot: ActivitySlot): string {
  return [clock(slot.start), slot.activity, slot.agent_name, places(slot.free_places)]
    .filter((part) => part !== "")
    .join(" · ");
}

/**
 * The wall-clock time the ISO string states, in the slot's own offset. Read off the string rather
 * than through `Date`, which would convert it into the browser's zone: a class at 18:30 in Madrid
 * is at 18:30 on a phone set to London too. A space in place of the `T` is read as well: it is
 * what a date-time printed with `str()` looks like, and a time is still a time.
 */
function clock(iso: string): string {
  const found = /^\d{4}-\d{2}-\d{2}[T ](\d{2}):(\d{2})/.exec(iso);
  return found ? `${found[1]}:${found[2]}` : iso;
}

function places(free: number | null): string {
  if (free === null) return "";
  if (free === 0) return "completa";
  return free === 1 ? "1 plaza" : `${free} plazas`;
}

function sentence(notice: AgentNotice): string {
  switch (notice.kind) {
    case "link_required":
      return `${notice.agent_name} necesita que vincules tu cuenta de socio.`;
    case "unsupported_request":
      return `${notice.agent_name} pide un dato que FinGuard no proporciona.`;
    default:
      return `${notice.agent_name} no ha respondido de forma válida.`;
  }
}

function isWebAddress(link: string | null): link is string {
  return link !== null && /^https?:\/\//i.test(link);
}
