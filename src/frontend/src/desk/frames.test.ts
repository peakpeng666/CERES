import { describe, expect, it } from 'vitest';
import { decodeStack, encodeStack, type Frame } from './frames';

describe('pane address', () => {
  it('round-trips every layer of a plan', () => {
    const stack: Frame[] = [
      { t: 'plans' }, { t: 'plan', run: 'run_20261104T154614_30a7da' }, { t: 'vehicle', run: 'run_20261104T154614_30a7da', v: 2 },
      { t: 'stop', run: 'run_20261104T154614_30a7da', v: 2, task: 35 },
    ];
    expect(decodeStack(encodeStack(stack))).toEqual(stack);
  });
  it('keeps field keys and panels', () => {
    const stack: Frame[] = [{ t: 'contract' }, { t: 'field', key: 'field_size_m' }];
    expect(decodeStack(encodeStack(stack))).toEqual(stack);
    expect(decodeStack('plans~panel.compare')).toEqual([{ t: 'plans' }, { t: 'panel', id: 'compare' }]);
  });
  it('keeps a physics run under its plan', () => {
    const stack: Frame[] = [{ t: 'plans' }, { t: 'plan', run: 'run_1' }, { t: 'phys', run: 'run_1', id: 'phys_20261105T050633_5be27d' }];
    expect(decodeStack(encodeStack(stack))).toEqual(stack);
    expect(decodeStack('plans~phys.run_1')).toBeNull();
  });
  it('rejects a broken address instead of guessing', () => {
    expect(decodeStack('plans~vehicle.run_1.x')).toBeNull();
    expect(decodeStack('nonsense')).toBeNull();
    expect(decodeStack('')).toBeNull();
    expect(decodeStack(null)).toBeNull();
  });
});
