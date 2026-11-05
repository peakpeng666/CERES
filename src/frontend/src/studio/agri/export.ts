import { envProblemMatrix, operationServiceMatrix, capabilitySummary, gapReport } from './matrix';
import { AGRI_PATTERNS, patternStatus } from './patterns';
import { CAP_AXES, capabilityEvidence, catalogStats } from './evidence';

/** Convert Maps explicitly: JSON.stringify(Map) would silently discard every cell. */
export function matrixDocument() {
  return {
    schema: 'urp.agriculture.catalogue/1',
    note: '目录与声明能力，不是求解或物理实验结果；空单元保留。',
    environment_problem: envProblemMatrix().map((row) => ({ id: row.envId, name: row.name,
      cells: Object.fromEntries([...row.cells].map(([id, cell]) => [id, cell.patterns.map((p) => p.id)])) })),
    operation_service: operationServiceMatrix().map((row) => ({ id: row.opId, name: row.name,
      cells: Object.fromEntries([...row.cells].map(([id, cell]) => [id, cell.patterns.map((p) => p.id)])) })),
    stats: catalogStats(), capabilities: capabilitySummary(), gaps: gapReport(),
    patterns: AGRI_PATTERNS.map((p) => ({ id: p.id, name: p.name, status: patternStatus(p), approximation: p.approximation ?? null,
      target: { serviceObject: p.serviceObject, planningProblem: p.planningProblem }, proxyModel: p.proxyModel ?? null, capabilities: p.capabilities, evidence: capabilityEvidence(p), refs: p.refs })),
  };
}
const csv = (value: string) => `"${value.replace(/"/g, '""')}"`;
export function matrixFile(format: 'json' | 'csv') {
  const doc = matrixDocument();
  const rows = [['matrix', 'row_id', 'row_name', 'column_id', 'pattern_ids', 'statuses']];
  for (const [name, entries] of [['environment_problem', doc.environment_problem], ['operation_service', doc.operation_service]] as const) {
    for (const row of entries) for (const [col, ids] of Object.entries(row.cells)) rows.push([
      name, row.id, row.name, col, ids.join(';'), ids.map((id) => patternStatus(AGRI_PATTERNS.find((p) => p.id === id)!)).join(';'),
    ]);
  }
  return { fileName: `agriculture-matrices.${format}`, serialized: format === 'json' ? JSON.stringify(doc, null, 2) : '\ufeff' + rows.map((r) => r.map(csv).join(',')).join('\r\n'),
    mime: format === 'json' ? 'application/json;charset=utf-8' : 'text/csv;charset=utf-8' };
}
/** 每个模式每个维度一行：声明等级、作者说明、事实依据、冲突。 */
export function evidenceFile() {
  const rows = [['pattern_id', 'pattern_name', 'status', 'axis', 'level', 'note', 'basis', 'conflict']];
  for (const p of AGRI_PATTERNS) { const ev = capabilityEvidence(p); for (const a of CAP_AXES)
    rows.push([p.id, p.name, patternStatus(p), a, ev[a].level, ev[a].note, ev[a].basis.join('；'), ev[a].conflict]); }
  return { fileName: 'agriculture-capability-evidence.csv', serialized: '\ufeff' + rows.map((r) => r.map(csv).join(',')).join('\r\n'), mime: 'text/csv;charset=utf-8' };
}
export function downloadFile(file: { serialized: string; mime: string; fileName: string }, onDownload?: (blob: Blob, name: string) => void) {
  const blob = new Blob([file.serialized], { type: file.mime });
  if (onDownload) onDownload(blob, file.fileName);
  else {
    const href = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = href; link.download = file.fileName; link.click();
    } finally { window.setTimeout(() => URL.revokeObjectURL(href), 2000); }
  }
  return file;
}

export function downloadMatrices(format: 'json' | 'csv', onDownload?: (blob: Blob, name: string) => void) {
  return downloadFile(matrixFile(format), onDownload);
}
