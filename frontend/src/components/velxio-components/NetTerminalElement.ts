const BaseElement: typeof HTMLElement = typeof HTMLElement !== 'undefined'
  ? HTMLElement
  : class {} as typeof HTMLElement;

export class NetTerminalElement extends BaseElement {
  private currentName = 'SIGNAL';

  get pinInfo(): Array<{ name: string; x: number; y: number }> {
    return [{ name: 'NET', x: 7, y: 18 }];
  }

  get netName(): string {
    return this.currentName;
  }

  set netName(value: string) {
    this.currentName = String(value ?? '').trim() || 'SIGNAL';
    this.renderName();
  }

  connectedCallback(): void {
    if (!this.shadowRoot) {
      const shadow = this.attachShadow({ mode: 'open' });
      shadow.innerHTML = [
        '<style>',
        ':host { display: block; width: 112px; height: 36px; }',
        '.terminal { display: flex; align-items: center; height: 36px; font: 600 11px monospace; }',
        '.contact { width: 8px; height: 8px; border-radius: 50%; border: 2px solid #38bdf8; box-sizing: border-box; flex: none; }',
        '.stem { width: 13px; height: 2px; background: #38bdf8; flex: none; }',
        '.name { box-sizing: border-box; padding: 6px 8px; border: 1px solid #38bdf8; border-radius: 5px; background: #172b3b; color: #e2f3ff; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; flex: 1; }',
        '</style>',
        '<div class="terminal">',
        '<span class="contact"></span>',
        '<span class="stem"></span>',
        '<span class="name"></span>',
        '</div>',
      ].join('');
    }
    this.renderName();
  }

  private renderName(): void {
    const label = this.shadowRoot?.querySelector('.name');
    if (label) label.textContent = this.currentName;
  }
}

if (typeof customElements !== 'undefined' && !customElements.get('velxio-net-terminal')) {
  customElements.define('velxio-net-terminal', NetTerminalElement);
}
