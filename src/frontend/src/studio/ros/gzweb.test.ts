import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

class Socket {
  static OPEN = 1;
  static sockets: Socket[] = [];
  readyState = 1;
  binaryType = 'blob';
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: ArrayBuffer | string }) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  constructor() { Socket.sockets.push(this); }
  close() { this.readyState = 3; }
  send(value: string) { this.sent.push(value); }
  message(value: string) { this.onmessage?.({ data: new TextEncoder().encode(value).buffer }); }
}
interface Transport {
  connect(url: string, key?: string): void;
  disconnect(): void;
  getConnectionStatus(): { subscribe(fn: (s: string) => void): { unsubscribe(): void } };
  sceneInfo$: { subscribe(fn: (v: unknown) => void): { unsubscribe(): void } };
  root: unknown;
  lastError: string | null;
  assetMap: Map<string, unknown>;
  getAsset(uri: string, cb: (bytes: Uint8Array, error?: string) => void): void;
  getWorld(): string;
}
class DelayedReader {
  static pending: (() => void)[] = [];
  result: string | ArrayBuffer | null = null;
  onloadend: (() => void) | null = null;
  readAsText(data: ArrayBuffer) { this.result = new TextDecoder().decode(data); this.onloadend?.(); }
  readAsArrayBuffer(data: ArrayBuffer) {
    DelayedReader.pending.push(() => { this.result = data; this.onloadend?.(); });
  }
}
let TransportClass: new () => Transport;
class SceneStub {
  renderer = { domElement: { remove: vi.fn() } };
  constructor(public config: { findResourceCb: (uri: string, cb: (...args: unknown[]) => void) => void }) {}
  setSize() {}
  cleanup() {}
}
let ManagerClass: new () => { transport: Transport; scene: SceneStub; setupVisualization(): void; destroy(): void;
  getResourceStatus(): { total: number; ready: number; failed: string[] } };
let loadStl: (this: object, uri: string, submesh: string, center: boolean, done: (v: unknown) => void, fail: (e: unknown) => void) => void;
let parseStl: (bytes: ArrayBuffer | Uint8Array) => { isMesh: boolean; geometry: { attributes: { position: { count: number } } } };
beforeAll(() => {
  // Execute the actual shipped Transport, without loading the WebGL renderer in Node.
  const path = new URL('../../../node_modules/gzweb/dist/gzweb.module.js', import.meta.url);
  const source = readFileSync(process.env.CERES_GZWEB_TEST_DISTRIBUTION ?? path, 'utf8');
  const start = source.indexOf('class Transport {');
  const end = source.indexOf('\nclass ', start + 1);
  const requireGz = createRequire(path);
  TransportClass = runInNewContext(source.slice(start, end) + '\nTransport', {
    BehaviorSubject: requireGz('rxjs').BehaviorSubject, parse: requireGz('protobufjs').parse,
    WebSocket: Socket, ArrayBuffer, Uint8Array, TextDecoder, TextEncoder, console,
    get FileReader() { return globalThis.FileReader; },
  });
  const manager = source.slice(source.indexOf('class SceneManager {'), source.indexOf('// Assuming all mobile devices'));
  ManagerClass = runInNewContext(manager + '\nSceneManager', {
    Transport: TransportClass, Scene: SceneStub, Shaders: class {}, SDFParser: class {}, WebSocket: Socket,
    window: { document: { getElementById: () => ({ appendChild() {}, childElementCount: 1, clientWidth: 800, clientHeight: 600 }) } },
  });
  const THREE = requireGz('three');
  const loaderSource = source.slice(source.indexOf('class STLLoader extends'), source.indexOf('class GLTFLoader extends'));
  const Loader = runInNewContext(loaderSource + '\nSTLLoader', { ...THREE, ArrayBuffer, DataView, Uint8Array, Float32Array, console });
  parseStl = bytes => new Loader().parse(bytes);
  const stlStart = source.indexOf('    loadSTL(uri, submesh, centerSubmesh, onLoad, onError) {');
  loadStl = runInNewContext('({' + source.slice(stlStart, source.indexOf('    /**\n     * Load GLTF/GLB file', stlStart)) + '}).loadSTL', { THREE });
});
beforeEach(() => {
  Socket.sockets = []; DelayedReader.pending = [];
  vi.stubGlobal('WebSocket', Socket); vi.stubGlobal('FileReader', DelayedReader);
});
afterEach(() => vi.restoreAllMocks());
const schema = 'syntax="proto3"; package ignition.msgs; message Scene {}';

describe('gzweb connection ownership and ordered frames', () => {
  it('uses byte offsets for non-ASCII asset names and reports completed resources', () => {
    const m = new ManagerClass(); const received = vi.fn();
    m.transport.connect('ws://live'); const ws = Socket.sockets[0];
    ws.message('syntax="proto3"; package ignition.msgs; message Bytes { bytes data=2; }');
    const uri = '/田块/障碍.stl'; m.transport.getAsset(uri, received);
    expect(m.getResourceStatus()).toEqual({ total: 1, ready: 0, failed: [] });
    const header = new TextEncoder().encode(`asset,${uri},ignition.msgs.Bytes,`);
    const frame = new Uint8Array(header.length + 5); frame.set(header); frame.set([18, 3, 0, 255, 44], header.length);
    ws.onmessage!({ data: frame.buffer });
    expect(Array.from(received.mock.calls[0][0])).toEqual([0, 255, 44]);
    expect(m.getResourceStatus()).toEqual({ total: 1, ready: 1, failed: [] });
    m.destroy();
  });
  it('loads actual binary STL as renderable meshes over both HTTP and direct Gazebo assets', () => {
    const bytes = new Uint8Array(134); const view = new DataView(bytes.buffer);
    view.setUint32(80, 1, true); view.setFloat32(92, 1, true); // one triangle, normal z
    view.setFloat32(108, 1, true); view.setFloat32(124, 1, true);
    for (const data of [bytes, bytes.buffer]) {
      const parsed = parseStl(data); expect(parsed.isMesh).toBe(true);
      expect(parsed.geometry.attributes.position.count).toBe(3);
    }
    const manager = { itemStart: vi.fn(), itemError: vi.fn(), markAsDone: vi.fn(), markAsError: vi.fn() };
    const load = vi.fn((_u, done) => done(parseStl(bytes.buffer)));
    const scene = { stlLoader: { parse: parseStl, load, manager }, meshes: new Map(),
      findResourceCb: vi.fn((_u, cb) => cb(bytes)) };
    const done = vi.fn(), fail = vi.fn();
    loadStl.call(scene, '/server/field.stl', '', false, done, fail);
    expect(load).not.toHaveBeenCalled(); expect(manager.markAsDone).toHaveBeenCalledWith('/server/field.stl');
    expect(done.mock.calls[0][0].isMesh).toBe(true);
    loadStl.call(scene, 'https://models.test/field.stl', '', false, done, fail);
    expect(load).toHaveBeenCalledOnce(); expect(done.mock.calls[1][0].geometry.attributes.position.count).toBe(3);
    scene.findResourceCb = vi.fn((_u, cb) => cb(undefined, 'asset_not_found'));
    loadStl.call(scene, '/missing.stl', '', false, done, fail);
    expect(fail).toHaveBeenCalledOnce(); expect(done).toHaveBeenCalledTimes(2);
  });
  it('has no post-disconnect protobuf-root callback', () => {
    const t = new TransportClass(); t.connect('ws://live');
    const ws = Socket.sockets[0]; ws.onopen!(); ws.message(schema);
    ws.message('pub,scene,ignition.msgs.Scene,');
    const close = ws.onclose!;
    t.disconnect(); close();
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    DelayedReader.pending.forEach(f => f());
    expect(error).not.toHaveBeenCalled();
  });
  it('invalidates queued old close/open/message callbacks after reconnect', () => {
    const t = new TransportClass();
    t.connect('ws://old');
    const old = Socket.sockets[0];
    const lateClose = old.onclose!;
    const lateOpen = old.onopen!;
    const lateMessage = old.onmessage!;
    t.connect('ws://new');
    const next = Socket.sockets[1];
    next.onopen!(); next.message(schema);
    const root = t.root;
    expect(root).toBeTruthy();
    const sent = [...next.sent];
    lateClose();
    expect(t.root).toBe(root);
    lateOpen(); lateMessage({ data: new TextEncoder().encode('invalid').buffer });
    expect(next.sent).toEqual(sent);
    expect(next.readyState).toBe(1);
    t.disconnect();
  });
  it('cancels old scene asset fallbacks and late asset callbacks without hiding active requests', () => {
    const m = new ManagerClass(); const error = vi.spyOn(console, 'error');
    m.transport.connect('ws://live'); const ws = Socket.sockets[0];
    ws.onopen!(); ws.message(schema); m.setupVisualization();
    const findAsset = m.scene.config.findResourceCb;
    const received = vi.fn(); findAsset('file:///mesh.stl', received);
    expect(ws.sent.at(-1)).toBe('asset,,,file:///mesh.stl');
    const pending = m.transport.assetMap.get('file:///mesh.stl') as { cb: (...args: unknown[]) => void };
    m.destroy(); const sent = [...ws.sent];
    findAsset('file:///late.stl', received); pending.cb('late mesh');
    expect(received).not.toHaveBeenCalled(); expect(error).not.toHaveBeenCalled();
    expect(ws.sent).toEqual(sent);
  });
  it('decodes binary schema then scene synchronously in wire order without FileReader', () => {
    vi.stubGlobal('FileReader', class { constructor() { throw new Error('async reader must not be used'); } });
    const t = new TransportClass(); const statuses: string[] = []; const scenes: unknown[] = [];
    t.getConnectionStatus().subscribe(s => statuses.push(s));
    t.sceneInfo$.subscribe(v => scenes.push(v));
    t.connect('ws://live');
    const ws = Socket.sockets[0];
    expect(ws.binaryType).toBe('arraybuffer');
    ws.onopen!(); ws.message(schema); ws.message('pub,scene,ignition.msgs.Scene,');
    expect(statuses.slice(-2)).toEqual(['connected', 'ready']);
    expect(scenes.at(-1)).toBeTruthy();
    const late = ws.onmessage!;
    t.assetMap.set('file:///mesh.stl', {});
    t.disconnect();
    const error = vi.spyOn(console, 'error');
    late({ data: new TextEncoder().encode('pub,scene,ignition.msgs.Scene,').buffer });
    expect(error).not.toHaveBeenCalled();
    expect(t.root).toBeNull(); expect(t.assetMap.size).toBe(0);
    expect(t.getWorld()).toBe(''); expect(statuses.at(-1)).toBe('disconnected');
  });
  it.each(['invalid', 'not a protobuf schema'])('keeps a genuine protocol failure visible: %s', frame => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const t = new TransportClass(); const statuses: string[] = [];
    t.getConnectionStatus().subscribe(s => statuses.push(s));
    t.connect('ws://bad'); const ws = Socket.sockets[0];
    ws.onopen!(); ws.message(frame);
    expect(statuses.at(-1)).toBe('error'); expect(t.lastError).toBeTruthy();
    expect(ws.readyState).toBe(3); expect(error).toHaveBeenCalledOnce();
  });
  it('malformed binary frames fail and a subsequent fresh connection recovers', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const t = new TransportClass(); const statuses: string[] = [];
    t.getConnectionStatus().subscribe(s => statuses.push(s));
    t.connect('ws://live'); Socket.sockets[0].message(schema);
    Socket.sockets[0].message('pub,scene,unknown,corrupt');
    expect(statuses.at(-1)).toBe('error');
    t.connect('ws://recovered'); Socket.sockets[1].message(schema);
    expect(statuses.at(-1)).toBe('connected'); expect(t.lastError).toBeNull();
    t.disconnect(); t.disconnect();
  });
});
