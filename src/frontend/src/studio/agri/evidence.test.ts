import { describe, expect, it } from 'vitest';
import { AGRI_PATTERNS, patternStatus } from './patterns';
import { CAP_AXES, capabilityEvidence, catalogStats } from './evidence';
import { capabilitySummary } from './matrix';
import { evidenceFile, matrixDocument } from './export';
import { filterPatterns } from './catalog';

describe('capability evidence', () => {
  it('every axis of every pattern has a basis', () => {
    for (const p of AGRI_PATTERNS) for (const a of CAP_AXES) expect(capabilityEvidence(p)[a].basis.length, `${p.id}/${a}`).toBeGreaterThan(0);
  });
  it('no declared level exceeds what the facts support', () => {
    expect(catalogStats().conflicts).toEqual([]);
  });
  it('proposals never claim editable, solvable or checkable', () => {
    for (const p of AGRI_PATTERNS.filter((x) => patternStatus(x) === 'proposal'))
      for (const a of ['editable', 'solvable', 'checkable'] as const) expect(capabilityEvidence(p)[a].level, `${p.id}/${a}`).toBe('no');
  });
  it('flags an overclaim', () => {
    const p = AGRI_PATTERNS.find((x) => patternStatus(x) === 'proposal')!;
    const bad = { ...p, capabilities: { ...p.capabilities, solvable: { level: 'yes' as const, note: 'x' } } };
    expect(capabilityEvidence(bad).solvable.conflict).toMatch('不支持');
  });
});

describe('single source for stats, matrix, filter and export', () => {
  it('matrix summary and export use catalogStats', () => {
    const s = catalogStats();
    expect(capabilitySummary()).toEqual(s.capabilities);
    const doc = matrixDocument();
    expect(doc.stats).toEqual(s);
    expect(doc.capabilities).toEqual(s.capabilities);
  });
  it('status counts equal filter results and export rows', () => {
    const s = catalogStats();
    for (const st of ['runnable', 'approx', 'proposal'] as const) {
      expect(filterPatterns('', '', st).length).toBe(s.status[st]);
      expect(matrixDocument().patterns.filter((p) => p.status === st).length).toBe(s.status[st]);
    }
    expect(s.total).toBe(AGRI_PATTERNS.length);
  });
  it('filtered stats are a subset count of the same function', () => {
    const sub = filterPatterns('', '', 'approx');
    expect(catalogStats(sub).status).toEqual({ runnable: 0, approx: sub.length, proposal: 0 });
  });
  it('export carries per-axis evidence and CSV has an evidence section', () => {
    const p = matrixDocument().patterns[0];
    expect(p.evidence.defined.basis.length).toBeGreaterThan(0);
    const lines = evidenceFile().serialized.split('\r\n');
    expect(lines.length).toBe(1 + AGRI_PATTERNS.length * CAP_AXES.length);
  });
});
