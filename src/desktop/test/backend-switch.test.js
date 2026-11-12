'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { pathToFileURL } = require('node:url');
const { test } = require('node:test');

// Execute the real main-process control flow with in-memory windows and backends. No Electron, Docker or ROS is started.
function shellHarness(mode = 'native') {
  const values = new Map([['backend', mode]]);
  const calls = [], events = [], handlers = new Map();
  let url = 'http://127.0.0.1:4100/#/system/desktop';
  const win = {
    isDestroyed: () => false,
    webContents: { getURL: () => url, send: (channel, value) => events.push({ channel, value }) },
    loadFile: async (file) => { url = pathToFileURL(file).href; calls.push('splash'); },
    loadURL: async (value) => { url = value; calls.push('page'); },
  };
  const backend = { logPath: '/test/backend.log', stop: async () => calls.push('native.stop'), start: async () => { calls.push('native.start'); return 'http://127.0.0.1:4101'; } };
  const container = { stop: async () => calls.push('container.stop'), start: async () => { calls.push('container.start'); return 'http://127.0.0.1:4100'; } };
  const settings = { get: (key, fallback) => values.get(key) ?? fallback, set: (key, value) => values.set(key, value) };
  const electron = {
    app: { requestSingleInstanceLock: () => true, setAppUserModelId: () => {}, on: () => {}, whenReady: () => ({ then: () => {} }) },
    ipcMain: { handle: (name, callback) => handlers.set(name, callback) },
  };
  const context = vm.createContext({
    require: (name) => name === 'electron' ? electron : name.startsWith('node:') ? require(name) : {},
    __dirname: path.resolve(__dirname, '../main'), process, URL,
    setTimeout: (callback) => setImmediate(callback), harness: { win, backend, container, settings },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../main/index.js'), 'utf8') + `
    win = harness.win; backend = harness.backend; container = harness.container; settings = harness.settings;
    origin = new URL(win.webContents.getURL()).origin;
    wire(); globalThis.control = { switchBackend, get busy() { return transitioning; } };
  `, context);
  return { calls, events, backend, container, settings, control: context.control,
    invoke: (channel) => handlers.get(channel)({ senderFrame: { url } }),
    failure: () => events.filter((e) => e.channel === 'boot:error').at(-1)?.value,
  };
}

test('a failed native stop is shown on the splash and retry stops it before starting the container', async () => {
  const h = shellHarness();
  let stops = 0;
  h.backend.stop = async () => { h.calls.push('native.stop'); if (++stops === 1) throw new Error('后端还未退出'); };
  const failed = await h.control.switchBackend('container');
  assert.match(failed.error, /后端还未退出/);
  assert.equal(h.settings.get('backend'), 'native');
  assert.equal(h.failure().mode, 'native');
  assert.match(h.failure().message, /后端还未退出/);
  assert.equal(h.control.busy, false);
  assert.deepEqual(h.calls, ['splash', 'native.stop']);
  const retried = await h.invoke('app:retry');
  assert.equal(retried.mode, 'container');
  assert.equal(retried.error, undefined);
  assert.equal(h.settings.get('backend'), 'container');
  assert.deepEqual(h.calls, ['splash', 'native.stop', 'splash', 'native.stop', 'container.start', 'page']);
});

test('returning to native after a container stop failure keeps the error and a working retry', async () => {
  const h = shellHarness('container');
  let stops = 0;
  h.container.stop = async () => { h.calls.push('container.stop'); if (++stops === 1) throw new Error('Docker daemon unavailable'); };
  await h.invoke('app:use-native');
  assert.equal(h.failure().mode, 'container');
  assert.match(h.failure().message, /Docker daemon unavailable/);
  assert.equal(h.settings.get('backend'), 'container');
  assert.deepEqual(h.calls, ['splash', 'container.stop']);
  await h.invoke('app:retry');
  assert.equal(h.settings.get('backend'), 'native');
  assert.deepEqual(h.calls, ['splash', 'container.stop', 'splash', 'container.stop', 'native.start', 'page']);
});

test('a target startup failure reports failure and retries that target without another backend start', async () => {
  const h = shellHarness();
  let starts = 0;
  h.container.start = async () => { h.calls.push('container.start'); if (++starts === 1) throw new Error('image unavailable'); return 'http://127.0.0.1:4100'; };
  const failed = await h.control.switchBackend('container');
  assert.match(failed.error, /image unavailable/);
  assert.equal(h.failure().mode, 'container');
  assert.equal(h.settings.get('backend'), 'container');
  await h.invoke('app:retry');
  assert.deepEqual(h.calls, ['splash', 'native.stop', 'container.start', 'splash', 'container.stop', 'container.start', 'page']);
});

test('a native child remains supervised when shutdown and forced termination both fail', async () => {
  const context = vm.createContext({
    require: (name) => name === 'node:child_process' ? { spawn: () => { throw new Error('no real subprocesses'); } } : require(name.startsWith('./') ? path.resolve(__dirname, '../main', name) : name),
    process: { platform: 'linux' }, module: { exports: {} },
    setTimeout: (callback) => setImmediate(callback),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../main/backend.js'), 'utf8') + '\nrequest = async () => ({status: 0});', context);
  const { Backend } = context.module.exports;
  const backend = new Backend({ getPath: () => '/test' }, {});
  const child = new EventEmitter();
  child.exitCode = null;
  child.stdin = { end: () => {} };
  child.kill = () => {};
  backend.child = child;
  backend.base = 'http://127.0.0.1:1';
  await assert.rejects(backend.stop(), /后端还未退出/);
  assert.equal(backend.child, child);
  child.stdin.end = () => { child.exitCode = 0; child.emit('exit', 0); };
  await backend.stop();
  assert.equal(backend.child, null);
});

test('only source Electron opts into local ROS, even if a packaged app inherits the source flag', () => {
  const { childEnv } = require('../main/backend');
  const previous = process.env.CERES_DESKTOP_SOURCE;
  process.env.CERES_DESKTOP_SOURCE = '1';
  try {
    const app = { getPath: () => '/test', getVersion: () => '0.2.0' };
    const source = childEnv({ ...app, isPackaged: false }, 4100);
    const packaged = childEnv({ ...app, isPackaged: true }, 4100);
    assert.equal(source.CERES_DESKTOP, '1');
    assert.equal(source.CERES_DESKTOP_SOURCE, '1');
    assert.equal(packaged.CERES_DESKTOP, '1');
    assert.equal(packaged.CERES_DESKTOP_SOURCE, '0');
  } finally {
    if (previous === undefined) delete process.env.CERES_DESKTOP_SOURCE;
    else process.env.CERES_DESKTOP_SOURCE = previous;
  }
});


test('changing the recovery target back to native still stops the native process that failed to exit', async () => {
  const h = shellHarness();
  let stops = 0;
  h.backend.stop = async () => { h.calls.push('native.stop'); if (++stops < 3) throw new Error('native still running'); };
  await h.control.switchBackend('container');
  const failed = await h.invoke('app:use-native');
  assert.match(failed.error, /native still running/);
  assert.equal(h.settings.get('backend'), 'native');
  assert.deepEqual(h.calls, ['splash', 'native.stop', 'splash', 'native.stop']);
  const retried = await h.invoke('app:retry');
  assert.equal(retried.mode, 'native');
  assert.equal(retried.error, undefined);
  assert.deepEqual(h.calls, ['splash', 'native.stop', 'splash', 'native.stop', 'splash', 'native.stop', 'native.start', 'page']);
});

test('retry after a native startup timeout stops the surviving process before another start', async () => {
  const h = shellHarness('container');
  let starts = 0;
  h.backend.start = async () => { h.calls.push('native.start'); if (++starts === 1) throw new Error('backend readiness timed out'); return 'http://127.0.0.1:4101'; };
  await h.control.switchBackend('native');
  assert.match(h.failure().message, /timed out/);
  await h.invoke('app:retry');
  assert.deepEqual(h.calls, ['splash', 'container.stop', 'native.start', 'splash', 'native.stop', 'native.start', 'page']);
});

test('a duplicate native start cannot replace the handle of a still-running process', async () => {
  const { Backend } = require('../main/backend');
  const backend = new Backend({ getPath: () => '/test' }, {});
  const child = { exitCode: null, signalCode: null };
  backend.child = child;
  await assert.rejects(backend.start(), /不能重复启动/);
  assert.equal(backend.child, child);
  assert.equal(backend.starting, null);
});

test('concurrent native startup requests share a single startup attempt', async () => {
  const { Backend } = require('../main/backend');
  const backend = new Backend({ getPath: () => '/test' }, {});
  let starts = 0, finish;
  backend._start = () => { starts += 1; return new Promise((resolve) => { finish = resolve; }); };
  const first = backend.start(), second = backend.start();
  assert.equal(starts, 1);
  finish('http://127.0.0.1:4100');
  assert.deepEqual(await Promise.all([first, second]), ['http://127.0.0.1:4100', 'http://127.0.0.1:4100']);
  assert.equal(backend.starting, null);
});

// Run the real startup and shutdown paths, but advance the clock and replace only OS I/O.
// The production timeout remains 120 seconds and no gateway, model or physical simulation is started.
function timedOutBackend({ exitsOnClose }) {
  const child = new EventEmitter();
  const calls = [];
  child.pid = 12345;
  child.exitCode = null;
  child.signalCode = null;
  child.stdin = { end: () => {
    calls.push('stdin.end');
    if (exitsOnClose) { child.exitCode = 0; child.emit('exit', 0); }
  } };
  child.kill = (signal) => { calls.push(signal); };
  let clock = 0;
  const context = vm.createContext({
    require: (name) => {
      if (name === 'node:child_process') return { spawn: () => { calls.push('spawn'); return child; } };
      if (name === 'node:fs') return { existsSync: () => true, mkdirSync: () => {}, openSync: () => 1,
        writeSync: () => {}, closeSync: () => {}, readFileSync: () => '', appendFileSync: () => {} };
      if (name === './data-home') return { prepareDataHome: () => {} };
      if (name === './env-compat') return require('../main/env-compat');
      return require(name);
    },
    process: { platform: 'linux' }, module: { exports: {} },
    Date: class extends Date { static now() { clock += 120_001; return clock; } },
    setTimeout: (callback) => setImmediate(callback),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../main/backend.js'), 'utf8') + `
    layout = () => ({python: '/fake/python', cwd: '/fake/lab'});
    childEnv = () => ({});
    choosePort = async () => 4100;
    healthy = async () => false;
    request = async () => ({status: 0});
  `, context);
  const backend = new context.module.exports.Backend({ getPath: () => '/test', getVersion: () => '0.2.0' }, { get: () => 4100, set: () => {} });
  return { backend, child, calls };
}

test('a native readiness timeout retires its owned child before reporting the startup error', async () => {
  const { backend, child, calls } = timedOutBackend({ exitsOnClose: true });
  await assert.rejects(backend.start(), /120 秒内没有就绪/);
  assert.deepEqual(calls, ['spawn', 'stdin.end']);
  assert.equal(child.exitCode, 0);
  assert.equal(backend.child, null);
  assert.equal(backend.starting, null);
});

test('a readiness timeout reports cleanup failure and keeps the child supervised', async () => {
  const { backend, child, calls } = timedOutBackend({ exitsOnClose: false });
  await assert.rejects(backend.start(), (error) => {
    assert.match(error.message, /120 秒内没有就绪/);
    assert.match(error.message, /清理未完成.*后端还未退出/);
    return true;
  });
  assert.deepEqual(calls, ['spawn', 'stdin.end', 'SIGKILL']);
  assert.equal(backend.child, child);
  assert.equal(backend.starting, null);
  await assert.rejects(backend.start(), /不能重复启动/);
  assert.equal(calls.filter((call) => call === 'spawn').length, 1);
});
