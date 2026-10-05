export interface PlatformIOTarget {
  platform: string;
  board: string;
  extra?: readonly string[];
}

const TARGETS: Record<string, PlatformIOTarget> = {
  'arduino-uno': { platform: 'atmelavr', board: 'uno' },
  'arduino-nano': { platform: 'atmelavr', board: 'nanoatmega328' },
  'arduino-mega': { platform: 'atmelavr', board: 'megaatmega2560' },
  attiny85: { platform: 'atmelavr', board: 'digispark-tiny' },
  'raspberry-pi-pico': {
    platform: 'https://github.com/maxgerhardt/platform-raspberrypi.git',
    board: 'rpipico',
    extra: ['board_build.core = earlephilhower'],
  },
  'pi-pico-w': {
    platform: 'https://github.com/maxgerhardt/platform-raspberrypi.git',
    board: 'rpipicow',
    extra: ['board_build.core = earlephilhower'],
  },
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
  'stm32-bluepill-f103cb': { platform: 'ststm32', board: 'genericSTM32F103CB' },
  'stm32-blackpill': { platform: 'ststm32', board: 'blackpill_f411ce' },
  'stm32-blackpill-f401': { platform: 'ststm32', board: 'blackpill_f401ce' },
  'stm32-f4-discovery': { platform: 'ststm32', board: 'disco_f407vg' },
  'stm32-olimex-h405': { platform: 'ststm32', board: 'genericSTM32F405RG' },
  'stm32-netduino-plus2': { platform: 'ststm32', board: 'genericSTM32F405RG' },
  'stm32-netduino2': { platform: 'ststm32', board: 'genericSTM32F205RG' },
};

const DEFAULT_MAIN_CPP = `#include <Arduino.h>

void setup() {
}

void loop() {
}
`;

function makeIni(target: PlatformIOTarget): string {
  return [
    '[platformio]',
    'default_envs = velxio',
    '',
    '[env:velxio]',
    `platform = ${target.platform}`,
    `board = ${target.board}`,
    'framework = arduino',
    'lib_ldf_mode = deep+',
    ...(target.extra ?? []),
    '',
  ].join('\n');
}

export function platformIOTargetForBoard(kind: string): PlatformIOTarget | undefined {
  return TARGETS[kind];
}

export function platformIOWorkspaceFiles(
  kind: string,
  mainCpp: string = DEFAULT_MAIN_CPP,
): Array<{ name: string; content: string }> | undefined {
  const target = platformIOTargetForBoard(kind);
  if (!target) return undefined;
  return [
    { name: 'src/main.cpp', content: mainCpp },
    { name: 'platformio.ini', content: makeIni(target) },
  ];
}

export function arduinoSketchToCpp(source: string): string {
  if (/^\s*#\s*include\s*[<\"]Arduino\.h[>\"]/m.test(source)) return source;
  return `#include <Arduino.h>\n\n${source}`;
}

export function migrateSingleSketchWorkspace(
  kind: string,
  files: Array<{ name: string; content: string }>,
): Array<{ name: string; content: string }> {
  if (files.some((f) => f.name.replace(/\\/g, '/') === 'platformio.ini')) return files;
  const sketches = files.filter((f) => f.name.toLowerCase().endsWith('.ino'));
  if (sketches.length !== 1) return files;
  const template = platformIOWorkspaceFiles(kind, arduinoSketchToCpp(sketches[0].content));
  if (!template) return files;

  const rest = files
    .filter((f) => f !== sketches[0])
    .map((f) => {
      const name = f.name.replace(/\\/g, '/').replace(/^\/+/, '');
      if (name.includes('/')) return { ...f, name };
      if (/\.(h|hpp)$/i.test(name)) return { ...f, name: `include/${name}` };
      if (/\.(c|cc|cpp|cxx)$/i.test(name)) return { ...f, name: `src/${name}` };
      return { ...f, name };
    });

  return [template[0], template[1], ...rest];
}
