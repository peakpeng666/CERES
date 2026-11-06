// Linux packaged-window CI: real Electron, preload IPC, local model, JS solve, and data migration.
// This deliberately stops the agent at confirmation; physical execution and update installation have separate acceptance.
// Usage: PLAYWRIGHT_CORE=<playwright-core> node packaged_smoke.mjs --exe /absolute/app --out /new/absolute/dir
// --source /absolute/src/desktop exercises current source with an Electron binary; CI omits it to require a package.
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { _electron: electron } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : undefined; };
const exe = arg('--exe');
const out = arg('--out');
const source = arg('--source');
if (source && (!isAbsolute(source) || !existsSync(join(source, 'package.json')))) throw new Error('--source must be an absolute desktop source directory');
if (!exe || !isAbsolute(exe) || !existsSync(exe) || !out || !isAbsolute(out) || existsSync(out)) {
  throw new Error('--exe must be an existing absolute executable; --out must be a new absolute directory');
}
mkdirSync(out, { recursive: true });
const home = mkdtempSync(join(tmpdir(), 'urp-window-ci-'));
const KEY = 'local-smoke-key-0123456789';
const calls = { models: 0, chats: 0 };
const host = createServer((req, res) => {
  const send = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  // Backend startup refreshes the catalog. Keep that request local and use the shipped catalog when no update is available.
  if (req.url === '/catalog') return send(503, { error: 'CI uses the bundled catalog' });
  if (req.headers.authorization !== `Bearer ${KEY}`) return send(401, { error: { message: 'invalid test key' } });
  if (req.method === 'GET' && req.url === '/v1/models') {
    calls.models += 1;
    return send(200, { data: [{ id: 'stand-in-large' }] });
  }
  let raw = '';
  req.on('data', (chunk) => { raw += chunk; });
  req.on('end', () => {
    try {
      const body = JSON.parse(raw);
      if (req.method !== 'POST' || req.url !== '/v1/chat/completions' || body.model !== 'stand-in-large') {
        return send(400, { error: { message: 'unexpected model request' } });
      }
      calls.chats += 1;
      send(200, { model: body.model, usage: { prompt_tokens: 10, completion_tokens: 5 },
        choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: '已记录这项任务。' } }] });
    } catch { send(400, { error: { message: 'invalid JSON' } }); }
  });
});
await new Promise((ok) => host.listen(0, '127.0.0.1', ok));
const modelURL = `http://127.0.0.1:${host.address().port}`;
// Do not inherit model credentials, user data roots, source-mode flags, or developer network overrides.
const env = Object.fromEntries(['PATH', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL', 'DBUS_SESSION_BUS_ADDRESS']
  .filter((key) => process.env[key]).map((key) => [key, process.env[key]]));
Object.assign(env, { HOME: home, XDG_CONFIG_HOME: join(home, '.config'), XDG_DATA_HOME: join(home, '.local/share'),
  XDG_CACHE_HOME: join(home, '.cache'), URP_DATA_HOME: join(home, 'data'), URP_NO_UPDATE: '1', URP_MODELS_URL: `${modelURL}/catalog` });
const report = { executable: resolve(exe), mode: source ? 'source' : 'packaged', source: source || null, started: new Date().toISOString(), steps: [], rendererErrors: [],
  scope: 'Linux window, IPC, local mock model, JS solver, backup/restore, reopen; no physical execution or update installation' };
const step = (name, extra = {}) => { report.steps.push({ name, ...extra }); console.log('✓', name, JSON.stringify(extra)); };
const api = (win, path, body) => win.evaluate(async ({ path, body }) => {
  const response = await fetch(`/lab/api${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}, { path, body });
const portOpen = (port) => new Promise((ok) => {
  const socket = net.connect(port, '127.0.0.1');
  socket.once('connect', () => { socket.destroy(); ok(true); });
  socket.once('error', () => ok(false));
});
async function waitClosed(port) {
  const end = Date.now() + 30_000;
  while (Date.now() < end) {
    if (!await portOpen(port)) return;
    await new Promise((ok) => setTimeout(ok, 200));
  }
  throw new Error(`backend ${port} is still listening after the app closed`);
}
async function until(win, path, states, timeout = 60_000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await api(win, path);
    if (states.includes(value.state)) return value;
    if (Date.now() >= end) throw new Error(`${path} stayed ${value.state}`);
    await win.waitForTimeout(200);
  }
}
let app;
let win;
let logs;
const ports = new Set();
async function launch() {
  app = await electron.launch({ executablePath: exe, env, timeout: 90_000,
    args: [...(process.env.URP_TEST_NO_SANDBOX === '1' ? ['--no-sandbox'] : []), ...(source ? [source] : [])] });
  logs = join(await app.evaluate(({ app }) => app.getPath('userData')), 'logs');
  win = await app.firstWindow({ timeout: 60_000 });
  win.setDefaultTimeout(30_000);
  win.on('pageerror', (error) => report.rendererErrors.push(String(error.stack || error)));
  await win.waitForURL(/^http:\/\/127\.0\.0\.1/, { timeout: 120_000 });
  await win.getByRole('textbox').first().waitFor();
  const info = await win.evaluate(() => window.desktop.info());
  logs = info.logs;
  const port = Number(new URL(win.url()).port);
  ports.add(port);
  return { info, port };
}
try {
  let { info, port } = await launch();
  assert.equal(info.backend, 'native');
  assert.equal(info.platform, 'linux');
  assert.equal(await win.title(), 'Lab');
  const security = await app.evaluate(({ app, BrowserWindow }) => {
    const prefs = BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return { packaged: app.isPackaged, contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox, nodeIntegration: prefs.nodeIntegration };
  });
  assert.deepEqual(security, { packaged: !source, contextIsolation: true, sandbox: true, nodeIntegration: false });
  assert.equal(await win.evaluate(() => typeof window.require), 'undefined');
  await win.evaluate(() => window.desktop.setTheme('dark'));
  await assert.rejects(win.evaluate(() => window.desktop.setTheme('invalid')), /未知的外观/);
  await win.evaluate(() => window.desktop.setTheme('system'));
  step(`${source ? 'source' : 'packaged'} window and isolated preload IPC`, { version: info.version, electron: info.electron, port });
  await win.screenshot({ path: join(out, '01-window.png') });

  assert.equal((await api(win, '/agent/providers')).selected, null);
  await win.evaluate(() => { location.hash = '#/system/models'; });
  await win.getByRole('button', { name: /^自定义接口/ }).click();
  await win.getByLabel('名称').fill('Stand In');
  await win.getByLabel('接口地址').fill(`${modelURL}/v1`);
  await win.getByLabel('API Key').fill(KEY);
  await win.getByRole('button', { name: '添加', exact: true }).click();
  await win.getByText('已验证').first().waitFor();
  const credentialFile = join(home, '.config/urp-workbench/credentials.json');
  assert.equal(JSON.parse(readFileSync(credentialFile, 'utf8'))['custom-stand-in'].api_key, KEY);
  assert.equal(statSync(credentialFile).mode & 0o777, 0o600);
  assert.ok(calls.models > 0);
  await win.evaluate(() => { location.hash = '#/agent'; });
  await win.getByRole('button', { name: 'stand-in-large' }).waitFor();
  await win.getByRole('textbox').first().fill('60×40米果园，2台车，8个采样点，每车容量4');
  await win.keyboard.press('Enter');
  await win.waitForFunction(() => location.hash.startsWith('#/agent/agt_'));
  const id = decodeURIComponent(new URL(win.url()).hash.split('/')[2].split('?')[0]);
  const session = await until(win, `/agent/sessions/${id}`, ['awaiting_confirmation', 'failed']);
  assert.equal(session.state, 'awaiting_confirmation', JSON.stringify(session.error));
  await win.getByRole('button', { name: '确认并执行' }).last().waitFor();
  assert.ok(calls.chats > 0);
  step('UI model connection and task confirmation with loopback model', { calls: { ...calls }, session_id: id });

  const scenario = (await api(win, '/scenarios')).scenarios.find((entry) => entry.problem_class === 'euclidean_cvrp');
  assert.ok(scenario, 'shipped CVRP scenario missing');
  const job = await api(win, '/solve', { scenario_id: scenario.id, algorithms: [{ id: 'urp.js.savings', params: { time_limit_s: 5 } }] });
  const run = await until(win, `/runs/${job.runs[0]}`, ['done', 'failed']);
  assert.equal(run.state, 'done', JSON.stringify(run.error));
  step('JS solver uses the app Electron runtime', { state: run.state, status: run.status });

  await win.evaluate(() => { location.hash = '#/system/desktop'; });
  await win.getByText('仿真后端').first().waitFor();
  assert.ok(await win.evaluate(() => window.desktop.update.state()));
  await win.screenshot({ path: join(out, '02-settings.png') });
  step('desktop settings page and update state IPC');

  const beforeData = await win.evaluate(() => window.desktop.data.info());
  const backup = join(out, 'desktop-data-backup.zip');
  const parent = join(out, 'restored');
  mkdirSync(parent);
  // Replace only the OS picker. The UI, IPC, Python migration and SQLite data remain real.
  await app.evaluate(({ dialog }, [file, directory]) => {
    globalThis.__smokeDialogs = { save: dialog.showSaveDialog, open: dialog.showOpenDialog };
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: file });
    let pick = 0;
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [pick++ === 0 ? file : directory] });
  }, [backup, parent]);
  await win.evaluate(() => { location.hash = '#/system/data'; });
  async function transfer(label) {
    await Promise.all([
      win.waitForEvent('framenavigated', { predicate: (frame) => frame === win.mainFrame() && /^http:/.test(frame.url()), timeout: 150_000 }),
      win.getByRole('button', { name: label, exact: true }).click(),
    ]);
    await win.getByRole('button', { name: '备份数据', exact: true }).waitFor();
    assert.equal((await win.evaluate(() => window.desktop.data.info())).lastTransfer?.state, 'verified');
  }
  await transfer('备份数据');
  assert.ok(statSync(backup).size > 0);
  await transfer('从备份恢复到新目录');
  const restored = await win.evaluate(() => window.desktop.data.info());
  assert.equal(restored.dataset.dataset_id, beforeData.dataset.dataset_id);
  assert.notEqual(restored.root, beforeData.root);
  assert.ok(restored.root.startsWith(`${parent}/`));
  assert.ok(existsSync(join(beforeData.runtime, 'agent.sqlite3')));
  assert.deepEqual(await api(win, `/agent/sessions/${id}`), session);
  assert.deepEqual(await api(win, `/runs/${job.runs[0]}`), run);
  await app.evaluate(({ dialog }) => {
    dialog.showSaveDialog = globalThis.__smokeDialogs.save;
    dialog.showOpenDialog = globalThis.__smokeDialogs.open;
    delete globalThis.__smokeDialogs;
  });
  step('UI backup and restore preserve session, solve and original database', { dataset_id: restored.dataset.dataset_id });

  await app.close(); app = null;
  await waitClosed(port);
  ({ port } = await launch());
  assert.equal((await api(win, '/agent/providers')).selected, 'custom-stand-in/stand-in-large');
  assert.deepEqual(await api(win, `/agent/sessions/${id}`), session);
  assert.equal((await win.evaluate(() => window.desktop.data.info())).root, restored.root);
  await app.close(); app = null;
  await waitClosed(port);
  step('reopen keeps model selection and restored data; window close stops backend');
  assert.deepEqual(report.rendererErrors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = String(error.stack || error);
  console.error(error);
  process.exitCode = 1;
  if (win && !win.isClosed()) await win.screenshot({ path: join(out, 'failure.png') }).catch(() => {});
} finally {
  if (app) await app.close().catch(() => {});
  // Preserve diagnostic logs before removing the isolated HOME; no credential files are exported.
  if (logs && existsSync(join(logs, 'backend.log'))) copyFileSync(join(logs, 'backend.log'), join(out, 'backend.log'));
  for (const port of ports) {
    try { await waitClosed(port); } catch (error) {
      report.passed = false; report.cleanupFailure = String(error); process.exitCode = 1;
    }
  }
  host.closeAllConnections();
  await new Promise((ok) => host.close(ok));
  writeFileSync(join(out, 'smoke.json'), JSON.stringify(report, null, 2));
  if (!report.cleanupFailure) rmSync(home, { recursive: true, force: true });
}
