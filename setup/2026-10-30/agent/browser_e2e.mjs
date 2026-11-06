// Browser acceptance of the agent workspace: request → contract → confirm → verified report → multi-turn edit.
// Usage: NODE_PATH=<dir containing playwright-core> [URP_CHROMIUM=<chrome binary>] node browser_e2e.mjs --out /new/absolute/dir [--url http://127.0.0.1:4100] [--mode auto|rule] [--provider <id>]
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright-core');
const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const out = arg('--out');
const base = arg('--url', 'http://127.0.0.1:4100');
const mode = arg('--mode', 'auto');
const provider = arg('--provider', '');
const request = arg('--text', '60×40米果园，2台车，8个采样点，每车容量4，1个池塘，每个点要在上午九点前完成');
if (!out || !out.startsWith('/') || existsSync(out)) throw new Error('--out 须为尚不存在的绝对路径');
mkdirSync(out, { recursive: true });

const log = { base, mode, provider, request, started: new Date().toISOString(), steps: [] };
const step = (name, extra = {}) => { log.steps.push({ name, t: new Date().toISOString(), ...extra }); console.log(name, JSON.stringify(extra)); };
const checkReport = (session) => {
  assert.equal(session.state, 'done', `任务未完成：${JSON.stringify(session.error)}`);
  assert.equal(session.contract.status, 'confirmed');
  const active = session.active_run_ids;
  assert.ok(active?.length > 0, '当前合同没有运行记录');
  const evidence = session.report?.evidence;
  assert.ok(evidence?.best_verified, '报告没有经独立复核的方案');
  assert.ok(active.includes(evidence.best_verified.run_id), '最好方案来自旧合同');
  assert.deepEqual(new Set(evidence.runs.map((r) => r.run_id)), new Set(active), '报告与当前合同批次不一致');
  for (const run of evidence.runs.filter((r) => r.status === 'feasible')) {
    assert.equal(session.verifications[run.run_id]?.valid, true, `${run.run_id} 未独立验算通过`);
    assert.equal(session.verifications[run.run_id]?.agrees, true, `${run.run_id} 复核与存档不一致`);
  }
};

const browser = await chromium.launch({ headless: true, executablePath: process.env.URP_CHROMIUM || undefined });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => step('pageerror', { message: String(e) }));
try {
  await page.goto(`${base}/#/agent`);
  await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('workbench.layout')).forEach((k) => localStorage.removeItem(k)));
  await page.reload();
  await page.getByText('农业作业任务智能体').waitFor({ timeout: 30_000 });
  if (provider) await page.getByLabel('新任务使用的模型').selectOption(provider);
  if (mode === 'rule') await page.getByRole('radio', { name: '规则流程' }).click().catch(() => page.getByText('规则流程', { exact: true }).first().click());
  await page.screenshot({ path: `${out}/01-welcome.png` });
  step('welcome');

  const t0 = Date.now();
  await page.getByLabel('给智能体的消息').fill(request);
  await page.keyboard.press('Enter');
  const confirm = page.getByRole('button', { name: '确认并执行' });
  await confirm.waitFor({ timeout: 600_000 });
  step('contract_ready', { seconds: (Date.now() - t0) / 1000 });
  await page.screenshot({ path: `${out}/02-contract.png` });

  const t1 = Date.now();
  await confirm.click();
  const sid = decodeURIComponent((page.url().split('#/agent/')[1] || '').split('?')[0]);
  const settled = async (turn) => {
    const end = Date.now() + 900_000;
    while (Date.now() < end) {
      const s = await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}`)).json();
      if (s.turn >= turn && ['done', 'failed', 'cancelled', 'awaiting_approval', 'awaiting_user'].includes(s.state)) return s;
      await page.waitForTimeout(1000);
    }
    throw new Error('等待会话结束超时');
  };
  await settled(1);
  step('report_ready', { seconds: (Date.now() - t1) / 1000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/03-report.png` });

  const session = await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}`)).json();
  checkReport(session);
  // Preserve the HTTP bytes: parsing/stringifying in JS changes e.g. 1.0 to 1
  // and invalidates the Python-canonical record checksum in the export.
  writeFileSync(`${out}/first-session.json`, await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}/export`)).text());
  step('session', { sid, state: session.state, mode: session.mode, provider: session.provider?.model ?? null,
    runs: session.run_ids.length, verified: Object.values(session.verifications).filter((v) => v.valid).length,
    unsupported: session.contract.unsupported, unverified_numbers: session.report?.unverified_numbers ?? null });

  const t2 = Date.now();
  await page.getByLabel('给智能体的消息').fill('改成 3 台车，其他不变');
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: '确认并执行' }).last().waitFor({ timeout: 600_000 });
  await page.getByRole('button', { name: '确认并执行' }).last().click();
  await settled(2);
  step('edit_done', { seconds: (Date.now() - t2) / 1000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/04-edit-report.png` });
  const after = await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}`)).json();
  const fields = Object.fromEntries(Object.entries(after.contract.fields).map(([k, v]) => [k, v.value]));
  checkReport(after);
  assert.equal(fields.vehicle_count, 3);
  assert.equal(fields.task_count, session.contract.fields.task_count.value);
  assert.equal(fields.vehicle_capacity, session.contract.fields.vehicle_capacity.value);
  assert.equal(after.scenario_id, session.scenario_id, '仅改车队应原地更新地图');
  assert.ok(session.run_ids.every((id) => after.run_ids.includes(id)), '历史运行丢失');
  assert.ok(after.active_run_ids.every((id) => !session.run_ids.includes(id)), '新合同沿用了旧批次');
  const exportedText = await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}/export`)).text();
  const exported = JSON.parse(exportedText);
  assert.equal(exported.record.scenarios[after.scenario_id].instance.vehicles.count, 3);
  for (const id of after.active_run_ids) assert.equal(exported.record.runs[id].instance.vehicles.count, 3);
  writeFileSync(`${out}/edited-session.json`, exportedText);
  step('after_edit', { state: after.state, vehicle_count: fields.vehicle_count, task_count: fields.task_count,
    capacity: fields.vehicle_capacity, scenario: after.scenario_id, runs: after.run_ids.length,
    verified: Object.values(after.verifications).filter((v) => v.valid).length });
  await page.reload();
  await page.getByLabel('给智能体的消息').waitFor({ timeout: 30_000 });
  assert.ok(page.url().includes(sid), '刷新后会话地址丢失');
  const restored = await (await page.request.get(`${base}/lab/api/agent/sessions/${sid}`)).json();
  assert.equal(restored.session_id, after.session_id);
  assert.deepEqual(restored.active_run_ids, after.active_run_ids);
  await page.screenshot({ path: `${out}/05-restored.png` });
  step('restored', { sid, active_runs: restored.active_run_ids.length });
  assert.equal(log.steps.filter((s) => s.name === 'pageerror').length, 0, '浏览器出现未处理错误');
  log.session = sid;
  log.result = 'pass';
} catch (e) {
  log.result = 'fail';
  log.error = String(e);
  await page.screenshot({ path: `${out}/99-failure.png` }).catch(() => undefined);
  process.exitCode = 1;
} finally {
  log.finished = new Date().toISOString();
  writeFileSync(`${out}/log.json`, JSON.stringify(log, null, 1));
  await browser.close();
}
