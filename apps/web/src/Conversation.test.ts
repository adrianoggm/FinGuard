/**
 * What the client remembers between turns — and, more to the point, what it no longer decides.
 *
 * This used to be five nullable fields the client combined by hand: the answered plan, the half-read
 * query, the half-built payment, the payment awaiting a yes, and the last refusal. `settled` worked
 * out what to keep from a pattern of nulls, which meant the frontend held the orchestrator's
 * semantics — and it got them wrong every time a new state appeared.
 *
 * Now the service says where the conversation is and this applies it. So these tests are about two
 * things and no others:
 *
 *   the state is stored, never recomputed    a field this end invents is a field that can disagree
 *   the screens are told when money moved    the one thing the service cannot do from its side
 *
 * A test asserting "the plan survives a refusal" would now be testing the orchestrator through the
 * client. That property still holds; it is checked where it is decided.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  Conversation,
  applyVoiceEvent,
  awaitingConfirmation,
  groupFigure,
  materializeVisualizationDerivation,
  metricSources,
  pendingIntent,
  quickReplies,
  recipientChoices,
  settled,
  voiceButtonAction,
  voiceEventIdentity,
  withChoice,
  type Thread,
  type Turn,
} from "./Conversation";
import { validateVoiceAnswer, voiceEnabled, type Answer, type ConversationState, type PlanContext } from "./api";
import { compile } from "vega-lite";
import { Chart, chartSample, chartTitle, spanishNumberLocale, visualPresentation } from "./Chart";

const LAST_MONTH: PlanContext = {
  direction: "EXPENSE",
  period: "LAST_MONTH",
  n: null,
  start: null,
  end: null,
  categories: ["GROCERIES"],
  merchants: [],
  group_by: "NONE",
  metric: "SUM",
};

function state(fields: Partial<ConversationState> = {}): ConversationState {
  return {
    workflow: null,
    awaiting: null,
    last_asked: null,
    answered_the_balance: false,
    last_plan: null,
    retry_plan: null,
    draft: null,
    capability: null,
    payment: null,
    suspended: false,
    candidates: [],
    transfer_to: null,
    transfer_amount_minor: null,
    intent_id: null,
    recipient: null,
    amount_minor: null,
    currency: null,
    concept: null,
    rail: null,
    recipient_iban: null,
    ...fields,
  };
}

function answer(fields: Partial<Answer> = {}): Answer {
  return {
    outcome: "PLANNED",
    text: "",
    question: null,
    intent_id: null,
    plan: null,
    conversation: null,
    currency: null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
    parts: [],
    ...fields,
  };
}

function thread(fields: Partial<Thread> = {}): Thread {
  return { turns: [], conversation: null, ...fields };
}

const noop = () => {};

describe("the state the service sent", () => {
  it("keeps voice opt-in without changing the server-rendered conversation", () => {
    const markup = renderToStaticMarkup(createElement(Conversation, { thread: thread(), onThread: noop, onClose: noop, onSettled: noop }));
    expect(markup).toContain('aria-label="Conversación"');
    if (!voiceEnabled) expect(markup).not.toContain('aria-label="Control de voz"');
  });

  it("renders monthly data keys as readable Spanish labels", () => {
    const groups = Array.from({ length: 13 }, (_, index) => {
      const date = new Date(2025, index, 1);
      const month = String(date.getMonth() + 1).padStart(2, "0");
      return { key: `${date.getFullYear()}-${month}`, total_minor: 100, movement_count: 1 };
    });
    const markup = renderToStaticMarkup(createElement(Conversation, {
      thread: thread({ turns: [{ who: "assistant", answer: answer({
        groups,
        visualization: {
          kind: "chart",
          chart_id: "chart-1",
          unit: "money",
          spec: { mark: { type: "bar" }, encoding: { x: { field: "dimension", type: "temporal" } } },
          data: groups.map((group) => ({ dimension: group.key, value_minor: group.total_minor })),
        },
      }) }] }),
      onThread: noop,
      onClose: noop,
      onSettled: noop,
    }));
    expect(markup).toContain("enero de 2025");
    expect(markup).toContain("enero de 2026");
    expect(markup).not.toContain(">2025-01<");
  });

  it("is stored as it arrived", () => {
    // Verbatim, not merged: the service decided what this turn leaves open, and combining it with
    // what came before is exactly the inference this replaced.
    const sent = state({
      workflow: "FINANCIAL_CLARIFICATION",
      awaiting: "YEAR",
      draft: LAST_MONTH,
    });

    const after = settled(thread(), [], answer({ conversation: sent }), noop);

    expect(after.conversation).toBe(sent);
  });

  it("replaces whatever was there before", () => {
    const before = state({ workflow: "PAYMENT_IN_PROGRESS", awaiting: "AMOUNT" });
    const now = state({ last_plan: LAST_MONTH });

    const after = settled(
      thread({ conversation: before }),
      [],
      answer({ conversation: now }),
      noop,
    );

    expect(after.conversation).toBe(now);
  });

  it("closes a confirmed payment when the service returns an idle conversation", () => {
    const before = state({
      workflow: "PAYMENT_CONFIRMATION",
      awaiting: "CONFIRMATION",
      intent_id: "intent-1",
      recipient: "Marta Solis Vega",
      amount_minor: 1_500,
    });
    const idle = state({ last_plan: LAST_MONTH });

    const after = settled(
      thread({ conversation: before }),
      [],
      answer({ outcome: "PAID", intent_id: "intent-1", conversation: idle }),
      noop,
    );

    expect(awaitingConfirmation(after)).toBe(false);
    expect(after.conversation).toBe(idle);
  });

  it("keeps the last one when an answer carries none", () => {
    // The payment endpoints answer about a payment rather than about a conversation, so they send
    // no state. Dropping it there would end the conversation because a bizum was confirmed.
    const before = state({ last_plan: LAST_MONTH });

    const after = settled(thread({ conversation: before }), [], answer(), noop);

    expect(after.conversation).toBe(before);
  });
});

describe("voice button controls", () => {
  it("reuses the existing client for barge-in without cancelling or closing it", async () => {
    const calls: string[] = [];
    const client = {
      start: async () => { calls.push("start"); },
      stopCapture: () => { calls.push("stopCapture"); },
      cancel: () => { calls.push("cancel"); },
      close: async () => { calls.push("close"); },
    };

    const first = await voiceButtonAction("processing", client);
    const second = await voiceButtonAction("speaking", client);

    expect(first).toBe("barge-in");
    expect(second).toBe("barge-in");
    expect(calls).toEqual(["start", "start"]);
    expect(calls).not.toContain("cancel");
    expect(calls).not.toContain("close");
  });

  it("keeps listening as the capture confirmation action", async () => {
    const calls: string[] = [];
    const client = {
      start: async () => { calls.push("start"); },
      stopCapture: () => { calls.push("stopCapture"); },
      cancel: () => { calls.push("cancel"); },
      close: async () => { calls.push("close"); },
    };

    await voiceButtonAction("listening", client);

    expect(calls).toEqual(["stopCapture"]);
  });
});

describe("voice event synchronisation", () => {
  const event = (type: string, sequence: number, fields: Record<string, unknown> = {}) => ({
    type,
    sessionId: "session-1",
    turnId: "turn-1",
    sequence,
    at: "2026-08-17T00:00:00.000Z",
    ...fields,
  });

  it("retains transcript, structured answer and conversation across same-tick events", () => {
    let current = thread();
    current = applyVoiceEvent(
      current,
      event("transcript.final", 1, { text: "gastos por categoría" }),
      noop,
    ).thread!;
    const structured = answer({
      text: "Comida fue la categoría principal.",
      groups: [{ key: "Comida", total_minor: 1200, movement_count: 2 }],
      // Built the way validateVoiceAnswer accepts (chartFamilies.test.ts holds the same shape).
      visualization: {
        kind: "chart",
        chart_id: "chart-1",
        unit: "money",
        spec: {
          mark: { type: "bar" },
          encoding: {
            x: { field: "dimension", type: "nominal" },
            y: { field: "value_minor", type: "quantitative" },
          },
        },
        data: [{ dimension: "Comida", value_minor: 1200 }],
      },
    });
    current = applyVoiceEvent(
      current,
      event("assistant.text.final", 2, { answer: structured }),
      noop,
    ).thread!;

    expect(current.turns.map((turn) => turn.who)).toEqual(["user", "assistant"]);
    expect((current.turns[1] as { who: "assistant"; answer: Answer }).answer.groups).toEqual(
      structured.groups,
    );
    // The chart on screen while the summary plays: the assistant turn already carries its
    // chart_id the instant assistant.text.final applies, before audio.started/audio.completed.
    const renderedVisualization = (current.turns[1] as { who: "assistant"; answer: Answer }).answer
      .visualization;
    expect(renderedVisualization?.kind === "chart" ? renderedVisualization.chart_id : null).toBe(
      "chart-1",
    );
    // Text and visual data are committed before playback finishes.
    expect(applyVoiceEvent(current, event("audio.completed", 3), noop).thread).toBeUndefined();

    const finalConversation = state({ last_plan: LAST_MONTH });
    current = applyVoiceEvent(
      current,
      event("turn.completed", 4, { conversation: finalConversation }),
      noop,
    ).thread!;
    expect(current.conversation).toBe(finalConversation);
  });

  it("maps stages and gives one-shot events a turn identity independent of sequence", () => {
    expect(
      applyVoiceEvent(thread(), event("assistant.stage", 1, { stage: "planning" }), noop).stage,
    ).toBe("Pensando la consulta…");
    expect(voiceEventIdentity(event("assistant.text.final", 2))).toBe(
      voiceEventIdentity(event("assistant.text.final", 9)),
    );
  });
});

describe("what the client still works out for itself", () => {
  it("renders every compound part with its own query", () => {
    const compound = thread({
      turns: [
        {
          who: "assistant",
          answer: answer({
            outcome: "COMPOUND",
            parts: [
              answer({ text: "Tienes 100,00 €.", plan: "saldo" }),
              answer({ text: "Gastaste 25,00 €.", plan: "este mes, expense" }),
            ],
          }),
        },
      ],
    });

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: compound,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );

    expect(markup).toContain("Tienes 100,00 €.");
    expect(markup).toContain("Gastaste 25,00 €.");
    expect(markup.match(/Ver la consulta/g)).toHaveLength(2);
  });

  it("renders only the latest answer's structured recovery actions", () => {
    const retrying = thread({
      turns: [
        { who: "assistant", answer: answer({ suggestions: ["Reintentar", "Cambiar consulta"] }) },
      ],
    });

    expect(quickReplies(retrying)).toEqual(["Reintentar", "Cambiar consulta"]);

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: retrying,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );
    expect(markup).toContain('aria-label="Acciones sugeridas"');
    expect(markup).toContain("Reintentar");
  });

  it("does not keep an older action live after the user writes another turn", () => {
    const movedOn = thread({
      turns: [
        { who: "assistant", answer: answer({ suggestions: ["Reintentar"] }) },
        { who: "user", text: "otra cosa" },
      ],
    });

    expect(quickReplies(movedOn)).toEqual([]);
  });

  it("knows a payment is waiting for a yes", () => {
    const holding = thread({
      conversation: state({
        workflow: "PAYMENT_CONFIRMATION",
        awaiting: "CONFIRMATION",
        intent_id: "intent-1",
      }),
    });

    expect(awaitingConfirmation(holding)).toBe(true);
    expect(pendingIntent(holding)).toBe("intent-1");
  });

  it("knows a half-built payment is not one", () => {
    // The distinction the input box renders on: a payment being built takes a sentence, a payment
    // waiting for a yes takes a yes.
    const building = thread({
      conversation: state({ workflow: "PAYMENT_IN_PROGRESS", awaiting: "AMOUNT" }),
    });

    expect(awaitingConfirmation(building)).toBe(false);
    expect(pendingIntent(building)).toBeNull();
  });

  it("offers the structured contacts when the payment asks for a recipient choice", () => {
    const candidates = [
      { contact_id: "contact-1", display_name: "Marta Ruiz Gomez", phone: "+34600111222" },
      { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
    ];
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        candidates,
      }),
    });

    expect(recipientChoices(choosing)).toBe(candidates);
    expect(recipientChoices(thread())).toEqual([]);
  });

  it("renders every recipient choice as a button with identifying details", () => {
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        candidates: [
          { contact_id: "contact-1", display_name: "Marta Ruiz Gomez", phone: "+34600111222" },
          { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
        ],
      }),
    });

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: choosing,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );

    expect(markup).toContain("Marta Ruiz Gomez");
    expect(markup).toContain("+34600111222");
    expect(markup).toContain("Marta Solis Vega");
    expect(markup).toContain('aria-label="Elige el contacto"');
  });

  it("tells the screens to reload when a payment resolved", () => {
    // The one side effect the service cannot perform from its side: the balance and the movements
    // behind the sheet have changed, and whatever is showing them is stale.
    let reloaded = 0;

    settled(
      thread(),
      [],
      answer({ outcome: "SENT", intent_id: "intent-1", conversation: state() }),
      () => (reloaded += 1),
    );

    expect(reloaded).toBe(1);
  });

  it("does not tell them anything while the payment is still a question", () => {
    let reloaded = 0;

    settled(
      thread(),
      [],
      answer({
        outcome: "AWAITING_CONFIRMATION",
        intent_id: "intent-1",
        conversation: state({ workflow: "PAYMENT_CONFIRMATION", intent_id: "intent-1" }),
      }),
      () => (reloaded += 1),
    );

    expect(reloaded).toBe(0);
  });

  it("does not tell them anything when no payment was involved", () => {
    let reloaded = 0;

    settled(thread(), [], answer({ conversation: state({ last_plan: LAST_MONTH }) }), () => {
      reloaded += 1;
    });

    expect(reloaded).toBe(0);
  });
});

describe("the one field this client writes", () => {
  it("puts the tapped contact on the half-built payment", () => {
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        payment: {
          recipient_name: null,
          recipient_contact_id: null,
          amount_minor: 1_500,
          currency: "EUR",
          concept: "comida",
        },
        candidates: [
          { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
        ],
      }),
    });

    const chosen = withChoice(choosing, "contact-2");

    expect(chosen.conversation?.payment?.recipient_contact_id).toBe("contact-2");
    // Everything else is the service's, and a client that rewrote any of it would be back to
    // deciding what the conversation means.
    expect(chosen.conversation?.payment?.amount_minor).toBe(1_500);
    expect(chosen.conversation?.payment?.concept).toBe("comida");
    expect(chosen.conversation?.awaiting).toBe("RECIPIENT_CHOICE");
  });

  it("changes nothing when there is no payment to choose for", () => {
    // A tap cannot arrive without candidates, and inventing a payment to hang an identifier on
    // would send the service a state it never issued.
    const idle = thread();

    expect(withChoice(idle, "contact-2")).toBe(idle);
  });
});

describe("financial breakdowns", () => {
  it("presents minor-unit money, count and ratio semantics without mutating API rows", () => {
    const moneyChart = visualPresentation({
      kind: "chart", chart_id: "money", unit: "money",
      spec: { encoding: { y: { field: "value_minor", type: "quantitative" }, tooltip: { field: "value_minor", type: "quantitative" } } },
      data: [{ value_minor: 48945 }],
    }, "EUR");
    expect(moneyChart.data[0]?.value_minor).toBe(48945);
    expect(moneyChart.data[0]?.__value_major).toBe(489.45);
    expect(moneyChart.data[0]?.__value_display).toBe("489,45 €");
    expect((moneyChart.spec.encoding as Record<string, Record<string, unknown>>).tooltip?.field).toBe("__value_display");
    expect((moneyChart.spec.encoding as Record<string, Record<string, Record<string, unknown>>>).y?.axis?.format).toBe("$,.0f");
    expect(spanishNumberLocale("EUR")).toMatchObject({
      decimal: ",", thousands: ".", currency: ["", " €"],
    });

    const twelveEuros = visualPresentation({
      kind: "chart", chart_id: "twelve-euros", unit: "money",
      spec: { encoding: { y: { field: "value_minor", type: "quantitative" } } },
      data: [{ value_minor: 1200 }],
    }, "EUR");
    expect(twelveEuros.data[0]?.__value_major).toBe(12);
    expect(twelveEuros.data[0]?.__value_display).toBe("12,00 €");

    const countChart = visualPresentation({
      kind: "chart", chart_id: "count", unit: "count",
      spec: { encoding: { y: { field: "value_minor", type: "quantitative" } } },
      data: [{ value_minor: 12 }],
    }, "EUR");
    expect(countChart.data[0]?.value_minor).toBe(12);
    expect(countChart.data[0]?.__value_major).toBeUndefined();
    expect((countChart.spec.encoding as Record<string, Record<string, Record<string, unknown>>>).y?.axis?.format).toBe("d");

    const ratioChart = visualPresentation({
      kind: "chart", chart_id: "ratio", unit: "ratio",
      spec: { encoding: { x: { field: "value_minor", type: "quantitative" }, tooltip: { field: "value_minor", type: "quantitative" } } },
      data: [{ value_minor: 1200 }],
    }, "EUR");
    expect(ratioChart.data[0]?.__value_major).toBe(12);
    expect((ratioChart.spec.encoding as Record<string, Record<string, Record<string, unknown>>>).x?.axis?.format).toBe(".0%");

    const sortedMonthly = visualPresentation({
      kind: "chart", chart_id: "sorted-months", unit: "money",
      spec: {
        encoding: {
          x: {
            field: "dimension", type: "nominal",
            sort: { field: "value_minor", order: "descending" },
          },
          y: { field: "value_minor", type: "quantitative" },
          tooltip: { field: "dimension", type: "temporal" },
        },
      },
      data: [{ dimension: "2025-08", value_minor: 21934 }],
    }, "EUR");
    expect(sortedMonthly.data[0]?.__dimension_display).toBe("ago 2025");
    const monthlyEncoding = sortedMonthly.spec.encoding as Record<string, Record<string, unknown>>;
    expect(monthlyEncoding.x?.field).toBe("__dimension_display");
    expect(monthlyEncoding.x?.sort).toEqual({ field: "value_minor", order: "descending" });
    expect(monthlyEncoding.tooltip?.field).toBe("__dimension_display");
  });

  it("hands the title to the app and sizes a discrete plot by its own rows", () => {
    const horizontal = visualPresentation({
      kind: "chart", chart_id: "by-category", unit: "money",
      spec: {
        title: "Gastos por categoría",
        encoding: {
          y: { field: "dimension", type: "nominal" },
          x: { field: "value_minor", type: "quantitative" },
        },
      },
      data: [{ dimension: "Comida", value_minor: 1200 }],
    }, "EUR");
    // Vega never typesets the title; the app does, from the untouched source spec.
    expect(horizontal.spec.title).toBeUndefined();
    expect(chartTitle({
      kind: "chart", chart_id: "by-category", unit: "money",
      spec: { title: "Gastos por categoría" }, data: [],
    })).toBe("Gastos por categoría");
    expect(horizontal.spec.height).toEqual({ step: 26 });
    const horizontalEncoding = horizontal.spec.encoding as Record<string, Record<string, Record<string, unknown>>>;
    // The group runs down the side, so nothing on it is rotated — and the money on x keeps its own
    // axis rather than inheriting a rotation meant for labels.
    expect(horizontalEncoding.y?.axis?.labelAngle).toBeUndefined();
    expect(horizontalEncoding.x?.axis?.format).toBe("$,.0f");

    const vertical = visualPresentation({
      kind: "chart", chart_id: "by-day", unit: "count",
      spec: {
        encoding: {
          x: { field: "dimension", type: "nominal" },
          y: { field: "value_minor", type: "quantitative" },
        },
      },
      data: [{ dimension: "lunes", value_minor: 3 }],
    }, "EUR");
    const verticalEncoding = vertical.spec.encoding as Record<string, Record<string, Record<string, unknown>>>;
    expect(verticalEncoding.x?.axis?.labelAngle).toBe(-32);
    expect(vertical.spec.height).toBeUndefined();

    // A normalised share is one stacked bar: no discrete axis, so it is not given a row height.
    const share = visualPresentation({
      kind: "chart", chart_id: "share", unit: "ratio",
      spec: {
        encoding: {
          x: { field: "value_minor", type: "quantitative" },
          color: { field: "dimension", type: "nominal" },
        },
      },
      data: [{ dimension: "Comida", value_minor: 4000 }],
    }, "EUR");
    expect(share.spec.height).toBe(56);
    expect(chartTitle({ kind: "kpi", value_field: "value_minor", unit: "money", data: [] })).toBeNull();

    // Structure is not the same as a spec Vega will accept. Compiling is the cheap way to find
    // out, and it is the compiler the app itself embeds.
    for (const presented of [horizontal, vertical, share]) {
      expect(() => compile({
        ...(presented.spec as Record<string, unknown>),
        mark: { type: "bar" },
        data: { values: presented.data },
      } as never)).not.toThrow();
    }
  });

  it("owns up to a sample only when the plot is actually hiding rows", () => {
    const rows = Array.from({ length: 27 }, (_, index) => ({
      dimension: `Comercio ${index}`,
      value_minor: 2700 - index,
    }));
    const chart = (limit: number | null) => ({
      kind: "chart" as const, chart_id: "by-merchant", unit: "money" as const,
      spec: {
        mark: { type: "bar" },
        encoding: {
          y: { field: "dimension", type: "nominal" },
          x: { field: "value_minor", type: "quantitative" },
        },
      },
      data: rows, limit,
    });
    expect(chartSample(chart(8))).toBe("Se muestran los 8 mayores de 27.");
    // Nothing hidden, nothing said: a caveat that is false is worse than none, because it makes
    // the true ones unbelievable. Same rule the movements caveat already follows.
    expect(chartSample(chart(null))).toBeNull();
    expect(chartSample(chart(27))).toBeNull();
    expect(chartSample({ ...chart(8), data: rows.slice(0, 5) })).toBeNull();
    expect(chartSample({ kind: "kpi", value_field: "value_minor", unit: "money", data: [] })).toBeNull();

    // And a limit the service could not have meant is refused at the boundary rather than drawn.
    const answered = (limit: unknown) => ({
      ...answer({ visualization: chart(8) }),
      visualization: { ...chart(8), limit },
    });
    expect(validateVoiceAnswer(answered(8)).visualization?.kind).toBe("chart");
    expect(() => validateVoiceAnswer(answered(0))).toThrow();
    expect(() => validateVoiceAnswer(answered(2.5))).toThrow();
  });

  it("takes the row limit from the derivation and never from the chart it came from", () => {
    const rows = Array.from({ length: 18 }, (_, index) => ({
      dimension: `Comercio ${index}`,
      value_minor: 1800 - index,
    }));
    const spec = {
      mark: { type: "bar" },
      encoding: {
        y: { field: "dimension", type: "nominal" },
        x: { field: "value_minor", type: "quantitative" },
      },
    };
    const source = answer({
      currency: "EUR",
      visualization: {
        kind: "chart", chart_id: "chart-a", unit: "money", spec, data: rows, limit: 8,
      },
    });
    const turns: Turn[] = [{ who: "assistant", answer: source }];

    // "Ver todos" is the derivation whose whole point is that the limit is gone. Inheriting the
    // source's would leave a caveat claiming eight rows under a plot drawing all eighteen.
    const lifted = materializeVisualizationDerivation(turns, answer({
      visualization_derivation: { source_chart_id: "chart-a", chart_id: "chart-b", spec, limit: null },
    }));
    expect(lifted.visualization?.kind).toBe("chart");
    expect(chartSample(lifted.visualization ?? null)).toBeNull();

    // And a derivation that sets its own limit is believed over the source's.
    const narrowed = materializeVisualizationDerivation(turns, answer({
      visualization_derivation: { source_chart_id: "chart-a", chart_id: "chart-c", spec, limit: 5 },
    }));
    expect(chartSample(narrowed.visualization ?? null)).toBe("Se muestran los 5 mayores de 18.");
    // The evidence is still the source's, by reference.
    expect(narrowed.visualization?.data).toBe(rows);
  });

  it("materializes immutable A to B to C derivations, including a nested source", () => {
    const data = [{ key: "a", value_minor: 1 }];
    const source: Answer = { ...answer({
      currency: "EUR",
      visualization: { kind: "chart", chart_id: "chart-a", unit: "money", spec: { mark: { type: "line" } }, data },
      save_candidate: { chart_id: "chart-a", definition_token: "token-a" },
    }) };
    const deriveB: Answer = answer({
      visualization_derivation: {
        source_chart_id: "chart-a", chart_id: "chart-b", spec: { mark: { type: "bar" } },
      },
      save_candidate: { chart_id: "chart-b", definition_token: "token-b" },
    });
    const turns: Thread["turns"] = [{
      who: "assistant",
      answer: answer({ parts: [source] }),
    }];
    const renderedB = materializeVisualizationDerivation(turns, deriveB);
    if (renderedB.visualization?.kind !== "chart") throw new Error("expected chart B");
    if (source.visualization?.kind !== "chart") throw new Error("expected chart A");
    expect(renderedB.visualization.data).toBe(data);
    expect(renderedB.currency).toBe("EUR");
    expect(source.visualization.chart_id).toBe("chart-a");
    expect(source.visualization.spec).toEqual({ mark: { type: "line" } });

    const withB: Thread["turns"] = [...turns, { who: "assistant", answer: renderedB }];
    const deriveC = answer({
      visualization_derivation: {
        source_chart_id: "chart-b", chart_id: "chart-c",
        spec: { mark: { type: "bar" }, encoding: { x: { sort: "descending" } } },
      },
    });
    const renderedC = materializeVisualizationDerivation(withB, deriveC);
    if (renderedC.visualization?.kind !== "chart") throw new Error("expected chart C");
    expect(renderedC.visualization.chart_id).toBe("chart-c");
    expect(renderedC.visualization.data).toBe(data);
    expect(renderedB.visualization.chart_id).toBe("chart-b");
    expect(renderedB.visualization.spec).toEqual({ mark: { type: "bar" } });
  });

  it("fails legibly when a derivation source is absent without changing history", () => {
    const turns: Thread["turns"] = [];
    const missing = answer({ visualization_derivation: {
      source_chart_id: "missing", chart_id: "new-chart", spec: {},
    } });
    expect(() => materializeVisualizationDerivation(turns, missing)).toThrow("visualization_source_missing");
    expect(turns).toEqual([]);
  });

  it("sends metric sources in active context order and caps long derivation chains", () => {
    const turns: Thread["turns"] = Array.from({ length: 7 }, (_, index) => ({
      who: "assistant" as const,
      answer: answer({ save_candidate: {
        chart_id: `chart-${index}`, definition_token: `token-${index}`,
      } }),
    }));
    const conversation = state({
      visual_contexts: Array.from({ length: 5 }, (_, index) => ({
        chart_id: `chart-${index + 2}`, spec: {},
        profile: {
          fields: [], cardinality: 0, positive: false,
          mutually_exclusive: false, verified_total: false,
        },
      })),
    });
    expect(metricSources(turns, conversation).map((source) => source.chart_id)).toEqual([
      "chart-2", "chart-3", "chart-4", "chart-5", "chart-6",
    ]);
    expect((turns[0] as { who: "assistant"; answer: Answer }).answer.save_candidate?.definition_token)
      .toBe("token-0");
  });
  it("validates presented Vega specs with encoding and separate data", () => {
    const payload = answer({ visualization: {
      kind: "chart", chart_id: "chart-1", unit: "money", spec: { mark: { type: "line" }, encoding: { x: { field: "key", type: "temporal" } } },
      data: [{ key: "lunes", value_minor: 1 }],
    } });
    expect(validateVoiceAnswer(payload).visualization).toBeDefined();
  });
  it("validates value-free additive visualization derivations", () => {
    const derivation = {
      source_chart_id: "chart-a",
      chart_id: "chart-b",
      spec: { mark: { type: "bar" }, encoding: { x: { field: "dimension", type: "nominal" } } },
    };
    expect(validateVoiceAnswer(answer({ visualization_derivation: derivation })).visualization_derivation)
      .toEqual(derivation);
    expect(() => validateVoiceAnswer(answer({ visualization_derivation: {
      ...derivation, chart_id: "chart-a",
    } }))).toThrow();
    expect(() => validateVoiceAnswer(answer({ visualization_derivation: {
      ...derivation, spec: { ...derivation.spec, data: { values: [{ amount: 10 }] } },
    } }))).toThrow();
  });
  it("rejects a chart whose encoding names a field absent from its rows", () => {
    const payload = answer({ visualization: {
      kind: "chart",
      chart_id: "chart-1",
      unit: "count",
      spec: {
        mark: { type: "line" },
        encoding: { x: { field: "missing", type: "temporal" } },
      },
      data: [{ key: "lunes", value_minor: 1 }],
    } });
    expect(() => validateVoiceAnswer(payload)).toThrow("Respuesta de voz inválida.");
  });
  it("accepts a KPI bound to its backend value", () => {
    const payload = answer({ visualization: {
      kind: "kpi",
      value_field: "value_minor",
      unit: "money",
      data: [{ dimension: "total", value_minor: 1234 }],
    } });
    expect(validateVoiceAnswer(payload).visualization?.kind).toBe("kpi");
    const markup = renderToStaticMarkup(createElement(Chart, {
      visualization: payload.visualization ?? null,
      currency: payload.currency,
    }));
    expect(markup).toContain("12,34");
    expect(markup).not.toContain("canvas");
  });
  it("formats an average from value_minor without recalculating total over count", () => {
    const payload = answer({
      currency: "EUR",
      groups: [{ key: "Comida", total_minor: 100, movement_count: 4, value_minor: 30 }],
      conversation: state({ last_plan: { ...LAST_MONTH, metric: "AVERAGE" } }),
    });
    expect(groupFigure(payload, payload.groups[0]!)).toContain("0,30");
  });
  it("validates the complete voice answer, including groups and parts", () => {
    const payload = answer({
      text: "Dos resultados",
      groups: [{ key: "Comida", total_minor: 1200, movement_count: 2 }],
      parts: [answer({ text: "Parte uno" })],
    });
    expect(validateVoiceAnswer(payload)).toBe(payload);
    expect(() => validateVoiceAnswer({ ...payload, groups: [{ key: "incompleto" }] })).toThrow();
    expect(() => validateVoiceAnswer({ ...payload, conversation: [] })).toThrow();
    expect(() => validateVoiceAnswer({ ...payload, total_minor: Number.NaN })).toThrow();
  });

  it("renders a count by merchant as movement counts, not monetary totals", () => {
    const countPlan = { ...LAST_MONTH, group_by: "MERCHANT", metric: "COUNT" };
    const counted = thread({
      turns: [
        {
          who: "assistant",
          answer: answer({
            text: "8 movimientos.",
            currency: "EUR",
            conversation: state({ last_plan: countPlan }),
            groups: [{ key: "Mercadona", total_minor: 48_945, movement_count: 8 }],
          }),
        },
      ],
    });

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: counted,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );

    expect(markup).toContain("Mercadona");
    expect(markup).toContain("8 movimientos");
    expect(markup).not.toContain("489,45");
  });
});
