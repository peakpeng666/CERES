import { describe, expect, it } from 'vitest';
import { buildHash, isPageChange, parseHash } from './router';

describe('parseHash', () => {
  it('routes known views and ids', () => {
    expect(parseHash('#/').view).toBe('lab');
    expect(parseHash('#/bpp').view).toBe('overview');
    expect(parseHash('#/plan/farm-rows-warehouse').id).toBe('farm-rows-warehouse');
    expect(buildHash('overview')).toBe('#/bpp');
    expect(buildHash('lab')).toBe('#/');
    expect(parseHash('#/samples').view).toBe('samples');
    const job = parseHash(buildHash('jobs', 'job-123'));
    expect(job.view).toBe('jobs');
    expect(job.id).toBe('job-123');
    const r = parseHash('#/replay/confirmation_weibull_3_45_r0__descending?a=SELECTOR&t=500');
    expect(r.view).toBe('replay');
    expect(r.id).toBe('confirmation_weibull_3_45_r0__descending');
    expect(r.params.get('a')).toBe('SELECTOR');
    expect(r.params.get('t')).toBe('500');
  });

  it('maps unknown views to notfound', () => {
    expect(parseHash('#/nope').view).toBe('notfound');
  });

  it('does not throw on malformed percent-encoding (F1)', () => {
    expect(() => parseHash('#/replay/%E0%A4%A')).not.toThrow();
    const r = parseHash('#/replay/%E0%A4%A');
    // Degrades to the raw segment so the view can show a graceful "not found".
    expect(r.view).toBe('replay');
    expect(typeof r.id).toBe('string');
  });

  it('round-trips through buildHash', () => {
    const h = buildHash('replay', 'abc', { a: 'SELECTOR', b: 'SBS', t: 500 });
    const r = parseHash(h);
    expect(r.id).toBe('abc');
    expect(r.params.get('b')).toBe('SBS');
  });

  it('decodes a valid encoded id', () => {
    expect(parseHash(`#/replay/${encodeURIComponent('a b')}`).id).toBe('a b');
  });
});

describe('isPageChange', () => {
  const change = (a: string, b: string) => isPageChange(parseHash(a), parseHash(b));
  it('a new view, or going between a new task and a task, is a change of page', () => {
    expect(change('#/agent', '#/maps')).toBe(true);
    expect(change('#/agent', '#/agent/agt_1')).toBe(true);
    expect(change('#/agent/agt_1', '#/agent')).toBe(true);
  });
  it('moving within a view is not', () => {
    expect(change('#/agent/agt_1', '#/agent/agt_2')).toBe(false);
    expect(change('#/system/models', '#/system/data')).toBe(false);
  });
});
