import { describe, expect, it } from 'vitest';
import { lShape, ringPoints, segDist, selfIntersectionPoint, selfIntersects, type Ring, type XY } from '../agri/geometry';

/** Independent brute force: do two non-adjacent edges come within ~0 of each other? */
function bruteForce(r: Ring): boolean {
  const n = r.length;
  if (n < 4) return false;
  const near = (a: XY, b: XY, c: XY, d: XY) => {
    if (Math.min(segDist(a, c, d), segDist(b, c, d), segDist(c, a, b), segDist(d, a, b)) < 1e-12) return true;
    // proper crossing: sample along ab for a sign change of the side of cd
    const side = (p: XY) => Math.sign((d[0] - c[0]) * (p[1] - c[1]) - (d[1] - c[1]) * (p[0] - c[0]));
    const side2 = (p: XY) => Math.sign((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]));
    return side(a) * side(b) < 0 && side2(c) * side2(d) < 0;
  };
  for (let i = 0; i < n; i++) for (let j = i + 2; j < n; j++) {
    if (i === 0 && j === n - 1) continue;
    if (near(r[i], r[(i + 1) % n], r[j], r[(j + 1) % n])) return true;
  }
  return false;
}

/** [name, ring, any contact (selfIntersectionPoint), strict crossing (Luna's selfIntersects)] */
const cases: [string, Ring, boolean, boolean][] = [
  ['square', [[0, 0], [4, 0], [4, 4], [0, 4]], false, false],
  ['triangle', [[0, 0], [4, 0], [2, 3]], false, false],
  ['L shape (concave)', lShape(10, 8), false, false],
  ['circle-ish 24-gon', ringPoints(0, 0, 5, 24), false, false],
  ['star (concave, simple)', [[0, 3], [1, 1], [3, 1], [1.5, -0.5], [2, -3], [0, -1.5], [-2, -3], [-1.5, -0.5], [-3, 1], [-1, 1]], false, false],
  ['bow-tie', [[0, 0], [4, 4], [4, 0], [0, 4]], true, true],
  ['pentagram', ringPoints(0, 0, 5, 5).map((_, k, a) => a[(k * 2) % 5]), true, true],
  ['figure eight (6 vertices)', [[0, 0], [2, 2], [4, 0], [4, 2], [2, 0], [0, 2]], true, true],
  ['degenerate: two points', [[0, 0], [1, 1]], false, false],
  ['degenerate: collinear triangle', [[0, 0], [1, 0], [2, 0]], false, false],
  ['degenerate: collinear quad (doubles back)', [[0, 0], [2, 0], [1, 0], [3, 0]], true, false],
  ['collinear touching: vertex on a non-adjacent edge', [[0, 0], [4, 0], [4, 4], [2, 0], [0, 4]], true, false],
  ['collinear overlap of non-adjacent edges', [[0, 0], [4, 0], [4, 2], [3, 2], [3, 0], [1, 0], [1, 2], [0, 2]], true, false],
  ['repeated vertex (pinch)', [[0, 0], [2, 0], [2, 2], [0, 0], [-2, 2], [-2, 0]], true, false],
  ['zero-area spike', [[0, 0], [4, 0], [4, 4], [2, 4], [2, 6], [2, 4], [0, 4]], true, false],
];

describe('selfIntersectionPoint is consistent with agri selfIntersects', () => {
  for (const [name, ring, contact, crossing] of cases) {
    it(name, () => {
      const hit = selfIntersectionPoint(ring);
      expect(selfIntersects(ring)).toBe(crossing);
      expect(hit !== null).toBe(contact);
      expect(bruteForce(ring)).toBe(contact);
      // Every strict crossing Luna reports is also reported here (ours additionally flags touching / collinear contact).
      if (selfIntersects(ring)) expect(hit).not.toBeNull();
      if (hit) {
        expect(hit.i).toBeLessThan(hit.j);
        const n = ring.length;
        expect(segDist(hit.at, ring[hit.i], ring[(hit.i + 1) % n])).toBeLessThan(1e-9);
        expect(segDist(hit.at, ring[hit.j], ring[(hit.j + 1) % n])).toBeLessThan(1e-9);
      }
    });
  }
});
