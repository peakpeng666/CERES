/**
 * 农业场景构建用的几何工具：确定性随机、多边形包含/距离、可放置性检查。
 *
 * 可放置性规则镜像后端 validate.py：位置必须在边界内、距边界 ≥ r、
 * 不在任何障碍内且距障碍 ≥ r。这是前端保守预检查，不替代后端Shapely校验；
 * tools/check-agri-contract.mjs 使用真实后端验证所有构建器。
 */

export type XY = [number, number];
export type Ring = XY[];

/** 确定性随机数（mulberry32 变体）：同一种子同一实例，保证可复现。 */
export function validateSeed(seed: number): void {
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 0xffffffff) {
    throw new Error('随机种子须为 0–4294967295 的整数');
  }
}

export function rng(seed: number): () => number {
  validateSeed(seed);
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 1831565813) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function pointInPolygon(p: XY, poly: Ring): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function segDist(p: XY, a: XY, b: XY): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

export function distToPolygon(p: XY, poly: Ring): number {
  let d = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) d = Math.min(d, segDist(p, poly[j], poly[i]));
  return d;
}

/** 后端 validate.py 放置校验的镜像：在边界内、离边界 ≥ r、不在障碍内、离障碍 ≥ r。 */
export function placeable(p: XY, boundary: Ring, obstacles: Ring[], r: number): boolean {
  const finitePoint = (xy: XY) => xy.length === 2 && xy.every(Number.isFinite);
  if (!finitePoint(p) || !Number.isFinite(r) || r < 0) return false;
  if ([boundary, ...obstacles].some(ring => ring.length < 3 || !ring.every(finitePoint))) return false;
  if (!pointInPolygon(p, boundary)) return false;
  if (distToPolygon(p, boundary) < r) return false;
  for (const ob of obstacles) {
    if (pointInPolygon(p, ob)) return false;
    if (distToPolygon(p, ob) < r) return false;
  }
  return true;
}

/** 圆盘多边形近似。 */
export function disc(cx: number, cy: number, r: number, n = 12, rot = 0): Ring {
  const pts: Ring = [];
  for (let i = 0; i < n; i++) {
    const a = rot + (2 * Math.PI * i) / n;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

export function rect(x0: number, y0: number, x1: number, y1: number): Ring {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** L 形田块（非凸用例）。 */
export function lShape(w: number, h: number): Ring {
  const cw = w * 0.55;
  const ch = h * 0.55;
  return [[0, 0], [w, 0], [w, ch], [cw, ch], [cw, h], [0, h]];
}

/** 圆角两位小数：构建产物坐标稳定、便于 diff 与缓存。 */
export function round2(x: number): number {
  return Math.round(x * 100) / 100;
}

/** 环形布置（葡萄园/茶园条垄等）。 */
export function ringPoints(cx: number, cy: number, r: number, n: number, rot = 0): Ring {
  return disc(cx, cy, r, n, rot);
}

/** 鞋带公式有向面积（逆时针为正）。 */
export function signedArea(poly: Ring): number {
  let s = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) s += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
  return s / 2;
}

function segmentsCross(a: XY, b: XY, c: XY, d: XY): boolean {
  const o = (p: XY, q: XY, r: XY) => Math.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]));
  return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0;
}

/** 非相邻边严格相交即视为自交（简单多边形检查，O(n²)，预览规模足够）。 */
export function selfIntersects(poly: Ring): boolean {
  const n = poly.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue;
      if (segmentsCross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return true;
    }
  }
  return false;
}

/** 闭线段相交（含端点接触与共线重叠）。 */
export function segmentsIntersect(a: XY, b: XY, c: XY, d: XY): boolean {
  const o = (p: XY, q: XY, r: XY) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const on = (p: XY, q: XY, r: XY) => Math.min(p[0], q[0]) <= r[0] && r[0] <= Math.max(p[0], q[0]) && Math.min(p[1], q[1]) <= r[1] && r[1] <= Math.max(p[1], q[1]);
  const d1 = o(c, d, a), d2 = o(c, d, b), d3 = o(a, b, c), d4 = o(a, b, d);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
  return (d1 === 0 && on(c, d, a)) || (d2 === 0 && on(c, d, b)) || (d3 === 0 && on(a, b, c)) || (d4 === 0 && on(a, b, d));
}

/** 环的首个非相邻边相交（含端点接触/共线重叠，比 selfIntersects 的严格相交更严）：边下标 i<j（边 i 为 ring[i]→ring[i+1]）与交点；简单多边形返回 null。 */
export function selfIntersectionPoint(ring: Ring): { i: number; j: number; at: XY } | null {
  const n = ring.length;
  if (n < 4) return null;
  for (let i = 0; i < n; i++) {
    const a = ring[i], b = ring[(i + 1) % n];
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue;
      const c = ring[j], d = ring[(j + 1) % n];
      if (!segmentsIntersect(a, b, c, d)) continue;
      const den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0]);
      let at: XY;
      if (den !== 0) {
        const t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den;
        at = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      } else {
        // 共线接触/重叠：取落在另一条边上的端点。
        at = [c, d, a, b].find((p, k) => (k < 2 ? segDist(p, a, b) : segDist(p, c, d)) === 0) ?? a;
      }
      return { i, j, at };
    }
  }
  return null;
}
