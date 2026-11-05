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
const app = { isPackaged: false, getVersion: () => '0.2.0' };

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
  const server = await listen((_req, res) => res.end('{"status":"ok"}'));
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
});

test('starting creates the shared configuration folder before docker can', { skip: process.platform === 'win32' }, async () => {
  const server = await listen((_req, res) => res.end('{"status":"ok"}'));
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
  }
});

test('a failed start reports what docker said', { skip: process.platform === 'win32' }, async () => {
  const d = fakeDocker('echo "manifest for ghcr.io/peakpeng666/urp-sim:v0.2.0 not found" >&2; exit 1');
  await assert.rejects(new Container(app, { port: 9 }).start(), /not found/);
  d.done();
});

test('a packaged app asks for the image of its own version', () => {
  process.resourcesPath = os.tmpdir();
  const c = new Container({ isPackaged: true, getVersion: () => '0.2.0' });
  delete process.resourcesPath;
  assert.equal(c.env().URP_IMAGE_TAG, 'v0.2.0');
});
