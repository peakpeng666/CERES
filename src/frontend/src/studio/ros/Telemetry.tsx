/**
 * Live telemetry with uPlot. In the field simulation each vehicle's speed, progress along its route
 * and cross-track error come from /workbench/fleet_status; for TurtleBot4 sessions the odometry
 * and command velocity come from /odom and /cmd_vel.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { cssVar, useThemeKey } from '../../lab/ui/theme';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { Activity } from 'lucide-react';
import { EmptyState } from '../../lab/ui';
import { useRos, useRosTopic } from './foxglove';
import { useLive } from './live';
import s from './ros.module.css';

const WINDOW_S = 600;
type Series = { label: string; color: string };

function Chart({ title, unit, series, data, height = 150 }: { title: string; unit: string; series: Series[]; data: number[][]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const key = series.map((x) => x.label + x.color).join('|');
  const theme = useThemeKey();
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const axis = cssVar('--mac-label-3');
    const grid = cssVar('--mac-separator');
    const font = `10px ${cssVar('--mac-font')}`;
    const opts: uPlot.Options = {
      width: Math.max(240, el.clientWidth),
      height,
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false } },
      scales: { x: { time: false } },
      axes: [
        { label: '仿真时间 (s)', stroke: axis, grid: { stroke: grid }, ticks: { stroke: grid }, size: 34, font, labelFont: font },
        { label: unit, stroke: axis, grid: { stroke: grid }, ticks: { stroke: grid }, size: 46, font, labelFont: font },
      ],
      series: [{ label: 't' }, ...series.map((x) => ({ label: x.label, stroke: x.color.startsWith('--') ? cssVar(x.color) : x.color, width: 1.6, points: { show: false } }))],
    };
    plot.current = new uPlot(opts, [[], ...series.map(() => [])] as uPlot.AlignedData, el);
    const ro = new ResizeObserver(() => plot.current?.setSize({ width: Math.max(240, el.clientWidth), height }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
  }, [key, unit, height, theme]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    plot.current?.setData(data as uPlot.AlignedData);
  }, [data]);
  return (
    <div className={s.chart}>
      <h4>{title}</h4>
      <div ref={box} />
    </div>
  );
}

interface FleetSample { t: number; vehicles: { name: string; label: string; color: string; speed: number; progress: number; cross_track_mean_m: number | null; pose: [number, number, number] | null; tasks_done: number; tasks_total: number; state: string }[] }

export function TelemetryView() {
  const live = useLive();
  const url = useRos((r) => r.url);
  const topics = useRos((r) => r.topics);
  const hasFleet = !!topics['/workbench/fleet_status'];
  const buf = useRef<FleetSample[]>([]);
  const odom = useRef<number[][]>([[], [], [], [], []]);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    buf.current = [];
    odom.current = [[], [], [], [], []];
  }, [url]);
  useEffect(() => {
    const t = window.setInterval(() => setTick((x) => x + 1), 500);
    return () => window.clearInterval(t);
  }, []);
  useRosTopic(url && hasFleet ? '/workbench/fleet_status' : null, (msg) => {
    try {
      const sample = JSON.parse(msg.data) as FleetSample;
      if (sample.t == null) return;
      const b = buf.current;
      if (b.length && sample.t < b[b.length - 1].t) b.length = 0; // simulation restarted
      b.push(sample);
      while (b.length && sample.t - b[0].t > WINDOW_S) b.shift();
    } catch {
      /* not a status sample */
    }
  });
  // TurtleBot4: /odom linear + angular speed and the commanded velocity
  const cmd = useRef<[number, number]>([0, 0]);
  useRosTopic(url && !hasFleet && topics['/cmd_vel'] ? '/cmd_vel' : null, (m) => { cmd.current = [m.linear.x, m.angular.z]; });
  useRosTopic(url && !hasFleet && topics['/odom'] ? '/odom' : null, (m) => {
    const t = m.header.stamp.sec + m.header.stamp.nanosec / 1e9;
    const o = odom.current;
    if (o[0].length && t <= o[0][o[0].length - 1]) return;
    o[0].push(t); o[1].push(m.twist.twist.linear.x); o[2].push(m.twist.twist.angular.z); o[3].push(cmd.current[0]); o[4].push(cmd.current[1]);
    while (o[0].length && t - o[0][0] > 120) o.forEach((a) => a.shift());
  });

  const fleet = useMemo(() => {
    const b = buf.current;
    if (!b.length) return null;
    const vehicles = b[b.length - 1].vehicles;
    const t = b.map((x) => x.t);
    const col = (f: (v: FleetSample['vehicles'][number]) => number | null) =>
      vehicles.map((v) => b.map((x) => f(x.vehicles.find((y) => y.name === v.name) ?? v) ?? 0));
    return {
      series: vehicles.map((v) => ({ label: v.label, color: v.color })),
      speed: [t, ...col((v) => v.speed)],
      progress: [t, ...col((v) => v.progress * 100)],
      cte: [t, ...col((v) => v.cross_track_mean_m)],
      latest: vehicles,
    };
  }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const odomData = useMemo(() => odom.current.map((a) => [...a]), [tick]);

  if (!url) {
    return <div className={s.empty}><EmptyState icon={Activity} title="没有遥测" detail="仿真运行后，这里实时绘制每台车的速度、进度和偏离规划路线的距离。" /></div>;
  }
  if (live.kind === 'field' || hasFleet) {
    if (!fleet) return <div className={s.empty}><EmptyState icon={Activity} title="等待 /workbench/fleet_status" /></div>;
    return (
      <div className={s.charts}>
        <Chart title="速度" unit="m/s" series={fleet.series} data={fleet.speed} />
        <Chart title="路线完成度" unit="%" series={fleet.series} data={fleet.progress} />
        <Chart title="平均横向偏差" unit="m" series={fleet.series} data={fleet.cte} />
      </div>
    );
  }
  return (
    <div className={s.charts}>
      <Chart title="线速度" unit="m/s" series={[{ label: '/odom', color: '--route-1' }, { label: '/cmd_vel', color: '--route-2' }]} data={[odomData[0], odomData[1], odomData[3]]} />
      <Chart title="角速度" unit="rad/s" series={[{ label: '/odom', color: '--route-3' }, { label: '/cmd_vel', color: '--route-4' }]} data={[odomData[0], odomData[2], odomData[4]]} />
    </div>
  );
}
