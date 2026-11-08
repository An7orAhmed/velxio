// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import {
  buildPlatformIOImport,
  importPlatformIODirectory,
  platformIOExampleFiles,
  platformIOSelected,
} from '../utils/platformioProject';
import { importProjectFile } from '../utils/importProject';
import { useEditorStore } from '../store/useEditorStore';
import { useSimulatorStore } from '../store/useSimulatorStore';
import { loadExample } from '../utils/loadExample';
import { exampleProjects } from '../data/examples';
import { applyPlatformIOImport } from '../utils/applyPlatformIOImport';

afterEach(() => {
  vi.unstubAllEnvs();
});

const config = [
  '[env:controller]',
  'platform = ststm32',
  'board = blackpill_f401ce',
  'framework = arduino',
  'lib_deps =',
  '  adafruit/Adafruit BusIO',
].join('\n');

describe('PlatformIO workspace mode', () => {
  it('keeps Arduino examples unchanged in Arduino compiler mode', () => {
    vi.stubEnv('VITE_VELXIO_COMPILER', 'arduino');
    const files = platformIOExampleFiles('arduino-uno', 'void setup() {}');
    expect(files).toEqual([{ name: 'sketch.ino', content: 'void setup() {}' }]);
  });

  it('creates src/main.cpp and platformio.ini with required libraries', () => {
    vi.stubEnv('VITE_VELXIO_COMPILER', 'platformio');
    expect(platformIOSelected()).toBe(true);
    const files = platformIOExampleFiles(
      'stm32-blackpill-f401',
      'void setup() {}\nvoid loop() {}',
      undefined,
      ['adafruit/Adafruit BusIO'],
    );
    expect(files.map((file) => file.name)).toEqual(['src/main.cpp', 'platformio.ini']);
    expect(files[0].content).toContain('#include <Arduino.h>');
    expect(files[0].content).toContain('void setup() {}');
    expect(files[1].content).toContain('board = blackpill_f401ce');
    expect(files[1].content).toContain('adafruit/Adafruit BusIO');
    expect(files.every((file) => !file.name.endsWith('.ino'))).toBe(true);
  });

  it('retains source folders and headers for multi-file examples', () => {
    vi.stubEnv('VITE_VELXIO_COMPILER', 'platformio');
    const files = platformIOExampleFiles('arduino-uno', '', [
      { name: 'sketch.ino', content: 'void setup() {}\nvoid loop() {}' },
      { name: 'foo.h', content: '#pragma once' },
      { name: 'helper.cpp', content: 'int foo() { return 1; }' },
    ]);
    expect(files.map((file) => file.name)).toContain('include/foo.h');
    expect(files.map((file) => file.name)).toContain('src/helper.cpp');
    expect(files.map((file) => file.name)).not.toContain('sketch.ino');
  });

  it('loads a gallery blink example without any .ino files in PlatformIO mode', async () => {
    vi.stubEnv('VITE_VELXIO_COMPILER', 'platformio');
    const example = exampleProjects.find((item) => item.id === 'blink-led');
    expect(example).toBeDefined();
    await loadExample(example!);
    const board = useSimulatorStore.getState().boards[0];
    const names = useEditorStore.getState()
      .getGroupFiles(board.activeFileGroupId)
      .map((file) => file.name);
    expect(names).toContain('src/main.cpp');
    expect(names).toContain('platformio.ini');
    expect(names.some((name) => name.endsWith('.ino'))).toBe(false);
  });
});

describe('PlatformIO import', () => {
  it('preserves native folders, config and source for a ZIP project', async () => {
    const zip = new JSZip();
    zip.file('firmware/platformio.ini', config);
    zip.file('firmware/src/main.cpp', '#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}');
    zip.file('firmware/include/board.h', '#pragma once');
    zip.file('firmware/lib/signal/src/signal.cpp', 'int signal() { return 1; }');
    zip.file('firmware/.pio/build/debug/firmware.map', 'generated');
    const blob = await zip.generateAsync({ type: 'blob' });
    const imported = await importProjectFile(new File([blob], 'firmware.zip'));
    expect(imported.kind).toBe('platformio');
    if (imported.kind !== 'platformio') return;
    expect(imported.boardType).toBe('stm32-blackpill-f401');
    expect(imported.files.map((item) => item.name)).toEqual([
      'platformio.ini',
      'src/main.cpp',
      'include/board.h',
      'lib/signal/src/signal.cpp',
    ]);
    expect(imported.files.find((item) => item.name === 'platformio.ini')?.content).toBe(config);
    expect(imported.files.some((item) => item.name.startsWith('.pio'))).toBe(false);
  });

  it('imports a selected project folder using relative paths', async () => {
    const make = (name: string, text: string) => {
      const file = new File([text], name);
      Object.defineProperty(file, 'webkitRelativePath', { value: 'firmware/' + name });
      Object.defineProperty(file, 'text', { value: async () => text });
      return file;
    };
    const imported = await importPlatformIODirectory([
      make('platformio.ini', config),
      make('src/main.cpp', 'void setup() {}\nvoid loop() {}'),
      make('include/board.h', '#pragma once'),
    ]);
    expect(imported.files).toHaveLength(3);
    expect(imported.boardType).toBe('stm32-blackpill-f401');
  });

  it('rejects an unmapped MCU instead of pretending it can be simulated', () => {
    const other = config.replace('blackpill_f401ce', 'some-other-board');
    expect(() => buildPlatformIOImport([
      { name: 'platformio.ini', content: other },
      { name: 'src/main.cpp', content: 'int main() {return 0;}' },
    ])).toThrow('not supported');
  });

  it('replaces the workspace but leaves the source files editable and saveable', () => {
    const result = buildPlatformIOImport([
      { name: 'platformio.ini', content: config },
      { name: 'src/main.cpp', content: 'void setup() {}\nvoid loop() {}' },
      { name: 'lib/signal/src/signal.cpp', content: 'int signal() {return 1;}' },
    ]);
    applyPlatformIOImport(result);
    const sim = useSimulatorStore.getState();
    const board = sim.boards[0];
    expect(board.boardKind).toBe('stm32-blackpill-f401');
    const workspace = useEditorStore.getState().getGroupFiles(board.activeFileGroupId);
    expect(workspace.map((file) => file.name)).toContain('lib/signal/src/signal.cpp');
    expect(workspace.map((file) => file.name)).toContain('platformio.ini');
  });
});
