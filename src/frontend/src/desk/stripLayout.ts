/** Where the dots of a strip plot go; no drawing here, so it can be tested. */
export interface StripRange { lo: number; hi: number }
export interface StripSpot { x: number; row: number }

const PAD = 0.1;             // room for the labels of the reference lines at both ends
const DOT_SHARE = 0.036;     // a dot and its ring as a share of a narrow pane
const MAX_ROWS = 6;

/** The axis of these values. Identical values still get a span, or every dot would divide by zero. */
export function stripRange(values: number[]): StripRange {
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || Math.max(Math.abs(hi) * 0.02, 0.01);
  return { lo: lo - span * PAD, hi: hi + span * PAD };
}

/** Each value's place along the axis, 0 to 1, and the row that keeps it clear of its neighbours. Input order is kept. */
export function stripLayout(values: number[], range: StripRange, gap = DOT_SHARE): StripSpot[] {
  const spots: StripSpot[] = values.map((v) => ({ x: (v - range.lo) / (range.hi - range.lo), row: 0 }));
  const ends: number[] = [];
  for (const i of values.map((_, k) => k).sort((a, b) => values[a] - values[b])) {
    let row = ends.findIndex((end) => spots[i].x - end >= gap);
    if (row < 0) row = ends.length < MAX_ROWS ? ends.length : ends.indexOf(Math.min(...ends));
    ends[row] = spots[i].x;
    spots[i].row = row;
  }
  return spots;
}
