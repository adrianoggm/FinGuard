export const VOICE_SUBPROTOCOL = "finguard.voice.v1";
export const VOICE_INPUT_RATE = 16_000;
export const VOICE_OUTPUT_RATE = 24_000;
// How far ahead of the clock the first frame of a turn is scheduled. Enough to absorb the
// jitter of frames arriving over a socket, small enough that nobody waits for it.
const PLAYBACK_LEAD_SECONDS = 0.06;
// W-5: how long a turn may wait, after the holder stops talking, for its own audio.started
// before the client gives up. Without this a turn whose answer never produced any audio (a
// refusal that forgot to synthesise, a dead TTS provider) left `maybeFinish` waiting forever on
// `audioCompleted`/`playerIdle`, which only an actual `audio.started`/`audio.completed` pair can
// ever set -- the status stuck on "processing", the button stuck on "Interrumpir y hablar", and
// the mic never released. Chosen generously against the pipeline this app fronts (ASR, planning,
// generation, TTS -- seconds, not milliseconds), not measured against a production P95.
export const SILENT_TURN_TIMEOUT_MS = 15_000;
export const SILENT_TURN_MESSAGE =
  "El asistente no ha respondido con voz a tiempo. Inténtalo de nuevo.";

export type VoiceStatus =
  | "idle"
  | "connecting"
  | "listening"
  | "processing"
  | "speaking"
  | "error";

export type VoiceEvent = {
  type: string;
  sessionId: string;
  sequence: number;
  at: string;
  turnId?: string;
  [key: string]: unknown;
};

type Socket = Pick<WebSocket, "send" | "close" | "binaryType" | "readyState"> & {
  onopen: ((event?: Event) => void) | null;
  onerror: ((event?: Event) => void) | null;
  onclose: ((event?: CloseEvent) => void) | null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
};

type SocketFactory = (url: string, protocols: string[]) => Socket;
type UrlApi = Pick<typeof URL, "createObjectURL" | "revokeObjectURL">;

export function pcm16le(samples: Float32Array): ArrayBuffer {
  const output = new ArrayBuffer(samples.length * 2);
  const view = new DataView(output);

  for (let index = 0; index < samples.length; index += 1) {
    const raw = samples[index] ?? 0;
    const sample = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0;
    const value = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
    view.setInt16(index * 2, value, true);
  }

  return output;
}

export function mono(left: Float32Array, right?: Float32Array): Float32Array {
  if (!right) return left.slice();

  const output = new Float32Array(Math.min(left.length, right.length));
  for (let index = 0; index < output.length; index += 1) {
    output[index] = (left[index]! + right[index]!) / 2;
  }
  return output;
}

/** Linear interpolation with phase and carry retained across arbitrary input blocks. */
export class StreamingResampler {
  private carry: number[] = [];
  private position = 0;
  private inputCount = 0;
  private outputCount = 0;

  constructor(
    private readonly fromRate: number,
    private readonly toRate = VOICE_INPUT_RATE,
  ) {
    if (
      !Number.isFinite(fromRate) ||
      fromRate <= 0 ||
      !Number.isFinite(toRate) ||
      toRate <= 0
    ) {
      throw new Error("Invalid audio sample rate");
    }
  }

  push(input: Float32Array): Float32Array {
    if (input.length === 0) return new Float32Array();
    if (this.fromRate === this.toRate) return input.slice();

    this.inputCount += input.length;
    this.carry.push(...input);
    return this.read();
  }

  flush(): Float32Array {
    return this.fromRate === this.toRate ? new Float32Array() : this.read();
  }

  private read(): Float32Array {
    const ratio = this.fromRate / this.toRate;
    const target = Math.floor((this.inputCount * this.toRate) / this.fromRate);
    const values: number[] = [];

    while (
      this.outputCount < target &&
      this.position + 1 < this.carry.length
    ) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const first = this.carry[left] ?? 0;
      const second = this.carry[left + 1] ?? first;
      values.push(first * (1 - fraction) + second * fraction);
      this.position += ratio;
      this.outputCount += 1;
    }

    const consumed = Math.max(0, Math.floor(this.position));
    this.carry = this.carry.slice(consumed);
    this.position -= consumed;
    return Float32Array.from(values);
  }
}

export function resampleMono(
  input: Float32Array,
  fromRate: number,
  toRate = VOICE_INPUT_RATE,
): Float32Array {
  const resampler = new StreamingResampler(fromRate, toRate);
  const first = resampler.push(input);
  const tail = resampler.flush();
  const output = new Float32Array(first.length + tail.length);
  output.set(first);
  output.set(tail, first.length);
  return output;
}

type AudioSource = {
  onended: (() => void) | null;
  buffer: AudioBuffer | null;
  connect(node: AudioNode): void;
  start(when?: number): void;
  stop(): void;
  disconnect(): void;
};

type PlayerContext = Pick<
  AudioContext,
  "currentTime" | "destination" | "createBuffer" | "createBufferSource" | "close"
>;

/**
 * Ordered PCM playback. end() drains; clear() cancels and drops every queued frame.
 *
 * Frames are scheduled back to back on the context clock rather than chained on the previous
 * frame's `onended`. That callback is delivered by the main-thread event loop, so chaining put a
 * scheduling turn of silence between every 20 ms of audio and an answer arrived as a run of clicks —
 * audible on any machine, and invisible to a benchmark that only records when the first frame lands.
 *
 * Scheduling ahead means several sources are live at once, so cancellation has to stop all of them:
 * barge-in that only dropped the queue would leave whatever was already scheduled still playing.
 */
export class PcmPlayer {
  private context: PlayerContext | null = null;
  private live = new Set<AudioSource>();
  private nextStart = 0;
  private streamEnded = false;
  private idleNotified = true;
  private ended?: () => void;
  private failed?: () => void;

  constructor(
    private readonly makeContext: () => PlayerContext = () =>
      new AudioContext({ sampleRate: VOICE_OUTPUT_RATE }),
  ) {}

  onIdle(callback: () => void): void {
    this.ended = callback;
  }

  onError(callback: () => void): void {
    this.failed = callback;
  }

  begin(): void {
    this.streamEnded = false;
    this.idleNotified = false;
    // A new turn starts from now, never from a schedule the previous one left behind.
    this.nextStart = 0;
  }

  enqueue(frame: ArrayBuffer): boolean {
    if (frame.byteLength === 0 || frame.byteLength % 2 !== 0 || this.streamEnded) {
      return false;
    }
    return this.schedule(frame);
  }

  end(): void {
    this.streamEnded = true;
    this.maybeIdle();
  }

  clear(): void {
    this.streamEnded = false;
    this.idleNotified = true;
    this.nextStart = 0;
    for (const source of [...this.live]) {
      this.live.delete(source);
      source.onended = null;
      try {
        source.stop();
      } catch {
        // The source may already have ended.
      }
      source.disconnect();
    }
  }

  async close(): Promise<void> {
    this.clear();
    const context = this.context;
    this.context = null;
    if (context) await context.close();
  }

  private maybeIdle(): void {
    if (this.streamEnded && this.live.size === 0 && !this.idleNotified) {
      this.idleNotified = true;
      this.ended?.();
    }
  }

  private schedule(frame: ArrayBuffer): boolean {
    try {
      this.context ??= this.makeContext();
      const samples = frame.byteLength / 2;
      const buffer = this.context.createBuffer(1, samples, VOICE_OUTPUT_RATE);
      const channel = buffer.getChannelData(0);
      const view = new DataView(frame);
      for (let index = 0; index < samples; index += 1) {
        const value = view.getInt16(index * 2, true);
        channel[index] = value < 0 ? value / 32768 : value / 32767;
      }

      const source = this.context.createBufferSource() as AudioSource;
      source.buffer = buffer;
      source.connect(this.context.destination);
      // A frame that arrives late starts now; one that arrives early waits for the frame before it
      // to finish, to the sample. The lead keeps a jittery provider from underrunning the device.
      const startAt = Math.max(this.context.currentTime + PLAYBACK_LEAD_SECONDS, this.nextStart);
      this.nextStart = startAt + samples / VOICE_OUTPUT_RATE;
      this.live.add(source);
      source.onended = () => {
        this.live.delete(source);
        source.disconnect();
        this.maybeIdle();
      };
      source.start(startAt);
      return true;
    } catch {
      this.clear();
      this.failed?.();
      return false;
    }
  }
}

export type VoiceClientOptions = {
  gatewayUrl: string;
  token: string;
  /**
   * `false` from an `assistant.text.final` callback means the chart it announced failed to
   * materialise on screen (the caller already showed why). The fence then has to treat this turn
   * as charting nothing, same as an uncharted answer -- announcing the chart it failed to render
   * would let its audio play over a bubble with no chart in it (W-2).
   */
  onEvent: (event: VoiceEvent) => void | boolean;
  onStatus: (status: VoiceStatus, error?: string) => void;
  onFinished?: () => void;
  /**
   * The conversation on screen, read when each turn starts and sent with its `input.start`. The
   * client holds it, as it does for a typed question, so a spoken turn continues whatever was
   * typed or said before it -- even on a socket opened after that (VOICE-001).
   */
  conversation?: () => unknown;
  /** Overrides `SILENT_TURN_TIMEOUT_MS`; 0 turns the timeout off. */
  silentTurnTimeoutMs?: number;
  fetch?: typeof fetch;
  websocket?: SocketFactory;
  mediaDevices?: Pick<MediaDevices, "getUserMedia">;
  captureContext?: () => AudioContext;
  player?: PcmPlayer;
  uuid?: () => string;
  clock?: () => string;
  url?: UrlApi;
};

const EVENT_TYPES = new Set([
  "session.ready",
  "assistant.stage",
  "transcript.final",
  "assistant.text.final",
  "audio.started",
  // D-H-2 (fix wave): one per binary PCM frame, naming the turn it belongs to.
  "audio.frame",
  "audio.completed",
  "turn.completed",
  "response.cancelled",
  "pong",
  "error",
]);

const TURN_EVENTS = new Set([
  "assistant.stage",
  "transcript.final",
  "assistant.text.final",
  "audio.started",
  "audio.completed",
  "turn.completed",
  "response.cancelled",
]);

export class VoiceClient {
  private socket: Socket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: AudioWorkletNode | ScriptProcessorNode | null = null;
  private objectUrl: string | null = null;
  private sessionId = "";
  private turnId = "";
  private clientSequence = 0;
  private serverSequence = -1;
  private resampler: StreamingResampler | null = null;
  private ready = false;
  private closed = true;
  private failed = false;
  private commitSent = false;
  private audioStarted = false;
  private audioCompleted = false;
  private turnCompleted = false;
  private playerIdle = false;
  private finishCalled = false;
  private turnState: "idle" | "listening" | "processing" | "speaking" = "idle";
  private discardFencedAudio = false;
  private bargeInActive = false;
  // The chart_id the current turn's assistant.text.final announced, or null when it announced
  // none. Reset alongside audioStarted at every per-turn reset.
  private announcedChartId: string | null = null;
  // Whether this turn's assistant.text.final has been applied yet. audio.started names a chart
  // before that text is what "chart," if any, it drew -- so an audio.started that arrives first
  // is buffered rather than fenced against a chart identity that has not arrived yet (W-4): the
  // ordering is not itself a violation, only guessing what it would have meant would be.
  private textFinalSeen = false;
  private pendingAudioStarted: VoiceEvent | null = null;
  private pendingAudioFrames: ArrayBuffer[] = [];
  // W-3: the turn a barge-in just cancelled, until the gateway confirms it drained
  // (`response.cancelled` for that turn id -- sent synchronously on the server's own receipt of
  // this turn's `input.start`, well before its TTS pipeline could produce a straggler). A
  // straggler PCM frame carries no turn identity of its own, so the fence cannot filter it by
  // inspecting the frame; instead it holds the *new* turn's own audio.started buffered until the
  // old one is confirmed gone, closing the window a stray frame from the old turn's task could
  // otherwise land in.
  private drainPending: string | null = null;
  // D-H-2 (fix wave): every binary PCM frame the gateway sends is now preceded by its own
  // `audio.frame` event naming the turn it belongs to -- read here and consumed by the very next
  // `ArrayBuffer` message (WebSocket delivery is ordered, so the pairing is exact). `null` means
  // no metadata has been seen for the next frame: an older gateway, or a test built before this
  // fix, that never sends `audio.frame` at all -- treated as unlabelled rather than foreign, so
  // this stays additive rather than a breaking protocol change for anything that does not opt in.
  // A frame whose named turn is not the current one is dropped for certain, replacing W-3's
  // ordering-only mitigation (still left in place below, now redundant but harmless): the old
  // mechanism could only fence a straggler while `discardFencedAudio` stayed armed; this one
  // drops it by name even after the new turn's own audio is already playing.
  private pendingFrameTurnId: string | null = null;
  // W-5: armed once the holder stops talking, cleared the moment this turn's own audio actually
  // starts or the turn otherwise finishes. If neither happens in time, the turn is failed with a
  // visible message instead of leaving the session stuck on "processing" forever.
  private silentTurnTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly player: PcmPlayer;

  constructor(private readonly options: VoiceClientOptions) {
    this.player = options.player ?? new PcmPlayer();
    this.player.onIdle(() => {
      this.playerIdle = true;
      this.maybeFinish();
    });
    this.player.onError(() => {
      void this.fail("No se pudo reproducir el audio de voz.");
    });
  }

  async start(): Promise<void> {
    if (!this.closed) {
      if (this.turnState === "speaking" || this.turnState === "processing") {
        await this.beginBargeIn();
      }
      return;
    }

    this.resetForStart();
    this.options.onStatus("connecting");

    try {
      const base = this.options.gatewayUrl.replace(/\/$/, "");
      const response = await (this.options.fetch ?? fetch)(
        `${base}/v1/voice/sessions`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${this.options.token}` },
        },
      );

      if (!response.ok) throw new Error("El gateway rechazó la sesión de voz.");

      const bootstrap = validateBootstrap(await response.json());
      this.sessionId = bootstrap.sessionId;
      const wsUrl = validateWebSocketUrl(bootstrap.websocketUrl, base);

      await this.open(wsUrl, bootstrap.ticket);
      const media = this.options.mediaDevices ?? navigator.mediaDevices;
      this.stream = await media.getUserMedia({
        // autoGainControl matters most of the three: a quiet or distant microphone is what makes a
        // recogniser hallucinate a word into a silence, and it arrives as a confident transcript.
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      // Ask for a context already at the rate the gateway wants: the browser then resamples the
      // microphone with its own polyphase filter instead of leaving it to StreamingResampler, which
      // interpolates without an anti-aliasing stage. The resampler stays for devices that ignore the
      // hint — it short-circuits to a passthrough when the rates already agree.
      this.context = (this.options.captureContext ??
        (() => new AudioContext({ sampleRate: VOICE_INPUT_RATE })))();

      // The capture source is deliberately connected only after input.start. Some browsers can
      // emit a worklet block synchronously from connect(), so the gateway must see the turn first.
      this.turnId = (this.options.uuid ?? (() => crypto.randomUUID()))();
      this.send("input.start", this.turnId);
      this.turnState = "listening";
      await this.connectCapture();
      this.options.onStatus("listening");
    } catch (error) {
      await this.fail(mapCaptureError(error));
    }
  }

  stopCapture(): void {
    if (this.closed || this.commitSent || !this.turnId) return;

    this.commitSent = true;
    this.detachCapture();
    const tail = this.resampler?.flush();
    if (tail && tail.length > 0) this.sendBinary(pcm16le(tail));
    this.send("input.commit", this.turnId);
    this.turnState = "processing";
    this.options.onStatus("processing");
    this.armSilentTurnTimeout();
  }

  cancel(): void {
    if (this.closed) return;
    this.clearSilentTurnTimeout();
    this.send("response.cancel", this.turnId);
    this.player.clear();
    this.discardFencedAudio = true;
    this.turnState = "idle";
    this.audioCompleted = true;
    this.turnCompleted = true;
    this.finishAfterCancel();
  }

  async close(): Promise<void> {
    await this.cleanup(true);
  }

  private resetForStart(): void {
    this.closed = false;
    this.failed = false;
    this.finishCalled = false;
    this.ready = false;
    this.commitSent = false;
    this.audioStarted = false;
    this.announcedChartId = null;
    this.textFinalSeen = false;
    this.pendingAudioStarted = null;
    this.pendingAudioFrames = [];
    this.pendingFrameTurnId = null;
    this.drainPending = null;
    this.clearSilentTurnTimeout();
    this.audioCompleted = false;
    this.turnCompleted = false;
    this.playerIdle = false;
    this.clientSequence = 0;
    this.serverSequence = -1;
    this.sessionId = "";
    this.turnId = "";
    this.turnState = "idle";
    this.discardFencedAudio = false;
    this.bargeInActive = false;
  }

  private async beginBargeIn(): Promise<void> {
    if (this.closed || !this.socket || this.socket.readyState !== 1 || !this.turnId) return;

    // This is deliberately synchronous before any await: audible output must stop
    // before the cancellation and the next input can be observed by the gateway.
    const previousTurn = this.turnId;
    this.player.clear();
    this.audioStarted = false;
    this.announcedChartId = null;
    this.textFinalSeen = false;
    this.pendingAudioStarted = null;
    this.pendingAudioFrames = [];
    // The gateway confirms this turn drained with its own `response.cancelled` (sent the moment
    // it sees this barge-in's `input.start`, long before the old turn's TTS loop could still be
    // producing a straggler) -- audio.started for the *new* turn waits for that confirmation.
    this.drainPending = previousTurn;
    this.clearSilentTurnTimeout();
    this.audioCompleted = false;
    this.turnCompleted = false;
    this.playerIdle = false;
    this.discardFencedAudio = true;
    this.bargeInActive = true;
    this.send("response.cancel", previousTurn);

    this.turnId = (this.options.uuid ?? (() => crypto.randomUUID()))();
    this.commitSent = false;
    this.resampler = null;
    this.send("input.start", this.turnId);
    this.turnState = "listening";
    try {
      const media = this.options.mediaDevices ?? navigator.mediaDevices;
      this.stream = await media.getUserMedia({
        // autoGainControl matters most of the three: a quiet or distant microphone is what makes a
        // recogniser hallucinate a word into a silence, and it arrives as a confident transcript.
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      await this.connectCapture();
      this.bargeInActive = false;
      this.options.onStatus("listening");
    } catch (error) {
      await this.fail(mapCaptureError(error));
    }
  }

  private async open(url: string, ticket: string): Promise<void> {
    const factory =
      this.options.websocket ??
      ((value, protocols) => new WebSocket(value, protocols) as unknown as Socket);

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const socket = factory(url, [VOICE_SUBPROTOCOL, ticket]);
      this.socket = socket;
      socket.binaryType = "arraybuffer";

      const rejectOnce = (error: Error) => {
        if (!settled) {
          settled = true;
          reject(error);
        } else {
          void this.fail(error.message);
        }
      };

      socket.onopen = () => this.send("session.configure");
      socket.onerror = () =>
        rejectOnce(new Error("No se pudo conectar con el gateway de voz."));
      socket.onclose = () => {
        if (!this.closed && !this.failed) {
          void this.fail("La conexión de voz se cerró.");
        }
      };
      socket.onmessage = (message: MessageEvent<unknown>) => {
        try {
          this.receive(message.data, () => {
            if (!settled) {
              settled = true;
              this.ready = true;
              resolve();
            }
          });
        } catch (error) {
          rejectOnce(
            error instanceof Error
              ? error
              : new Error("Respuesta de voz inválida."),
          );
        }
      };
    });
  }

  /**
   * `event.answer.visualization.chart_id` when every step of that is present and a string; a
   * restyle carries `visualization: null` and puts the new chart only in
   * `visualization_derivation.chart_id` instead (`Conversation.tsx`'s
   * `materializeVisualizationDerivation` renders that same chart), so that is read as the
   * fallback (I-1).
   */
  private static chartIdOf(value: unknown): string | null {
    if (typeof value !== "object" || value === null) return null;
    const visualization = (value as { visualization?: unknown }).visualization;
    if (typeof visualization === "object" && visualization !== null) {
      const chartId = (visualization as { chart_id?: unknown }).chart_id;
      if (typeof chartId === "string") return chartId;
    }
    const derivation = (value as { visualization_derivation?: unknown })
      .visualization_derivation;
    if (typeof derivation !== "object" || derivation === null) return null;
    const derivedChartId = (derivation as { chart_id?: unknown }).chart_id;
    return typeof derivedChartId === "string" ? derivedChartId : null;
  }

  private receive(data: unknown, onReady: () => void): void {
    if (data instanceof ArrayBuffer) {
      // D-H-2 (fix wave): the frame's own name, consumed the instant it is read so the next
      // frame starts unlabelled again until its own `audio.frame` arrives. `null` (no metadata
      // seen) is let through unchanged -- see the field's own comment for why. A named frame for
      // a turn that is not the current one is dropped for certain, before any of the ordering
      // heuristics below ever run: this is a strictly stronger check, so nothing it lets through
      // is a frame those heuristics would have refused anyway.
      const frameTurnId = this.pendingFrameTurnId;
      this.pendingFrameTurnId = null;
      if (frameTurnId !== null && frameTurnId !== this.turnId) return;
      if (this.discardFencedAudio && !this.audioStarted) return;
      if (this.pendingAudioStarted) {
        // Its own audio.started is buffered, waiting on the text that will say which chart (if
        // any) this turn is about -- nothing plays yet, but nothing already sent is dropped
        // either once that arrives and the fence is actually checked (W-4).
        this.pendingAudioFrames.push(data);
        return;
      }
      if (
        !this.audioStarted ||
        this.audioCompleted ||
        !this.player.enqueue(data)
      ) {
        throw new Error("Audio binario fuera de audio.started/audio.completed.");
      }
      this.playerIdle = false;
      return;
    }

    if (typeof data !== "string") {
      throw new Error("Respuesta de voz inválida: se esperaba JSON o PCM16.");
    }

    let event: VoiceEvent;
    try {
      event = JSON.parse(data) as VoiceEvent;
    } catch {
      throw new Error("Respuesta de voz inválida: JSON incorrecto.");
    }

    validateEvent(event, this.sessionId, this.serverSequence);
    this.serverSequence = event.sequence;
    if (event.type === "session.ready") {
      if (this.ready) throw new Error("session.ready duplicado.");
      onReady();
      return;
    }
    if (!this.ready) throw new Error("El gateway no está listo.");

    if (event.type === "audio.frame") {
      // D-H-2 (fix wave): recorded for the very next `ArrayBuffer` message regardless of whose
      // turn this is -- a stale turn's own `audio.frame` must still be recorded so the binary
      // frame right behind it is recognised as foreign and dropped; silently filtering *this*
      // event by the generic turn check below (as every other turn event is) would leave the
      // frame that follows unlabelled instead, exactly the gap this fix closes.
      this.pendingFrameTurnId = typeof event.turnId === "string" ? event.turnId : null;
      return;
    }

    if (event.type === "response.cancelled" && event.turnId === this.drainPending) {
      // W-3: the gateway's own confirmation that the turn a barge-in just cancelled has fully
      // drained -- sent synchronously on the server's receipt of the new turn's `input.start`,
      // long before that old turn's TTS loop could still be producing a straggler frame. Held
      // back from the generic turnId filter below (which would otherwise discard it as late work
      // from a turn that is no longer current) because this is exactly the signal the new turn's
      // own audio.started is waiting on.
      this.drainPending = null;
      this.tryReleasePendingAudioStarted();
      return;
    }

    if (
      TURN_EVENTS.has(event.type) &&
      (typeof event.turnId !== "string" || event.turnId !== this.turnId)
    ) {
      // The server sequence is still consumed above, but late work from a
      // cancelled turn is intentionally invisible to the active turn.
      return;
    }

    const outcome = this.options.onEvent(event);

    if (event.type === "assistant.text.final") {
      // `outcome === false` is the caller reporting that the chart this text announced failed to
      // materialise (W-2): the fence must not treat the turn as charting it, or a chart's audio
      // would play over a bubble that never got one. Treated exactly like an uncharted answer.
      this.announcedChartId = outcome === false ? null : VoiceClient.chartIdOf(event.answer);
      this.textFinalSeen = true;
      this.tryReleasePendingAudioStarted();
    } else if (event.type === "audio.started") {
      if (this.textFinalSeen && this.drainPending === null) {
        this.applyAudioStarted(event);
      } else {
        // W-4/W-3: buffered, deterministically, until whichever this turn is still waiting on
        // resolves -- the text that says which chart (if any) it drew (W-4), and/or the previous
        // turn's own drain confirmation, which closes the window a straggler PCM frame from that
        // cancelled turn could otherwise land in as if it were this one's (W-3). Applied the
        // instant both are true.
        this.pendingAudioStarted = event;
      }
    } else if (event.type === "audio.completed") {
      if (!this.audioStarted || this.audioCompleted) {
        throw new Error("audio.completed fuera de orden.");
      }
      this.audioCompleted = true;
      this.player.end();
    } else if (event.type === "turn.completed") {
      this.turnCompleted = true;
      this.maybeFinish();
    } else if (event.type === "response.cancelled") {
      if (this.bargeInActive) return;
      this.player.clear();
      this.audioCompleted = true;
      this.turnCompleted = true;
      this.finishAfterCancel();
    } else if (event.type === "error") {
      void this.fail(
        typeof event.message === "string"
          ? event.message
          : "Error del gateway de voz.",
      );
    }
  }

  /** The audio.started fence and its side effects, applied once the chart it names (if any) is
   * actually known -- either immediately, or once a buffered event's text arrives (W-4). */
  private applyAudioStarted(event: VoiceEvent): void {
    // This turn has audio, so the W-5 guard no longer applies -- audio.completed/turn.completed
    // take over from here.
    this.clearSilentTurnTimeout();
    // The gateway stamps the chart the turn drew on audio.started; audio for a chart this
    // client did not just render is a protocol error, never played.
    const chartId = typeof event.chartId === "string" ? event.chartId : null;
    if (chartId !== this.announcedChartId) {
      throw new Error("Audio de otro gráfico.");
    }
    if (event.sampleRate !== VOICE_OUTPUT_RATE || this.audioStarted) {
      throw new Error("Frecuencia o estado de reproducción inválido.");
    }
    this.audioStarted = true;
    this.audioCompleted = false;
    this.playerIdle = false;
    this.player.begin();
    this.discardFencedAudio = false;
    this.bargeInActive = false;
    this.turnState = "speaking";
    this.options.onStatus("speaking");
  }

  /** Applies a buffered audio.started (and any binary already queued behind it) the moment
   * everything it was waiting on resolves -- W-4's text, W-3's drain confirmation, or both. */
  private tryReleasePendingAudioStarted(): void {
    if (!this.pendingAudioStarted || !this.textFinalSeen || this.drainPending !== null) return;
    const buffered = this.pendingAudioStarted;
    this.pendingAudioStarted = null;
    this.applyAudioStarted(buffered);
    const frames = this.pendingAudioFrames;
    this.pendingAudioFrames = [];
    for (const frame of frames) {
      if (this.audioCompleted || !this.player.enqueue(frame)) {
        throw new Error("Audio binario fuera de audio.started/audio.completed.");
      }
      this.playerIdle = false;
    }
  }

  private maybeFinish(): void {
    if (this.turnCompleted && this.audioCompleted && this.playerIdle) {
      void this.finishTurn();
    }
  }

  private finishAfterCancel(): void {
    this.playerIdle = true;
    void this.finishTurn();
  }

  private async finishTurn(): Promise<void> {
    if (this.closed || this.finishCalled) return;
    this.finishCalled = true;
    this.clearSilentTurnTimeout();
    this.turnState = "idle";
    await this.cleanup(false);
    this.options.onStatus("idle");
    this.options.onFinished?.();
  }

  private async fail(message: string): Promise<void> {
    if (this.failed) return;
    this.failed = true;
    this.clearSilentTurnTimeout();
    await this.cleanup(true);
    this.options.onStatus("error", message);
  }

  /** W-5: fails the turn, visibly, if it produces no audio at all within
   * `SILENT_TURN_TIMEOUT_MS` of the holder falling silent -- see the constant's own comment. */
  private armSilentTurnTimeout(): void {
    this.clearSilentTurnTimeout();
    const timeout = this.options.silentTurnTimeoutMs ?? SILENT_TURN_TIMEOUT_MS;
    // ponytail: off means a turn whose audio never comes stays "processing" until the button
    // interrupts it -- acceptable for trying a slow model by hand, not as a deployed setting.
    if (timeout <= 0) return;
    this.silentTurnTimer = setTimeout(() => {
      this.silentTurnTimer = null;
      void this.fail(SILENT_TURN_MESSAGE);
    }, timeout);
  }

  private clearSilentTurnTimeout(): void {
    if (this.silentTurnTimer !== null) {
      clearTimeout(this.silentTurnTimer);
      this.silentTurnTimer = null;
    }
  }

  private send(type: string, turnId?: string): void {
    if (!this.socket || this.closed || this.socket.readyState !== 1) return;
    this.socket.send(
      JSON.stringify({
        type,
        sessionId: this.sessionId,
        ...(turnId ? { turnId } : {}),
        ...(type === "input.start" && this.options.conversation
          ? { conversation: this.options.conversation() ?? null }
          : {}),
        sequence: this.clientSequence++,
        at: this.options.clock?.() ?? new Date().toISOString(),
      }),
    );
  }

  private sendBinary(frame: ArrayBuffer): void {
    if (
      frame.byteLength > 0 &&
      this.socket &&
      !this.closed &&
      this.socket.readyState === 1
    ) {
      this.socket.send(frame);
    }
  }

  private async connectCapture(): Promise<void> {
    const context = this.context!;
    this.source = context.createMediaStreamSource(this.stream!);
    this.resampler = new StreamingResampler(context.sampleRate);
    const send = (samples: Float32Array) => {
      const frame = this.resampler!.push(samples);
      if (frame.length > 0) this.sendBinary(pcm16le(frame));
    };

    if (context.audioWorklet && typeof AudioWorkletNode !== "undefined") {
      const url = this.options.url ?? URL;
      this.objectUrl = url.createObjectURL(
        new Blob(
          [
            "class Capture extends AudioWorkletProcessor { process(inputs){const c=inputs[0]?.[0];if(c)this.port.postMessage(c.slice());return true}}registerProcessor('finguard-capture',Capture);",
          ],
          { type: "application/javascript" },
        ),
      );

      try {
        await context.audioWorklet.addModule(this.objectUrl);
      } finally {
        url.revokeObjectURL(this.objectUrl);
        this.objectUrl = null;
      }

      const node = new AudioWorkletNode(context, "finguard-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
      });
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        if (event.data instanceof Float32Array) send(event.data);
      };
      this.processor = node;
      this.source.connect(node);
      return;
    }

    const node = context.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const output = event.outputBuffer.getChannelData(0);
      output.fill(0);
      send(input);
    };
    this.processor = node;
    this.source.connect(node);
    node.connect(context.destination);
  }

  private detachCapture(): void {
    if (
      typeof AudioWorkletNode !== "undefined" &&
      this.processor instanceof AudioWorkletNode
    ) {
      this.processor.port.onmessage = null;
    } else if (this.processor) {
      (this.processor as ScriptProcessorNode).onaudioprocess = null;
    }
    this.processor?.disconnect();
    this.processor = null;
    this.source?.disconnect();
    this.source = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  private async cleanup(sendClose: boolean): Promise<void> {
    this.clearSilentTurnTimeout();
    if (this.closed && !this.socket && !this.context && !this.stream) return;

    const socket = this.socket;
    if (sendClose && socket?.readyState === 1) {
      try {
        socket.send(
          JSON.stringify({
            type: "session.close",
            sessionId: this.sessionId,
            sequence: this.clientSequence++,
            at: this.options.clock?.() ?? new Date().toISOString(),
          }),
        );
      } catch {
        // Cleanup continues even if the peer has gone away.
      }
    }

    this.closed = true;
    this.detachCapture();
    if (this.objectUrl) {
      (this.options.url ?? URL).revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }

    const context = this.context;
    this.context = null;
    if (context) await context.close();
    socket?.close();
    this.socket = null;
    await this.player.close();
  }
}

function validateBootstrap(value: unknown): {
  sessionId: string;
  ticket: string;
  websocketUrl: string;
} {
  if (!value || typeof value !== "object") {
    throw new Error("Bootstrap de voz inválido.");
  }
  const body = value as Record<string, unknown>;
  if (
    [body.sessionId, body.ticket, body.websocketUrl].some(
      (field) => typeof field !== "string" || field.length === 0,
    )
  ) {
    throw new Error("Bootstrap de voz inválido.");
  }
  return {
    sessionId: body.sessionId as string,
    ticket: body.ticket as string,
    websocketUrl: body.websocketUrl as string,
  };
}

function validateWebSocketUrl(websocketUrl: string, gatewayUrl: string): string {
  const gateway = new URL(gatewayUrl);
  const expectedProtocol = gateway.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(websocketUrl, gatewayUrl.replace(/^http/, "ws"));
  if (
    url.protocol !== expectedProtocol ||
    url.hostname !== gateway.hostname ||
    url.port !== gateway.port ||
    url.search ||
    url.hash ||
    /ticket|token/i.test(url.toString())
  ) {
    throw new Error("La URL WebSocket de voz no es segura.");
  }
  return url.toString();
}

function validateEvent(
  event: VoiceEvent,
  sessionId: string,
  previous: number,
): void {
  if (
    !event ||
    typeof event !== "object" ||
    !EVENT_TYPES.has(event.type) ||
    event.sessionId !== sessionId ||
    !Number.isInteger(event.sequence) ||
    event.sequence !== previous + 1 ||
    typeof event.at !== "string"
  ) {
    throw new Error("Respuesta de voz inválida: sesión, secuencia o tipo incorrectos.");
  }
  if (
    event.type === "session.ready" &&
    (event.sequence !== 0 ||
      event.inputSampleRate !== VOICE_INPUT_RATE ||
      event.outputSampleRate !== VOICE_OUTPUT_RATE)
  ) {
    throw new Error("session.ready inválido.");
  }
  if (
    (event.type === "transcript.final" || event.type === "assistant.text.final") &&
    typeof event.text !== "string"
  ) {
    throw new Error("Evento de texto inválido.");
  }
  if (event.type === "error" && typeof event.code !== "string") {
    throw new Error("Evento de error inválido.");
  }
  if (event.type === "audio.frame" && typeof event.turnId !== "string") {
    throw new Error("Evento audio.frame inválido.");
  }
}

function mapCaptureError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") {
      return "Permiso de micrófono denegado. Actívalo para usar la voz.";
    }
    if (error.name === "NotFoundError") {
      return "No se encontró ningún micrófono. Conecta uno para usar la voz.";
    }
    if (error.name === "NotReadableError") {
      return "El micrófono no está disponible. Cierra otras aplicaciones que lo estén usando.";
    }
  }
  return error instanceof Error ? error.message : "No se pudo iniciar la voz.";
}
