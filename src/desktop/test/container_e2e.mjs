// The container backend with real Docker: the app finds Docker, moves the window into the simulation image, a task runs in
// Gazebo there with the two-engine comparison at the end, and the app moves back. The model key is a throwaway test value.
// Usage: PLAYWRIGHT_CORE=<playwright-core> CERES_IMAGE_TAG=<tag on this machine> node container_e2e.mjs --exe <app binary> --out /new/absolute/dir
// Removes nothing: `docker compose -p ceres-lab down -v` afterwards clears the test volumes.
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { _electron: electron } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : undefined; };
const exe = arg('--exe');
const out = arg('--out');
if (!exe || !out || !out.startsWith('/') || existsSync(out) || !process.env.CERES_IMAGE_TAG) throw new Error('需要 --exe、尚不存在的绝对路径 --out 和 CERES_IMAGE_TAG');
mkdirSync(out, { recursive: true });

const KEY = 'stand-in-test-key-0123456789';
const standIn = createServer((req, res) => {
  res.writeHead(req.headers.authorization === `Bearer ${KEY}` ? 200 : 401, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ data: [{ id: 'stand-in-large' }] }));
});
await new Promise((ok) => standIn.listen(0, '127.0.0.1', ok));

const home = mkdtempSync(join(tmpdir(), 'desktop-container-'));
const env = { ...process.env, HOME: home, XDG_CONFIG_HOME: join(home, '.config'), CERES_NO_UPDATE: '1', APPIMAGE_EXTRACT_AND_RUN: '1' };
delete env.ELECTRON_RUN_AS_NODE;
const log = { exe, image: process.env.CERES_IMAGE_TAG, started: new Date().toISOString(), steps: [], errors: [] };
const step = (name, extra = {}) => { log.steps.push({ name, at: new Date().toISOString(), ...extra }); console.log('✓', name, Object.keys(extra).length ? JSON.stringify(extra) : ''); };

const app = await electron.launch({ executablePath: exe, env, timeout: 90_000 });
const win = await app.firstWindow({ timeout: 60_000 });
win.setDefaultTimeout(60_000);
win.on('pageerror', (e) => log.errors.push(String(e.stack || e).slice(0, 300)));

const api = (method, path, body) => win.evaluate(async ([m, p, b]) => {
  const res = await fetch(`/lab/api${p}`, { method: m, headers: b ? { 'Content-Type': 'application/json' } : {}, body: b ? JSON.stringify(b) : undefined });
  return { status: res.status, body: await res.json() };
}, [method, path, body]).then((r) => { assert.ok(r.status < 400, `${method} ${path}: ${r.status} ${JSON.stringify(r.body).slice(0, 300)}`); return r.body; });
async function until(id, states, ms) {
  const end = Date.now() + ms;
  for (;;) {
    const s = await api('GET', `/agent/sessions/${id}`);
    if (states.includes(s.state)) return s;
    if (Date.now() > end) throw new Error(`会话 ${id} 停在 ${s.state}`);
    await win.waitForTimeout(1000);
  }
}
const waitForApp = async (backend, ms) => {
  await win.waitForURL(/^http:\/\/127\.0\.0\.1/, { timeout: ms });
  await win.getByRole('textbox').first().waitFor({ timeout: ms });
  assert.equal((await win.evaluate(() => window.desktop.info())).backend, backend);
};

let failed = null;
try {
  await waitForApp('native', 90_000);
  const gap = (await api('GET', '/physics/engines')).gazebo_gap;
  assert.match(gap, /容器后端/);
  await api('POST', '/agent/providers/custom', { name: 'Stand In', base_url: `http://127.0.0.1:${standIn.address().port}/v1`, api_key: KEY });
  step('内置后端：Gazebo 标为需要容器，模型已连接', { gap });

  await win.evaluate(() => { location.hash = '#/system/desktop'; });
  await win.getByText(/^可用 /).first().waitFor();
  const status = await win.evaluate(() => window.desktop.sim.status());
  assert.ok(status.docker.ready && status.compose.ready && status.imagePresent, JSON.stringify(status));
  await win.screenshot({ path: `${out}/01-before.png` });
  step('应用检测到 Docker 和镜像', { docker: status.docker.version, compose: status.compose.version, image: status.image, bytes: status.imageBytes });

  const started = Date.now();
  await win.getByRole('button', { name: '开启容器后端' }).click();
  await win.waitForURL(/splash/, { timeout: 20_000 }).catch(() => {});
  await waitForApp('container', 300_000);
  const engines = await api('GET', '/physics/engines');
  assert.equal(engines.gazebo_gap, null);
  const sims = Object.fromEntries((await api('GET', '/agent/capabilities')).simulators.map((c) => [c.id, c.status]));
  assert.equal(sims['gazebo.field'], 'ready', JSON.stringify(sims));
  const seen = await api('GET', '/agent/providers');
  assert.equal(seen.selected, 'custom-stand-in/stand-in-large');
  assert.equal(statSync(join(home, '.config/ceres-workbench/credentials.json')).mode & 0o777, 0o600);
  await win.screenshot({ path: `${out}/02-container.png` });
  step('窗口切到容器，Gazebo 可用，模型连接带了过去', { seconds: (Date.now() - started) / 1000, simulators: sims, selected: seen.selected });

  const s0 = await api('POST', '/agent/sessions', { mode: 'rule', text: '在 60×40 米的菜地里，1 台车访问 6 个点，单车容量 8' });
  const sid = s0.session_id;
  let s = await until(sid, ['awaiting_confirmation', 'failed'], 180_000);
  assert.equal(s.state, 'awaiting_confirmation', s.error);
  await api('PATCH', `/agent/sessions/${sid}/contract`, { fields: { simulate: true } });
  await until(sid, ['awaiting_confirmation'], 30_000);
  await api('POST', `/agent/sessions/${sid}/control`, { action: 'confirm' });
  s = await until(sid, ['awaiting_approval', 'done', 'failed'], 420_000);
  assert.equal(s.state, 'awaiting_approval', s.error);
  await api('POST', `/agent/sessions/${sid}/control`, { action: 'approve' });
  s = await until(sid, ['done', 'failed', 'cancelled'], 1_500_000);
  writeFileSync(`${out}/session.json`, JSON.stringify(s, null, 1));
  assert.equal(s.state, 'done', s.error);
  const sim = s.report.evidence.simulation_result;
  assert.equal(sim.state, 'succeeded', JSON.stringify(sim.errors));
  assert.equal(sim.check_valid, true);
  const gapM = sim.comparison;
  assert.equal(typeof gapM.path_max_m, 'number');
  await win.evaluate((id) => { location.hash = `#/agent/${encodeURIComponent(id)}`; }, sid);
  await win.waitForTimeout(1500);
  await win.screenshot({ path: `${out}/03-report.png` });
  step('容器里的任务走完 Gazebo 和双引擎对比', { makespan_s: sim.metrics?.makespan_s, path_max_m: gapM.path_max_m, path_mean_m: gapM.path_mean_m, time_rmse_m: gapM.time_rmse_m });

  await win.evaluate(() => { location.hash = '#/system/desktop'; });
  await win.getByRole('button', { name: '回到内置后端' }).click();
  await win.waitForURL(/splash/, { timeout: 20_000 }).catch(() => {});
  await waitForApp('native', 180_000);
  assert.match((await api('GET', '/physics/engines')).gazebo_gap, /容器后端/);
  assert.equal((await api('GET', '/agent/providers')).selected, 'custom-stand-in/stand-in-large');
  const running = execSync('docker ps --filter label=com.docker.compose.project=ceres-lab --format "{{.Names}}"').toString().trim();
  assert.equal(running, '');
  step('回到内置后端，容器已停止');
  assert.deepEqual(log.errors, []);
} catch (error) {
  failed = String(error.stack || error).slice(0, 800);
  console.log('✗', failed);
  await win.screenshot({ path: `${out}/failed.png` }).catch(() => {});
}
log.passed = failed === null;
log.failure = failed;
writeFileSync(`${out}/log.json`, JSON.stringify(log, null, 1));
await app.close().catch(() => {});
standIn.close();
rmSync(home, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
