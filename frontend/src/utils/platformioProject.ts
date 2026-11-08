import JSZip from 'jszip';
import type { BoardKind } from '../types/board';

export interface PlatformIOBoardTarget {
  platform: string;
  board: string;
}

export interface PlatformIOImport {
  kind: 'platformio';
  boardType: BoardKind;
  files: Array<{ name: string; content: string }>;
  warnings: string[];
}

const BOARD_TARGETS: Record<string, PlatformIOBoardTarget> = {
  'arduino-uno': { platform: 'atmelavr', board: 'uno' },
  'arduino-nano': { platform: 'atmelavr', board: 'nanoatmega328' },
  'arduino-mega': { platform: 'atmelavr', board: 'megaatmega2560' },
  attiny85: { platform: 'atmelavr', board: 'attiny85' },
  'raspberry-pi-pico': { platform: 'raspberrypi', board: 'pico' },
  'pi-pico-w': { platform: 'raspberrypi', board: 'pico' },
  esp32: { platform: 'espressif32', board: 'esp32dev' },
  'esp32-devkit-c-v4': { platform: 'espressif32', board: 'esp32dev' },
  'esp32-cam': { platform: 'espressif32', board: 'esp32cam' },
  'wemos-lolin32-lite': { platform: 'espressif32', board: 'lolin32_lite' },
  'esp32-s3': { platform: 'espressif32', board: 'esp32-s3-devkitc-1' },
  'xiao-esp32-s3': { platform: 'espressif32', board: 'seeed_xiao_esp32s3' },
  'arduino-nano-esp32': { platform: 'espressif32', board: 'arduino_nano_esp32' },
  'esp32-c3': { platform: 'espressif32', board: 'esp32-c3-devkitm-1' },
  'xiao-esp32-c3': { platform: 'espressif32', board: 'seeed_xiao_esp32c3' },
  'aitewinrobot-esp32c3-supermini': { platform: 'espressif32', board: 'esp32-c3-devkitm-1' },
  'stm32-bluepill': { platform: 'ststm32', board: 'bluepill_f103c8' },
  'stm32-bluepill-f103cb': { platform: 'ststm32', board: 'bluepill_f103c8_128k' },
  'stm32-blackpill': { platform: 'ststm32', board: 'blackpill_f411ce' },
  'stm32-blackpill-f401': { platform: 'ststm32', board: 'blackpill_f401ce' },
  'stm32-f4-discovery': { platform: 'ststm32', board: 'disco_f407vg' },
  'stm32-olimex-h405': { platform: 'ststm32', board: 'genericSTM32F405RG' },
  'stm32-netduino-plus2': { platform: 'ststm32', board: 'genericSTM32F405RG' },
  'stm32-netduino2': { platform: 'ststm32', board: 'genericSTM32F205RG' },
};

const TEXT_EXTENSIONS = new Set([
  'c', 'cpp', 'cc', 'cxx', 'h', 'hpp', 'ino', 'ini', 'txt', 'json',
  'md', 'py', 's', 'asm', 'ld', 'csv', 'html', 'css', 'js',
]);

export function platformIOSelected(): boolean {
  return import.meta.env.VITE_VELXIO_COMPILER === 'platformio';
}

export function platformIOTarget(boardKind: string): PlatformIOBoardTarget | null {
  return BOARD_TARGETS[boardKind] ?? null;
}

export function platformIOConfig(boardKind: string, libraries: string[] = []): string | null {
  const target = platformIOTarget(boardKind);
  if (!target) return null;
  const config = [
    '[env:' + target.board + ']',
    'platform = ' + target.platform,
    'board = ' + target.board,
    'framework = arduino',
    'build_type = release',
  ];
  if (libraries.length) {
    config.push('lib_deps =', ...libraries.map((library) => '  ' + library));
  }
  return config.join('\n') + '\n';
}

function cppFromSketch(code: string): string {
  const source = code.replace(/\r\n/g, '\n');
  const includesArduino = /^\s*#\s*include\s*[<"]Arduino\.h[>"]/m.test(source);
  const functionPattern = /^\s*(?:static\s+)?(?:void|bool|int|unsigned|float|double|String|char|long|short|size_t|u?int(?:8|16|32|64)_t)\s+([A-Za-z_]\w*)\s*\(([^(){};]*)\)\s*\{/gm;
  const declarations = new Set<string>();
  let firstFunction = -1;
  let match: RegExpExecArray | null;

  while ((match = functionPattern.exec(source)) !== null) {
    if (firstFunction < 0) firstFunction = match.index;
    if (match[1] === 'setup' || match[1] === 'loop' || match[2].includes('=')) continue;
    const signature = match[0].trim().replace(/\s*\{\s*$/, '');
    declarations.add(signature + ';');
  }

  const before = firstFunction < 0 ? source : source.slice(0, firstFunction);
  const after = firstFunction < 0 ? '' : source.slice(firstFunction);
  const prototypes = declarations.size ? Array.from(declarations).join('\n') + '\n\n' : '';
  const body = before + prototypes + after;
  return (includesArduino ? '' : '#include <Arduino.h>\n\n') + body;
}

export function platformIOExampleFiles(
  boardKind: string,
  code: string,
  originalFiles?: Array<{ name: string; content: string }>,
  libraries: string[] = [],
): Array<{ name: string; content: string }> {
  const config = platformIOConfig(boardKind, libraries);
  const files = originalFiles?.length
    ? originalFiles
    : [{ name: 'sketch.ino', content: code }];
  if (!platformIOSelected() || !config) return files;
  if (files.some((file) => file.name === 'platformio.ini')) return files;

  const sketches = files.filter((file) => file.name.toLowerCase().endsWith('.ino'));
  const converted: Array<{ name: string; content: string }> = [];
  if (sketches.length) {
    const merged = sketches
      .slice()
      .sort((a, b) => (a.name === 'sketch.ino' ? -1 : b.name === 'sketch.ino' ? 1 : 0))
      .map((file) => file.content)
      .join('\n\n');
    converted.push({ name: 'src/main.cpp', content: cppFromSketch(merged) });
  }

  for (const file of files) {
    if (file.name.toLowerCase().endsWith('.ino')) continue;
    const normalized = file.name.replace(/\\/g, '/');
    const name = normalized.startsWith('src/') || normalized.startsWith('include/') ||
      normalized.startsWith('lib/') || normalized.startsWith('test/')
      ? normalized
      : /\.(h|hpp)$/i.test(normalized)
        ? 'include/' + normalized
        : /\.(c|cpp|cc|cxx|s)$/i.test(normalized)
          ? 'src/' + normalized
          : normalized;
    converted.push({ name, content: file.content });
  }

  converted.push({ name: 'platformio.ini', content: config });
  return converted;
}

function findPlatformIOConfig(files: Array<{ name: string; content: string }>): string {
  const entry = files.find((file) => file.name.toLowerCase() === 'platformio.ini');
  if (!entry) throw new Error('No platformio.ini was found at the project root.');
  return entry.content;
}

export function platformIOBoardFromIni(content: string): string {
  const envs = [...content.matchAll(/^\s*\[env:([^\]]+)\]\s*$/gm)];
  const preferred = content.match(/^\s*default_envs\s*=\s*([^\n#;]+)/m)?.[1]
    .trim().split(/[\s,]+/)[0];
  const section = envs.find((env) => env[1].trim() === preferred) ?? envs[0];
  if (!section) throw new Error('platformio.ini has no [env:...] build environment.');
  const start = section.index! + section[0].length;
  const remainder = content.slice(start);
  const end = remainder.search(/^\s*\[[^\]]+\]/m);
  const envContent = end < 0 ? remainder : remainder.slice(0, end);
  const baseSection = content.match(/^\s*\[env\]\s*$/m);
  const following = baseSection ? content.slice(baseSection.index! + baseSection[0].length) : '';
  const nextSection = following.search(/^\s*\[[^\]]+\]/m);
  const base = nextSection < 0 ? following : following.slice(0, nextSection);
  const board = envContent.match(/^\s*board\s*=\s*([^\n#;]+)/m)?.[1]?.trim() ??
    base.match(/^\s*board\s*=\s*([^\n#;]+)/m)?.[1]?.trim();
  if (!board) throw new Error('platformio.ini must declare a board in the selected environment.');
  return board;
}

function boardKindForPIO(board: string): BoardKind {
  const entry = Object.entries(BOARD_TARGETS).find(([, target]) => target.board === board);
  if (!entry) throw new Error('The PlatformIO board "' + board + '" is not supported by this Velxio simulator.');
  return entry[0] as BoardKind;
}

function safePath(name: string): string | null {
  const path = name.replace(/\\/g, '/').replace(/^\/+/, '');
  const parts = path.split('/');
  if (!path || parts.some((part) => part === '..' || !part || part === '.')) return null;
  if (parts.some((part) => ['.git', '.pio', '.platformio', 'node_modules', '__pycache__'].includes(part))) return null;
  if (path.startsWith('__MACOSX/') || path.includes('\0')) return null;
  if (path === 'platformio.ini') return path;
  const extension = path.split('.').pop()?.toLowerCase();
  return extension && TEXT_EXTENSIONS.has(extension) ? path : null;
}

export function buildPlatformIOImport(
  files: Array<{ name: string; content: string }>,
  warnings: string[] = [],
): PlatformIOImport {
  const config = findPlatformIOConfig(files);
  const boardType = boardKindForPIO(platformIOBoardFromIni(config));
  if (!platformIOSelected()) warnings.push('Restart Velxio with ./run.sh --compiler platformio to compile this imported firmware.');
  if (!files.some((file) => /^(src|lib)\/.*\.(c|cc|cpp|cxx|ino)$/i.test(file.name))) {
    throw new Error('No source file found in src/ or lib/.');
  }
  return { kind: 'platformio', boardType, files, warnings };
}

export async function importPlatformIOZip(file: File): Promise<PlatformIOImport | null> {
  if (file.size > 30 * 1024 * 1024) throw new Error('Project archive exceeds the 30 MB import limit.');
  const zip = await JSZip.loadAsync(file);
  const names = Object.keys(zip.files).filter((name) => !zip.files[name].dir);
  const configName = names.find((name) => name === 'platformio.ini' ||
    /^[^/]+\/platformio\.ini$/i.test(name));
  if (!configName) return null;
  const prefix = configName.slice(0, -'platformio.ini'.length);
  const selected = names.filter((name) => name.startsWith(prefix));
  if (selected.length > 500) throw new Error('PlatformIO project has more than 500 files.');
  const files: Array<{ name: string; content: string }> = [];
  const warnings: string[] = [];
  let size = 0;
  for (const filename of selected) {
    const name = safePath(filename.slice(prefix.length));
    if (!name) continue;
    const content = await zip.files[filename].async('string');
    size += content.length;
    if (size > 12 * 1024 * 1024) throw new Error('PlatformIO project text files exceed 12 MB.');
    files.push({ name, content });
  }
  if (files.length < selected.length) warnings.push('Non source files and generated build directories were skipped.');
  return buildPlatformIOImport(files, warnings);
}

export async function importPlatformIODirectory(selected: FileList | File[]): Promise<PlatformIOImport> {
  const entries = Array.from(selected);
  if (entries.length > 500) throw new Error('PlatformIO project has more than 500 files.');
  const configFile = entries.find((file) => file.webkitRelativePath.endsWith('/platformio.ini'));
  if (!configFile) throw new Error('Choose a folder with platformio.ini in its root.');
  const prefix = configFile.webkitRelativePath.slice(0, -'platformio.ini'.length);
  const files: Array<{ name: string; content: string }> = [];
  const warnings: string[] = [];
  let size = 0;
  for (const file of entries) {
    if (!file.webkitRelativePath.startsWith(prefix)) continue;
    const name = safePath(file.webkitRelativePath.slice(prefix.length));
    if (!name) continue;
    size += file.size;
    if (size > 12 * 1024 * 1024) throw new Error('PlatformIO project text files exceed 12 MB.');
    files.push({ name, content: await file.text() });
  }
  if (files.length < entries.length) warnings.push('Non source files and generated build directories were skipped.');
  return buildPlatformIOImport(files, warnings);
}
