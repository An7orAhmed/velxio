/**
 * Firmware file loader — reads .hex, .bin, and .elf files and converts them
 * into the string format expected by compileBoardProgram().
 *
 * Important STM32 rule:
 * - STM32 QEMU consumes the original ELF, not a flattened PT_LOAD image.
 *   Keeping the complete ELF preserves the entry point, program headers,
 *   symbols/debug data, and the exact layout QEMU expects for -kernel.
 *
 * Other families keep their existing behavior:
 * - AVR boards expect Intel HEX text
 * - RP2040 / ESP32 boards expect base64-encoded raw binary
 */

import type { BoardKind } from '../types/board';

export type FirmwareFormat = 'hex' | 'bin' | 'elf';

const ELF_MAGIC = [0x7f, 0x45, 0x4c, 0x46];

export function detectFirmwareFormat(filename: string, bytes: Uint8Array): FirmwareFormat {
  if (
    bytes.length >= 4 &&
    bytes[0] === ELF_MAGIC[0] &&
    bytes[1] === ELF_MAGIC[1] &&
    bytes[2] === ELF_MAGIC[2] &&
    bytes[3] === ELF_MAGIC[3]
  ) {
    return 'elf';
  }

  const ext = filename.toLowerCase().split('.').pop() ?? '';
  if (ext === 'hex' || ext === 'ihex') return 'hex';
  if (ext === 'elf') return 'elf';

  if (bytes[0] === 0x3a) return 'hex';
  return 'bin';
}

const EM_ARM = 0x28;
const EM_AVR = 0x53;
const EM_XTENSA = 0x5e;
const EM_RISCV = 0xf3;

export interface ElfInfo {
  machine: number;
  is32bit: boolean;
  isLittleEndian: boolean;
  suggestedBoard: BoardKind | null;
  architectureName: string;
}

export function detectArchitectureFromElf(bytes: Uint8Array): ElfInfo | null {
  if (bytes.length < 20) return null;
  if (bytes[0] !== 0x7f || bytes[1] !== 0x45 || bytes[2] !== 0x4c || bytes[3] !== 0x46) {
    return null;
  }

  const is32bit = bytes[4] === 1;
  const isLittleEndian = bytes[5] === 1;
  const machine = isLittleEndian ? bytes[18] | (bytes[19] << 8) : (bytes[18] << 8) | bytes[19];

  let suggestedBoard: BoardKind | null = null;
  let architectureName = 'Unknown';

  switch (machine) {
    case EM_AVR:
      suggestedBoard = 'arduino-uno';
      architectureName = 'AVR';
      break;
    case EM_ARM:
      suggestedBoard = null;
      architectureName = 'ARM';
      break;
    case EM_RISCV:
      suggestedBoard = 'esp32-c3';
      architectureName = 'RISC-V';
      break;
    case EM_XTENSA:
      suggestedBoard = 'esp32';
      architectureName = 'Xtensa';
      break;
  }

  return { machine, is32bit, isLittleEndian, suggestedBoard, architectureName };
}

export function extractLoadSegmentsFromElf(bytes: Uint8Array): Uint8Array {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const is32bit = bytes[4] === 1;
  const isLE = bytes[5] === 1;

  if (!is32bit) throw new Error('Only 32-bit ELF files are supported');

  const u16 = (off: number) => view.getUint16(off, isLE);
  const u32 = (off: number) => view.getUint32(off, isLE);

  const e_phoff = u32(28);
  const e_phentsize = u16(42);
  const e_phnum = u16(44);

  if (e_phoff === 0 || e_phnum === 0) {
    throw new Error('ELF file has no program headers');
  }

  const PT_LOAD = 1;
  const segments: { paddr: number; data: Uint8Array }[] = [];

  for (let i = 0; i < e_phnum; i++) {
    const phOff = e_phoff + i * e_phentsize;
    if (phOff + e_phentsize > bytes.length) break;
    if (u32(phOff) !== PT_LOAD) continue;

    const p_offset = u32(phOff + 4);
    const p_paddr = u32(phOff + 12);
    const p_filesz = u32(phOff + 16);

    if (p_filesz === 0) continue;
    if (p_offset + p_filesz > bytes.length) {
      throw new Error(`ELF segment at offset 0x${p_offset.toString(16)} extends beyond file`);
    }

    segments.push({
      paddr: p_paddr,
      data: bytes.slice(p_offset, p_offset + p_filesz),
    });
  }

  if (segments.length === 0) throw new Error('No loadable segments found in ELF file');

  segments.sort((a, b) => a.paddr - b.paddr);
  const baseAddr = segments[0].paddr;
  const lastSeg = segments[segments.length - 1];
  const totalSize = lastSeg.paddr - baseAddr + lastSeg.data.length;
  const result = new Uint8Array(totalSize);

  for (const seg of segments) result.set(seg.data, seg.paddr - baseAddr);
  return result;
}

export function binaryToIntelHex(data: Uint8Array): string {
  const lines: string[] = [];
  const BYTES_PER_LINE = 16;

  for (let addr = 0; addr < data.length; addr += BYTES_PER_LINE) {
    const count = Math.min(BYTES_PER_LINE, data.length - addr);
    let line = ':';

    line += count.toString(16).padStart(2, '0').toUpperCase();
    line += (addr & 0xffff).toString(16).padStart(4, '0').toUpperCase();
    line += '00';

    let checksum = count + ((addr >> 8) & 0xff) + (addr & 0xff);
    for (let i = 0; i < count; i++) {
      const b = data[addr + i];
      line += b.toString(16).padStart(2, '0').toUpperCase();
      checksum += b;
    }

    line += ((~checksum + 1) & 0xff).toString(16).padStart(2, '0').toUpperCase();
    lines.push(line);
  }

  lines.push(':00000001FF');
  return lines.join('\n');
}

function bytesToBase64(bytes: Uint8Array): string {
  const CHUNK = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    const end = Math.min(bytes.length, i + CHUNK);
    for (let j = i; j < end; j++) binary += String.fromCharCode(bytes[j]);
  }
  return btoa(binary);
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  return bytesToBase64(new Uint8Array(buffer));
}

const AVR_BOARDS = new Set<BoardKind>(['arduino-uno', 'arduino-nano', 'arduino-mega', 'attiny85']);

function isAvrBoard(kind: BoardKind): boolean {
  return AVR_BOARDS.has(kind);
}

function isStm32Board(kind: BoardKind): boolean {
  return kind.startsWith('stm32-');
}

export interface FirmwareLoadResult {
  program: string;
  format: FirmwareFormat;
  elfInfo: ElfInfo | null;
  message: string;
}

const MAX_FILE_SIZE = 16 * 1024 * 1024;

export async function readFirmwareFile(
  file: File,
  boardKind: BoardKind,
): Promise<FirmwareLoadResult> {
  if (file.size > MAX_FILE_SIZE) {
    throw new Error(
      `File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max ${MAX_FILE_SIZE / 1024 / 1024} MB.`,
    );
  }

  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const format = detectFirmwareFormat(file.name, bytes);

  let elfInfo: ElfInfo | null = null;
  let program: string;
  let message: string;

  switch (format) {
    case 'hex': {
      program = new TextDecoder().decode(bytes);
      message = `Loaded Intel HEX firmware (${(file.size / 1024).toFixed(1)} KB)`;
      break;
    }

    case 'bin': {
      program = arrayBufferToBase64(buffer);
      message = `Loaded binary firmware (${(file.size / 1024).toFixed(1)} KB)`;
      break;
    }

    case 'elf': {
      elfInfo = detectArchitectureFromElf(bytes);
      const archName = elfInfo?.architectureName ?? 'unknown';

      if (isStm32Board(boardKind)) {
        if (elfInfo && elfInfo.machine !== EM_ARM) {
          throw new Error(`ELF architecture is ${archName}; STM32 requires a 32-bit ARM ELF.`);
        }
        if (elfInfo && !elfInfo.is32bit) {
          throw new Error('STM32 emulator requires a 32-bit ARM ELF.');
        }

        program = arrayBufferToBase64(buffer);
        message = `Loaded STM32 ELF directly (${(file.size / 1024).toFixed(1)} KB, no source compile)`;
        break;
      }

      const loadData = extractLoadSegmentsFromElf(bytes);

      if (isAvrBoard(boardKind)) {
        program = binaryToIntelHex(loadData);
        message = `Loaded ELF firmware (${archName}, ${(file.size / 1024).toFixed(1)} KB) → Intel HEX`;
      } else {
        program = bytesToBase64(loadData);
        message = `Loaded ELF firmware (${archName}, ${(file.size / 1024).toFixed(1)} KB) → binary`;
      }
      break;
    }
  }

  return { program, format, elfInfo, message };
}
