/** Asset layers inside the task-planning workspace: the map background and the fleet's vehicle asset. */
import { useEffect, useState } from 'react';
import { Eye, EyeOff, Layers, PackageOpen, Unlink } from 'lucide-react';
import { create } from 'zustand';
import { Button, Select } from '../../lab/ui';
import type { Solution } from '../../lab/types';
import { asFleet, useStudio } from '../store';
import type { PerVehicle } from '../types';
import { useReplay } from '../experiment/replay';
import { assetsApi, LEVEL_LABEL, type AssetSummary } from './api';
import { BackgroundShape } from './Background';
import { bindingDrift, bindVehicle, unbindVehicle, type AssetAwareInstance, type BackgroundLayer } from './planBridge';
import { useAssets } from './store';
import p from '../panels/panels.module.css';
import s from './assets.module.css';

const useBgView = create<{ visible: boolean; opacity: number | null; set: (x: Partial<{ visible: boolean; opacity: number | null }>) => void }>((set) => ({
  visible: true, opacity: null, set: (x) => set(x),
}));

export function useBackgroundLayer(): BackgroundLayer | null {
  const sc = useStudio((x) => x.scenario);
  return ((sc?.layers as { background?: BackgroundLayer } | undefined)?.background) ?? null;
}

/** The background drawn under the editable task map (display only, never passable). */
export function PlanBackground() {
  const layer = useBackgroundLayer();
  const { visible, opacity } = useBgView();
  if (!layer || !visible) return null;
  return <BackgroundShape layer={layer} opacity={opacity ?? layer.opacity} />;
}

export function BackgroundControl() {
  const layer = useBackgroundLayer();
  const { visible, opacity, set } = useBgView();
  if (!layer) return null;
  return (
    <span className={s.row} style={{ gap: 6 }} title={`底图来自资产 ${layer.asset.name ?? layer.asset.id} v${layer.asset.version}；只作显示，规划用的是地图上的边界、地块与障碍`}>
      <Button size="sm" variant="plain" icon={visible ? Eye : EyeOff} onClick={() => set({ visible: !visible })}>底图</Button>
      {visible && (
        <input type="range" min={0.1} max={1} step={0.05} value={opacity ?? layer.opacity ?? 0.6} aria-label="底图不透明度" style={{ width: 70 }}
          onChange={(e) => set({ opacity: Number(e.target.value) })} />
      )}
    </span>
  );
}

const openAsset = (id: string) => {
  useStudio.getState().setWorkspace('assets');
  void useAssets.getState().select(id);
};

/** Fleet section of the task-map inspector: which vehicle asset the fleet uses, and the vehicle instances. */
export function FleetAssetSection() {
  const draft = useStudio((x) => x.draft);
  const runs = useStudio((x) => x.runs);
  const focusRun = useStudio((x) => x.focusRun);
  const notify = useStudio((x) => x.notify);
  const replay = useReplay((x) => x.result);
  const inst = asFleet(draft) as AssetAwareInstance;
  const [vehicles, setVehicles] = useState<AssetSummary[]>([]);
  const [pick, setPick] = useState('');
  useEffect(() => {
    assetsApi.list('vehicle').then((r) => setVehicles(r.assets)).catch(() => setVehicles([]));
  }, []);
  const b = inst.fleet;
  const drift = bindingDrift(inst);
  const run = focusRun ? (runs[focusRun] as Solution | undefined) : undefined;
  const per = (run?.state === 'done' ? (run.metrics.per_vehicle as unknown as PerVehicle[] | undefined) : undefined) ?? [];
  const expanded = inst.vehicles.fleet?.length
    ? inst.vehicles.fleet.flatMap((item, classIndex) => Array.from({ length: item.count }, () => ({ name: item.name ?? `车型 ${classIndex + 1}`, classIndex })))
    : Array.from({ length: inst.vehicles.count ?? 1 }, () => ({ name: '统一车型', classIndex: 0 }));
  const names = inst.vehicles.names ?? expanded.map((item, i) => `${item.name} · 车${i + 1}`);
  const depots = inst.depots?.length ? inst.depots : [inst.depot];
  const simOf = (i: number) => (replay && replay.scenario_id === useStudio.getState().scenarioId && (!focusRun || replay.run_id === focusRun) ? replay.vehicles[i] : undefined);
  const apply = async () => {
    if (!pick) return;
    try {
      const a = await assetsApi.get(pick);
      const err = bindVehicle(a, a.versions.find((v) => v.v === a.current)!);
      notify(err ? 'error' : 'ok', err ?? `车队已设为 ${a.name}`);
    } catch (e) {
      notify('error', (e as Error).message);
    }
  };
  return (
    <>
      <h3>车辆资产</h3>
      {b ? (
        <div className={s.kv}>
          <span>车型</span>
          <span>
            <button type="button" className={p.link} style={{ border: 0, background: 'none', padding: 0 }} onClick={() => openAsset(b.asset.id)}>{b.asset.name ?? b.asset.id}</button>
            {' '}v{b.asset.version}
          </span>
          <span>规划参数</span><span>车体半径 {b.robot_radius_m} m（足迹外接圆）· 容量 {b.capacity ?? '不载货'} · {b.speed_mps} m/s{b.kinematics ? ` · ${b.kinematics}` : ''}</span>
          <span>仿真</span><span>{b.adapter ? '可在 Gazebo 田间仿真中执行（车体与资产一致）' : `不能在工作台仿真中执行：${b.adapter_note ?? '缺仿真适配'}`}</span>
          {b.ignored.length > 0 && <><span>未参与规划</span><span>{b.ignored.join('；')}</span></>}
          {b.min_turn_radius_m ? <><span>转弯半径</span><span>{b.min_turn_radius_m} m：Dubins 规划与采样几何检查；当前田间差速模型不支持执行</span></> : null}
        </div>
      ) : (
        <p className={p.hint}>选一种车辆资产，车体半径、容量和车速随方案记录。</p>
      )}
      {drift.length > 0 && <p className={p.hint}><strong className={p.warn}>与车辆资产不一致：</strong>{drift.join('；')}（求解按任务地图里的值）。</p>}
      <div className={p.row}>
        <Select aria-label="车辆资产" value={pick} onChange={(e) => setPick(e.target.value)} style={{ maxWidth: 210 }}>
          <option value="">{b ? '换一种车辆…' : '选择车辆资产…'}</option>
          {vehicles.map((v) => (
            <option key={v.id} value={v.id} disabled={v.level === 'display' || v.level === 'incomplete'}>
              {v.name}（{LEVEL_LABEL[v.level ?? 'incomplete']?.text ?? v.level}）
            </option>
          ))}
        </Select>
        <Button size="sm" disabled={!pick} onClick={() => void apply()}>应用</Button>
        {b && <Button size="sm" variant="plain" icon={Unlink} title="解除绑定（数值保留为手工设置）" onClick={unbindVehicle} />}
        <Button size="sm" variant="plain" icon={PackageOpen} title="打开资产库" onClick={() => useStudio.getState().setWorkspace('assets')} />
      </div>
      <h3>车辆实例</h3>
      <table className={p.table}>
        <thead><tr><th>车辆 / 类别</th><th>家库（轮询分配）</th><th className={p.num}>分配任务</th><th>状态</th></tr></thead>
        <tbody>
          {names.map((n, i) => {
            const pv = per.find((x) => x.vehicle === n) ?? per[i];
            const sv = simOf(i);
            const home = pv?.depot ?? i % depots.length;
            return (
              <tr key={`${n}-${i}`} style={{ cursor: 'default' }}>
                <td><span className={p.swatch} style={{ background: pv?.color ?? sv?.color ?? '#c7c7cc' }} /> {n}</td>
                <td className={p.mono}>D{home + 1} ({depots[home]?.map((x) => x.toFixed(1)).join(', ') ?? '—'})</td>
                <td className={p.num}>{pv ? `${pv.tasks}${pv.trips > 1 ? ` · ${pv.trips} 趟` : ''}` : '—'}</td>
                <td>{sv ? `仿真 ${sv.tasks_done}/${sv.tasks_total}${sv.state === 'failed' ? ' 受阻' : sv.state === 'done' ? ' 完成' : ''}` : pv ? '已规划' : run ? '未使用' : '未求解'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className={p.meta}><Layers size={11} style={{ verticalAlign: -1 }} /> 车辆按表序轮询分配到各车库。</p>
    </>
  );
}
