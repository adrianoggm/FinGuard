import { describe, expect, it, vi } from "vitest";
import { mono, pcm16le, PcmPlayer, resampleMono, StreamingResampler, VoiceClient, VOICE_SUBPROTOCOL } from "./voice";

const json = (value: unknown) => JSON.stringify(value);
const event = (type: string, sessionId: string, sequence: number, fields: Record<string, unknown> = {}) => json({ type, sessionId, sequence, at: `2026-08-17T00:00:0${sequence}.000Z`, ...fields });

function socketFake() {
  const sent: Array<string | ArrayBuffer> = [];
  const socket = { readyState: 1, binaryType: "blob" as BinaryType, onopen: null as (() => void) | null, onerror: null as (() => void) | null, onclose: null as (() => void) | null, onmessage: null as ((message: MessageEvent<unknown>) => void) | null, send(value: string | ArrayBuffer) { sent.push(value); }, close() { this.readyState = 3; this.onclose?.(); } };
  return { socket, sent, open: () => socket.onopen?.(), message: (data: unknown) => socket.onmessage?.({ data } as MessageEvent<unknown>), fail: () => socket.onerror?.() };
}

function captureContext(sampleRate = 48_000, onConnect?: () => void) {
  const stats = { sourceConnects: 0, sourceDisconnects: 0, processorConnects: 0, processorDisconnects: 0 };
  let processor: { onaudioprocess: ((event: { inputBuffer: { getChannelData: (channel: number) => Float32Array }; outputBuffer: { getChannelData: (channel: number) => Float32Array } }) => void) | null; connect: () => void; disconnect: () => void } | null = null;
  const context = {
    sampleRate,
    audioWorklet: undefined,
    destination: {},
    createMediaStreamSource: () => ({
      connect: () => { stats.sourceConnects += 1; onConnect?.(); processor?.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(160).fill(0.25) }, outputBuffer: { getChannelData: () => new Float32Array(160) } }); },
      disconnect: () => { stats.sourceDisconnects += 1; },
    }),
    createScriptProcessor: () => { processor = {
      onaudioprocess: null,
      connect: () => { stats.processorConnects += 1; },
      disconnect: () => { stats.processorDisconnects += 1; },
    }; return processor; },
    close: async () => undefined,
  };
  return { context, stats } as unknown as { context: AudioContext; stats: typeof stats };
}

function playbackContext() {
  const sources: Array<{ onended: (() => void) | null; buffer: AudioBuffer | null; connect: () => void; start: () => void; stop: () => void; disconnect: () => void }> = [];
  const context = { destination: {}, createBuffer: (_channels: number, length: number) => ({ getChannelData: () => new Float32Array(length) }), createBufferSource: () => { const source = { onended: null as (() => void) | null, buffer: null as AudioBuffer | null, connect: () => undefined, start: () => undefined, stop: () => source.onended?.(), disconnect: () => undefined }; sources.push(source); return source; }, close: async () => undefined } as unknown as AudioContext;
  return { context, sources };
}

function mediaFake() {
  let stopped = 0;
  const track = { stop: () => { stopped += 1; } } as MediaStreamTrack;
  return { media: { getUserMedia: async () => ({ getTracks: () => [track] }) } as unknown as MediaDevices, stopped: () => stopped };
}

describe("DSP contract K", () => {
  it("encodes PCM16 LE with clamp and finite handling", () => expect([...new Uint8Array(pcm16le(new Float32Array([-1, 0, 1, 2, Number.NaN]))) ]).toEqual([0, 128, 0, 0, 255, 127, 255, 127, 0, 0]));
  it("mixes mono and resamples 44.1/48 kHz across blocks without empty frames", () => {
    expect([...mono(new Float32Array([1, -1]), new Float32Array([-1, 1]))]).toEqual([0, 0]);
    expect([...resampleMono(new Float32Array([0, 0.5, 1, 1]), 4, 2)]).toEqual([0, 1]);
    for (const rate of [44_100, 48_000]) { const r = new StreamingResampler(rate); const blocks = Array.from({ length: 9 }, () => r.push(new Float32Array(rate === 44_100 ? 147 : 160).fill(0.25))); const output = [...blocks, r.flush()].filter((frame) => frame.length > 0); expect(output.every((frame) => frame.length > 0)).toBe(true); expect(output.reduce((n, frame) => n + frame.length, 0)).toBe(480); }
  });
});

describe("PcmPlayer contract G", () => {
  it("keeps order, drains normally and clear cancels without idle", async () => {
    const fake = playbackContext(); let idle = 0; const player = new PcmPlayer(() => fake.context); player.onIdle(() => { idle += 1; }); player.begin(); player.enqueue(new Uint8Array([0, 0]).buffer); player.enqueue(new Uint8Array([255, 127]).buffer); player.end(); expect(fake.sources).toHaveLength(1); fake.sources[0]!.onended?.(); expect(fake.sources).toHaveLength(2); expect(idle).toBe(0); fake.sources[1]!.onended?.(); await Promise.resolve(); expect(idle).toBe(1); player.begin(); player.enqueue(new ArrayBuffer(2)); player.clear(); expect(idle).toBe(1); await player.close();
  });
});

describe("VoiceClient transport A-J", () => {
  function makeClient(uuid: (() => string) = () => "turn-1") {
    const fakeSocket = socketFake(); const media = mediaFake(); const order: string[] = []; const capture = captureContext(48_000, () => { order.push(typeof fakeSocket.sent.at(-1) === "string" ? JSON.parse(fakeSocket.sent.at(-1) as string).type : "binary"); }); const playback = playbackContext(); const statuses: string[] = []; const finished: string[] = []; const fetchCalls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const client = new VoiceClient({ gatewayUrl: "http://gateway.test", token: "secret", fetch: async (input, init) => { fetchCalls.push({ input, init }); return new Response(json({ sessionId: "s-1", ticket: "opaque-ticket", websocketUrl: "/v1/voice/sessions/s-1" }), { status: 200, headers: { "content-type": "application/json" } }); }, websocket: (_url, protocols) => { expect(protocols).toEqual([VOICE_SUBPROTOCOL, "opaque-ticket"]); expect(_url).not.toContain("ticket"); return fakeSocket.socket; }, mediaDevices: media.media, captureContext: () => capture.context, player: new PcmPlayer(() => playback.context), uuid, clock: () => "2026-08-17T00:00:00.000Z", onEvent: () => undefined, onStatus: (status, error) => { statuses.push(error ? `${status}:${error}` : status); }, onFinished: () => { finished.push("finished"); } });
    return { client, fakeSocket, media, capture, playback, statuses, finished, order, fetchCalls };
  }
  async function ready() { const setup = makeClient(); const promise = setup.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); setup.fakeSocket.open(); expect(setup.fakeSocket.sent).toHaveLength(1); setup.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await promise; expect(setup.statuses).toEqual(["connecting", "listening"]); expect(JSON.parse(setup.fakeSocket.sent[1] as string).type).toBe("input.start"); return setup; }

  it("uses POST Bearer auth, keeps ticket in subprotocol, and sends start before capture binary", async () => {
    const setup = makeClient(); const promise = setup.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); setup.fakeSocket.open(); expect(setup.fetchCalls).toHaveLength(1); expect(setup.fetchCalls[0]!.init?.method).toBe("POST"); expect((setup.fetchCalls[0]!.init?.headers as Record<string, string>).Authorization).toBe("Bearer secret"); expect(setup.fakeSocket.sent[0]).toBeTypeOf("string"); setup.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await promise; expect(setup.order).toEqual(["input.start"]); const captureMessages = setup.fakeSocket.sent.slice(1); expect(JSON.parse(captureMessages[0] as string).type).toBe("input.start"); expect(captureMessages[1]).toBeInstanceOf(ArrayBuffer); setup.client.stopCapture(); setup.client.stopCapture(); const controls = setup.fakeSocket.sent.filter((value): value is string => typeof value === "string").map((value) => JSON.parse(value)); expect(controls.filter((value) => value.type === "input.commit")).toHaveLength(1); expect(controls.map((value) => value.sequence)).toEqual([0, 1, 2]);
  });
  it("handles permission, HTTP and socket errors with cleanup and no throw", async () => {
    const setup = makeClient(); setup.media.media.getUserMedia = async () => { throw new DOMException("secret browser detail", "NotAllowedError"); }; const promise = setup.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); setup.fakeSocket.open(); setup.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await promise; expect(setup.statuses.at(-1)).toBe("error:Permiso de micrófono denegado. Actívalo para usar la voz."); expect(setup.statuses.join(" ")).not.toContain("secret browser detail"); expect(setup.fakeSocket.socket.readyState).toBe(3);
    const missing = makeClient(); missing.media.media.getUserMedia = async () => { throw new DOMException("device path", "NotFoundError"); }; const missingPromise = missing.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); missing.fakeSocket.open(); missing.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await missingPromise; expect(missing.statuses.at(-1)).toBe("error:No se encontró ningún micrófono. Conecta uno para usar la voz.");
    const statuses: string[] = []; const client = new VoiceClient({ gatewayUrl: "http://gateway.test", token: "x", fetch: async () => new Response("", { status: 503 }), onEvent: () => undefined, onStatus: (s) => statuses.push(s) }); await client.start(); expect(statuses.at(-1)).toBe("error");
    const failed = makeClient(); const failedPromise = failed.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); failed.fakeSocket.fail(); await failedPromise; expect(failed.statuses.at(-1)).toMatch(/^error/);
  });
  it("rejects session.ready with wrong rates and cleans the bootstrap socket", async () => {
    for (const rates of [{ inputSampleRate: 8_000, outputSampleRate: 24_000 }, { inputSampleRate: 16_000, outputSampleRate: 48_000 }]) { const setup = makeClient(); const promise = setup.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); setup.fakeSocket.open(); setup.fakeSocket.message(event("session.ready", "s-1", 0, rates)); await promise; expect(setup.statuses.at(-1)).toMatch(/^error/); expect(setup.fakeSocket.socket.readyState).toBe(3); }
  });
  it("turns malformed JSON, session, sequence and type failures after ready into error+close", async () => {
    for (const bad of ["{", event("pong", "wrong", 0), event("pong", "s-1", 2), event("unknown", "s-1", 0)]) { const setup = await ready(); setup.fakeSocket.message(bad); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(setup.statuses.at(-1)).toMatch(/^error/); expect(setup.fakeSocket.socket.readyState).toBe(3); }
  });
  it("rejects an absolute websocket URL on another host before offering the ticket", async () => {
    const socket = socketFake(); const statuses: string[] = []; const client = new VoiceClient({ gatewayUrl: "http://gateway.test", token: "secret", fetch: async () => new Response(json({ sessionId: "s-1", ticket: "opaque", websocketUrl: "ws://evil.test/v1/voice/sessions/s-1" })), websocket: () => { throw new Error("socket must not be created"); }, onEvent: () => undefined, onStatus: (status) => statuses.push(status) }); await client.start(); expect(statuses.at(-1)).toBe("error"); expect(socket.socket.readyState).toBe(1);
  });
  it("accepts binary only between audio events and closes after either completion order", async () => {
    for (const reverse of [false, true]) { const setup = await ready(); setup.client.stopCapture(); setup.fakeSocket.message(event("audio.started", "s-1", 1, { turnId: "turn-1", sampleRate: 24_000 })); setup.fakeSocket.message(new Uint8Array([0, 0]).buffer); if (reverse) setup.fakeSocket.message(event("turn.completed", "s-1", 2, { turnId: "turn-1" })); setup.fakeSocket.message(event("audio.completed", "s-1", reverse ? 3 : 2, { turnId: "turn-1" })); if (!reverse) setup.fakeSocket.message(event("turn.completed", "s-1", 3, { turnId: "turn-1" })); expect(setup.finished).toHaveLength(0); setup.playback.sources[0]!.onended?.(); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(setup.finished).toHaveLength(1); expect(setup.fakeSocket.socket.readyState).toBe(3); }
  });
  it("clears active playback on response.cancelled/error and never starts the queued second frame", async () => {
    const cancelled = await ready(); cancelled.client.stopCapture(); cancelled.fakeSocket.message(event("audio.started", "s-1", 1, { turnId: "turn-1", sampleRate: 24_000 })); cancelled.fakeSocket.message(new Uint8Array([0, 0]).buffer); cancelled.fakeSocket.message(new Uint8Array([1, 0]).buffer); cancelled.fakeSocket.message(event("response.cancelled", "s-1", 2, { turnId: "turn-1" })); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(cancelled.playback.sources).toHaveLength(1); expect(cancelled.finished).toHaveLength(1); expect(cancelled.statuses.at(-1)).toBe("idle");
    const failed = await ready(); failed.client.stopCapture(); failed.fakeSocket.message(event("audio.started", "s-1", 1, { turnId: "turn-1", sampleRate: 24_000 })); failed.fakeSocket.message(new Uint8Array([0, 0]).buffer); failed.fakeSocket.message(new Uint8Array([1, 0]).buffer); failed.fakeSocket.message(event("error", "s-1", 2, { code: "PROVIDER_ERROR", message: "provider failed", fatal: true })); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(failed.playback.sources).toHaveLength(1); expect(failed.statuses.at(-1)).toBe("error:provider failed"); expect(failed.fakeSocket.socket.readyState).toBe(3);
  });
  it("rejects binary before audio.started and cleans every resource idempotently", async () => {
    const setup = await ready(); setup.fakeSocket.message(new ArrayBuffer(2)); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(setup.statuses.at(-1)).toMatch(/^error/); expect(setup.fakeSocket.socket.readyState).toBe(3); await setup.client.close(); await setup.client.close(); expect(setup.media.stopped()).toBe(1); expect(setup.capture.stats.sourceDisconnects).toBe(1); expect(setup.capture.stats.processorDisconnects).toBe(1);
  });
  it("closes worklet URL, context, source, tracks and socket exactly once", async () => {
    const setup = makeClient(); const base = setup.capture.context; let contextsClosed = 0; let workletNode: { port: { onmessage: ((event: MessageEvent<Float32Array>) => void) | null }; disconnects: number } | null = null; const context = { ...base, audioWorklet: { addModule: async () => undefined }, close: async () => { contextsClosed += 1; } } as unknown as AudioContext; const revoked: string[] = []; const urls = { createObjectURL: () => "blob:capture", revokeObjectURL: (value: string) => revoked.push(value) }; vi.stubGlobal("AudioWorkletNode", class { port = { onmessage: null as ((event: MessageEvent<Float32Array>) => void) | null }; disconnects = 0; constructor() { workletNode = this; } connect() { /* fake */ } disconnect() { this.disconnects += 1; } });
    const client = new VoiceClient({ gatewayUrl: "http://gateway.test", token: "x", fetch: async () => new Response(json({ sessionId: "s-1", ticket: "t", websocketUrl: "/v1/voice/sessions/s-1" })), websocket: () => setup.fakeSocket.socket, mediaDevices: setup.media.media, captureContext: () => context, player: new PcmPlayer(() => setup.playback.context), url: urls, onEvent: () => undefined, onStatus: () => undefined }); const promise = client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); setup.fakeSocket.open(); setup.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await promise; await client.close(); await client.close(); expect(revoked).toEqual(["blob:capture"]); expect(contextsClosed).toBe(1); expect(setup.media.stopped()).toBe(1); expect(setup.capture.stats.sourceDisconnects).toBe(1); const releasedWorklet = workletNode as { port: { onmessage: unknown }; disconnects: number } | null; expect(releasedWorklet?.port.onmessage).toBeNull(); expect(releasedWorklet?.disconnects).toBe(1); expect(setup.fakeSocket.socket.readyState).toBe(3); vi.unstubAllGlobals();
  });
  it("cancels playback as a one-shot and reports idle once", async () => { const setup = await ready(); setup.client.cancel(); for (let i = 0; i < 8; i += 1) await Promise.resolve(); expect(setup.finished).toHaveLength(1); expect(setup.statuses.at(-1)).toBe("idle"); expect(setup.fakeSocket.socket.readyState).toBe(3); });
  it("barge-in stops playback synchronously and orders cancel before the new start", async () => {
    let next = 0;
    const setup = await (async () => { const value = makeClient(() => `turn-${++next}`); const promise = value.client.start(); for (let i = 0; i < 10; i += 1) await Promise.resolve(); value.fakeSocket.open(); value.fakeSocket.message(event("session.ready", "s-1", 0, { inputSampleRate: 16_000, outputSampleRate: 24_000 })); await promise; return value; })();
    setup.client.stopCapture();
    setup.fakeSocket.message(event("audio.started", "s-1", 1, { turnId: "turn-1", sampleRate: 24_000 }));
    setup.fakeSocket.message(new Uint8Array([0, 0]).buffer);
    const before = setup.fakeSocket.sent.length;
    await setup.client.start();
    expect(setup.fakeSocket.sent.slice(before, before + 2).map((value) => typeof value === "string" ? JSON.parse(value).type : "binary")).toEqual(["response.cancel", "input.start"]);
    expect(setup.playback.sources).toHaveLength(1);
    setup.fakeSocket.message(event("response.cancelled", "s-1", 2, { turnId: "turn-1" }));
    setup.fakeSocket.message(event("assistant.text.final", "s-1", 3, { turnId: "turn-1", text: "stale" }));
    expect(setup.statuses.at(-1)).toBe("listening");
  });
});
