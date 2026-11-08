import type { ComponentMetadata } from '../types/component-metadata';

export const NET_TERMINAL_IDS = new Set([
  'net-label',
  'net-power-5v',
  'net-power-3v3',
  'net-power-12v',
  'net-ground',
]);

export const NET_TERMINAL_PIN = 'NET';

export function normalizeNetName(value: unknown): string {
  return String(value ?? '').trim().replace(/\s+/g, '_').toUpperCase().slice(0, 48);
}

export function terminalName(component: {
  metadataId: string;
  properties: Record<string, unknown>;
}): string | null {
  if (!NET_TERMINAL_IDS.has(component.metadataId)) return null;
  return normalizeNetName(component.properties.netName) || null;
}

export function terminalRail(name: string): number | 'ground' | 'vcc' | null {
  const normalized = normalizeNetName(name);
  if (normalized === 'GND' || normalized === 'GROUND' || normalized === 'VSS') return 'ground';
  if (normalized === 'VCC') return 'vcc';
  if (normalized === '+3.3V' || normalized === '3V3' || normalized === '3.3V') return 3.3;
  if (normalized === '+5V' || normalized === '5V') return 5;
  if (normalized === '+12V' || normalized === '12V') return 12;
  return null;
}

function terminalMetadata(
  id: string,
  name: string,
  netName: string,
  description: string,
): ComponentMetadata {
  const safeLabel = netName.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return {
    id,
    name,
    tagName: 'velxio-net-terminal',
    category: 'connectivity',
    description,
    thumbnail: '<svg xmlns="http://www.w3.org/2000/svg" width="84" height="50" viewBox="0 0 84 50"><circle cx="12" cy="25" r="4" fill="#38bdf8"/><path d="M16 25H31" stroke="#38bdf8" stroke-width="2"/><rect x="30" y="13" width="50" height="24" rx="5" fill="#172b3b" stroke="#38bdf8"/><text x="55" y="29" text-anchor="middle" font-family="monospace" font-size="11" fill="#e2f3ff">' + safeLabel + '</text></svg>',
    properties: [{ name: 'netName', type: 'string', defaultValue: netName, control: 'text' }],
    defaultValues: { netName },
    pinCount: 1,
    tags: ['net', 'label', 'terminal', 'connection', 'wire', 'power', 'ground', 'rail'],
  };
}

export const NET_TERMINAL_METADATA: ComponentMetadata[] = [
  terminalMetadata('net-label', 'Net label', 'SIGNAL', 'Matching net names connect internally without drawing a long wire.'),
  terminalMetadata('net-power-5v', '+5 V terminal', '+5V', 'Ideal 5 V power rail. All +5V labels share this net.'),
  terminalMetadata('net-power-3v3', '+3.3 V terminal', '+3.3V', 'Ideal 3.3 V power rail. All +3.3V labels share this net.'),
  terminalMetadata('net-power-12v', '+12 V terminal', '+12V', 'Ideal 12 V power rail. All +12V labels share this net.'),
  terminalMetadata('net-ground', 'Ground terminal', 'GND', 'Global circuit ground. All GND labels share this net.'),
];
