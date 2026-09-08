export const VOICE_SUBPROTOCOL = "finguard.voice.v1";
export const VOICE_INPUT_RATE = 16_000;
export const VOICE_OUTPUT_RATE = 24_000;
// How far ahead of the clock the first frame of a turn is scheduled. Enough to absorb the
// jitter of frames arriving over a socket, small enough that nobody waits for it.
const PLAYBACK_LEAD_SECONDS = 0.06;

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
  onEvent: (event: VoiceEvent) => void;
  onStatus: (status: VoiceStatus, error?: string) => void;
  onFinished?: () => void;
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
  }

  cancel(): void {
    if (this.closed) return;
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

  /** `event.answer.visualization.chart_id` when every step of that is present and a string. */
  private static chartIdOf(value: unknown): string | null {
    if (typeof value !== "object" || value === null) return null;
    const visualization = (value as { visualization?: unknown }).visualization;
    if (typeof visualization !== "object" || visualization === null) return null;
    const chartId = (visualization as { chart_id?: unknown }).chart_id;
    return typeof chartId === "string" ? chartId : null;
  }

  private receive(data: unknown, onReady: () => void): void {
    if (data instanceof ArrayBuffer) {
      if (this.discardFencedAudio && !this.audioStarted) return;
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

    if (
      TURN_EVENTS.has(event.type) &&
      (typeof event.turnId !== "string" || event.turnId !== this.turnId)
    ) {
      // The server sequence is still consumed above, but late work from a
      // cancelled turn is intentionally invisible to the active turn.
      return;
    }

    this.options.onEvent(event);

    if (event.type === "assistant.text.final") {
      this.announcedChartId = VoiceClient.chartIdOf(event.answer);
    } else if (event.type === "audio.started") {
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
    this.turnState = "idle";
    await this.cleanup(false);
    this.options.onStatus("idle");
    this.options.onFinished?.();
  }

  private async fail(message: string): Promise<void> {
    if (this.failed) return;
    this.failed = true;
    await this.cleanup(true);
    this.options.onStatus("error", message);
  }

  private send(type: string, turnId?: string): void {
    if (!this.socket || this.closed || this.socket.readyState !== 1) return;
    this.socket.send(
      JSON.stringify({
        type,
        sessionId: this.sessionId,
        ...(turnId ? { turnId } : {}),
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
