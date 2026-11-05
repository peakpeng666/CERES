/**
 * Map asset → editable multi-vehicle task map. The boundary, plots, obstacles and no-go zones come from the map
 * (ROS grid vectorised, GeoJSON roles, or a calibrated image's extent); depot and task points are taken from the
 * map, generated with a recorded seed, or clicked; the fleet comes from a vehicle asset. The result is an ordinary
 * user scenario whose instance records the asset versions it was built from.
 */
import { importScenarioOnce } from '../scenarioWrites';
import { useEffect, useMemo, useState } from 'react';
import { Crosshair, Dices, Home, MapPinned, Plus, Trash2, X } from 'lucide-react';
import { Button, Input, Notice, Segmented, Select, Spinner } from '../../lab/ui';
import type { XY } from '../../lab/types';
import { OBSTACLE_STYLE, area, pointInPolygon } from '../editor/geometry';
import { useStudio } from '../store';
import type { ObstacleKind } from '../types';
import { applyAffine, assetsApi, LEVEL_LABEL, type Asset, type AssetSummary, type AssetVersion, type PlanningGeometry } from './api';
import { BackgroundShape } from './Background';
import { MapView2D } from './MapView2D';
import { backgroundOf, fleetBindingOf, openTaskMap, unconstrained, type BackgroundLayer, type FleetAssetBinding } from './planBridge';
import { useAssets } from './store';
import s from './assets.module.css';

type Task = { id: number; xy: XY; demand: number; label: string; service_s: number };
type Mode = 'depot' | 'task' | null;

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function distToSeg(p: XY, a: XY, b: XY) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
function distToPoly(p: XY, poly: XY[]) {
  let d = Infinity;
  for (let i = 0; i < poly.length; i++) d = Math.min(d, distToSeg(p, poly[i], poly[(i + 1) % poly.length]));
  return d;
}

/** Same rule as the server validator: inside the boundary, outside every obstacle, with the robot radius to spare. */
function clearAt(p: XY, g: { boundary: XY[]; obstacles: { polygon: XY[] }[] }, r: number): boolean {
  if (!pointInPolygon(p, g.boundary) || distToPoly(p, g.boundary) < r) return false;
  return g.obstacles.every((o) => !pointInPolygon(p, o.polygon) && distToPoly(p, o.polygon) >= r);
}

const r2 = (p: XY): XY => [Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100];

export function MapToTaskMap() {
  const from = useAssets((x) => x.taskMapFrom);
  if (!from) return null;
  return <Wizard key={`${from.id}@${from.v}`} id={from.id} v={from.v} />;
}

function Wizard({ id, v }: { id: string; v: number }) {
  const close = () => useAssets.getState().setTaskMapFrom(null);
  const notify = useStudio((x) => x.notify);
  const [asset, setAsset] = useState<Asset | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    assetsApi.get(id).then(setAsset).catch((e) => setError((e as Error).message));
  }, [id]);
  const ver = asset?.versions.find((x) => x.v === v) ?? null;
  return (
    <div className={s.backdrop} onMouseDown={close}>
      <div className={s.modal} style={{ width: 'min(1180px, 97vw)' }} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="用地图新建任务地图">
        <div className={s.modalHead}>
          <MapPinned size={16} />
          <h3>用地图新建任务地图{asset ? ` · ${asset.name} v${v}` : ''}</h3>
          <span className={s.grow} />
          <Button size="sm" variant="plain" icon={X} onClick={close} aria-label="关闭" />
        </div>
        {error && <div style={{ padding: 16 }}><Notice tone="error">{error}</Notice></div>}
        {!error && (!asset || !ver) && <div style={{ padding: 16 }}><Spinner /></div>}
        {asset && ver && <Body asset={asset} ver={ver} onDone={(sid, title) => { close(); notify('ok', `已创建「${title}」，可继续编辑地块、障碍和任务点`); openTaskMap(sid); }} />}
      </div>
    </div>
  );
}

function Body({ asset, ver, onDone }: { asset: Asset; ver: AssetVersion; onDone: (id: string, title: string) => void }) {
  const bg = backgroundOf(asset, ver);
  const layer = typeof bg === 'string' ? null : bg;
  const [unknownAs, setUnknownAs] = useState<'obstacle' | 'free'>('obstacle');
  const [minArea, setMinArea] = useState(asset.subtype === 'ros_grid' ? '0.05' : '0.5');
  const [simplify, setSimplify] = useState(asset.subtype === 'ros_grid' ? String(Math.max(0.02, ver.meta.frame?.resolution ?? 0.05)) : '0.1');
  const [geom, setGeom] = useState<PlanningGeometry | null>(null);
  const [geomError, setGeomError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [depot, setDepot] = useState<XY | null>(null);
  const [depotSource, setDepotSource] = useState('');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskSource, setTaskSource] = useState('');
  const [mode, setMode] = useState<Mode>(null);
  const [n, setN] = useState('30');
  const [seed, setSeed] = useState(String(1 + Math.floor(Math.random() * 9999)));
  const [vehicles, setVehicles] = useState<AssetSummary[]>([]);
  const [vehicleId, setVehicleId] = useState('vehicle-field-diffdrive-builtin');
  const [vehicle, setVehicle] = useState<{ a: Asset; ver: AssetVersion } | null>(null);
  const [count, setCount] = useState('3');
  const [objective, setObjective] = useState<'total_distance' | 'makespan'>('makespan');
  const [travel, setTravel] = useState<'free' | 'rows'>('free');
  const [title, setTitle] = useState(`${asset.name} · 任务地图`);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // geometry
  const extract = async () => {
    setLoading(true);
    setGeomError(null);
    try {
      let g: PlanningGeometry;
      if (asset.subtype === 'image') {
        const m = ver.meta.calibration!.matrix;
        const [w, h] = ver.meta.size_px ?? [0, 0];
        const corners = [[0, h], [w, h], [w, 0], [0, 0]].map(([u, vv]) => r2(applyAffine(m, u, vv)));
        g = { boundary: corners, plots: [], obstacles: [], tasks: [], depot: null, notes: ['图片底图没有可通行信息：边界取图片范围，地块与障碍请在任务地图里沿底图描出'] };
      } else {
        g = await assetsApi.geometry(asset.id, ver.v, { unknown_as: unknownAs, min_area_m2: Number(minArea) || 0, simplify_m: Number(simplify) || 0 });
      }
      if (!g.boundary || g.boundary.length < 3) throw new Error('这张地图提取不出边界（没有足够大的可通行区域）');
      setGeom(g);
      if (g.depot) { setDepot(g.depot); setDepotSource('地图要素'); }
      if (g.tasks.length) { setTasks(g.tasks.map((t) => ({ ...t, service_s: t.service_s ?? 0 }))); setTaskSource('地图要素'); }
    } catch (e) {
      setGeomError((e as Error).message);
      setGeom(null);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void extract(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // vehicles
  useEffect(() => {
    assetsApi.list('vehicle').then((r) => setVehicles(r.assets)).catch(() => setVehicles([]));
  }, []);
  useEffect(() => {
    setVehicle(null);
    assetsApi.get(vehicleId).then((a) => setVehicle({ a, ver: a.versions.find((x) => x.v === a.current)! })).catch(() => setVehicle(null));
  }, [vehicleId]);
  const binding: FleetAssetBinding | string | null = vehicle ? fleetBindingOf(vehicle.a, vehicle.ver) : null;
  const radius = typeof binding === 'object' && binding ? binding.robot_radius_m : 0.5;

  const planGeom = useMemo(() => (geom?.boundary ? { boundary: geom.boundary, obstacles: geom.obstacles } : null), [geom]);
  const suggestDepot = () => {
    if (!planGeom) return;
    const xs = planGeom.boundary.map((p) => p[0]), ys = planGeom.boundary.map((p) => p[1]);
    const [x0, y0, x1, y1] = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    const step = Math.max((x1 - x0) / 80, (y1 - y0) / 80, radius / 2);
    let best: XY | null = null, bd = Infinity;
    for (let x = x0; x <= x1; x += step) {
      for (let y = y0; y <= y1; y += step) {
        const d = Math.hypot(x - x0, y - y0);
        if (d < bd && clearAt([x, y], planGeom, radius + 0.05)) { bd = d; best = [x, y]; }
      }
    }
    if (best) { setDepot(r2(best)); setDepotSource('自动建议（离西南角最近的可通行点，可点选修改）'); }
  };
  // re-suggest while the depot is still the automatic one (a larger vehicle needs more clearance)
  useEffect(() => { if (planGeom && (!depot || depotSource.startsWith('自动建议'))) suggestDepot(); }, [planGeom, radius]); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = () => {
    if (!planGeom) return;
    const want = Math.max(1, Math.min(800, Math.round(Number(n) || 0)));
    const rnd = mulberry32(Number(seed) || 1);
    const regions = geom!.plots.length ? geom!.plots.map((p) => p.polygon) : [planGeom.boundary];
    const weights = regions.map((r) => area(r));
    const total = weights.reduce((a, b) => a + b, 0);
    const out: Task[] = [];
    const spacing = Math.max(radius * 2, 0.5);
    for (let tries = 0; out.length < want && tries < want * 400; tries++) {
      let pick = rnd() * total, k = 0;
      while (k < regions.length - 1 && pick > weights[k]) { pick -= weights[k]; k++; }
      const reg = regions[k];
      const xs = reg.map((p) => p[0]), ys = reg.map((p) => p[1]);
      const p: XY = [Math.min(...xs) + rnd() * (Math.max(...xs) - Math.min(...xs)), Math.min(...ys) + rnd() * (Math.max(...ys) - Math.min(...ys))];
      if (!pointInPolygon(p, reg) || !clearAt(p, planGeom, radius + 0.05)) continue;
      if (depot && Math.hypot(p[0] - depot[0], p[1] - depot[1]) < spacing * 2) continue;
      if (out.some((t) => Math.hypot(t.xy[0] - p[0], t.xy[1] - p[1]) < spacing)) continue;
      out.push({ id: out.length + 1, xy: r2(p), demand: 1, label: `T${out.length + 1}`, service_s: 0 });
    }
    setTasks(out);
    setTaskSource(`${geom!.plots.length ? '在地块内' : '在可通行区内'}随机生成（种子 ${seed}）${out.length < want ? `，只放下 ${out.length} 个` : ''}`);
  };

  const onMapClick = (p: XY) => {
    if (mode === 'depot') { setDepot(r2(p)); setDepotSource('手动点选'); setMode(null); }
    if (mode === 'task') {
      setTasks((ts) => [...ts, { id: (ts.length ? Math.max(...ts.map((t) => t.id)) : 0) + 1, xy: r2(p), demand: 1, label: `T${ts.length + 1}`, service_s: 0 }]);
      setTaskSource((x) => (x.includes('手动') ? x : `${x ? `${x} + ` : ''}手动点选`));
    }
  };

  const bad = useMemo(() => {
    if (!planGeom) return new Set<string>();
    const out = new Set<string>();
    if (depot && !clearAt(depot, planGeom, radius)) out.add('depot');
    for (const t of tasks) if (!clearAt(t.xy, planGeom, radius)) out.add(`t${t.id}`);
    return out;
  }, [planGeom, depot, tasks, radius]);

  const create = async () => {
    if (!geom?.boundary || !depot || !tasks.length || typeof binding !== 'object' || !binding) return;
    setSaving(true);
    setSaveError(null);
    const k = Math.max(1, Math.min(60, Math.round(Number(count) || 1)));
    const xs = geom.boundary.map((p) => p[0]), ys = geom.boundary.map((p) => p[1]);
    const W = Math.max(...xs) - Math.min(...xs), H = Math.max(...ys) - Math.min(...ys);
    const mapRef = { role: 'map' as const, id: asset.id, version: ver.v, sha256: ver.sha256, name: asset.name };
    const tf = ver.meta.transform ?? {};
    const crs = asset.subtype === 'ros_grid' ? `map（ROS 栅格 ${asset.name}）`
      : asset.subtype === 'vector' ? `${tf.projected_crs ?? 'local'} 局部，原点 ${(tf.origin_projected ?? []).map((x: number) => x.toFixed(2)).join(', ')}`
        : `local（图片标定 ${ver.meta.calibration?.model ?? ''}）`;
    const sid = `tm-${Date.now().toString(36)}-${Math.floor(Math.random() * 46656).toString(36)}`;
    const scenario = {
      id: sid, title: title.trim().slice(0, 60) || sid, problem_class: 'fleet_routing',
      summary: `${k} 台 ${vehicle!.a.name} · ${tasks.length} 个任务点 · ${W.toFixed(0)}×${H.toFixed(0)} m · ${geom.plots.length} 块地 · ${geom.obstacles.length} 处障碍`,
      frame: { kind: 'field', units: 'm', crs },
      provenance: { note: `由地图资产 ${asset.name}（${asset.id} v${ver.v}，sha256 ${ver.sha256.slice(0, 12)}）生成；depot：${depotSource}；任务点：${taskSource}。` },
      layers: layer ? { background: layer as BackgroundLayer } : {},
      instance: {
        boundary: geom.boundary,
        plots: geom.plots.map((p) => ({ id: p.id, name: p.name, crop: p.crop, rows_deg: p.rows_deg, polygon: p.polygon })),
        obstacles: geom.obstacles.map((o) => ({ ...o, kind: (o.kind in OBSTACLE_STYLE ? o.kind : 'other') as ObstacleKind })),
        depot, tasks,
        vehicles: { count: k, capacity: binding.capacity ?? unconstrained({ tasks }), speed_mps: binding.speed_mps, trips: 'multi', travel: geom.plots.length ? travel : 'free' },
        objective, robot_radius: binding.robot_radius_m, units: 'm',
        asset_refs: [mapRef, binding.asset], fleet: binding,
        meta: { task_word: '任务点', from_asset: mapRef, seed: taskSource.includes('种子') ? Number(seed) : null },
      },
    };
    try {
      // Stable per content: a retry after a lost response reuses the first id instead of minting a new one.
      const sc = await importScenarioOnce(scenario);
      onDone(sc.id, scenario.title);
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const bounds = useMemo((): [number, number, number, number] | null => {
    const pts = geom?.boundary ?? null;
    if (pts) {
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    if (ver.meta.frame) return ver.meta.frame.bounds;
    return (ver.meta.summary?.bounds as [number, number, number, number] | undefined) ?? null;
  }, [geom, ver]);

  const ready = !!geom?.boundary && !!depot && tasks.length > 0 && typeof binding === 'object' && !!binding && !bad.size;
  return (
    <>
      <div className={s.modalBody} style={{ gridTemplateColumns: '1.5fr 1fr' }}>
        <div className={s.miniPreview} style={{ height: 'min(66vh, 600px)' }}>
          <MapView2D bounds={bounds} fitKey={`${asset.id}@${ver.v}:${geom ? 'g' : 'n'}`} onClick={mode ? onMapClick : undefined}
            overlay={mode ? <div className={s.glass}>{mode === 'depot' ? '点击地图放置车库' : '点击地图添加任务点（再点工具按钮结束）'}</div> : undefined}>
            {(view) => {
              const k = view.k || 1;
              return (
                <>
                  {layer && <BackgroundShape layer={layer} opacity={0.55} />}
                  {geom?.boundary && <polygon points={geom.boundary.map((p) => p.join(',')).join(' ')} fill="none" stroke="#30b158" strokeWidth={2} vectorEffect="non-scaling-stroke" />}
                  {geom?.plots.map((p) => <polygon key={p.id} points={p.polygon.map((q) => q.join(',')).join(' ')} fill="rgba(98,160,72,0.25)" stroke="#3f6b2a" strokeWidth={1} vectorEffect="non-scaling-stroke" />)}
                  {geom?.obstacles.map((o) => (
                    <polygon key={o.id} points={o.polygon.map((q) => q.join(',')).join(' ')} vectorEffect="non-scaling-stroke" strokeWidth={1}
                      fill={(o as { nogo?: boolean }).nogo ? 'rgba(255,69,58,0.25)' : 'rgba(255,69,58,0.35)'} stroke="#ff453a" strokeDasharray={(o as { nogo?: boolean }).nogo ? '4 3' : undefined} />
                  ))}
                  {tasks.map((t) => (
                    <circle key={t.id} cx={t.xy[0]} cy={t.xy[1]} r={4.5 / k} fill={bad.has(`t${t.id}`) ? '#ff453a' : '#0a84ff'} stroke="#fff" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
                  ))}
                  {depot && <rect x={depot[0] - 7 / k} y={depot[1] - 7 / k} width={14 / k} height={14 / k} rx={3 / k} fill={bad.has('depot') ? '#ff453a' : '#ff9f0a'} stroke="#fff" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
                </>
              );
            }}
          </MapView2D>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          <section>
            <h4 className={s.stepTitle}>① 规划几何</h4>
            {asset.subtype === 'ros_grid' && (
              <div className={s.row}>
                <Select aria-label="未知区" value={unknownAs} onChange={(e) => setUnknownAs(e.target.value as 'obstacle' | 'free')}><option value="obstacle">未知区按障碍</option><option value="free">未知区按可通行</option></Select>
                <label className={s.hint}>最小障碍</label><Input style={{ width: 56 }} value={minArea} onChange={(e) => setMinArea(e.target.value)} /><span className={s.hint}>m²</span>
                <label className={s.hint}>简化</label><Input style={{ width: 56 }} value={simplify} onChange={(e) => setSimplify(e.target.value)} /><span className={s.hint}>m</span>
                <Button size="sm" disabled={loading} onClick={() => void extract()}>{loading ? '提取中…' : '重新提取'}</Button>
              </div>
            )}
            {loading && <Spinner />}
            {geomError && <Notice tone="error">{geomError}</Notice>}
            {geom && (
              <p className={s.hint}>
                边界 {geom.boundary?.length ?? 0} 个顶点 · {geom.plots.length} 块地 · {geom.obstacles.length} 处障碍 / 禁行区
                {geom.stats?.free_area_m2 != null ? ` · 可通行 ${geom.stats.free_area_m2} m²` : ''}
              </p>
            )}
            {geom?.notes.map((x) => <p key={x} className={s.hint}>· {x}</p>)}
          </section>
          <section>
            <h4 className={s.stepTitle}>② 车库与任务点</h4>
            <div className={s.row}>
              <Button size="sm" icon={Home} variant={mode === 'depot' ? 'primary' : 'secondary'} onClick={() => setMode(mode === 'depot' ? null : 'depot')}>点选车库</Button>
              <span className={s.hint}>{depot ? `(${depot.join(', ')}) · ${depotSource}` : '未放置'}{bad.has('depot') ? ' · 离障碍或边界太近' : ''}</span>
            </div>
            <div className={s.row} style={{ marginTop: 6 }}>
              <Input style={{ width: 56 }} value={n} aria-label="任务点数" onChange={(e) => setN(e.target.value)} /><span className={s.hint}>个，种子</span>
              <Input style={{ width: 64 }} value={seed} aria-label="随机种子" onChange={(e) => setSeed(e.target.value)} />
              <Button size="sm" icon={Dices} disabled={!planGeom} onClick={generate}>生成</Button>
              <Button size="sm" icon={Plus} variant={mode === 'task' ? 'primary' : 'secondary'} onClick={() => setMode(mode === 'task' ? null : 'task')}>点选</Button>
              <Button size="sm" icon={Trash2} variant="plain" disabled={!tasks.length} onClick={() => { setTasks([]); setTaskSource(''); }} />
            </div>
            <p className={s.hint}>{tasks.length} 个任务点{taskSource ? ` · ${taskSource}` : ''}{[...bad].filter((b) => b.startsWith('t')).length ? ` · ${[...bad].filter((b) => b.startsWith('t')).length} 个离障碍或边界太近（红色）` : ''}</p>
          </section>
          <section>
            <h4 className={s.stepTitle}>③ 车队（车辆资产）</h4>
            <div className={s.row}>
              <Select aria-label="车辆资产" value={vehicleId} onChange={(e) => setVehicleId(e.target.value)} style={{ maxWidth: 260 }}>
                {vehicles.map((x) => <option key={x.id} value={x.id}>{x.name}{x.level ? `（${LEVEL_LABEL[x.level]?.text ?? x.level}）` : ''}</option>)}
              </Select>
              <Input style={{ width: 48 }} value={count} aria-label="车辆数" onChange={(e) => setCount(e.target.value)} /><span className={s.hint}>台</span>
            </div>
            {typeof binding === 'string' && <Notice tone="warn">{binding}</Notice>}
            {binding && typeof binding === 'object' && (
              <p className={s.hint}>
                车体半径 {binding.robot_radius_m} m（足迹外接圆）· 容量 {binding.capacity ?? '不载货（不设容量约束）'} · 车速 {binding.speed_mps} m/s
                {binding.kinematics ? ` · ${binding.kinematics}` : ''}{binding.adapter ? ` · 可在田间仿真中执行` : ` · 不能在工作台仿真中执行：${binding.adapter_note}`}
                {binding.ignored.length ? `。未参与规划：${binding.ignored.join('；')}` : ''}
              </p>
            )}
            <div className={s.row} style={{ marginTop: 6 }}>
              <Segmented value={objective} onChange={setObjective} options={[{ value: 'makespan', label: '尽快完成' }, { value: 'total_distance', label: '总里程最短' }]} />
              {geom && geom.plots.length > 0 && (
                <Segmented value={travel} onChange={setTravel} options={[{ value: 'free', label: '自由通行' }, { value: 'rows', label: '沿行间' }]} />
              )}
            </div>
          </section>
          <section>
            <h4 className={s.stepTitle}>④ 名称</h4>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: '100%' }} />
          </section>
          {saveError && <Notice tone="error">服务端校验未通过：{saveError}</Notice>}
        </div>
      </div>
      <div className={s.modalFoot}>
        <span className={s.hint}><Crosshair size={12} style={{ verticalAlign: -2 }} /> 坐标单位米；创建后在「任务规划」里继续编辑，底图随任务地图保存。</span>
        <span className={s.grow} />
        <Button variant="primary" icon={MapPinned} loading={saving} disabled={!ready} onClick={() => void create()}
          title={ready ? '' : !geom?.boundary ? '先提取几何' : !depot ? '先放置车库' : !tasks.length ? '先生成或点选任务点' : typeof binding !== 'object' ? '先选可规划的车辆' : '有点位离障碍或边界太近'}>
          创建任务地图
        </Button>
      </div>
    </>
  );
}
