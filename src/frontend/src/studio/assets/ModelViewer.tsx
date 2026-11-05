/**
 * 3D view of a vehicle / facility model in the robot frame (x forward, y left, z up): the appearance model
 * (glTF/GLB, STL/OBJ/DAE, URDF with its meshes, or a built-in), the planning footprint and the collision
 * geometry as separate, individually toggled layers, so their sizes can be checked against each other.
 */
import { Canvas, useThree } from '@react-three/fiber';
import { useCssVars } from '../../lab/ui/theme';
import { Html, Line, OrbitControls } from '@react-three/drei';
import { useEffect, useMemo, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js';
import URDFLoader from 'urdf-loader';
import type { PlanningProfile } from './api';
import s from './assets.module.css';

export type ModelSource =
  | { type: 'gltf' | 'mesh'; url: string; ext: string }
  | { type: 'urdf'; url: string; resolve: (pkgOrRel: string, rel: string) => string }
  | { type: 'builtin'; builtin: string };

export interface Layers { appearance: boolean; footprint: boolean; collision: boolean; axes: boolean }
export interface Measured { length_m: number; width_m: number; height_m: number }

const AXES: Record<string, THREE.Vector3> = {
  '+x': new THREE.Vector3(1, 0, 0), '-x': new THREE.Vector3(-1, 0, 0), '+y': new THREE.Vector3(0, 1, 0),
  '-y': new THREE.Vector3(0, -1, 0), '+z': new THREE.Vector3(0, 0, 1), '-z': new THREE.Vector3(0, 0, -1),
};

/** Rotation taking the model's forward axis to robot +x and its up axis to robot +z. */
export function orientation(forward: string, up: string): THREE.Matrix4 | null {
  const f = AXES[forward], u = AXES[up];
  if (!f || !u || Math.abs(f.dot(u)) > 1e-6) return null;
  const left = new THREE.Vector3().crossVectors(u, f);
  const m = new THREE.Matrix4();
  m.set(f.x, f.y, f.z, 0, left.x, left.y, left.z, 0, u.x, u.y, u.z, 0, 0, 0, 0, 1);
  return m;
}

async function loadModel(src: ModelSource, collision: boolean): Promise<THREE.Object3D> {
  if (src.type === 'builtin') return builtinModel(src.builtin);
  if (src.type === 'gltf') {
    const gltf = await new GLTFLoader().loadAsync(src.url);
    return gltf.scene;
  }
  if (src.type === 'mesh') {
    if (src.ext === 'stl') {
      const geo = await new STLLoader().loadAsync(src.url);
      return new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: '#b8bcc4', roughness: 0.7 }));
    }
    if (src.ext === 'obj') return new OBJLoader().loadAsync(src.url);
    const dae = await new ColladaLoader().loadAsync(src.url);
    if (!dae) throw new Error('无法解析 DAE');
    dae.scene.rotation.set(0, 0, 0); // keep the file's own axes; the forward / up choice below orients it
    return dae.scene;
  }
  if (src.type !== 'urdf') throw new Error(`不支持的模型类型 ${src.type}`);
  const resolvePackage = src.resolve;
  return new Promise((resolve, reject) => {
    const manager = new THREE.LoadingManager();
    let robot: THREE.Object3D | null = null;
    manager.onLoad = () => robot && resolve(robot);
    const loader = new URDFLoader(manager);
    loader.parseCollision = collision;
    loader.packages = (pkg: string) => resolvePackage(pkg, '');
    loader.loadMeshCb = (path, mgr, material, done) => {
      if (/\.dae$/i.test(path)) {
        new ColladaLoader(mgr).load(path, (dae) => {
          if (!dae) return done(new THREE.Object3D());
          dae.scene.rotation.set(0, 0, 0); // ROS meshes are z-up; URDF places them as authored
          done(dae.scene);
        }, undefined, (err) => done(new THREE.Object3D(), err as Error));
      } else {
        loader.defaultMeshLoader(path, mgr, material, done);
      }
    };
    loader.load(src.url, (r) => { robot = r; }, undefined, (err) => reject(err instanceof Error ? err : new Error(String(err))));
    window.setTimeout(() => robot && resolve(robot), 15000); // a missing mesh must not hang the preview forever
  });
}

function builtinModel(kind: string): THREE.Object3D {
  const g = new THREE.Group();
  const mat = (c: string) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.6 });
  const box = (x: number, y: number, z: number, px: number, py: number, pz: number, c: string) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(x, y, z), mat(c));
    m.position.set(px, py, pz);
    g.add(m);
  };
  if (kind === 'field-diffdrive') {
    box(0.8, 0.56, 0.22, 0, 0, 0.25, '#f2f2f0');
    box(0.7, 0.5, 0.03, 0, 0, 0.375, '#0a84ff');
    box(0.02, 0.4, 0.08, 0.41, 0, 0.27, '#1d1d1f');
    for (const y of [0.33, -0.33]) {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 0.08, 24), mat('#26262a'));
      w.position.set(0.12, y, 0.15);
      g.add(w);
    }
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.7, 8), mat('#4a4a4f'));
    mast.rotation.x = Math.PI / 2;
    mast.position.set(-0.25, 0, 0.7);
    g.add(mast);
    const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), mat('#0a84ff'));
    beacon.position.set(-0.25, 0, 1.07);
    g.add(beacon);
  }
  return g;
}

function Fit({ size }: { size: number }) {
  const camera = useThree((st) => st.camera);
  const controls = useThree((st) => st.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  useEffect(() => {
    const d = Math.max(1.2, size * 1.9);
    camera.position.set(d * 0.9, d * 0.75, d * 0.95);
    camera.lookAt(0, size * 0.25, 0);
    if (controls) {
      controls.target.set(0, size * 0.25, 0);
      controls.update();
    }
  }, [size, camera, controls]);
  return null;
}

export function ModelViewer({ source, forward = '+x', up = '+z', scale = 1, planning, layers, onMeasured, height }: {
  source: ModelSource | null; forward?: string; up?: string; scale?: number; planning?: PlanningProfile | null; layers: Layers;
  onMeasured?: (m: Measured | null) => void; height?: number;
}) {
  const [model, setModel] = useState<THREE.Object3D | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = source ? JSON.stringify({ ...source, resolve: undefined }) : '';
  useEffect(() => {
    let alive = true;
    setModel(null);
    setError(null);
    if (!source) return;
    loadModel(source, true).then((m) => alive && setModel(m)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  // orient → scale → sit on the ground, centred over the origin
  const placed = useMemo(() => {
    if (!model) return null;
    const rot = source?.type === 'urdf' || source?.type === 'builtin' ? new THREE.Matrix4() : orientation(forward, up);
    if (!rot) return null;
    const holder = new THREE.Group();
    const inner = model;
    inner.matrixAutoUpdate = false;
    inner.matrix.copy(new THREE.Matrix4().multiplyMatrices(rot, new THREE.Matrix4().makeScale(scale, scale, scale)));
    holder.add(inner);
    holder.updateMatrixWorld(true);
    let box = new THREE.Box3();
    inner.traverse((o) => {
      const isCollider = (o as unknown as { isURDFCollider?: boolean }).isURDFCollider;
      if ((o as THREE.Mesh).isMesh && !isCollider) box.expandByObject(o);
    });
    if (box.isEmpty()) box = new THREE.Box3().setFromObject(inner);
    const c = box.getCenter(new THREE.Vector3());
    holder.position.set(-c.x, -c.y, -box.min.z);
    const size = box.getSize(new THREE.Vector3());
    return { holder, size };
  }, [model, forward, up, scale, source?.type]);

  useEffect(() => {
    onMeasured?.(placed ? { length_m: +placed.size.x.toFixed(4), width_m: +placed.size.y.toFixed(4), height_m: +placed.size.z.toFixed(4) } : null);
  }, [placed]); // eslint-disable-line react-hooks/exhaustive-deps

  // collision layer: URDF colliders (when the description has them)
  useEffect(() => {
    if (!model) return;
    model.traverse((o) => {
      const isCollider = (o as unknown as { isURDFCollider?: boolean }).isURDFCollider;
      if (isCollider) {
        o.visible = layers.collision;
        o.traverse((m) => {
          if ((m as THREE.Mesh).isMesh) (m as THREE.Mesh).material = new THREE.MeshBasicMaterial({ color: '#ff9f0a', transparent: true, opacity: 0.35, depthWrite: false });
        });
      } else if ((o as THREE.Mesh).isMesh) {
        o.visible = layers.appearance;
      }
    });
  }, [model, layers.appearance, layers.collision]);

  const fp = planning?.footprint;
  const hasColliders = useMemo(() => {
    let found = false;
    model?.traverse((o) => { if ((o as unknown as { isURDFCollider?: boolean }).isURDFCollider) found = true; });
    return found;
  }, [model]);
  const span = placed ? Math.max(placed.size.x, placed.size.y, placed.size.z) : 1;
  const colors = useCssVars({ bg: '--map-bg', edge: '--mac-separator-strong', line: '--mac-separator' });
  return (
    <div className={s.preview} style={height ? { height } : undefined}>
      <Canvas camera={{ position: [3, 2.5, 3], fov: 40, near: 0.01, far: 500 }} dpr={[1, 2]}>
        <color attach="background" args={[colors.bg]} />
        <ambientLight intensity={1.0} />
        <directionalLight position={[4, 8, 5]} intensity={1.8} />
        <directionalLight position={[-5, 3, -4]} intensity={0.5} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
        <Fit size={span} />
        <gridHelper args={[Math.max(4, Math.ceil(span * 3)), Math.max(8, Math.ceil(span * 3) * 2), colors.edge, colors.line]} key={colors.edge} />
        <group rotation-x={-Math.PI / 2}>
          {placed && <primitive object={placed.holder} />}
          {layers.footprint && fp && (fp.type === 'rect' && fp.length_m && fp.width_m ? (
            <group position={[0, 0, 0.005]}>
              <mesh><planeGeometry args={[fp.length_m, fp.width_m]} /><meshBasicMaterial color="#0a84ff" transparent opacity={0.18} depthWrite={false} /></mesh>
              <Line points={[[-fp.length_m / 2, -fp.width_m / 2, 0], [fp.length_m / 2, -fp.width_m / 2, 0], [fp.length_m / 2, fp.width_m / 2, 0], [-fp.length_m / 2, fp.width_m / 2, 0], [-fp.length_m / 2, -fp.width_m / 2, 0]]} color="#0a84ff" lineWidth={2} />
            </group>
          ) : fp.type === 'circle' && fp.radius_m ? (
            <group position={[0, 0, 0.005]}>
              <mesh><circleGeometry args={[fp.radius_m, 48]} /><meshBasicMaterial color="#0a84ff" transparent opacity={0.18} depthWrite={false} /></mesh>
            </group>
          ) : null)}
          {layers.collision && placed && !hasColliders && (
            <mesh position={[0, 0, placed.size.z / 2]}>
              <boxGeometry args={[placed.size.x, placed.size.y, placed.size.z]} />
              <meshBasicMaterial color="#ff9f0a" wireframe />
            </mesh>
          )}
          {layers.axes && (
            <group>
              <Line points={[[0, 0, 0.01], [Math.max(0.6, span * 0.7), 0, 0.01]]} color="#ff3b30" lineWidth={3} />
              <Line points={[[0, 0, 0.01], [0, Math.max(0.4, span * 0.45), 0.01]]} color="#30b158" lineWidth={3} />
              <Line points={[[0, 0, 0], [0, 0, Math.max(0.4, span * 0.45)]]} color="#0a84ff" lineWidth={3} />
              <Html position={[Math.max(0.6, span * 0.7) + 0.05, 0, 0.02]} center><span className={s.chip} data-tone="red">前方 +x</span></Html>
            </group>
          )}
        </group>
      </Canvas>
      <div className={s.overlay}>
        {error && <div className={s.glass} style={{ color: '#b1271d' }}>模型载入失败：{error}</div>}
        {!error && !model && source && <div className={s.glass}>载入模型…</div>}
        {layers.collision && model && !hasColliders && <div className={s.glass}>没有碰撞几何：橙色线框是外观的包围盒，不是碰撞体</div>}
      </div>
      {placed && (
        <div className={`${s.bottomRight} ${s.glass}`}>
          外观 {placed.size.x.toFixed(2)} × {placed.size.y.toFixed(2)} × {placed.size.z.toFixed(2)} m
          {fp?.type === 'rect' && fp.length_m && fp.width_m ? ` · 足迹 ${fp.length_m} × ${fp.width_m} m` : fp?.type === 'circle' && fp.radius_m ? ` · 足迹半径 ${fp.radius_m} m` : ''}
        </div>
      )}
    </div>
  );
}
