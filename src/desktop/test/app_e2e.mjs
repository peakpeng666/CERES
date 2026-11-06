// The installed app, end to end: first launch, connect a model, run a task, download a data pack, close, reopen, and a
// crash of the app itself. The stand-in model and the pack host run in this process, with throwaway test values.
// Usage: PLAYWRIGHT_CORE=<playwright-core> node app_e2e.mjs --exe <AppImage or app binary> --out /new/absolute/dir [--packs <dir with zips>]
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { createReadStream, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { _electron: electron } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const exe = arg('--exe');
const out = arg('--out');
const packs = arg('--packs');
const appimage = arg('--appimage');          // when given, a newer version is offered from a local feed and must download
if (!exe || !out || !out.startsWith('/') || existsSync(out)) throw new Error('需要 --exe 和尚不存在的绝对路径 --out');
mkdirSync(out, { recursive: true });

const KEY = 'stand-in-test-key-0123456789';
const chats = [];
const standIn = createServer((req, res) => {
  const send = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    if (req.headers.authorization !== `Bearer ${KEY}`) return send(401, { error: { message: 'invalid key' } });
    if (req.method === 'GET') return send(200, { data: [{ id: 'stand-in-large' }] });
    chats.push(JSON.parse(raw).model);
    send(200, { model: 'stand-in-large', usage: { prompt_tokens: 10, completion_tokens: 5 }, choices: [{ finish_reason: 'stop', message: { content: '已记录这项任务。' } }] });
  });
});
const packHost = createServer((req, res) => {
  const name = req.url.slice(1).split('?')[0];
  if (name === 'latest-linux.yml' && appimage) {
    const feed = readFileSync(join(appimage, '..', 'latest-linux.yml'), 'utf8').replace(/^version: .*/m, 'version: 9.9.9');
    res.writeHead(200, { 'Content-Type': 'text/yaml' });
    res.end(feed);
    return;
  }
  const file = appimage && name.endsWith('.AppImage') ? appimage : join(packs ?? '', name);
  if (!(packs || appimage) || !existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Length': statSync(file).size });
  createReadStream(file).pipe(res);
});
await Promise.all([standIn, packHost].map((s) => new Promise((ok) => s.listen(0, '127.0.0.1', ok))));

const home = mkdtempSync(join(tmpdir(), 'desktop-e2e-'));
const env = { ...process.env, HOME: home, XDG_CONFIG_HOME: join(home, '.config'), URP_NO_UPDATE: '1', APPIMAGE_EXTRACT_AND_RUN: '1', URP_PACKS_BASE_URL: `http://127.0.0.1:${packHost.address().port}` };
delete env.ELECTRON_RUN_AS_NODE;
const log = { exe, started: new Date().toISOString(), steps: [], errors: [] };
const step = (name, extra = {}) => { log.steps.push({ name, ...extra }); console.log('✓', name, Object.keys(extra).length ? JSON.stringify(extra) : ''); };

async function launch() {
  const started = Date.now();
  const app = await electron.launch({ executablePath: exe, env, timeout: 90_000,
    args: process.env.URP_TEST_NO_SANDBOX === '1' ? ['--no-sandbox'] : [] }); // isolated container QA only
  const win = await app.firstWindow({ timeout: 60_000 });
  win.setDefaultTimeout(40_000);
  win.on('pageerror', (e) => log.errors.push(String(e.stack || e).slice(0, 300)));
  await win.waitForURL(/^http:\/\/127\.0\.0\.1/, { timeout: 90_000 });
  await win.getByRole('textbox').first().waitFor();
  return { app, win, seconds: (Date.now() - started) / 1000, base: new URL(win.url()).origin };
}
const api = (win, path) => win.evaluate(async (p) => (await fetch(`/lab/api${p}`)).json(), path);
async function until(win, id, states, ms = 180_000) {
  const end = Date.now() + ms;
  for (;;) {
    const s = await api(win, `/agent/sessions/${id}`);
    if (states.includes(s.state)) return s;
    if (Date.now() > end) throw new Error(`仍为 ${s.state}`);
    await win.waitForTimeout(400);
  }
}
const portOpen = (port) => new Promise((ok) => {
  const c = net.connect(port, '127.0.0.1');
  c.once('connect', () => { c.destroy(); ok(true); });
  c.once('error', () => ok(false));
});
async function waitClosed(port, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (!await portOpen(port)) return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

let app;
try {
  let run = await launch();
  ({ app } = run);
  let { win, base } = run;
  const port = Number(new URL(base).port);
  const info = await win.evaluate(() => window.desktop.info());
  assert.equal(info.backend, 'native');
  assert.equal(await win.title(), 'Lab');
  await win.screenshot({ path: `${out}/01-first-launch.png` });
  step('第一次打开：内置后端就绪', { seconds: run.seconds, version: info.version, port });

  await win.evaluate(() => { location.hash = '#/system/models'; });
  await win.getByRole('button', { name: /^自定义接口/ }).click();
  await win.getByLabel('名称').fill('Stand In');
  await win.getByLabel('接口地址').fill(`http://127.0.0.1:${standIn.address().port}/v1`);
  await win.getByLabel('API Key').fill(KEY);
  await win.getByRole('button', { name: '添加', exact: true }).click();
  await win.getByText('已验证').first().waitFor();
  const stored = JSON.parse(readFileSync(join(home, '.config/urp-workbench/credentials.json'), 'utf8'));
  assert.equal(stored['custom-stand-in'].api_key, KEY);
  assert.equal(statSync(join(home, '.config/urp-workbench/credentials.json')).mode & 0o777, 0o600);
  step('只填 Key 就连上模型', { selected: (await api(win, '/agent/providers')).selected });

  await win.evaluate(() => { location.hash = '#/agent'; });
  await win.getByRole('button', { name: 'stand-in-large' }).waitFor();
  await win.getByRole('textbox').first().fill('60×40米果园，2台车，8个采样点，每车容量4');
  await win.keyboard.press('Enter');
  await win.waitForFunction(() => location.hash.startsWith('#/agent/agt_'));
  const id = decodeURIComponent(new URL(win.url()).hash.split('/')[2].split('?')[0]);
  await until(win, id, ['awaiting_confirmation']);
  await win.getByRole('button', { name: '确认并执行' }).last().click();
  const s = await until(win, id, ['done', 'failed']);
  assert.equal(s.state, 'done', JSON.stringify(s.error));
  const physics = s.report.evidence.physics.filter((r) => r.state === 'succeeded' && r.check_valid).length;
  assert.ok(physics > 0 && chats.length > 0);
  await win.screenshot({ path: `${out}/02-task.png` });
  step('任务走完：模型、求解、复核、MuJoCo、报告', { best: s.report.evidence.best_verified.algorithm, physics, model_calls: chats.length });

  const cvrp = (await api(win, '/scenarios')).scenarios.find((x) => x.problem_class === 'euclidean_cvrp');
  const job = await win.evaluate(async (id) => (await fetch('/lab/api/solve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario_id: id, algorithms: [{ id: 'urp.js.savings', params: { time_limit_s: 5 } }] }) })).json(), cvrp.id);
  let js = await api(win, `/runs/${job.runs[0]}`);
  for (let i = 0; i < 60 && ['queued', 'running'].includes(js.state); i += 1) { await win.waitForTimeout(500); js = await api(win, `/runs/${job.runs[0]}`); }
  assert.equal(js.state, 'done', js.error);
  step('JS 基线跑在应用自身的运行时上', { status: js.status });

  const gazebo = (await api(win, '/agent/capabilities')).simulators.find((x) => x.id === 'gazebo.field');
  assert.equal(gazebo.status, 'unavailable');
  assert.match(gazebo.reason, /容器后端/);
  step('Gazebo 明确标为需要容器后端');

  await win.evaluate(() => { location.hash = '#/system/desktop'; });
  await win.getByText('仿真后端').first().waitFor();
  await win.getByText('Docker', { exact: true }).waitFor();
  await win.screenshot({ path: `${out}/03-desktop-settings.png` });
  step('设置页：版本、更新和仿真后端');

  // Substitute only the OS file picker; exercise the real UI -> IPC -> shipped Python -> SQLite data path.
  const beforeData = await win.evaluate(() => window.desktop.data.info());
  const backup = join(out, 'desktop-data-backup.zip');
  const restoreParent = join(out, 'restored');
  mkdirSync(restoreParent);
  await app.evaluate(({ dialog }, [backup, parent]) => {
    globalThis.__urpDialogs = { save: dialog.showSaveDialog, open: dialog.showOpenDialog };
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: backup });
    let pick = 0;
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [pick++ === 0 ? backup : parent] });
  }, [backup, restoreParent]);
  await win.evaluate(() => { location.hash = '#/system/data'; });
  await win.getByRole('button', { name: '备份数据', exact: true }).waitFor();
  await Promise.all([
    win.waitForEvent('framenavigated', { predicate: (frame) => frame === win.mainFrame() && /^http:/.test(frame.url()), timeout: 120_000 }),
    win.getByRole('button', { name: '备份数据', exact: true }).click(),
  ]);
  const exported = await win.evaluate(() => window.desktop.data.info());
  assert.equal(exported.lastTransfer?.state, 'verified', JSON.stringify(exported.lastTransfer));
  assert.ok(statSync(backup).size > 0);
  await win.getByRole('button', { name: '从备份恢复到新目录', exact: true }).waitFor();
  await Promise.all([
    win.waitForEvent('framenavigated', { predicate: (frame) => frame === win.mainFrame() && /^http:/.test(frame.url()), timeout: 120_000 }),
    win.getByRole('button', { name: '从备份恢复到新目录', exact: true }).click(),
  ]);
  const afterData = await win.evaluate(() => window.desktop.data.info());
  assert.equal(afterData.dataset.dataset_id, beforeData.dataset.dataset_id);
  assert.notEqual(afterData.root, beforeData.root);
  assert.ok(afterData.root.startsWith(restoreParent));
  assert.equal(afterData.lastTransfer.state, 'verified');
  assert.ok(existsSync(join(beforeData.runtime, 'agent.sqlite3')));
  assert.deepEqual(await api(win, `/agent/sessions/${id}`), s);
  await app.evaluate(({ dialog }) => {
    dialog.showSaveDialog = globalThis.__urpDialogs.save;
    dialog.showOpenDialog = globalThis.__urpDialogs.open;
    delete globalThis.__urpDialogs;
  });
  step('数据页实际备份、校验、恢复到新目录：任务相同、旧库仍在', { dataset_id: afterData.dataset.dataset_id });

  if (packs) {
    await win.evaluate(() => { location.hash = '#/system/data'; });
    await win.getByRole('button', { name: '下载', exact: true }).click();
    await win.getByRole('button', { name: '删除', exact: true }).waitFor({ timeout: 120_000 });
    await win.evaluate(() => { location.hash = '#/bpp'; });
    await win.waitForTimeout(1500);
    assert.equal(await win.getByText('此视图渲染失败').count(), 0);
    const size = await win.evaluate(async () => (await (await fetch('/data/catalog.json')).text()).length);
    assert.ok(size > 1000);
    await win.screenshot({ path: `${out}/04-history.png` });
    step('数据包在应用里下载并生效', { catalog_chars: size });
  }

  await app.close();
  app = null;
  assert.ok(await waitClosed(port, 25_000), '关闭窗口后后端还在');
  step('关闭窗口，后端有序退出');

  run = await launch();
  ({ app, win } = run);
  await win.getByText(/果园/).first().waitFor();
  const again = await api(win, '/agent/providers');
  assert.equal(again.selected, 'custom-stand-in/stand-in-large');
  step('重新打开：任务和模型连接都在', { seconds: run.seconds });

  if (appimage) {
    await app.close();
    await waitClosed(Number(new URL(base).port), 25_000);
    Object.assign(env, { URP_NO_UPDATE: '0', APPIMAGE: appimage, URP_UPDATE_URL: `http://127.0.0.1:${packHost.address().port}` });
    run = await launch();
    ({ app, win } = run);
    await win.evaluate(() => { location.hash = '#/system/desktop'; });
    await win.getByRole('button', { name: /检查更新|重启并更新/ }).waitFor();
    await win.evaluate(() => window.desktop.update.check());
    await win.getByRole('button', { name: '重启并更新' }).waitFor({ timeout: 120_000 });
    const update = await win.evaluate(() => window.desktop.update.state());
    assert.equal(update.version, '9.9.9');
    await win.screenshot({ path: `${out}/05-update.png` });
    step('发现新版本，下载并校验完成', { version: update.version });
  }

  const newPort = Number(new URL(win.url()).port);
  const pid = app.process().pid;
  process.kill(pid, 'SIGKILL');
  for (const line of execSync(`pgrep -P ${pid} || true`).toString().trim().split('\n').filter(Boolean)) {
    try { process.kill(Number(line), 'SIGKILL'); } catch { /* already gone */ }
  }
  app = null;
  assert.ok(await waitClosed(newPort, 40_000), '应用被杀后后端还在');
  step('应用被强行杀死，后端自己退出');

  assert.deepEqual(log.errors, []);
  log.passed = true;
} catch (e) {
  log.passed = false;
  log.failure = String(e.stack || e);
  console.error(e);
  process.exitCode = 1;
} finally {
  writeFileSync(`${out}/log.json`, JSON.stringify(log, null, 2));
  if (app) await app.close().catch(() => undefined);
  standIn.close();
  packHost.close();
  rmSync(home, { recursive: true, force: true });
}
