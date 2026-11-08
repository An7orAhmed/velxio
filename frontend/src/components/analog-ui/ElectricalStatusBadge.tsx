import { useElectricalStore } from '../../store/useElectricalStore';
import { cssVar } from '../../lib/theme';
import { useResolvedTheme } from '../../hooks/useTheme';
import { useMemo } from 'react';
import './ElectricalStatusBadge.css';

export function ElectricalStatusBadge() {
  const analysisMode = useElectricalStore((state) => state.analysisMode);
  const nodeVoltages = useElectricalStore((state) => state.nodeVoltages);
  const solveMs = useElectricalStore((state) => state.lastSolveMs);
  const converged = useElectricalStore((state) => state.converged);
  const error = useElectricalStore((state) => state.error);
  const theme = useResolvedTheme();
  const colors = useMemo(() => ({
    background: cssVar('--color-canvas-label-bg'),
    dc: cssVar('--color-probe-dc-dim'),
    ac: cssVar('--color-probe-ac'),
    error: cssVar('--color-probe-error'),
    errorDim: cssVar('--color-probe-error-dim'),
  }), [theme]);
  const color = error ? colors.errorDim : analysisMode === 'tran' ? colors.ac : colors.dc;
  const summary = error
    ? 'Warning: ' + error
    : !converged
      ? 'Warning: did not converge'
      : Object.keys(nodeVoltages).length + ' nets | ' + solveMs.toFixed(0) + ' ms';

  return (
    <div className="electrical-status-badge" style={{ background: colors.background, borderColor: color }}>
      <span className="electrical-status-mode" style={{ color, borderColor: color }}>
        {analysisMode === 'tran' ? 'AC' : 'DC'}
      </span>
      <span className="electrical-status-value" style={{ color: error ? colors.error : colors.dc }}>
        SPICE {summary}
      </span>
    </div>
  );
}
