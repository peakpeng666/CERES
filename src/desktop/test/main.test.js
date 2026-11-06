'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { choosePort, healthy, portIsFree, request } = require('../main/backend');
const { Settings } = require('../main/settings');
const { prepareDataHome } = require('../main/data-home');

const listen = (handler) => new Promise((resolve) => {
  const server = http.createServer(handler);
  server.listen(0, '127.0.0.1', () => resolve(server));
});

test('a free port is reused and a taken one is replaced', async () => {
  const blocker = net.createServer();
  await new Promise((r) => blocker.listen(0, '127.0.0.1', r));
  const taken = blocker.address().port;
  assert.equal(await portIsFree(taken), false);
  const port = await choosePort(taken);
  assert.notEqual(port, taken);
  blocker.close();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(await portIsFree(taken), true);
  assert.equal(await choosePort(taken), taken);
});

test('only a real gateway counts as healthy', async () => {
  const good = await listen((_req, res) => res.end('{"status":"ok"}'));
  const other = await listen((_req, res) => res.end('<html>another service</html>'));
  assert.equal(await healthy(`http://127.0.0.1:${good.address().port}`), true);
  assert.equal(await healthy(`http://127.0.0.1:${other.address().port}`), false);
  good.close(); other.close();
  assert.equal(await healthy('http://127.0.0.1:9'), false);
});

test('the shutdown request carries a JSON body, as the gateway requires', async () => {
  let seen;
  const server = await listen((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => { seen = { method: req.method, type: req.headers['content-type'], body }; res.statusCode = 202; res.end('{}'); });
  });
  const r = await request(`http://127.0.0.1:${server.address().port}/lab/api/shutdown`, { method: 'POST' });
  server.close();
  assert.equal(r.status, 202);
  assert.deepEqual(seen, { method: 'POST', type: 'application/json', body: '{}' });
});

test('settings survive a restart and a damaged file is not fatal', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-settings-'));
  const file = path.join(dir, 'nested', 'desktop.json');
  const a = new Settings(file);
  assert.equal(a.get('backend', 'native'), 'native');
  a.set('backend', 'container');
  assert.equal(new Settings(file).get('backend'), 'container');
  fs.writeFileSync(file, '{broken');
  assert.equal(new Settings(file).get('backend', 'native'), 'native');
  fs.rmSync(dir, { recursive: true });
});

// The container path against a stand-in docker command: which arguments go out, and what comes back to the page.
const { Container } = require('../main/container');

function fakeDocker(script) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-docker-'));
  const file = path.join(dir, 'docker');
  fs.writeFileSync(file, `#!/bin/sh\necho "$@" >> "${dir}/calls"\nenv | grep '^URP_' >> "${dir}/env"\n${script}\n`);
  fs.chmodSync(file, 0o755);
  const before = process.env.PATH;
  process.env.PATH = `${dir}${path.delimiter}${before}`;
  return { dir, calls: () => fs.readFileSync(path.join(dir, 'calls'), 'utf8'), env: () => fs.readFileSync(path.join(dir, 'env'), 'utf8'), done: () => { process.env.PATH = before; fs.rmSync(dir, { recursive: true }); } };
}
const testData = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-data-'));
const { after } = require('node:test');
after(() => fs.rmSync(testData, { recursive: true, force: true }));
const app = { isPackaged: false, getVersion: () => '0.2.0', getPath: () => testData };

test('a machine with Docker and the image reports what it has', { skip: process.platform === 'win32' }, async () => {
  const d = fakeDocker(`case "$1 $2" in "version --format") echo 29.9.0;; "compose version") echo 2.30.1;; "image inspect") echo 9000000000;; esac`);
  const st = await new Container(app, { port: 9 }).status();
  d.done();
  assert.deepEqual([st.docker.ready, st.docker.version, st.compose.version, st.imagePresent, st.imageBytes, st.running], [true, '29.9.0', '2.30.1', true, 9000000000, false]);
  assert.equal(st.image, 'ghcr.io/peakpeng666/urp-sim:deploy');
});

test('a machine without Docker says so instead of failing', { skip: process.platform === 'win32' }, async () => {
  const d = fakeDocker('echo "Cannot connect to the Docker daemon" >&2; exit 1');
  const st = await new Container(app, { port: 9 }).status();
  d.done();
  assert.equal(st.docker.ready, false);
  assert.match(st.docker.detail, /Cannot connect/);
  assert.equal(st.imagePresent, false);
});

test('starting passes both compose files and the shared configuration, then waits for the gateway', { skip: process.platform === 'win32' }, async () => {
  const dataset = prepareDataHome(app).dataset;
  const server = await listen((_req, res) => res.end(JSON.stringify({ status: 'ok', dataset_id: dataset.dataset_id })));
  const d = fakeDocker('exit 0');
  const c = new Container(app, { port: server.address().port });
  const lines = [];
  assert.equal(await c.start((x) => lines.push(x)), c.base);
  const calls = d.calls();
  const env = d.env();
  d.done();
  server.close();
  assert.match(calls, /compose -f .*docker-compose\.sim\.yml -f .*docker-compose\.desktop\.yml up -d/);
  assert.match(env, /URP_IMAGE_TAG=deploy/);
  assert.match(env, /URP_HOST_CONFIG=.*urp-workbench/);
  assert.match(env, /URP_HOST_RUNTIME=.*runtime/);
  assert.match(env, /URP_HOST_ARCHIVE=.*archive/);
  assert.match(env, new RegExp(`URP_HOST_UID=${os.userInfo().uid}`));
});

test('starting creates the shared configuration folder before docker can', { skip: process.platform === 'win32' }, async () => {
  const dataset = prepareDataHome(app).dataset;
  const server = await listen((_req, res) => res.end(JSON.stringify({ status: 'ok', dataset_id: dataset.dataset_id })));
  const d = fakeDocker('exit 0');
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'home-'));
  const before = process.env.HOME;
  process.env.HOME = home;
  try {
    await new Container(app, { port: server.address().port }).start();
    assert.ok(fs.statSync(path.join(home, '.config', 'urp-workbench')).isDirectory());
  } finally {
    process.env.HOME = before;
    d.done();
    server.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test('a failed start reports what docker said', { skip: process.platform === 'win32' }, async () => {
  const d = fakeDocker('echo "manifest for ghcr.io/peakpeng666/urp-sim:v0.2.0 not found" >&2; exit 1');
  await assert.rejects(new Container(app, { port: 9 }).start(), /not found/);
  d.done();
});

test('a packaged app asks for the image of its own version', () => {
  process.resourcesPath = os.tmpdir();
  const c = new Container({ ...app, isPackaged: true });
  delete process.resourcesPath;
  assert.equal(c.env().URP_IMAGE_TAG, 'v0.2.0');
});

test('native and container modes use the same dataset after moving it', () => {
  const { childEnv } = require('../main/backend');
  const { prepareDataHome } = require('../main/data-home');
  const settings = new Settings(path.join(testData, 'move-settings.json'));
  settings.set('dataRoot', path.join(testData, 'moved', 'data'));
  const first = prepareDataHome(app, settings);
  const native = childEnv(app, 4100, settings);
  const container = new Container(app, { settings }).env();
  assert.equal(native.URP_LAB_RUNTIME, container.URP_HOST_RUNTIME);
  assert.equal(path.dirname(native.URP_API_DB), container.URP_HOST_ARCHIVE);
  assert.equal(prepareDataHome(app, settings).dataset.dataset_id, first.dataset.dataset_id);
  assert.equal(new Settings(settings.file).get('dataRoot'), first.root);
});

test('a healthy container with another dataset cannot be presented as this workbench', { skip: process.platform === 'win32' }, async () => {
  const server = await listen((_req, res) => res.end('{"status":"ok","dataset_id":"another-dataset"}'));
  const docker = fakeDocker('exit 0');
  try { await assert.rejects(new Container(app, { port: server.address().port }).start(), /未使用当前数据集/); }
  finally { docker.done(); server.close(); }
});

test('data transfer runs the bundled Python from the Lab folder, wherever the app was launched', { skip: process.platform === 'win32' }, async () => {
  const { DataTransfer } = require('../main/data-transfer');
  // An installed app: urp_lab sits in backend/app/src/lab and is importable only with that folder as the working directory.
  const resources = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-resources-'));
  const lab = path.join(resources, 'backend', 'app', 'src', 'lab');
  const python = path.join(lab, '.venv', 'bin', 'python');
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, '#!/bin/sh\nprintf \'{"cwd":"%s","pythonpath":"%s"}\' "$(pwd -P)" "${PYTHONPATH:-}"\n');
  fs.chmodSync(python, 0o755);
  const launchedFrom = process.cwd();
  process.resourcesPath = resources;
  process.chdir(os.tmpdir());
  try {
    const settings = new Settings(path.join(testData, 'transfer-cwd-settings.json'));
    const result = await new DataTransfer({ ...app, isPackaged: true }, settings).command(['export', '--out', 'x.zip']);
    assert.equal(result.cwd, fs.realpathSync(lab));
    assert.equal(result.pythonpath, '');
  } finally {
    process.chdir(launchedFrom);
    delete process.resourcesPath;
    fs.rmSync(resources, { recursive: true, force: true });
  }
});

test('slimming the staged runtime keeps the PROJ database pyproj reads at run time', { skip: process.platform === 'win32' }, () => {
  const { spawnSync } = require('node:child_process');
  const site = fs.mkdtempSync(path.join(os.tmpdir(), 'staged-site-'));
  const kept = path.join(site, 'pyproj', 'proj_dir', 'share', 'proj', 'proj.db');
  const trimmed = path.join(site, 'otherpkg', 'share', 'doc', 'notes.txt');
  for (const file of [kept, trimmed]) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, 'x'); }
  const script = 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import stage_backend; stage_backend.slim(Path(sys.argv[2]))';
  try {
    const r = spawnSync('python3', ['-I', '-c', script, path.join(__dirname, '..', 'scripts'), site], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr || String(r.error));
    assert.ok(fs.existsSync(kept), 'pyproj/proj_dir/share/proj/proj.db must survive slimming');
    assert.ok(!fs.existsSync(path.join(site, 'otherpkg', 'share')), 'other share directories are still trimmed');
  } finally {
    fs.rmSync(site, { recursive: true, force: true });
  }
});

test('an unsuccessful restore cannot advance the data setting', async () => {
  const { DataTransfer } = require('../main/data-transfer');
  const settings = new Settings(path.join(testData, 'failed-import-settings.json'));
  const data = new DataTransfer(app, settings);
  const before = data.info().dataset.dataset_id;
  data.command = async () => { throw new Error('hash mismatch'); };
  await assert.rejects(data.restore('/missing.zip', path.join(testData, 'should-not-exist')), /hash mismatch/);
  assert.equal(settings.get('dataRoot'), undefined);
  assert.equal(data.info().dataset.dataset_id, before);
});
