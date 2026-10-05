/**
 * WebSocket bridge for one STM32 simulation instance.
 * GPIO pins use a linear port*16+pin index (PC13 = 45).
 */

import { showConfirmDialog } from '../store/useMessageDialogStore';
import type { BoardKind } from '../types/board';
import { generateUUID } from '../utils/uuid';
import type { LineSupport } from './line/LineHost';
import { recordPartGap } from './line/requestLine';
import { sensorRecordOwnsPin as recordOwnsPin } from './sensorModels';
import { withHostClock } from './parts/hostClock';

const API_BASE = (): string => {
  if (typeof window !== 'undefined') {
    const injected = (window as { __VELXIO_API_BASE__?: string }).__VELXIO_API_BASE__;
    if (typeof injected === 'string' && injected) return injected.replace(/\/+$/, '');
  }
  return (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8001/api';
};

export function getTabSessionId(): string {
  if (typeof sessionStorage === 'undefined') return generateUUID();
  const key = 'velxio-tab-id';
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = generateUUID();
    sessionStorage.setItem(key, id);
  }
  return id;
}

const PORT_INDEX: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4, F: 5, G: 6 };
const PORT_LETTER = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

export function stm32PinNameToLinear(name: string): number {
  const match = /^P([A-G])(\d{1,2})$/.exec(name.trim().toUpperCase());
  if (!match) return -1;
  const port = PORT_INDEX[match[1]];
  const pin = parseInt(match[2], 10);
  if (port === undefined || pin < 0 || pin > 15) return -1;
  return port * 16 + pin;
}

export function stm32LinearToPinName(linear: number): string {
  const port = Math.floor(linear / 16);
  const pin = linear % 16;
  return `P${PORT_LETTER[port] ?? '?'}${pin}`;
}

export class Stm32Bridge {
  readonly boardId: string;
  readonly boardKind: BoardKind;

  onSerialData: ((char: string, uart?: number) => void) | null = null;
  onUartTxBytes: ((uart: number, bytes: Uint8Array) => void) | null = null;
  onPinChange: ((gpioPin: number, state: boolean) => void) | null = null;
  onPinChangeWithTime: ((gpioPin: number, state: boolean, timeMs: number) => void) | null = null;
  onPinDir: ((gpioPin: number, dir: 0 | 1) => void) | null = null;
  onPinPull: ((gpioPin: number, pull: 0 | 1 | 2) => void) | null = null;
  onConnected: (() => void) | null = null;
  onDisconnected: (() => void) | null = null;
  onError: ((msg: string, code?: string) => void) | null = null;
  onSystemEvent: ((event: string, data: Record<string, unknown>) => void) | null = null;
  onCrash: ((data: Record<string, unknown>) => void) | null = null;
  onI2cTrace: ((addr: number, op: string, result: number) => void) | null = null;
  onI2cTransaction: ((addr: number, data: number[], owner?: string) => void) | null = null;
  onSpiBatch: ((bytes: Uint8Array) => void) | null = null;
  onBusMapRequest: (() => { i2c?: unknown[]; uart?: unknown[]; pulls?: unknown[] } | null) | null =
    null;

  private socket: WebSocket | null = null;
  private _connected = false;
  private _pendingFirmware: string | null = null;
  private _pendingSensors: Array<Record<string, unknown>> = [];
  private _busMap: unknown[] = [];
  private _busMapI2c: unknown[] | null = null;
  private _busMapUart: unknown[] | null = null;
  private _busMapPulls: unknown[] | null = null;
  private cleanupPromptShown = false;

  constructor(boardId: string, boardKind: BoardKind) {
    this.boardId = boardId;
    this.boardKind = boardKind;
  }

  get connected(): boolean {
    return this._connected;
  }

  get clientId(): string {
    return getTabSessionId() + '::' + this.boardId;
  }

  connect(): void {
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) return;

    const base = API_BASE();
    const wsProtocol = base.startsWith('https') ? 'wss:' : 'ws:';
    const sessionId = getTabSessionId();
    const wsUrl =
      base.replace(/^https?:/, wsProtocol) +
      `/simulation/ws/${encodeURIComponent(sessionId + '::' + this.boardId)}`;

    const socket = new WebSocket(wsUrl);
    this.socket = socket;

    socket.onopen = () => {
      this._connected = true;
      this.onConnected?.();
      this._send({
        type: 'start_stm32',
        data: {
          board: this.boardKind,
          sensors: withHostClock(this._pendingSensors),
          bus_map: this.startBusMap(),
          ...(this._pendingFirmware ? { firmware_b64: this._pendingFirmware } : {}),
        },
      });
    };

    socket.onmessage = (event: MessageEvent) => {
      let msg: { type: string; data: Record<string, unknown> };
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return;
      }

      switch (msg.type) {
        case 'serial_output': {
          const text = (msg.data.data as string) ?? '';
          const uart = msg.data.uart as number | undefined;
          if (this.onUartTxBytes) {
            const b64 = msg.data.b64;
            const raw =
              typeof b64 === 'string'
                ? Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0))
                : Uint8Array.from(text, (ch) => ch.charCodeAt(0) & 0xff);
            this.onUartTxBytes(uart ?? 0, raw);
          }
          if (this.onSerialData) for (const ch of text) this.onSerialData(ch, uart);
          break;
        }
        case 'gpio_change': {
          const pin = msg.data.pin as number;
          const state = (msg.data.state as number) === 1;
          this.onPinChange?.(pin, state);
          this.onPinChangeWithTime?.(pin, state, performance.now());
          break;
        }
        case 'gpio_dir':
          this.onPinDir?.(msg.data.pin as number, msg.data.dir as 0 | 1);
          break;
        case 'gpio_pull':
          this.onPinPull?.(msg.data.pin as number, msg.data.pull as 0 | 1 | 2);
          break;
        case 'system': {
          const evt = msg.data.event as string;
          if (evt === 'crash') this.onCrash?.(msg.data);
          if (evt === 'sensor_refused') {
            recordPartGap({
              sensorType: String(msg.data.sensor_type ?? ''),
              pin: Number(msg.data.pin ?? -1),
              why: String(msg.data.why ?? 'this board does not model it'),
              componentId: msg.data.component_id ? String(msg.data.component_id) : undefined,
            });
          }
          if (evt === 'stm32_runtime_built') void this.promptBuildCleanup(msg.data);
          this.onSystemEvent?.(evt, msg.data);
          break;
        }
        case 'i2c_transaction': {
          const addr = msg.data.addr as number;
          const data = (msg.data.data as number[]) ?? [];
          const owner = msg.data.owner ? String(msg.data.owner) : undefined;
          this.onI2cTransaction?.(addr, data, owner);
          break;
        }
        case 'i2c_trace':
          this.onI2cTrace?.(
            msg.data.addr as number,
            (msg.data.op as string) ?? '',
            (msg.data.result as number) ?? 0,
          );
          break;
        case 'spi_batch': {
          const b64 = msg.data.b64 as string;
          if (b64 && this.onSpiBatch) {
            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            this.onSpiBatch(bytes);
          }
          break;
        }
        case 'error':
          this.onError?.(msg.data.message as string, msg.data.code as string | undefined);
          break;
      }
    };

    socket.onclose = () => {
      this._connected = false;
      this.socket = null;
      this.onDisconnected?.();
    };
    socket.onerror = () => this.onError?.('WebSocket error');
  }

  disconnect(): void {
    if (this.socket) {
      this._send({ type: 'stop_stm32' });
      this.socket.close();
      this.socket = null;
    }
    this._connected = false;
  }

  hasFirmware(): boolean {
    return this._pendingFirmware !== null && this._pendingFirmware !== '';
  }

  loadFirmware(firmwareBase64: string): void {
    this._pendingFirmware = firmwareBase64;
    if (this._connected) {
      this._send({ type: 'stm32_load_firmware', data: { firmware_b64: firmwareBase64 } });
    }
  }

  lineSupport(): LineSupport {
    return { mode: 'hosted' };
  }

  ownsSensorPin(gpioPin: number): boolean {
    return this._pendingSensors.some(
      (sensor) =>
        String(sensor['sensor_type'] ?? '') === 'matrix-keypad' && recordOwnsPin(sensor, gpioPin),
    );
  }

  sendPinEvent(gpioPin: number, state: boolean): void {
    if (this.ownsSensorPin(gpioPin)) return;
    this._send({ type: 'stm32_gpio_in', data: { pin: gpioPin, state: state ? 1 : 0 } });
  }

  sendSerialBytes(bytes: number[], uart = 0): void {
    if (bytes.length === 0) return;
    this._send({ type: 'stm32_serial_input', data: { bytes, uart } });
  }

  setSensors(sensors: Array<Record<string, unknown>>): void {
    const merged = this._pendingSensors.slice();
    for (const sensor of sensors) {
      const pin = sensor['pin'];
      const index = merged.findIndex((entry) => entry['pin'] === pin);
      if (index >= 0) merged[index] = sensor;
      else merged.push(sensor);
    }
    this._pendingSensors = merged;
  }

  sendSensorAttach(sensorType: string, pin: number, properties: Record<string, unknown>): void {
    const entry = { sensor_type: sensorType, pin, ...properties };
    const existing = this._pendingSensors.findIndex((sensor) => sensor['pin'] === pin);
    if (existing >= 0) this._pendingSensors[existing] = entry;
    else this._pendingSensors.push(entry);
    if (this._connected) this._send({ type: 'stm32_sensor_attach', data: entry });
  }

  sendSensorUpdate(pin: number, properties: Record<string, unknown>): void {
    const index = this._pendingSensors.findIndex((sensor) => sensor['pin'] === pin);
    if (index >= 0) {
      this._pendingSensors[index] = { ...this._pendingSensors[index], ...properties };
    }
    this._send({ type: 'stm32_sensor_update', data: { pin, ...properties } });
  }

  sendSensorDetach(pin: number): void {
    this._pendingSensors = this._pendingSensors.filter((sensor) => sensor['pin'] !== pin);
    this._send({ type: 'stm32_sensor_detach', data: { pin } });
  }

  sendBusMap(spi: unknown[], i2c?: unknown[], uart?: unknown[]): void {
    this._busMap = spi;
    if (i2c) this._busMapI2c = i2c;
    if (uart) this._busMapUart = uart;
    if (this._connected) {
      this._send({
        type: 'stm32_bus_map',
        data: { spi, ...(i2c ? { i2c } : {}), ...(uart ? { uart } : {}) },
      });
    }
  }

  sendI2cBusMap(i2c: unknown[]): void {
    this._busMapI2c = i2c;
    if (this._connected) this._send({ type: 'stm32_bus_map', data: { i2c } });
  }

  sendUartBusMap(uart: unknown[]): void {
    this._busMapUart = uart;
    if (this._connected) this._send({ type: 'stm32_bus_map', data: { uart } });
  }

  sendPullMap(pulls: unknown[]): void {
    this._busMapPulls = pulls;
    if (this._connected) this._send({ type: 'stm32_bus_map', data: { pulls } });
  }

  sendBusAttrs(owner: string, attrs: Record<string, number>): void {
    for (const entry of this._busMap as Array<{ owner?: string; model?: { attrs?: object } }>) {
      if (entry?.owner === owner && entry.model) {
        entry.model.attrs = { ...(entry.model.attrs ?? {}), ...attrs };
      }
    }
    if (this._connected) this._send({ type: 'stm32_bus_attrs', data: { owner, attrs } });
  }

  private startBusMap(): {
    spi: unknown[];
    i2c?: unknown[];
    uart?: unknown[];
    pulls?: unknown[];
  } {
    try {
      const fresh = this.onBusMapRequest?.();
      if (fresh?.i2c) this._busMapI2c = fresh.i2c;
      if (fresh?.uart) this._busMapUart = fresh.uart;
      if (fresh?.pulls) this._busMapPulls = fresh.pulls;
    } catch (error) {
      console.warn(`[Stm32Bridge:${this.boardId}] bus maps could not be built`, error);
    }
    return {
      spi: this._busMap,
      ...(this._busMapI2c ? { i2c: this._busMapI2c } : {}),
      ...(this._busMapUart ? { uart: this._busMapUart } : {}),
      ...(this._busMapPulls ? { pulls: this._busMapPulls } : {}),
    };
  }

  private async promptBuildCleanup(data: Record<string, unknown>): Promise<void> {
    if (this.cleanupPromptShown || data.cleanup_available !== true) return;
    this.cleanupPromptShown = true;

    const bytes = Number(data.cleanup_bytes ?? 0);
    const size = bytes > 0 ? ` (${this.formatBytes(bytes)})` : '';
    const approved = await showConfirmDialog(
      `STM32 emulator build finished. Remove temporary source/build files${size}? ` +
        'The compiled emulator runtime will be kept. System-wide build tools will not be removed.',
      {
        title: 'Clean up STM32 build files?',
        confirmLabel: 'Clean up',
        cancelLabel: 'Keep files',
        kind: 'info',
      },
    );
    if (!approved || !this._connected) return;

    this._send({
      type: 'stm32_bus_attrs',
      data: { owner: '__velxio_runtime__', attrs: { cleanup_build: 1 } },
    });
  }

  private formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  private _send(payload: unknown): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }
}
