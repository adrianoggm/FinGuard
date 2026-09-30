/**
 * The classes a search offered, and a line for each gym that could not answer.
 *
 * Everything drawn here was verified by the service against the gym's own reply, and it is drawn
 * as text: an activity or a name is a third party's words, and a reference may even look like an
 * address, so nothing an agent wrote becomes markup or a link. The one link is the linking address
 * the service checked against the gym's own hosts, offered only when the gym asked for it.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ActivityList } from "./ActivityList";
import { validateVoiceAnswer, type ActivitySlot, type AgentNotice } from "./api";
import { Conversation, type Thread } from "./Conversation";

function slot(over: Partial<ActivitySlot> = {}): ActivitySlot {
  return {
    agent_id: "gym-sur",
    agent_name: "Gimnasio Sur",
    activity: "Spinning",
    start: "2026-10-01T18:30:00+02:00",
    end: null,
    free_places: 6,
    slot_ref: null,
    ...over,
  };
}

function html(slots: ActivitySlot[], notices: AgentNotice[] = []): string {
  return renderToStaticMarkup(createElement(ActivityList, { slots, notices }));
}

describe("the classes a search offered", () => {
  it("lists every slot in the order the service sent it", () => {
    const markup = html([
      slot(),
      slot({
        agent_id: "gym-norte",
        agent_name: "Gimnasio Norte",
        start: "2026-10-01T19:00:00+02:00",
        free_places: 4,
        slot_ref: "N-1001-1900",
      }),
    ]);

    expect(markup).toContain('<ul class="activity-list">');
    const sur = markup.indexOf("18:30 · Spinning · Gimnasio Sur · 6 plazas");
    const norte = markup.indexOf("19:00 · Spinning · Gimnasio Norte · 4 plazas");
    expect(sur).toBeGreaterThan(-1);
    expect(norte).toBeGreaterThan(sur);
  });

  it("shows the time in the slot's own offset, never the browser's", () => {
    expect(html([slot({ start: "2026-10-01T17:05:00+00:00" })])).toContain("17:05 · Spinning");
    expect(html([slot({ start: "2026-10-01T09:15:00Z" })])).toContain("09:15 · Spinning");
  });

  it("reads the time off a date-time written with a space as well as a T", () => {
    const markup = html([slot({ start: "2026-10-01 18:30:00+02:00" })]);
    expect(markup).toContain("18:30 · Spinning · Gimnasio Sur · 6 plazas");
    expect(markup).not.toContain("2026-10-01");
  });

  it("says nothing about places when the gym did not state them", () => {
    const markup = html([slot({ free_places: null })]);
    expect(markup).toContain("18:30 · Spinning · Gimnasio Sur</li>");
    expect(markup).not.toContain("plaza");
  });

  it("names one place, and a full class, the way a person would", () => {
    expect(html([slot({ free_places: 1 })])).toContain("Gimnasio Sur · 1 plaza</li>");
    expect(html([slot({ free_places: 0 })])).toContain("Gimnasio Sur · completa</li>");
  });

  it("offers the linking address only to a gym that asked for linking and gave one", () => {
    const link = "http://127.0.0.1:9101/vincular?ref=abc";
    const asked = html([], [{ agent_name: "Gimnasio Norte", kind: "link_required", link }]);
    expect(asked).toContain('role="status"');
    expect(asked).toContain("Gimnasio Norte necesita que vincules tu cuenta de socio.");
    expect(asked).toContain(
      `<a href="http://127.0.0.1:9101/vincular?ref=abc" target="_blank" rel="noopener noreferrer">Vincular mi cuenta</a>`,
    );

    const noLink = html([], [{ agent_name: "Gimnasio Norte", kind: "link_required", link: null }]);
    expect(noLink).toContain("necesita que vincules tu cuenta de socio.");
    expect(noLink).not.toContain("<a");

    const silent = html([], [{ agent_name: "Gimnasio Sur", kind: "unavailable", link }]);
    expect(silent).toContain("Gimnasio Sur no ha respondido de forma válida.");
    expect(silent).not.toContain("<a");

    const asking = html([], [{ agent_name: "Gimnasio Sur", kind: "unsupported_request", link: null }]);
    expect(asking).toContain("Gimnasio Sur pide un dato que FinGuard no proporciona.");
  });

  it("never offers a link that is not a web address", () => {
    const markup = html([], [{ agent_name: "Gimnasio Norte", kind: "link_required", link: "javascript:alert(1)" }]);
    expect(markup).not.toContain("<a");
    expect(markup).not.toContain("javascript:");
  });

  it("draws an agent's words as text, never as markup", () => {
    const markup = html(
      [slot({ activity: "<img src=x>", agent_name: "<b>Sur</b>", slot_ref: "http://evil.example/x" })],
      [{ agent_name: "<script>x</script>", kind: "unavailable", link: null }],
    );
    expect(markup).toContain("&lt;img src=x&gt;");
    expect(markup).not.toContain("<img");
    expect(markup).not.toContain("<b>");
    expect(markup).not.toContain("<script>");
    expect(markup).not.toContain('href="http://evil.example');
  });

  it("draws nothing when there is nothing to list", () => {
    expect(html([], [])).toBe("");
  });
});

describe("the classes in the conversation", () => {
  it("are drawn under the answer that offered them, and nowhere else", () => {
    const found = validateVoiceAnswer({
      outcome: "ACTIVITIES_FOUND",
      text: "He encontrado una clase de spinning el jueves 1 de octubre: 18:30 en Gimnasio Sur.",
      activities: [slot()],
      agent_notices: [{ agent_name: "Gimnasio Norte", kind: "unavailable", link: null }],
    });
    const plain = validateVoiceAnswer({ outcome: "PLANNED", text: "Has gastado 12,00 €." });
    const thread: Thread = {
      turns: [
        { who: "assistant", answer: plain },
        { who: "assistant", answer: found },
      ],
      conversation: null,
    };
    const noop = () => {};

    const markup = renderToStaticMarkup(
      createElement(Conversation, { thread, onThread: noop, onClose: noop, onSettled: noop }),
    );

    expect(markup.match(/class="activity-list"/g)).toHaveLength(1);
    expect(markup).toContain("18:30 · Spinning · Gimnasio Sur · 6 plazas");
    expect(markup).toContain("Gimnasio Norte no ha respondido de forma válida.");
  });
});

describe("the classes on the way in", () => {
  const answer = (fields: Record<string, unknown>) => ({
    outcome: "ACTIVITIES_FOUND",
    text: "He encontrado una clase de spinning el jueves 1 de octubre: 18:30 en Gimnasio Sur.",
    ...fields,
  });

  it("keeps the classes and the notices an answer carries", () => {
    const notices: AgentNotice[] = [{ agent_name: "Gimnasio Norte", kind: "unavailable", link: null }];
    const validated = validateVoiceAnswer(answer({ activities: [slot()], agent_notices: notices }));
    expect(validated.activities).toEqual([slot()]);
    expect(validated.agent_notices).toEqual(notices);
  });

  it("refuses a malformed class or notice rather than drawing half of one", () => {
    expect(() => validateVoiceAnswer(answer({ activities: [{ agent_name: "Sur" }] }))).toThrow();
    expect(() => validateVoiceAnswer(answer({ activities: {} }))).toThrow();
    expect(() =>
      validateVoiceAnswer(answer({ agent_notices: [{ agent_name: "Sur", kind: "hacked", link: null }] })),
    ).toThrow();
  });
});
