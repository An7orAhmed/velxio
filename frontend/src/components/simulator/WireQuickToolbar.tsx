import { useEffect, useRef, useState } from 'react';
import './WireQuickToolbar.css';

type CustomPart = 'resistor' | 'capacitor' | 'net-label';

interface WireQuickToolbarProps {
  wiring: boolean;
  onInsert: (metadataId: string, values?: Record<string, string>) => void;
  onOpenPicker: () => void;
}

const PRESETS = [
  { id: 'net-power-5v', label: '+5V', title: 'Add 5 V power terminal' },
  { id: 'net-power-3v3', label: '3V3', title: 'Add 3.3 V power terminal' },
  { id: 'net-ground', label: 'GND', title: 'Add ground terminal' },
  { id: 'net-power-12v', label: '+12V', title: 'Add 12 V power terminal' },
];

export function WireQuickToolbar({ wiring, onInsert, onOpenPicker }: WireQuickToolbarProps) {
  const [custom, setCustom] = useState<CustomPart | null>(null);
  const [resistance, setResistance] = useState('1k');
  const [capacitance, setCapacitance] = useState('100n');
  const [label, setLabel] = useState('SIGNAL');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (custom) inputRef.current?.focus();
  }, [custom]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const stop = (event: Event) => event.stopPropagation();
    for (const event of ['mousedown', 'touchstart', 'touchend', 'touchmove']) {
      element.addEventListener(event, stop);
    }
    return () => {
      for (const event of ['mousedown', 'touchstart', 'touchend', 'touchmove']) {
        element.removeEventListener(event, stop);
      }
    };
  }, []);

  const addCustom = () => {
    if (!custom) return;
    const value = custom === 'resistor' ? resistance.trim()
      : custom === 'capacitor' ? capacitance.trim()
        : label.trim();
    if (!value) return;
    onInsert(custom, custom === 'net-label' ? { netName: value } : { value });
    setCustom(null);
  };

  return (
    <div
      ref={containerRef}
      className={'wire-quick-toolbar' + (wiring ? ' wire-quick-toolbar-wiring' : '')}
      role="toolbar"
      aria-label="Quick wiring components"
    >
      <span className="wire-quick-heading">Wiring</span>
      <button type="button" title="Custom resistor" onClick={() => setCustom('resistor')}>R</button>
      <button type="button" title="Custom capacitor" onClick={() => setCustom('capacitor')}>C</button>
      <span className="wire-quick-divider" />
      {PRESETS.map((preset) => (
        <button
          key={preset.id}
          type="button"
          title={preset.title}
          onClick={() => {
            onInsert(preset.id);
            setCustom(null);
          }}
        >
          {preset.label}
        </button>
      ))}
      <button
        type="button"
        title="Named wire label. Matching names are connected."
        onClick={() => setCustom('net-label')}
      >
        Label
      </button>
      <span className="wire-quick-divider" />
      <button type="button" title="More components" onClick={onOpenPicker}>More</button>
      {custom && (
        <form
          className="wire-quick-edit"
          onSubmit={(event) => {
            event.preventDefault();
            addCustom();
          }}
        >
          <label htmlFor="wire-quick-value">
            {custom === 'resistor' ? 'Resistance' : custom === 'capacitor' ? 'Capacitance' : 'Net name'}
          </label>
          <input
            id="wire-quick-value"
            ref={inputRef}
            value={custom === 'resistor' ? resistance : custom === 'capacitor' ? capacitance : label}
            onChange={(event) => {
              const value = event.target.value;
              if (custom === 'resistor') setResistance(value);
              else if (custom === 'capacitor') setCapacitance(value);
              else setLabel(value);
            }}
            maxLength={48}
            aria-label={custom === 'net-label' ? 'Net name' : 'Component value'}
          />
          <button type="submit">Add</button>
          <button type="button" aria-label="Cancel quick add" onClick={() => setCustom(null)}>×</button>
        </form>
      )}
    </div>
  );
}
