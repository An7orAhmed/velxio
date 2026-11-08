// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NET_TERMINAL_METADATA, terminalName, terminalRail } from '../data/netTerminals';
import { buildNetlist } from '../simulation/spice/NetlistBuilder';
import { virtualNamedWires } from '../utils/virtualNamedWires';
import { isProBoardKind } from '../lib/proBoardGate';
import '../components/velxio-components/NetTerminalElement';

afterEach(() => {
  vi.unstubAllEnvs();
});

const terminal = (id: string, netName: string) => ({
  id,
  metadataId: 'net-label',
  properties: { netName },
});
const endpoint = (componentId: string, pinName: string) => ({ componentId, pinName });
const wire = (id: string, a: ReturnType<typeof endpoint>, b: ReturnType<typeof endpoint>) => ({
  id,
  start: { ...a, x: 0, y: 0 },
  end: { ...b, x: 0, y: 0 },
  color: 'green',
  waypoints: [],
});

describe('Wiring quick parts', () => {
  it('registers named labels and powered terminals as single-pin components', () => {
    expect(NET_TERMINAL_METADATA.map((item) => item.id)).toEqual([
      'net-label', 'net-power-5v', 'net-power-3v3', 'net-power-12v', 'net-ground',
    ]);
    expect(NET_TERMINAL_METADATA.every((item) => item.pinCount === 1)).toBe(true);
    expect(terminalRail('+5v')).toBe(5);
    expect(terminalRail('3v3')).toBe(3.3);
    expect(terminalRail('GND')).toBe('ground');
    expect(terminalRail('DATA')).toBe(null);
    expect(terminalName(terminal('left', ' data '))).toBe('DATA');
  });

  it('renders the net name as text without parsing it as HTML', () => {
    const element = document.createElement('velxio-net-terminal') as HTMLElement & {
      netName: string;
      pinInfo: Array<{ name: string }>;
    };
    element.netName = '<img src=x onerror=alert(1)>';
    document.body.appendChild(element);
    expect(element.pinInfo[0].name).toBe('NET');
    expect(element.shadowRoot?.querySelector('.name')?.textContent).toBe(element.netName);
    expect(element.shadowRoot?.querySelector('img')).toBeNull();
    element.remove();
  });

  it('merges matching named labels in the electrical netlist', () => {
    const components = [
      terminal('label_a', 'DATA'),
      terminal('label_b', 'data'),
      { id: 'r1', metadataId: 'resistor', properties: { value: '1k' } },
    ];
    const wires = [
      wire('w1', endpoint('board', 'PA0'), endpoint('label_a', 'NET')),
      wire('w2', endpoint('label_b', 'NET'), endpoint('r1', '1')),
    ];
    const result = buildNetlist({
      components,
      wires,
      boards: [{ id: 'board', vcc: 3.3, pins: {} }],
      analysis: { kind: 'op' },
    });
    expect(result.pinNetMap.get('board:PA0')).toBe(result.pinNetMap.get('r1:1'));
  });

  it('emits ideal voltage sources for a 3.3 V label and a 5 V label', () => {
    const result = buildNetlist({
      components: [
        { id: 'supply_a', metadataId: 'net-power-5v', properties: { netName: '+5V' } },
        { id: 'supply_b', metadataId: 'net-power-3v3', properties: { netName: '+3.3V' } },
        { id: 'ground', metadataId: 'net-ground', properties: { netName: 'GND' } },
        { id: 'r1', metadataId: 'resistor', properties: { value: '1k' } },
        { id: 'r2', metadataId: 'resistor', properties: { value: '1k' } },
      ],
      wires: [
        wire('w1', endpoint('supply_a', 'NET'), endpoint('r1', '1')),
        wire('w2', endpoint('supply_b', 'NET'), endpoint('r2', '1')),
        wire('w3', endpoint('ground', 'NET'), endpoint('r1', '2')),
        wire('w4', endpoint('ground', 'NET'), endpoint('r2', '2')),
      ],
      boards: [],
      analysis: { kind: 'op' },
    });
    expect(result.netlist).toContain('V_AUX_RAIL_5 aux_rail_5 0 DC 5');
    expect(result.netlist).toContain('V_AUX_RAIL_3V3 aux_rail_3v3 0 DC 3.3');
    expect(result.pinNetMap.get('r1:2')).toBe('0');
    expect(result.pinNetMap.get('r2:2')).toBe('0');
  });

  it('creates invisible board-to-board links for matching named labels', () => {
    const components = [terminal('lab1', 'UART_TX'), terminal('lab2', 'uart_tx')];
    const physical = [
      wire('w1', endpoint('board_a', 'PA9'), endpoint('lab1', 'NET')),
      wire('w2', endpoint('board_b', 'PA10'), endpoint('lab2', 'NET')),
    ];
    const derived = virtualNamedWires(components, physical);
    expect(derived).toHaveLength(1);
    expect(derived[0].start.componentId).toBe('board_a');
    expect(derived[0].end.componentId).toBe('board_b');
    expect(physical).toHaveLength(2);
  });

  it('does not join different labels', () => {
    const components = [terminal('lab1', 'SDA'), terminal('lab2', 'SCL')];
    const physical = [
      wire('w1', endpoint('board_a', 'SDA'), endpoint('lab1', 'NET')),
      wire('w2', endpoint('board_b', 'SCL'), endpoint('lab2', 'NET')),
    ];
    expect(virtualNamedWires(components, physical)).toHaveLength(0);
  });

  it('removes the paid badge only for the available local STM32 target', () => {
    vi.stubEnv('VITE_VELXIO_COMPILER', 'platformio');
    expect(isProBoardKind('stm32-blackpill-f401')).toBe(false);
    expect(isProBoardKind('stm32-blackpill')).toBe(true);
    expect(isProBoardKind('raspberry-pi-3')).toBe(true);
  });
});
