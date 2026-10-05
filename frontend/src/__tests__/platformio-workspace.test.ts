import { describe, expect, it } from 'vitest';
import {
  arduinoSketchToCpp,
  migrateSingleSketchWorkspace,
  platformIOWorkspaceFiles,
} from '../utils/platformioWorkspace';

describe('PlatformIO workspace defaults', () => {
  it('creates a real Uno PlatformIO project', () => {
    const files = platformIOWorkspaceFiles('arduino-uno');
    expect(files).toBeDefined();
    expect(files?.map((f) => f.name)).toEqual(['src/main.cpp', 'platformio.ini']);
    expect(files?.[0].content).toContain('#include <Arduino.h>');
    expect(files?.[1].content).toContain('platform = atmelavr');
    expect(files?.[1].content).toContain('board = uno');
    expect(files?.[1].content).toContain('framework = arduino');
  });

  it('uses the matching PlatformIO STM32 target', () => {
    const files = platformIOWorkspaceFiles('stm32-blackpill-f401');
    expect(files?.[1].content).toContain('platform = ststm32');
    expect(files?.[1].content).toContain('board = blackpill_f401ce');
  });

  it('converts one legacy sketch to src/main.cpp', () => {
    const migrated = migrateSingleSketchWorkspace('arduino-uno', [
      { name: 'sketch.ino', content: 'void setup() {}\nvoid loop() {}\n' },
      { name: 'pins.h', content: '#pragma once\n' },
    ]);
    expect(migrated.map((f) => f.name)).toEqual([
      'src/main.cpp',
      'platformio.ini',
      'include/pins.h',
    ]);
    expect(migrated[0].content).toContain('#include <Arduino.h>');
  });

  it('does not rewrite multi-sketch legacy projects automatically', () => {
    const files = [
      { name: 'a.ino', content: 'void setup() {}' },
      { name: 'b.ino', content: 'void loop() {}' },
    ];
    expect(migrateSingleSketchWorkspace('arduino-uno', files)).toBe(files);
  });

  it('does not duplicate Arduino.h', () => {
    const source = '#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n';
    expect(arduinoSketchToCpp(source)).toBe(source);
  });
});
