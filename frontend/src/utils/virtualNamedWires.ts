import type { Wire } from '../types/wire';
import { NET_TERMINAL_PIN, terminalName } from '../data/netTerminals';

interface TerminalComponent {
  id: string;
  metadataId: string;
  properties: Record<string, unknown>;
}

type Endpoint = Wire['start'];

export function virtualNamedWires(
  components: readonly TerminalComponent[],
  wires: readonly Wire[],
): Wire[] {
  const terminals = new Map<string, string>();
  for (const component of components) {
    const name = terminalName(component);
    if (name) terminals.set(component.id, name);
  }

  const linked = new Map<string, Endpoint[]>();
  for (const wire of wires) {
    if (wire.bb) continue;
    const from = terminals.get(wire.start.componentId);
    const to = terminals.get(wire.end.componentId);
    const add = (name: string, endpoint: Endpoint) => {
      const group = linked.get(name) ?? [];
      if (!group.some((entry) => entry.componentId === endpoint.componentId && entry.pinName === endpoint.pinName)) {
        group.push(endpoint);
      }
      linked.set(name, group);
    };

    if (from && wire.start.pinName === NET_TERMINAL_PIN && !to) add(from, wire.end);
    if (to && wire.end.pinName === NET_TERMINAL_PIN && !from) add(to, wire.start);
  }

  const result: Wire[] = [];
  for (const [name, endpoints] of linked) {
    if (endpoints.length < 2) continue;
    endpoints.sort((a, b) =>
      (a.componentId + ':' + a.pinName).localeCompare(b.componentId + ':' + b.pinName)
    );
    const anchor = endpoints[0];
    for (let index = 1; index < endpoints.length; index++) {
      const target = endpoints[index];
      result.push({
        id: '__virtual_net_' + name + '_' + index,
        start: anchor,
        end: target,
        color: '#38bdf8',
        waypoints: [],
      });
    }
  }
  return result;
}
