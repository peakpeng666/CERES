import { useCallback, useEffect, useId, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type ReactNode, type WheelEvent as RWheelEvent } from 'react';
import { useElementSize } from '../../lib/useElementSize';
import { fitView, toScreen, toWorld, worldTransform, zoomAt, type View } from '../../lab/canvas/view';
import type { XY } from '../../lab/types';
import type { FleetInstance, FleetRoute, ObstacleKind, Selection, Tool } from '../types';
import { addDepot, depotsOf, planningFingerprint, setDepot } from '../fleet';
import { playbackDistanceAtTime } from '../planningPlayback';
import {
  OBSTACLE_STYLE,
  along,
  boundsOf,
  centroid,
  nextObstacleId,
  obstacleShape,
  pointInPolygon,
  rect,
  round,
  taskIssues,
  translate,
  vehicleOfTask,
} from './geometry';
import { fromSelection, itemPoints, itemsInBox, liveRefs, refKey, sameRef, toSelection, translateItems, type ItemRef } from './selection';
import { SelectionPanel } from './SelectionPanel';
import { IssuesPanel } from './IssuesPanel';
import { validateGeometry, type GeometryIssue } from './validation';
import { decimateForDisplay } from './decimate';
import { ALL_LAYERS, AxisCompass, KindPattern, LayerLegendPanel, OriginMark, legendEntries, routeDash, type LayerKey, type Layers } from './legend';
import s from './editor.module.css';

export interface FieldEditorProps {
  inst: FleetInstance;
  routes: FleetRoute[];
  hidden: string[];
  playback: number | null;
  tool: Tool;
  obstacleKind: ObstacleKind;
  selection: Selection;
  editable: boolean;
  fitKey: string;
  /** Lane centre lines between crop rows (drawn faintly when vehicles travel along the rows). */
  lanes?: XY[][];
  violations?: XY[];
  /** Display-only layer under the plots (a map asset in task-map metres); never hit-tested. */
  background?: ReactNode;
  /** Extra world-space layer above the routes (e.g. a simulation replay); receives the view for pixel-sized marks. */
  overlay?: (view: View) => ReactNode;
  onEdit: (fn: (i: FleetInstance) => FleetInstance) => void;
  onSelect: (s: Selection) => void;
  onCursor?: (p: XY | null) => void;
}

type PolyTarget = 'obstacle' | 'plot' | 'corridor';
type Drag =
  | { kind: 'pan'; sx: number; sy: number; view: View }
  | { kind: 'task'; id: number; from: XY; orig: XY }
  | { kind: 'depot'; index: number; from: XY; orig: XY }
  | { kind: 'unload'; index: number; from: XY; orig: XY }
  | { kind: 'poly'; target: PolyTarget; id: string; from: XY; orig: XY[] }
  | { kind: 'vertex'; target: PolyTarget | 'boundary'; id: string; index: number }
  | { kind: 'shape'; target: PolyTarget; from: XY; to: XY }
  | { kind: 'marquee'; from: XY; to: XY; add: boolean }
  | { kind: 'multi'; from: XY; refs: ItemRef[]; orig: FleetInstance };

const GRID_STEPS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];

export function FieldEditor(props: FieldEditorProps) {
  const { inst, routes, hidden, playback, tool, obstacleKind, selection, editable, fitKey, lanes, violations, background, overlay, onEdit, onSelect, onCursor } = props;
  const [box, size] = useElementSize<HTMLDivElement>();
  const [outer, outerSize] = useElementSize<HTMLDivElement>();
  // Panels sit beside the map (wide) or below it (narrow / portrait), never on top; the whole side can be collapsed.
  const narrow = outerSize.width > 0 && (outerSize.width < 720 || outerSize.height > outerSize.width * 1.1);
  const [sideOpen, setSideOpen] = useState<boolean | null>(null);
  const sideShown = sideOpen ?? !narrow;
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 });
  const [preview, setPreview] = useState<FleetInstance | null>(null);
  const [shape, setShape] = useState<{ a: XY; b: XY; target: PolyTarget } | null>(null);
  const drag = useRef<Drag | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const fittedFor = useRef('');
  // Until the user pans or zooms, the map refits whenever its panel resizes.
  const touched = useRef(false);
  const shown = preview ?? inst;
  // Multi-selection lives here; the store keeps the primary item (first of the list) as its single selection.
  const [picked, setPicked] = useState<ItemRef[]>([]);
  const [marquee, setMarquee] = useState<{ a: XY; b: XY } | null>(null);
  const [layers, setLayers] = useState<Layers>(ALL_LAYERS);
  const toggleLayer = useCallback((k: LayerKey) => setLayers((l) => ({ ...l, [k]: !l[k] })), []);
  const idp = useId().replace(/:/g, '');
  const multi = useMemo(() => liveRefs(inst, picked), [inst, picked]);
  useEffect(() => {
    const r = fromSelection(selection);
    if (!r) setPicked((cur) => (cur.length > 1 ? cur : []));
    else setPicked((cur) => (cur.some((x) => sameRef(x, r)) ? cur : [r]));
  }, [selection]);
  const selectRefs = useCallback((refs: ItemRef[]) => {
    setPicked(refs);
    onSelect(toSelection(refs[0]));
  }, [onSelect]);

  const fit = useCallback(() => {
    if (!size.width || !size.height) return;
    touched.current = false;
    const [x0, y0, x1, y1] = boundsOf(inst);
    setView(fitView({ minX: x0, minY: y0, maxX: x1, maxY: y1 }, size.width, size.height, 28));
  }, [inst, size.width, size.height]);
  const fitRef = useRef(fit);
  fitRef.current = fit;

  useEffect(() => {
    if (!size.width || !size.height) return;
    if (fittedFor.current !== fitKey || !touched.current) {
      fittedFor.current = fitKey;
      fitRef.current();
    }
  }, [fitKey, size.width, size.height]);

  useEffect(() => {
    const onFit = () => fit();
    window.addEventListener('studio:fit', onFit);
    return () => window.removeEventListener('studio:fit', onFit);
  }, [fit]);

  // Centre on a point (e.g. an anomaly in a replay), showing about `span` metres across.
  useEffect(() => {
    const onCenter = (e: Event) => {
      const { xy, span = 24, expectedInput } = (e as CustomEvent<{ xy: XY; span?: number; expectedInput?: string }>).detail ?? {};
      if (expectedInput && expectedInput !== planningFingerprint(inst)) return;
      if (!xy || !size.width || !size.height) return;
      touched.current = true;
      setView(fitView({ minX: xy[0] - span / 2, minY: xy[1] - span / 2, maxX: xy[0] + span / 2, maxY: xy[1] + span / 2 }, size.width, size.height, 28));
    };
    window.addEventListener('studio:center', onCenter);
    return () => window.removeEventListener('studio:center', onCenter);
  }, [size.width, size.height, inst]);

  const issues = useMemo(() => taskIssues(shown), [shown]);
  const geoIssues = useMemo(() => validateGeometry(shown), [shown]);
  const [focusedIssue, setFocusedIssue] = useState<GeometryIssue | null>(null);
  const focusLive = focusedIssue && geoIssues.some((i) => i.key === focusedIssue.key) ? focusedIssue : null;
  const locateIssue = (issue: GeometryIssue) => {
    setFocusedIssue(issue);
    const t = issue.target;
    if (t && t.type !== 'boundary') selectRefs([t]);
    else if (t) { setPicked([]); onSelect({ type: 'boundary' }); }
    if (issue.bounds && size.width && size.height) {
      const [x0, y0, x1, y1] = issue.bounds;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      const half = Math.max(4, (x1 - x0) * 0.6, (y1 - y0) * 0.6);
      touched.current = true;
      setView(fitView({ minX: cx - half, minY: cy - half, maxX: cx + half, maxY: cy + half }, size.width, size.height, 28));
    }
  };
  const visibleRoutes = useMemo(() => routes.filter((r) => !hidden.includes(r.vehicle_name)), [routes, hidden]);
  const taskOwner = useMemo(() => vehicleOfTask(visibleRoutes), [visibleRoutes]);
  // Where each task is served when that is not the task point itself (from a lane or the headland).
  const stops = useMemo(() => {
    const at = new Map(inst.tasks.map((t) => [t.id, t.xy] as const));
    return visibleRoutes.flatMap((r) => r.services.flatMap((sv) => {
      const stop = (sv as { stop?: XY }).stop;
      const xy = at.get(sv.task);
      return stop && xy && Math.hypot(stop[0] - xy[0], stop[1] - xy[1]) > 0.05 ? [{ key: `${r.vehicle_name}-${sv.task}`, xy, stop, color: r.color }] : [];
    }));
  }, [visibleRoutes, inst.tasks]);

  const world = (e: { clientX: number; clientY: number }): XY => {
    const rect0 = svg.current!.getBoundingClientRect();
    return toWorld(view, e.clientX - rect0.left, e.clientY - rect0.top);
  };

  function onPointerDown(e: RPointerEvent<SVGSVGElement>) {
    const el = (e.target as Element).closest('[data-hit]') as HTMLElement | SVGElement | null;
    const hit = el?.getAttribute('data-hit') ?? '';
    const id = el?.getAttribute('data-id') ?? '';
    const p = world(e);
    svg.current?.setPointerCapture(e.pointerId);
    const panning = e.button === 1 || tool === 'pan' || (e.button === 0 && e.shiftKey && tool === 'select');
    if (panning) {
      drag.current = { kind: 'pan', sx: e.clientX, sy: e.clientY, view };
      return;
    }
    if (e.button !== 0) return;
    if (tool === 'box') {
      drag.current = { kind: 'marquee', from: p, to: p, add: e.ctrlKey || e.metaKey };
      setMarquee({ a: p, b: p });
      return;
    }
    if (tool === 'select' || !editable) {
      const additive = e.ctrlKey || e.metaKey;
      const ref: ItemRef | null =
        hit === 'task' || hit === 'obstacle' || hit === 'plot' || hit === 'corridor' || hit === 'unload' ? { type: hit, id } :
        hit === 'depot' ? { type: 'depot', id: String(Number(id || 0)) } : null;
      if (ref && additive) {
        const next = multi.some((x) => sameRef(x, ref)) ? multi.filter((x) => !sameRef(x, ref)) : [...multi, ref];
        selectRefs(next);
        return;
      }
      if (ref && multi.length > 1 && multi.some((x) => sameRef(x, ref))) {
        if (editable) drag.current = { kind: 'multi', from: p, refs: multi, orig: inst };
        return;
      }
      if (ref) setPicked([ref]);
      if (!hit && (e.altKey || additive)) {
        drag.current = { kind: 'marquee', from: p, to: p, add: additive };
        setMarquee({ a: p, b: p });
        return;
      }
      if (hit === 'vertex' && editable) {
        const [target, owner, index] = id.split(':');
        drag.current = { kind: 'vertex', target: target as PolyTarget | 'boundary', id: owner, index: Number(index) };
        return;
      }
      if (hit === 'task') {
        const t = inst.tasks.find((x) => x.id === Number(id));
        onSelect({ type: 'task', id: Number(id) });
        if (t && editable) drag.current = { kind: 'task', id: t.id, from: p, orig: t.xy };
        return;
      }
      if (hit === 'depot') {
        const index = Number(id || 0);
        const xy = depotsOf(inst)[index];
        onSelect({ type: 'depot', index });
        if (xy && editable) drag.current = { kind: 'depot', index, from: p, orig: xy };
        return;
      }
      if (hit === 'unload') {
        const index = Number(id);
        const xy = inst.unload_points?.[index];
        onSelect({ type: 'unload', index });
        if (xy && editable) drag.current = { kind: 'unload', index, from: p, orig: xy };
        return;
      }
      if (hit === 'obstacle' || hit === 'plot' || hit === 'corridor') {
        const list = hit === 'obstacle' ? inst.obstacles : hit === 'plot' ? inst.plots : inst.corridors ?? [];
        const item = list.find((x) => x.id === id);
        onSelect({ type: hit, id } as Selection);
        if (item && editable) drag.current = { kind: 'poly', target: hit, id, from: p, orig: item.polygon };
        return;
      }
      if (hit === 'boundary') {
        onSelect({ type: 'boundary' });
        return;
      }
      setPicked([]);
      onSelect(null);
      drag.current = { kind: 'pan', sx: e.clientX, sy: e.clientY, view };
      return;
    }
    if (tool === 'task') {
      if (!pointInPolygon(p, inst.boundary)) return;
      const nid = Math.max(0, ...inst.tasks.map((t) => t.id)) + 1;
      onEdit((i) => ({ ...i, tasks: [...i.tasks, { id: nid, xy: [round(p[0]), round(p[1])], demand: 1, label: `T${nid}`, plot: null }] }));
      onSelect({ type: 'task', id: nid });
      return;
    }
    if (tool === 'depot') {
      const xy: XY = [round(p[0]), round(p[1])];
      const index = depotsOf(inst).length;
      onEdit((i) => (depotsOf(i).length === 1 ? setDepot(i, 0, xy) : addDepot(i, xy)));
      onSelect({ type: 'depot', index: index === 1 ? 0 : Math.min(index, 7) });
      return;
    }
    if (tool === 'unload') {
      const xy: XY = [round(p[0]), round(p[1])];
      const index = inst.unload_points?.length ?? 0;
      if (index >= 16) return;
      onEdit((i) => ({ ...i, unload_points: [...(i.unload_points ?? []), xy] }));
      onSelect({ type: 'unload', index });
      return;
    }
    if (tool === 'obstacle' || tool === 'plot' || tool === 'corridor') {
      drag.current = { kind: 'shape', target: tool, from: p, to: p };
      setShape({ a: p, b: p, target: tool });
    }
  }

  function onPointerMove(e: RPointerEvent<SVGSVGElement>) {
    const p = world(e);
    onCursor?.(p);
    const d = drag.current;
    if (!d) return;
    if (d.kind === 'pan') {
      touched.current = true;
      setView({ ...d.view, tx: d.view.tx + (e.clientX - d.sx), ty: d.view.ty + (e.clientY - d.sy) });
      return;
    }
    if (d.kind === 'shape') {
      d.to = p;
      setShape({ a: d.from, b: p, target: d.target });
      return;
    }
    if (d.kind === 'marquee') {
      d.to = p;
      setMarquee({ a: d.from, b: p });
      return;
    }
    if (d.kind === 'multi') {
      setPreview(translateItems(d.orig, d.refs, round(p[0] - d.from[0]), round(p[1] - d.from[1])));
      return;
    }
    const base = preview ?? inst;
    if (d.kind === 'task') {
      const xy: XY = [round(d.orig[0] + p[0] - d.from[0]), round(d.orig[1] + p[1] - d.from[1])];
      setPreview({ ...base, tasks: base.tasks.map((t) => (t.id === d.id ? { ...t, xy } : t)) });
    } else if (d.kind === 'depot') {
      const xy: XY = [round(d.orig[0] + p[0] - d.from[0]), round(d.orig[1] + p[1] - d.from[1])];
      setPreview(setDepot(base, d.index, xy));
    } else if (d.kind === 'unload') {
      const xy: XY = [round(d.orig[0] + p[0] - d.from[0]), round(d.orig[1] + p[1] - d.from[1])];
      setPreview({ ...base, unload_points: (base.unload_points ?? []).map((q, i) => (i === d.index ? xy : q)) });
    } else if (d.kind === 'poly') {
      const moved = translate(d.orig, p[0] - d.from[0], p[1] - d.from[1]);
      if (d.target === 'obstacle') setPreview({ ...base, obstacles: base.obstacles.map((o) => (o.id === d.id ? { ...o, polygon: moved } : o)) });
      else if (d.target === 'plot') setPreview({ ...base, plots: base.plots.map((o) => (o.id === d.id ? { ...o, polygon: moved } : o)) });
      else setPreview({ ...base, corridors: (base.corridors ?? []).map((o) => (o.id === d.id ? { ...o, polygon: moved } : o)) });
    } else if (d.kind === 'vertex') {
      const xy: XY = [round(p[0]), round(p[1])];
      const set = (poly: XY[]) => poly.map((q, i) => (i === d.index ? xy : q));
      if (d.target === 'boundary') setPreview({ ...base, boundary: set(base.boundary) });
      else if (d.target === 'obstacle') setPreview({ ...base, obstacles: base.obstacles.map((o) => (o.id === d.id ? { ...o, polygon: set(o.polygon) } : o)) });
      else if (d.target === 'plot') setPreview({ ...base, plots: base.plots.map((o) => (o.id === d.id ? { ...o, polygon: set(o.polygon) } : o)) });
      else setPreview({ ...base, corridors: (base.corridors ?? []).map((o) => (o.id === d.id ? { ...o, polygon: set(o.polygon) } : o)) });
    }
  }

  function onPointerUp() {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.kind === 'marquee') {
      setMarquee(null);
      const found = itemsInBox(inst, d.from, d.to);
      selectRefs(d.add ? [...multi, ...found.filter((r) => !multi.some((x) => sameRef(x, r)))] : found);
      return;
    }
    if (d.kind === 'shape') {
      setShape(null);
      const small = Math.hypot(d.to[0] - d.from[0], d.to[1] - d.from[1]) < 0.8;
      const a = small ? ([d.from[0] - 3, d.from[1] - 2] as XY) : d.from;
      const b = small ? ([d.from[0] + 3, d.from[1] + 2] as XY) : d.to;
      if (d.target === 'obstacle') {
        const polygon = obstacleKind === 'tree' && small ? obstacleShape('tree', d.from, [d.from[0] + 1.2, d.from[1]]) : obstacleShape(obstacleKind, a, b);
        let newId = '';
        onEdit((i) => {
          newId = nextObstacleId(i.obstacles);
          return { ...i, obstacles: [...i.obstacles, { id: newId, kind: obstacleKind, name: OBSTACLE_STYLE[obstacleKind].label, polygon }] };
        });
        if (newId) onSelect({ type: 'obstacle', id: newId });
      } else if (d.target === 'plot') {
        const letter = String.fromCharCode(65 + (inst.plots.length % 26));
        let pid = letter;
        let k = 2;
        while (inst.plots.some((pl) => pl.id === pid)) pid = `${letter}${k++}`;
        onEdit((i) => ({ ...i, plots: [...i.plots, { id: pid, name: `${pid} 区`, crop: '', rows_deg: 0, polygon: rect(a, b) }] }));
        onSelect({ type: 'plot', id: pid });
      } else {
        let cid = `C${(inst.corridors?.length ?? 0) + 1}`;
        let k = 2;
        while (inst.corridors?.some((c) => c.id === cid)) cid = `C${k++}`;
        onEdit((i) => ({ ...i, corridors: [...(i.corridors ?? []), { id: cid, name: `机耕道 ${cid}`, polygon: rect(a, b) }] }));
        onSelect({ type: 'corridor', id: cid });
      }
      return;
    }
    if (d.kind !== 'pan' && preview) {
      const committed = preview;
      setPreview(null);
      onEdit(() => committed);
    }
  }

  function onWheel(e: RWheelEvent<SVGSVGElement>) {
    const rect0 = svg.current!.getBoundingClientRect();
    touched.current = true;
    setView((v) => zoomAt(v, Math.exp(-e.deltaY * 0.0015), e.clientX - rect0.left, e.clientY - rect0.top, 0.05, 400));
  }

  const scr = (p: XY) => toScreen(view, p[0], p[1]);
  const routeIndex = useMemo(() => new Map(routes.map((r, i) => [r.vehicle_name, i] as const)), [routes]);
  const legend = legendEntries(shown, visibleRoutes, layers, {
    lanes: !!lanes?.length, badTasks: issues.size, violations: violations?.length ?? 0, geoIssues: geoIssues.filter((i) => i.at).length, idp, routeIndex: (r) => routeIndex.get(r.vehicle_name) ?? 0,
  });
  // Large scenes: simplified copies for drawing only; edits, validation and the solver keep using `inst`.
  const kBucket = 2 ** (Math.round(Math.log2(Math.max(view.k, 1e-6)) * 2) / 2);
  const exactKey = [...multi.map(refKey), selection?.type === 'boundary' ? 'boundary:boundary' : ''].join('|');
  const disp = useMemo(
    () => decimateForDisplay(shown, visibleRoutes, lanes, kBucket, new Set(exactKey.split('|'))),
    [shown, visibleRoutes, lanes, kBucket, exactKey],
  );
  const originScr = scr([0, 0]);
  const originOnScreen = originScr[0] >= 0 && originScr[0] <= size.width && originScr[1] >= 0 && originScr[1] <= size.height;
  const gridStep = GRID_STEPS.find((g) => g * view.k >= 40) ?? 1000;
  const [bx0, by0, bx1, by1] = boundsOf(shown);
  const playbackPos = useMemo(() => {
    if (playback === null) return [];
    return visibleRoutes.map((r) => ({ r, ...along(r.polyline, playbackDistanceAtTime(inst, r, playback)) }));
  }, [playback, visibleRoutes, inst]);

  const selPoly =
    selection?.type === 'obstacle' ? shown.obstacles.find((o) => o.id === selection.id)?.polygon :
    selection?.type === 'plot' ? shown.plots.find((o) => o.id === selection.id)?.polygon :
    selection?.type === 'corridor' ? shown.corridors?.find((o) => o.id === selection.id)?.polygon :
    selection?.type === 'boundary' ? shown.boundary : undefined;
  const selTarget = selection?.type === 'obstacle' || selection?.type === 'plot' || selection?.type === 'corridor' || selection?.type === 'boundary' ? selection.type : null;
  const selId = selection && 'id' in selection ? String(selection.id) : 'boundary';

  return (
    <div ref={outer} className={s.editorShell} data-narrow={narrow} data-side={sideShown}>
    <div ref={box} className={s.editor} data-tool={tool}>
      <button type="button" className={s.sideToggle} aria-expanded={sideShown} aria-controls={`${idp}-side`} onClick={() => setSideOpen(!sideShown)}>
        {sideShown ? (narrow ? '▾ 收起面板' : '◂ 收起面板') : (narrow ? '▴ 面板' : '▸ 面板')}
      </button>
      <svg
        ref={svg}
        className={s.svg}
        width={size.width}
        height={size.height}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => onCursor?.(null)}
        onWheel={onWheel}
        role="application"
        aria-label="任务地图编辑器"
      >
        <defs>
          {shown.plots.map((pl) => (
            <pattern key={pl.id} id={`rows-${pl.id}`} width="3" height="3" patternUnits="userSpaceOnUse" patternTransform={`rotate(${pl.rows_deg ?? 0})`}>
              <line x1="0" y1="1.5" x2="3" y2="1.5" className={s.cropRow} />
            </pattern>
          ))}
          {(Object.keys(OBSTACLE_STYLE) as (keyof typeof OBSTACLE_STYLE)[]).map((k) => <KindPattern key={k} id={`${idp}-ob-${k}`} kind={k} u={1.6} />)}
        </defs>
        <rect className={s.backdrop} width={size.width} height={size.height} />
        {layers.grid && <Grid view={view} step={gridStep} width={size.width} height={size.height} />}
        <g transform={worldTransform(view)}>
          <polygon data-hit="boundary" className={s.boundary} points={disp.inst.boundary.map((p) => p.join(',')).join(' ')} />
          {layers.background && background}
          {layers.plots && disp.inst.plots.map((pl) => (
            <g key={pl.id}>
              <polygon className={s.plot} points={pl.polygon.map((p) => p.join(',')).join(' ')} />
              <polygon data-hit="plot" data-id={pl.id} className={s.plotRows} style={{ fill: `url(#rows-${pl.id})` }} points={pl.polygon.map((p) => p.join(',')).join(' ')} />
            </g>
          ))}
          {layers.lanes && disp.lanes && disp.lanes.length > 0 && (
            <g className={s.lanes}>
              {disp.lanes.map((ln, i) => <polyline key={i} points={ln.map((p) => p.join(',')).join(' ')} />)}
            </g>
          )}
          {layers.corridors && (disp.inst.corridors ?? []).map((c) => (
            <polygon key={c.id} data-hit="corridor" data-id={c.id} className={s.corridor} points={c.polygon.map((p) => p.join(',')).join(' ')}>
              <title>{`${c.name ?? c.id} · rows 模式并入通行网络，free 模式仅保留几何`}</title>
            </polygon>
          ))}
          {layers.obstacles && disp.inst.obstacles.map((o) => (
            <polygon
              key={o.id}
              data-hit="obstacle"
              data-id={o.id}
              className={s.obstacle}
              style={{ fill: `url(#${idp}-ob-${o.kind})`, stroke: OBSTACLE_STYLE[o.kind].stroke }}
              points={o.polygon.map((p) => p.join(',')).join(' ')}
            >
              <title>{`${o.name ?? OBSTACLE_STYLE[o.kind].label} · ${OBSTACLE_STYLE[o.kind].label} · ${o.id}`}</title>
            </polygon>
          ))}
          {layers.routes && disp.routes.map((r) => (
            <polyline key={r.vehicle_name} className={s.route} style={{ stroke: r.color }} strokeDasharray={routeDash(routeIndex.get(r.vehicle_name) ?? 0)} points={r.polyline.map((p) => p.join(',')).join(' ')} />
          ))}
          {layers.routes && stops.map((st) => (
            <line key={st.key} className={s.stopLink} style={{ stroke: st.color }} x1={st.xy[0]} y1={st.xy[1]} x2={st.stop[0]} y2={st.stop[1]} />
          ))}
          {overlay?.(view)}
          {selPoly && <polygon className={s.selected} points={selPoly.map((p) => p.join(',')).join(' ')} />}
        </g>
        {multi.length > 1 && multi.map((r) => {
          const pts = itemPoints(shown, r);
          if (!pts) return null;
          if (pts.length === 1) {
            const [x, y] = scr(pts[0]);
            return <circle key={refKey(r)} cx={x} cy={y} r={13} className={s.multiSel} />;
          }
          return <polygon key={refKey(r)} className={s.multiSel} points={pts.map((q) => scr(q).join(',')).join(' ')} />;
        })}
        {marquee && (() => {
          const [ax, ay] = scr(marquee.a);
          const [bx, by] = scr(marquee.b);
          return <rect className={s.marquee} x={Math.min(ax, bx)} y={Math.min(ay, by)} width={Math.abs(bx - ax)} height={Math.abs(by - ay)} />;
        })()}
        {layers.routes && disp.routes.map((r) => <Arrows key={r.vehicle_name} poly={r.polyline} view={view} color={r.color} />)}
        {layers.plots && layers.labels && shown.plots.map((pl) => {
          const [cx, cy] = scr(centroid(pl.polygon));
          return (
            <text key={pl.id} x={cx} y={cy} className={s.plotLabel}>
              {pl.name}{pl.crop ? ` · ${pl.crop}` : ''}
            </text>
          );
        })}
        {shape && (() => {
          const poly = shape.target === 'obstacle' ? obstacleShape(obstacleKind, shape.a, shape.b) : rect(shape.a, shape.b);
          return <polygon className={s.rubber} points={poly.map((p) => scr(p).join(',')).join(' ')} />;
        })()}
        {layers.issues && (violations ?? []).map((p, i) => {
          const [x, y] = scr(p);
          return <circle key={i} cx={x} cy={y} r={9} className={s.violation} />;
        })}
        {layers.tasks && shown.tasks.map((t) => {
          const [x, y] = scr(t.xy);
          const owner = taskOwner.get(t.id);
          const bad = issues.get(t.id);
          const sel = selection?.type === 'task' && selection.id === t.id;
          return (
            <g key={t.id} data-hit="task" data-id={t.id} className={s.taskG}>
              <title>{`${t.label} · 需求 ${t.demand}${owner ? ` · ${owner.vehicle} 第 ${owner.trip} 趟第 ${owner.order} 站` : ''}${bad ? ` · ${bad}` : ''}`}</title>
              {sel && <circle cx={x} cy={y} r={11} className={s.taskHalo} />}
              <circle cx={x} cy={y} r={owner ? 6.5 : 5.5} className={bad ? s.taskBad : s.task} strokeDasharray={bad ? '2 2' : undefined} style={owner ? { stroke: owner.color } : undefined} />
              {bad && <text x={x + 7} y={y + 4} className={s.taskBadMark}>!</text>}
              {owner && layers.labels && view.k > 1.1 && (
                <text x={x} y={y + 3} className={s.order} style={{ fill: owner.color }}>{owner.order}</text>
              )}
              {layers.labels && view.k > 2.2 && <text x={x + 9} y={y - 7} className={s.taskLabel}>{t.label}</text>}
            </g>
          );
        })}
        {layers.depots && (shown.unload_points ?? []).map((xy, index) => {
          const [x, y] = scr(xy);
          const selected = selection?.type === 'unload' && selection.index === index;
          return (
            <g key={`${index}-${xy.join(',')}`} data-hit="unload" data-id={index} className={s.unload} transform={`translate(${x} ${y})`}>
              <title>{`补给 / 卸载点 U${index + 1}`}</title>
              {selected && <circle r={14} className={s.taskHalo} />}
              <path d="M 0 -9 L 9 0 L 0 9 L -9 0 Z" />
              <text x={11} y={-8}>U{index + 1}</text>
            </g>
          );
        })}
        {layers.depots && depotsOf(shown).map((xy, index) => (
          <Depot key={`${index}-${xy.join(',')}`} xy={scr(xy)} index={index} selected={selection?.type === 'depot' && (selection.index ?? 0) === index} />
        ))}
        {playbackPos.map(({ r, xy, heading }) => {
          const [x, y] = scr(xy);
          return (
            <g key={r.vehicle_name} transform={`translate(${x} ${y}) rotate(${(-heading * 180) / Math.PI})`} className={s.vehicle}>
              <circle r={9} style={{ fill: r.color }} />
              <path d="M -3 -4 L 5 0 L -3 4 Z" fill="#fff" />
            </g>
          );
        })}
        {selPoly && selTarget && editable &&
          selPoly.map((p, i) => {
            const [x, y] = scr(p);
            return <rect key={i} data-hit="vertex" data-id={`${selTarget}:${selId}:${i}`} x={x - 4.5} y={y - 4.5} width={9} height={9} className={s.vertex} />;
          })}
        {layers.issues && geoIssues.map((i) => {
          if (!i.at) return null;
          const [x, y] = scr(i.at);
          return (
            <g key={i.key} className={s.issueMark} data-severity={i.severity} transform={`translate(${x} ${y - 12})`}>
              <path d="M 0 -7 L 7 5 L -7 5 Z" />
              <text y={4}>{i.severity === 'error' ? '×' : '!'}</text>
            </g>
          );
        })}
        {focusLive?.bounds && (() => {
          const [x0, y0, x1, y1] = focusLive.bounds;
          const [ax, ay] = scr([x0, y1]);
          const [bx, by] = scr([x1, y0]);
          return <rect className={s.issueFocus} x={ax - 6} y={ay - 6} width={bx - ax + 12} height={by - ay + 12} />;
        })()}
        {originOnScreen && <OriginMark at={originScr} />}
        <AxisCompass x={size.width - 110} y={size.height - 34} />
        <ScaleBar step={gridStep} k={view.k} height={size.height} />
        {disp.decimated && <text x={size.width - 10} y={size.height - 84} className={s.extent}>{`显示已抽稀 ${disp.shownVertices}/${disp.vertices} 顶点（求解输入不变）`}</text>}
        <text x={size.width - 10} y={size.height - 10} className={s.extent}>{`${(bx1 - bx0).toFixed(0)} × ${(by1 - by0).toFixed(0)} m`}</text>
      </svg>
    </div>
      <div id={`${idp}-side`} className={s.panelStack} hidden={!sideShown}>
        <SelectionPanel inst={inst} refs={multi} editable={editable} onEdit={onEdit} onSelectRefs={selectRefs} />
        <IssuesPanel issues={geoIssues} focused={focusLive?.key ?? null} onLocate={locateIssue} />
        <LayerLegendPanel layers={layers} onToggle={toggleLayer} entries={legend} />
      </div>
    </div>
  );
}

function Grid({ view, step, width, height }: { view: View; step: number; width: number; height: number }) {
  const [wx0, wy1] = toWorld(view, 0, 0);
  const [wx1, wy0] = toWorld(view, width, height);
  const xs: number[] = [];
  const ys: number[] = [];
  for (let x = Math.ceil(wx0 / step) * step; x <= wx1 && xs.length < 400; x += step) xs.push(x);
  for (let y = Math.ceil(wy0 / step) * step; y <= wy1 && ys.length < 400; y += step) ys.push(y);
  return (
    <g className={s.grid}>
      {xs.map((x) => {
        const [sx] = toScreen(view, x, 0);
        return <line key={`x${x}`} x1={sx} x2={sx} y1={0} y2={height} />;
      })}
      {ys.map((y) => {
        const [, sy] = toScreen(view, 0, y);
        return <line key={`y${y}`} x1={0} x2={width} y1={sy} y2={sy} />;
      })}
    </g>
  );
}

function ScaleBar({ step, k, height }: { step: number; k: number; height: number }) {
  const w = step * k;
  return (
    <g transform={`translate(14 ${height - 18})`} className={s.scale}>
      <line x1={0} x2={w} y1={0} y2={0} />
      <line x1={0} x2={0} y1={-4} y2={4} />
      <line x1={w} x2={w} y1={-4} y2={4} />
      <text x={w + 6} y={4}>{step} m</text>
    </g>
  );
}

function Depot({ xy, index, selected }: { xy: XY; index: number; selected: boolean }) {
  const [x, y] = xy;
  return (
    <g data-hit="depot" data-id={index} className={s.depot} transform={`translate(${x} ${y})`}>
      <title>{`车库 D${index + 1}（车辆按类别展开后的表序轮询分配家库）`}</title>
      {selected && <circle r={15} className={s.taskHalo} />}
      <rect x={-10} y={-10} width={20} height={20} rx={5} />
      <path d="M -6 1 L 0 -5 L 6 1 M -4 0 L -4 6 L 4 6 L 4 0" />
      <text x={12} y={-10}>D{index + 1}</text>
    </g>
  );
}

function Arrows({ poly, view, color }: { poly: XY[]; view: View; color: string }) {
  const marks: { x: number; y: number; a: number }[] = [];
  let acc = 0;
  for (let i = 1; i < poly.length; i++) {
    const [x0, y0] = toScreen(view, poly[i - 1][0], poly[i - 1][1]);
    const [x1, y1] = toScreen(view, poly[i][0], poly[i][1]);
    const seg = Math.hypot(x1 - x0, y1 - y0);
    let at = 70 - acc;
    while (at < seg) {
      const f = at / seg;
      marks.push({ x: x0 + f * (x1 - x0), y: y0 + f * (y1 - y0), a: (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI });
      at += 110;
    }
    acc = (acc + seg) % 110;
    if (marks.length > 300) break;
  }
  return (
    <g style={{ fill: color }} className={s.arrows}>
      {marks.map((m, i) => (
        <path key={i} d="M -4 -3.5 L 4 0 L -4 3.5 Z" transform={`translate(${m.x} ${m.y}) rotate(${m.a})`} />
      ))}
    </g>
  );
}
