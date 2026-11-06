import { useEffect, useRef, type RefObject } from 'react';
import { routeColor } from '../lab/views/plan/planModel';
import type { XY } from '../lab/types';
import type { FleetInstance } from '../studio/types';
import { n0, n1, obstacleName } from './format';
import s from './desk.module.css';

const NS = 'http://www.w3.org/2000/svg';
type Attrs = Record<string, string | number | null | undefined>;
function sv<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: (Node | string | number)[]): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) e.setAttribute(k, String(v));
  for (const k of kids) e.append(typeof k === 'object' ? k : String(k));
  return e;
}
const pts = (a: XY[]) => a.map((p) => `${p[0]},${-p[1]}`).join(' ');
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);
export type Box = [number, number, number, number];
export function boxOf(points: XY[]): Box {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of points) {
    x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]);
  }
  return [x0, y0, x1, y1];
}
export function cumulative(line: XY[]): number[] {
  const c = [0];
  for (let i = 1; i < line.length; i++) c.push(c[i - 1] + dist(line[i - 1], line[i]));
  return c;
}

export interface MapRoute { name: string; line: XY[]; cost: number | null; services: { task: number }[] }
export interface MapPlan { key: string; routes: MapRoute[] }
export interface MapView {
  plan?: MapPlan | null;
  /** Focused vehicle index. */
  v?: number | null;
  stop?: { xy: XY; label: string } | null;
  plot?: string | null;
  obstacle?: string | null;
  /** Recorded trajectories drawn over the dashed plan, in vehicle order. */
  trails?: { line: XY[]; index: number }[] | null;
}
export interface MapApi { hover: (v: number | null) => void }

const WATER = new Set(['pond', 'ditch', 'canal']);

/** Read-only plan view whose camera follows the current layer. Imperative on purpose: it animates per frame. */
class FieldMap implements MapApi {
  private cam = { cx: 0, cy: 0, w: 100 };
  private W = 0;
  private H = 0;
  private raf = 0;
  private dots: { el: SVGCircleElement; r: number }[] = [];
  private marks: { el: SVGElement; x: number; y: number; a?: number; fixed?: boolean }[] = [];
  private inst: FleetInstance | null = null;
  private world: Box | null = null;
  private focus: Box | null = null;
  private plots: Record<string, SVGPolygonElement> = {};
  private obs: Record<string, SVGPolygonElement> = {};
  private routes: SVGPolylineElement[] = [];
  private planKey: string | null = null;
  private sceneKey: string | null = null;
  private readonly svg = sv('svg', { class: s.mapSvg, role: 'img', 'aria-label': '任务地图' });
  private readonly gBase = sv('g');
  private readonly gLane = sv('g');
  private readonly gObs = sv('g');
  private readonly gRoute = sv('g');
  private readonly gTrail = sv('g');
  private readonly gTask = sv('g');
  private readonly gMark = sv('g');
  private readonly gSeq = sv('g');
  private readonly gLbl = sv('g');
  private readonly scaleText = document.createElement('span');
  private readonly scaleBar = document.createElement('i');
  private readonly dims = document.createElement('div');
  private readonly tip = document.createElement('div');
  private readonly observer: ResizeObserver;
  onTask: ((id: number) => void) | null = null;
  onRoute: ((v: number) => void) | null = null;

  constructor(private readonly host: HTMLElement) {
    const defs = sv('defs');
    defs.append(sv('pattern', { id: 'desk-hatch', patternUnits: 'userSpaceOnUse', width: 2.2, height: 2.2, patternTransform: 'rotate(45)' },
      sv('rect', { width: 2.2, height: 2.2, style: 'fill:var(--map-solid)' }), sv('line', { x1: 0, y1: 0, x2: 0, y2: 2.2, style: 'stroke:var(--map-solid-edge);stroke-width:.5' })));
    this.svg.append(defs, this.gBase, this.gLane, this.gObs, this.gRoute, this.gTrail, this.gTask, this.gMark, this.gSeq, this.gLbl);
    const scale = document.createElement('div');
    scale.className = s.scalebar;
    scale.append(this.scaleText, this.scaleBar);
    this.dims.className = s.dims;
    const fit = document.createElement('button');
    fit.type = 'button'; fit.className = s.fitBtn; fit.textContent = '复位';
    fit.onclick = () => this.fly(this.focus);
    this.tip.className = s.mapTip; this.tip.hidden = true;
    host.append(this.svg, scale, this.dims, fit, this.tip);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.bind();
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.observer.disconnect();
    this.host.replaceChildren();
  }

  private resize() {
    const r = this.host.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    this.W = r.width; this.H = r.height;
    const c = this.camFor(this.focus);
    if (c) { this.cam = c; this.apply(); }
  }

  scene(key: string, inst: FleetInstance, lanes: XY[][]) {
    if (this.sceneKey === key) return;
    this.sceneKey = key; this.planKey = null; this.inst = inst; this.routes = [];
    for (const g of [this.gBase, this.gLane, this.gObs, this.gRoute, this.gTrail, this.gTask, this.gMark, this.gSeq, this.gLbl]) g.replaceChildren();
    this.dots = []; this.marks = []; this.plots = {}; this.obs = {};
    this.gBase.append(sv('polygon', { class: s.mGround, points: pts(inst.boundary) }));
    for (const p of inst.plots ?? []) {
      const e = sv('polygon', { class: s.mPlot, points: pts(p.polygon) });
      this.plots[p.id] = e; this.gBase.append(e);
      const b = boxOf(p.polygon);
      this.gLbl.append(sv('text', { class: s.mLabel, x: b[0], y: -b[3], dx: '.5em', dy: '1.3em' }, [p.name, p.crop].filter(Boolean).join(' · ')));
    }
    for (const ln of lanes) this.gLane.append(sv('polyline', { class: s.mLane, points: pts(ln) }));
    for (const o of inst.obstacles ?? []) {
      const water = WATER.has(o.kind);
      const e = sv('polygon', { class: `${s.mObstacle} ${water ? s.mWater : s.mSolid}`, points: pts(o.polygon), style: water ? null : 'fill:url(#desk-hatch)', 'data-tip': o.name || obstacleName(o.kind) });
      this.obs[o.id] = e; this.gObs.append(e);
    }
    for (const d of inst.depots?.length ? inst.depots : [inst.depot]) {
      const g = sv('g', { class: s.mDepot }, sv('rect', { x: -4.5, y: -4.5, width: 9, height: 9, rx: 1.5 }), sv('path', { d: 'M-2 0.5 0-1.6 2 0.5' }));
      this.gMark.append(g); this.marks.push({ el: g, x: d[0], y: d[1], fixed: true });
    }
    const b = boxOf(inst.boundary);
    this.world = b; this.focus = null;
    this.dims.textContent = `${n0(b[2] - b[0])} × ${n0(b[3] - b[1])} m`;
    const c = this.camFor(null);
    if (c) { this.cam = c; this.apply(); }
  }

  draw(o: MapView) {
    const key = o.plan?.key ?? '';
    if (this.planKey !== key) {
      this.planKey = key; this.gRoute.replaceChildren(); this.routes = [];
      (o.plan?.routes ?? []).forEach((r, i) => {
        const line = sv('polyline', { class: s.mRoute, points: pts(r.line), style: `stroke:${routeColor(i)}` });
        this.routes.push(line);
        this.gRoute.append(line, sv('polyline', { class: s.mHit, points: pts(r.line), 'data-v': i, 'data-tip': `${r.name} · ${n1(r.cost)} m` }));
      });
    }
    if (o.v !== null && o.v !== undefined) this.gRoute.setAttribute('data-focus', String(o.v)); else this.gRoute.removeAttribute('data-focus');
    this.gRoute.removeAttribute('data-hover');
    this.routes.forEach((e, i) => { e.classList.toggle(s.mOn, i === o.v); e.classList.remove(s.mHov); });
    for (const k of Object.keys(this.plots)) this.plots[k].classList.toggle(s.mOn, k === o.plot);
    for (const k of Object.keys(this.obs)) this.obs[k].classList.toggle(s.mOn, k === o.obstacle);
    this.tasks(o);
    this.gTrail.replaceChildren();
    if (o.trails?.length) {
      this.gRoute.style.display = 'none';
      for (const t of o.trails) {
        const plan = o.plan?.routes[t.index];
        if (plan) this.gTrail.append(sv('polyline', { class: s.mGhost, points: pts(plan.line) }));
        this.gTrail.append(sv('polyline', { class: s.mTrail, points: pts(t.line), style: `stroke:${routeColor(t.index)}` }));
      }
    } else this.gRoute.style.display = '';
    this.apply();
  }

  private tasks(o: MapView) {
    this.gTask.replaceChildren(); this.gSeq.replaceChildren();
    for (const m of this.marks) if (!m.fixed) m.el.remove();
    this.marks = this.marks.filter((m) => m.fixed);
    this.dots = [];
    const owner = new Map<number, number>(); const order = new Map<number, number>();
    o.plan?.routes.forEach((r, i) => r.services.forEach((sv2, n) => { owner.set(sv2.task, i); order.set(sv2.task, n + 1); }));
    const v = o.v ?? null;
    for (const t of this.inst?.tasks ?? []) {
      const own = owner.get(t.id);
      const focused = v !== null && own === v;
      const off = v !== null && !focused;
      const c = sv('circle', {
        class: s.mTask + (off ? ` ${s.mOff}` : ''), cx: t.xy[0], cy: -t.xy[1], 'data-task': t.id, 'data-v': own ?? null,
        'data-tip': `${t.label ?? t.id}${own !== undefined ? ` · ${o.plan!.routes[own].name} 第 ${order.get(t.id)} 站` : ''}`,
        style: `fill:${own !== undefined ? routeColor(own) : 'var(--map-halo)'};stroke:${own !== undefined ? 'var(--map-halo)' : 'var(--map-text)'}`,
      });
      this.gTask.append(c);
      this.dots.push({ el: c, r: focused ? 8 : off ? 2 : 3 });
      if (focused) this.gSeq.append(sv('text', { class: s.mSeq, x: t.xy[0], y: -t.xy[1] }, order.get(t.id) ?? ''));
    }
    if (v !== null && o.plan?.routes[v]) {
      const line = o.plan.routes[v].line;
      const c = cumulative(line); const total = c[c.length - 1];
      const span = this.world ? Math.max(this.world[2] - this.world[0], 1) : 100;
      const n = Math.max(4, Math.min(12, Math.round(total / (span / 3))));
      let seg = 0;
      for (let i = 1; i < n; i++) {
        const at = (total * i) / n;
        while (seg < c.length - 2 && c[seg + 1] < at) seg++;
        const a = line[seg], b = line[seg + 1];
        if (!b || dist(a, b) < 1e-6) continue;
        const f = (at - c[seg]) / (c[seg + 1] - c[seg]);
        const el = sv('path', { class: s.mArrow, d: 'M-3.6 -3 4 0 -3.6 3 -2 0z', style: `color:${routeColor(v)}` });
        this.gMark.append(el);
        this.marks.push({ el, x: a[0] + (b[0] - a[0]) * f, y: a[1] + (b[1] - a[1]) * f, a: (Math.atan2(-(b[1] - a[1]), b[0] - a[0]) * 180) / Math.PI });
      }
    }
    if (o.stop) {
      const ring = sv('circle', { class: s.mRing, cx: o.stop.xy[0], cy: -o.stop.xy[1] });
      this.gTask.append(ring); this.dots.push({ el: ring, r: 13 });
      this.gSeq.append(sv('text', { class: s.mSelLabel, x: o.stop.xy[0], y: -o.stop.xy[1], dx: '1.5em', dy: '.35em' }, o.stop.label));
    }
  }

  private camFor(b: Box | null) {
    const box = b ?? this.world;
    if (!box || !this.W) return null;
    const pad = 26;
    const bw = Math.max(box[2] - box[0], 0.5), bh = Math.max(box[3] - box[1], 0.5);
    const w = Math.max((bw / (this.W - 2 * pad)) * this.W, (bh / (this.H - 2 * pad - 14)) * this.W);
    return { cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2 - (9 * w) / this.W, w };
  }

  fly(b: Box | null) {
    this.focus = b;
    const to = this.camFor(b);
    if (!to) return;
    cancelAnimationFrame(this.raf);
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { this.cam = to; this.apply(); return; }
    const from = { ...this.cam }, t0 = performance.now(), ms = 460;
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / ms), e = 1 - (1 - p) ** 3;
      this.cam = { cx: from.cx + (to.cx - from.cx) * e, cy: from.cy + (to.cy - from.cy) * e, w: Math.exp(Math.log(from.w) + (Math.log(to.w) - Math.log(from.w)) * e) };
      this.apply();
      if (p < 1) this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  private apply() {
    if (!this.W) return;
    const { cx, cy, w } = this.cam; const k = w / this.W; const hh = k * this.H;
    this.svg.setAttribute('viewBox', `${cx - w / 2} ${-cy - hh / 2} ${w} ${hh}`);
    for (const d of this.dots) d.el.setAttribute('r', String(d.r * k));
    for (const m of this.marks) m.el.setAttribute('transform', `translate(${m.x} ${-m.y}) rotate(${m.a ?? 0}) scale(${k})`);
    this.gLbl.style.fontSize = `${11 * k}px`;
    this.gSeq.style.fontSize = `${10.5 * k}px`;
    const L = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].find((x) => x / k >= 56) ?? 1000;
    this.scaleText.textContent = `${L} m`;
    this.scaleBar.style.width = `${L / k}px`;
  }

  hover(v: number | null) {
    if (this.gRoute.hasAttribute('data-focus')) return;
    if (v === null) this.gRoute.removeAttribute('data-hover'); else this.gRoute.setAttribute('data-hover', String(v));
    this.routes.forEach((e, i) => e.classList.toggle(s.mHov, i === v));
  }

  private bind() {
    const box = this.host;
    box.addEventListener('wheel', (e) => {
      e.preventDefault();
      cancelAnimationFrame(this.raf);
      const r = box.getBoundingClientRect(), k = this.cam.w / this.W;
      const wx = this.cam.cx + (e.clientX - r.left - this.W / 2) * k, wy = this.cam.cy - (e.clientY - r.top - this.H / 2) * k;
      const f = Math.exp(e.deltaY * 0.0016);
      this.cam = { w: this.cam.w * f, cx: wx - (wx - this.cam.cx) * f, cy: wy - (wy - this.cam.cy) * f };
      this.apply();
    }, { passive: false });
    let drag: { x: number; y: number; c: { cx: number; cy: number; w: number }; moved: boolean } | null = null;
    box.addEventListener('pointerdown', (e) => {
      if ((e.target as Element).closest('button')) return;
      drag = { x: e.clientX, y: e.clientY, c: { ...this.cam }, moved: false };
      box.setPointerCapture(e.pointerId);
    });
    box.addEventListener('pointermove', (e) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        if (drag.moved) {
          const k = this.cam.w / this.W;
          this.cam = { ...this.cam, cx: drag.c.cx - dx * k, cy: drag.c.cy + dy * k };
          this.apply();
        }
        return;
      }
      const t = document.elementFromPoint(e.clientX, e.clientY);
      const text = t?.getAttribute?.('data-tip');
      if (text) {
        const r = box.getBoundingClientRect();
        this.tip.hidden = false; this.tip.textContent = text;
        this.tip.style.left = `${Math.min(Math.max(e.clientX - r.left, 70), r.width - 70)}px`;
        this.tip.style.top = `${Math.max(e.clientY - r.top, 30)}px`;
      } else this.tip.hidden = true;
    });
    box.addEventListener('pointerleave', () => { this.tip.hidden = true; });
    box.addEventListener('pointerup', (e) => {
      const d = drag; drag = null;
      if (!d || d.moved) return;
      box.releasePointerCapture(e.pointerId);
      const t = document.elementFromPoint(e.clientX, e.clientY);
      if (!t?.getAttribute) return;
      if (t.hasAttribute('data-task')) this.onTask?.(Number(t.getAttribute('data-task')));
      else if (t.getAttribute('class') === s.mHit) this.onRoute?.(Number(t.getAttribute('data-v')));
    });
  }
}

export function LayerMap({ sceneKey, inst, lanes, view, focus, onTask, onRoute, apiRef }: {
  sceneKey: string; inst: FleetInstance; lanes: XY[][]; view: MapView; focus: Box | null;
  onTask?: (id: number) => void; onRoute?: (v: number) => void; apiRef?: RefObject<MapApi | null>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<FieldMap | null>(null);
  useEffect(() => {
    const m = new FieldMap(host.current!);
    map.current = m;
    if (apiRef) apiRef.current = m;
    return () => { m.destroy(); map.current = null; if (apiRef) apiRef.current = null; };
  }, [apiRef]);
  useEffect(() => {
    if (!map.current) return;
    map.current.onTask = onTask ?? null;
    map.current.onRoute = onRoute ?? null;
  });
  const lanesKey = lanes.length;
  useEffect(() => { map.current?.scene(`${sceneKey}:${lanesKey}`, inst, lanes); }, [sceneKey, lanesKey, inst, lanes]);
  useEffect(() => { map.current?.draw(view); map.current?.fly(focus); }, [sceneKey, lanesKey, view, focus]);
  return <div ref={host} className={s.mapHost} />;
}

/** Small static plan for lists and result cards. */
export function Thumb({ inst, routes }: { inst: FleetInstance; routes?: { line: XY[] }[] }) {
  const b = boxOf(inst.boundary);
  const pad = Math.max(b[2] - b[0], b[3] - b[1]) * 0.05;
  return (
    <svg className={s.thumb} viewBox={`${b[0] - pad} ${-b[3] - pad} ${b[2] - b[0] + pad * 2} ${b[3] - b[1] + pad * 2}`} preserveAspectRatio="xMidYMid meet" aria-hidden>
      <polygon points={pts(inst.boundary)} style={{ fill: 'var(--map-ground)', stroke: 'var(--map-edge)', strokeWidth: 0.75, vectorEffect: 'non-scaling-stroke' }} />
      {(inst.plots ?? []).map((p) => <polygon key={p.id} points={pts(p.polygon)} style={{ fill: 'var(--map-field)' }} />)}
      {(inst.obstacles ?? []).map((o) => <polygon key={o.id} points={pts(o.polygon)} style={{ fill: WATER.has(o.kind) ? 'var(--map-water)' : 'var(--map-solid-edge)' }} />)}
      {routes?.map((r, i) => <polyline key={i} points={pts(r.line)} style={{ fill: 'none', stroke: routeColor(i), strokeWidth: 0.8, vectorEffect: 'non-scaling-stroke' }} />)}
    </svg>
  );
}
