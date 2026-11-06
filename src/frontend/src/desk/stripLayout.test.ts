import { describe, expect, it } from 'vitest';
import { stripLayout, stripRange } from './stripLayout';

describe('strip plot layout', () => {
  it('keeps every value on the axis with room at both ends', () => {
    const range = stripRange([100, 150, 200]);
    expect(range.lo).toBeLessThan(100);
    expect(range.hi).toBeGreaterThan(200);
    for (const spot of stripLayout([100, 150, 200], range)) {
      expect(spot.x).toBeGreaterThan(0);
      expect(spot.x).toBeLessThan(1);
    }
  });

  it('gives identical values a span instead of dividing by zero', () => {
    const range = stripRange([5, 5, 5]);
    expect(range.hi).toBeGreaterThan(range.lo);
    expect(stripLayout([5, 5, 5], range).every((s) => Number.isFinite(s.x))).toBe(true);
  });

  it('places a value in proportion along the axis, in the order given', () => {
    const spots = stripLayout([200, 100], { lo: 100, hi: 200 });
    expect(spots[0].x).toBe(1);
    expect(spots[1].x).toBe(0);
  });

  it('stacks dots that would touch and leaves distant ones on the first row', () => {
    const spots = stripLayout([0.5, 0.505, 0.9], { lo: 0, hi: 1 }, 0.036);
    expect(spots[0].row).toBe(0);
    expect(spots[1].row).toBe(1);
    expect(spots[2].row).toBe(0);
  });

  it('never lets two dots on one row touch while there are rows left', () => {
    const values = [0.1, 0.11, 0.12, 0.13, 0.5, 0.51, 0.9];
    const spots = stripLayout(values, { lo: 0, hi: 1 }, 0.036);
    for (const a of spots) for (const b of spots) if (a !== b && a.row === b.row) expect(Math.abs(a.x - b.x)).toBeGreaterThanOrEqual(0.036);
  });

  it('stops at a fixed height when many runs share one value', () => {
    const spots = stripLayout(Array.from({ length: 40 }, () => 7), { lo: 0, hi: 10 });
    expect(Math.max(...spots.map((s) => s.row))).toBeLessThan(6);
  });
});
