/**
 * Live 3D view of the ROS 2 graph through Foxglove Bridge, in the spirit of RViz2: occupancy map,
 * Nav2 plans, laser scan, TF, the TurtleBot4 URDF from the installed description packages, and any
 * visualization_msgs/MarkerArray topic (the field simulation publishes its field, routes, tasks,
 * trails and vehicles that way). ROS is z-up; everything sits in one group rotated into three's y-up.
 */
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, OrbitControls } from '@react-three/drei';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js';
import URDFLoader from 'urdf-loader';
import { Bot, Crosshair, Eye, EyeOff, Radio } from 'lucide-react';
import { EmptyState } from '../../lab/ui';
import { useCssVars } from '../../lab/ui/theme';
import { urls } from '../api';
import { useRos, useRosTopic } from './foxglove';
import { useLive } from './live';
import { TfBuffer, type TransformStamped } from './tf';
import s from './ros.module.css';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Vec = { x: number; y: number; z: number };
type Rgba = { r: number; g: number; b: number; a: number };
interface RosMarker {
  header: { frame_id: string };
  ns: string;
  id: number;
  type: number;
  action: number;
  pose: { position: Vec; orientation: Vec & { w: number } };
  scale: Vec;
  color: Rgba;
  points: Vec[];
  colors: Rgba[];
  text: string;
}
const M = { ARROW: 0, CUBE: 1, SPHERE: 2, CYLINDER: 3, LINE_STRIP: 4, LINE_LIST: 5, CUBE_LIST: 6, SPHERE_LIST: 7, POINTS: 8, TEXT: 9, MESH: 10, TRIANGLES: 11 };

const LAYERS = [
  ['field', '田块'], ['routes', '规划路线'], ['trails', '实际轨迹'], ['tasks', '任务点'], ['vehicles', '车辆'], ['labels', '标签'],
  ['map', '地图'], ['costmap', '代价地图'], ['scan', '激光'], ['nav2', 'Nav2 路径'], ['robot', 'TurtleBot4'], ['tf', 'TF'],
] as const;
type LayerId = (typeof LAYERS)[number][0];
const DEFAULT_ON: Record<LayerId, boolean> = {
  field: true, routes: true, trails: true, tasks: true, vehicles: true, labels: true,
  map: true, costmap: false, scan: true, nav2: true, robot: true, tf: false,
};

/** Screen-space widths (px) for the workbench's line markers. */
const LINE_PX: Record<string, number> = { routes: 3, trails: 2.5, boundary: 2, plots: 1 };
const srgb = (c: Rgba) => new THREE.Color().setRGB(c.r, c.g, c.b, THREE.SRGBColorSpace);
const STATUS_TEXT = { idle: '未连接', connecting: '连接中', connected: '已连接', closed: '重连中' } as const;

/** Which layer a marker topic belongs to. */
function layerOf(topic: string): LayerId {
  if (topic.endsWith('/field')) return 'field';
  if (topic.endsWith('/routes') || topic.endsWith('planned_path')) return 'routes';
  if (topic.endsWith('/trails') || topic.endsWith('actual_path')) return 'trails';
  if (topic.endsWith('/tasks')) return 'tasks';
  if (topic.endsWith('/vehicles')) return 'vehicles';
  return 'field';
}

/* ------------------------------------------------------------------ view -- */
export function RosView() {
  const colors = useCssVars({ bg: '--map-bg' });
  const live = useLive();
  const status = useRos((r) => r.status);
  const url = useRos((r) => r.url);
  const topics = useRos((r) => r.topics);
  const [layers, setLayers] = useState(DEFAULT_ON);
  const [follow, setFollow] = useState<string | null>(null);
  const [bigVehicles, setBigVehicles] = useState(true);
  const [showTopics, setShowTopics] = useState(false);
  const tf = useMemo(() => new TfBuffer(), []);
  const [bounds, setBounds] = useState<THREE.Box3 | null>(null);
  const [fitNonce, setFitNonce] = useState(0);
  const growBounds = useCallback((b: THREE.Box3) => setBounds((prev) => {
    if (prev && prev.containsBox(b)) return prev;
    return prev ? prev.clone().union(b) : b.clone();
  }), []);

  useRosTopic(url ? '/tf' : null, (m) => tf.update(m.transforms as TransformStamped[]));
  useRosTopic(url ? '/tf_static' : null, (m) => tf.update(m.transforms as TransformStamped[]));
  useEffect(() => {
    setBounds(null);
    setFitNonce((n) => n + 1);
  }, [url]);

  const markerTopics = Object.values(topics).filter((t) => t.type === 'visualization_msgs/msg/MarkerArray').map((t) => t.topic).sort();
  const pathTopics = Object.values(topics).filter((t) => t.type === 'nav_msgs/msg/Path').map((t) => t.topic).sort();
  const vehicles = live.kind === 'field' ? live.field?.vehicles ?? [] : [];
  const on = (k: LayerId) => layers[k];

  if (!url) {
    return (
      <div className={s.empty}>
        <EmptyState icon={Radio} title="没有运行中的仿真" detail="在「任务规划」的方案对比里点「Gazebo」把多车方案送进田间仿真，或在 TurtleBot4 页启动仿真；这里会实时显示 ROS 2 话题。" />
      </div>
    );
  }
  return (
    <div className={s.view}>
      <Canvas className={s.canvas} camera={{ position: [0, 80, 80], fov: 40, near: 0.1, far: 20000 }} gl={{ antialias: true }} dpr={[1, 2]}>
        <color attach="background" args={[colors.bg]} />
        <ambientLight intensity={1.1} />
        <directionalLight position={[60, 120, 40]} intensity={1.6} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} maxPolarAngle={Math.PI / 2.05} />
        <AutoFit bounds={bounds} nonce={fitNonce} />
        <GroundGrid bounds={bounds} />
        <group rotation-x={-Math.PI / 2}>
          {on('map') && <OccupancyLayer topic="/map" onBounds={growBounds} />}
          {on('costmap') && <OccupancyLayer topic="/global_costmap/costmap" costmap />}
          {markerTopics.map((t) => on(layerOf(t)) && (
            <MarkerTopic key={t} topic={t} tf={tf} labels={on('labels')} bigVehicles={bigVehicles} onBounds={t.endsWith('/field') ? growBounds : undefined} />
          ))}
          {on('nav2') && pathTopics.filter((t) => !t.startsWith('/workbench')).map((t) => (
            <PathTopic key={t} topic={t} tf={tf} color={t === '/plan' ? '#30b158' : t.includes('local') ? '#ff9f0a' : '#5e5ce6'} />
          ))}
          {on('routes') && pathTopics.includes('/workbench/planned_path') && <PathTopic topic="/workbench/planned_path" tf={tf} color="#0a84ff" width={3} />}
          {on('trails') && pathTopics.includes('/workbench/actual_path') && <PathTopic topic="/workbench/actual_path" tf={tf} color="#ff375f" width={2.5} />}
          {on('scan') && <ScanLayer tf={tf} />}
          {on('robot') && <Turtlebot tf={tf} />}
          {on('tf') && <TfAxes tf={tf} />}
        </group>
        <Follow target={follow} vehicles={vehicles} tf={tf} />
      </Canvas>

      <div className={s.hud}>
        <div className={s.hudRow}>
          <span className={s.dot} data-s={status} />
          <span className={s.hudTitle}>{live.kind === 'field' ? '田间多车仿真' : live.kind === 'robot' ? 'TurtleBot4 仿真' : 'ROS 2'}</span>
          <span className={s.hudMeta}>Foxglove Bridge · {STATUS_TEXT[status]}</span>
        </div>
        <div className={s.chips}>
          {LAYERS.map(([k, label]) => (
            <button key={k} type="button" className={s.chip} data-on={layers[k] || undefined} onClick={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}>
              {layers[k] ? <Eye size={11} /> : <EyeOff size={11} />}{label}
            </button>
          ))}
          <button type="button" className={s.chip} data-on={bigVehicles || undefined} onClick={() => setBigVehicles(!bigVehicles)} title="田块很大时把车辆放大显示">
            <Bot size={11} />放大车辆
          </button>
          <button type="button" className={s.chip} onClick={() => { setFollow(null); setFitNonce((n) => n + 1); }} title="适配全图">
            <Crosshair size={11} />全图
          </button>
        </div>
        {live.kind === 'robot' && (
          <div className={s.chips}>
            <button type="button" className={s.chip} data-on={follow === 'base_link' || undefined} onClick={() => setFollow(follow === 'base_link' ? null : 'base_link')}>
              <Bot size={11} />跟随 TurtleBot4
            </button>
          </div>
        )}
        {vehicles.length > 0 && (
          <div className={s.chips}>
            {vehicles.map((v) => (
              <button key={v.name} type="button" className={s.chip} data-on={follow === v.name || undefined} onClick={() => setFollow(follow === v.name ? null : v.name)}>
                <i className={s.swatch} style={{ background: v.color }} />跟随 {v.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className={s.topics}>
        <button type="button" className={s.topicsHead} onClick={() => setShowTopics(!showTopics)}>
          {Object.keys(topics).length} 个话题 {showTopics ? '▾' : '▸'}
        </button>
        {showTopics && (
          <table>
            <tbody>
              {Object.values(topics).sort((a, b) => a.topic.localeCompare(b.topic)).map((t) => (
                <tr key={t.topic}><td>{t.topic}</td><td>{t.type.replace('/msg/', '/')}</td><td>{t.hz ? `${t.hz} Hz` : ''}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ camera -- */
/** 10 m grid under whatever the scene shows (three's ground plane is xz). */
function GroundGrid({ bounds }: { bounds: THREE.Box3 | null }) {
  const c = bounds ? bounds.getCenter(new THREE.Vector3()) : new THREE.Vector3();
  const size = bounds ? bounds.getSize(new THREE.Vector3()) : new THREE.Vector3(40, 40, 0);
  const span = Math.ceil((Math.max(size.x, size.y) + 40) / 10) * 10;
  const colors = useCssVars({ edge: '--mac-separator-strong', line: '--mac-separator' });
  return <gridHelper key={colors.edge} args={[span, span / 10, colors.edge, colors.line]} position={[c.x, -0.03, -c.y]} />;
}

function AutoFit({ bounds, nonce }: { bounds: THREE.Box3 | null; nonce: number }) {
  const camera = useThree((st) => st.camera);
  const controls = useThree((st) => st.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const done = useRef(-1);
  useEffect(() => {
    if (!bounds || !controls || done.current === nonce) return;
    done.current = nonce;
    const c = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.y, 8);
    const target = new THREE.Vector3(c.x, 0, -c.y); // ROS (x, y) → three (x, -y) on the ground
    controls.target.copy(target);
    camera.position.set(target.x, span * 0.95, target.z + span * 0.62);
    camera.lookAt(target);
    controls.update();
  }, [bounds, nonce, camera, controls]);
  return null;
}

function Follow({ target, vehicles, tf }: { target: string | null; vehicles: { name: string; pose: [number, number, number] | null }[]; tf: TfBuffer }) {
  const controls = useThree((st) => st.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const camera = useThree((st) => st.camera);
  const closeIn = useRef<string | null>(null);
  useFrame(() => {
    if (!target || !controls) {
      closeIn.current = null;
      return;
    }
    const m = tf.lookup(target === 'base_link' ? 'base_link' : `${target}/base_link`);
    const pose = m ? new THREE.Vector3().setFromMatrixPosition(m) : (() => {
      const v = vehicles.find((x) => x.name === target)?.pose;
      return v ? new THREE.Vector3(v[0], v[1], 0) : null;
    })();
    if (!pose) return;
    const goal = new THREE.Vector3(pose.x, 0, -pose.y);
    if (closeIn.current !== target) {
      // first frame on a new target: jump close enough to see the robot itself
      closeIn.current = target;
      const reach = target === 'base_link' ? 2.2 : 14;
      controls.target.copy(goal);
      camera.position.set(goal.x - reach * 0.6, reach, goal.z + reach);
      controls.update();
      return;
    }
    const delta = goal.clone().sub(controls.target).multiplyScalar(0.12);
    controls.target.add(delta);
    camera.position.add(delta);
    controls.update();
  });
  return null;
}

/* ------------------------------------------------------------ markers -- */
const MarkerTopic = memo(function MarkerTopic({ topic, tf, labels, bigVehicles, onBounds }: {
  topic: string; tf: TfBuffer; labels: boolean; bigVehicles: boolean; onBounds?: (b: THREE.Box3) => void;
}) {
  const store = useRef(new Map<string, RosMarker>());
  const [version, setVersion] = useState(0);
  const pending = useRef(0);
  useRosTopic(topic, (msg) => {
    for (const m of msg.markers as RosMarker[]) {
      if (m.action === 3) store.current.clear();
      else if (m.action === 2) store.current.delete(`${m.ns}/${m.id}`);
      else store.current.set(`${m.ns}/${m.id}`, m);
    }
    if (onBounds) {
      const b = new THREE.Box3();
      for (const m of store.current.values()) for (const p of m.points ?? []) b.expandByPoint(new THREE.Vector3(p.x, p.y, 0));
      if (!b.isEmpty()) onBounds(b);
    }
    if (!pending.current) pending.current = requestAnimationFrame(() => { pending.current = 0; setVersion((v) => v + 1); });
  });
  useEffect(() => () => cancelAnimationFrame(pending.current), []);
  const markers = useMemo(() => [...store.current.values()], [version]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <group>
      {markers.map((m) => <MarkerView key={`${m.ns}/${m.id}`} m={m} tf={tf} labels={labels} big={bigVehicles} />)}
    </group>
  );
});

const MarkerView = memo(function MarkerView({ m, labels, big }: { m: RosMarker; tf: TfBuffer; labels: boolean; big: boolean }) {
  const color = useMemo(() => srgb(m.color), [m.color]);
  const p = m.pose.position;
  const q = m.pose.orientation;
  const transparent = m.color.a < 0.999;
  const pose = { position: [p.x, p.y, p.z] as [number, number, number], quaternion: [q.x, q.y, q.z, q.w] as [number, number, number, number] };
  switch (m.type) {
    case M.TRIANGLES:
      return <TriangleMesh m={m} color={color} />;
    case M.LINE_STRIP:
    case M.LINE_LIST:
      if ((m.points?.length ?? 0) < 2) return null;
      return (
        <group {...pose}>
          <Line points={m.points.map((v) => [v.x, v.y, v.z] as [number, number, number])} color={color} lineWidth={LINE_PX[m.ns] ?? Math.min(4, Math.max(1, m.scale.x * 10))}
            segments={m.type === M.LINE_LIST} transparent={transparent} opacity={m.color.a} />
        </group>
      );
    case M.CUBE:
      if (m.ns === 'vehicles') return <Vehicle m={m} color={color} scale={big ? 3 : 1} />;
      return <mesh {...pose} scale={[m.scale.x, m.scale.y, m.scale.z]}><boxGeometry /><meshStandardMaterial color={color} transparent={transparent} opacity={m.color.a} /></mesh>;
    case M.SPHERE:
      return <mesh {...pose} scale={[m.scale.x, m.scale.y, m.scale.z]}><sphereGeometry args={[0.5, 20, 14]} /><meshStandardMaterial color={color} transparent={transparent} opacity={m.color.a} /></mesh>;
    case M.CYLINDER:
      return (
        <group {...pose} scale={m.ns === 'tasks' && big ? 2.5 : 1}>
          <mesh rotation-x={Math.PI / 2} scale={[m.scale.x, m.scale.z, m.scale.y]}><cylinderGeometry args={[0.5, 0.5, 1, 20]} /><meshStandardMaterial color={color} transparent={transparent} opacity={m.color.a} /></mesh>
        </group>
      );
    case M.POINTS:
    case M.SPHERE_LIST:
    case M.CUBE_LIST:
      return <PointCloud points={m.points} color={color} size={Math.max(0.05, m.scale.x)} />;
    case M.TEXT:
      if (!labels || !m.text) return null;
      return (
        <Html position={[p.x, p.y, p.z]} center zIndexRange={[20, 0]} className={m.ns.startsWith('vehicle') ? s.vehicleLabel : s.label}
          style={{ color: `rgb(${Math.round(m.color.r * 255)},${Math.round(m.color.g * 255)},${Math.round(m.color.b * 255)})` }}>
          {m.text}
        </Html>
      );
    default:
      return null;
  }
});

function TriangleMesh({ m, color }: { m: RosMarker; color: THREE.Color }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(m.points.length * 3);
    m.points.forEach((v, i) => pos.set([v.x, v.y, v.z], i * 3));
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.computeVertexNormals();
    return g;
  }, [m.points]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const p = m.pose.position;
  return (
    <mesh geometry={geometry} position={[p.x, p.y, p.z]}>
      <meshLambertMaterial color={color} side={THREE.DoubleSide} transparent={m.color.a < 0.999} opacity={m.color.a} depthWrite={m.color.a >= 0.999} />
    </mesh>
  );
}

function Vehicle({ m, color, scale }: { m: RosMarker; color: THREE.Color; scale: number }) {
  const p = m.pose.position;
  const q = m.pose.orientation;
  return (
    <group position={[p.x, p.y, 0]} quaternion={[q.x, q.y, q.z, q.w]} scale={scale}>
      <mesh position={[0, 0, 0.25]}><boxGeometry args={[0.8, 0.56, 0.22]} /><meshStandardMaterial color="#f2f2f0" /></mesh>
      <mesh position={[0, 0, 0.375]}><boxGeometry args={[0.7, 0.5, 0.03]} /><meshStandardMaterial color={color} /></mesh>
      <mesh position={[0.41, 0, 0.27]}><boxGeometry args={[0.02, 0.4, 0.08]} /><meshStandardMaterial color="#1d1d1f" /></mesh>
      {[0.33, -0.33].map((y) => (
        <mesh key={y} position={[0.12, y, 0.15]} rotation-x={Math.PI / 2}><cylinderGeometry args={[0.15, 0.15, 0.08, 18]} /><meshStandardMaterial color="#26262a" /></mesh>
      ))}
      <mesh position={[-0.25, 0, 0.7]} rotation-x={Math.PI / 2}><cylinderGeometry args={[0.02, 0.02, 0.7, 8]} /><meshStandardMaterial color="#4a4a4f" /></mesh>
      <mesh position={[-0.25, 0, 1.07]}><sphereGeometry args={[0.09, 16, 12]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} /></mesh>
    </group>
  );
}

function PointCloud({ points, color, size }: { points: Vec[]; color: THREE.Color; size: number }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(points.length * 3);
    points.forEach((v, i) => pos.set([v.x, v.y, v.z], i * 3));
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    return g;
  }, [points]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <points geometry={geometry}><pointsMaterial color={color} size={size} sizeAttenuation /></points>;
}

/* -------------------------------------------------------------- paths -- */
function PathTopic({ topic, tf, color, width = 2.5 }: { topic: string; tf: TfBuffer; color: string; width?: number }) {
  const [pts, setPts] = useState<[number, number, number][]>([]);
  useRosTopic(topic, (msg) => {
    const frame = msg.header?.frame_id || 'map';
    const m = frame === 'map' ? null : tf.lookup(frame);
    setPts((msg.poses as { pose: { position: Vec } }[]).map(({ pose }) => {
      const v = new THREE.Vector3(pose.position.x, pose.position.y, 0.06);
      if (m) v.applyMatrix4(m);
      return [v.x, v.y, 0.06];
    }));
  });
  if (pts.length < 2) return null;
  return <Line points={pts} color={color} lineWidth={width} />;
}

/* ---------------------------------------------------------------- map -- */
function OccupancyLayer({ topic, costmap = false, onBounds }: { topic: string; costmap?: boolean; onBounds?: (b: THREE.Box3) => void }) {
  const [grid, setGrid] = useState<{ tex: THREE.DataTexture; w: number; h: number; x: number; y: number; yaw: number } | null>(null);
  useRosTopic(topic, (msg) => {
    const { width, height, resolution, origin } = msg.info;
    const data = msg.data as Int8Array;
    const rgba = new Uint8Array(width * height * 4);
    for (let i = 0; i < width * height; i++) {
      const v = data[i];
      let c: [number, number, number, number];
      if (costmap) {
        c = v < 0 ? [0, 0, 0, 0] : v === 0 ? [0, 0, 0, 0] : v >= 99 ? [255, 45, 140, 170] : [Math.round(40 + 2 * v), 80, Math.round(230 - 2 * v), Math.round(40 + v)];
      } else {
        c = v < 0 ? [205, 208, 214, 120] : v >= 65 ? [46, 48, 54, 255] : [250, 250, 248, 255 - Math.round(v * 1.5)];
      }
      rgba.set(c, i * 4);
    }
    const tex = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
    tex.magFilter = THREE.NearestFilter;
    tex.needsUpdate = true;
    const q = origin.orientation;
    setGrid((prev) => {
      prev?.tex.dispose();
      return { tex, w: width * resolution, h: height * resolution, x: origin.position.x, y: origin.position.y, yaw: Math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)) };
    });
    if (onBounds) onBounds(new THREE.Box3(new THREE.Vector3(origin.position.x, origin.position.y, 0), new THREE.Vector3(origin.position.x + width * resolution, origin.position.y + height * resolution, 0)));
  });
  if (!grid) return null;
  return (
    <group position={[grid.x, grid.y, costmap ? 0.01 : 0]} rotation-z={grid.yaw}>
      <mesh position={[grid.w / 2, grid.h / 2, 0]}>
        <planeGeometry args={[grid.w, grid.h]} />
        <meshBasicMaterial map={grid.tex} transparent depthWrite={false} />
      </mesh>
    </group>
  );
}

/* --------------------------------------------------------------- scan -- */
function ScanLayer({ tf }: { tf: TfBuffer }) {
  const [pts, setPts] = useState<Float32Array | null>(null);
  useRosTopic('/scan', (msg) => {
    const m = tf.lookup(msg.header.frame_id);
    if (!m) return;
    const ranges = msg.ranges as Float32Array;
    const out: number[] = [];
    const v = new THREE.Vector3();
    for (let i = 0; i < ranges.length; i++) {
      const r = ranges[i];
      if (!(r >= msg.range_min && r <= msg.range_max)) continue;
      const a = msg.angle_min + i * msg.angle_increment;
      v.set(r * Math.cos(a), r * Math.sin(a), 0).applyMatrix4(m);
      out.push(v.x, v.y, 0.12);
    }
    setPts(new Float32Array(out));
  });
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    if (pts) g.setAttribute('position', new THREE.BufferAttribute(pts, 3));
    return g;
  }, [pts]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  if (!pts) return null;
  return <points geometry={geometry}><pointsMaterial color="#ff3b30" size={0.05} sizeAttenuation /></points>;
}

/* -------------------------------------------------------------- robot -- */
let robotPromise: Promise<THREE.Object3D> | null = null;
function loadTurtlebot(): Promise<THREE.Object3D> {
  if (!robotPromise) {
    robotPromise = new Promise((resolve, reject) => {
      // urdf-loader returns the robot before its meshes arrive; resolve once the manager has loaded everything.
      const manager = new THREE.LoadingManager();
      let robot: THREE.Object3D | null = null;
      manager.onLoad = () => robot && resolve(robot);
      const loader = new URDFLoader(manager);
      loader.packages = (pkg: string) => urls.rosPackage(pkg);
      loader.loadMeshCb = (path, manager, material, done) => {
        if (/\.dae$/i.test(path)) {
          // ROS meshes are authored z-up and URDF places them as-is, so undo ColladaLoader's y-up turn.
          new ColladaLoader(manager).load(path, (dae) => {
            if (!dae) return done(new THREE.Object3D());
            dae.scene.rotation.set(0, 0, 0);
            done(dae.scene);
          }, undefined, (err) => done(new THREE.Object3D(), err as Error));
        } else {
          loader.defaultMeshLoader(path, manager, material, done);
        }
      };
      loader.load(urls.robotUrdf('turtlebot4'), (r) => { robot = r; }, undefined, (err) => { robotPromise = null; reject(err); });
    });
  }
  return robotPromise;
}

function Turtlebot({ tf }: { tf: TfBuffer }) {
  const [robot, setRobot] = useState<THREE.Object3D | null>(null);
  const group = useRef<THREE.Group>(null);
  const [present, setPresent] = useState(false);
  useEffect(() => {
    let alive = true;
    if (present) loadTurtlebot().then((r) => alive && setRobot(r.clone(true))).catch((e) => console.warn('TurtleBot4 模型载入失败', e));
    return () => { alive = false; };
  }, [present]);
  useFrame(() => {
    const m = tf.lookup('base_link');
    if (!present && m) setPresent(true);
    if (!m || !group.current) return;
    group.current.matrixAutoUpdate = false;
    group.current.matrix.copy(m);
    group.current.matrixWorldNeedsUpdate = true;
  });
  return <group ref={group}>{robot && <primitive object={robot} />}</group>;
}

function TfAxes({ tf }: { tf: TfBuffer }) {
  const group = useRef<THREE.Group>(null);
  const frames = ['base_link', 'odom', 'rplidar_link'];
  useFrame(() => {
    group.current?.children.forEach((child, i) => {
      const m = tf.lookup(frames[i]);
      child.visible = !!m;
      if (m) { child.matrixAutoUpdate = false; child.matrix.copy(m); }
    });
  });
  return <group ref={group}>{frames.map((f) => <axesHelper key={f} args={[0.6]} />)}</group>;
}
