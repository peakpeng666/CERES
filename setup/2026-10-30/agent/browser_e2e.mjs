// Browser acceptance of the agent workspace: request → contract → confirm → verified report → multi-turn edit.
// Usage: NODE_PATH=<dir containing playwright-core> [URP_CHROMIUM=<chrome binary>] node browser_e2e.mjs --out /new/absolute/dir [--url http://127.0.0.1:4100] [--mode auto|rule]
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright-core');
const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const out = arg('--out');
const base = arg('--url', 'http://127.0.0.1:4100');
const mode = arg('--mode', 'auto');
const request = arg('--text', '60×40米果园，2台车，8个采样点，每车容量4，1个池塘，每个点要在上午九点前完成');
if (!out || !out.startsWith('/') || existsSync(out)) throw new Error('--out 须为尚不存在的绝对路径');
mkdirSync(out, { recursive: true });

const log = { base, mode, request, started: new Date().toISOString(), steps: [] };
const step = (name, extra = {}) => { log.steps.push({ name, t: new Date().toISOString(), ...extra }); console.log(name, JSON.stringify(extra)); };

const browser = await chromium.launch({ headless: true, executablePath: process.env.URP_CHROMIUM || undefined });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => step('pageerror', { message: String(e) }));
try {
  await page.goto(`${base}/#/agent`);
  await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('workbench.layout')).forEach((k) => localStorage.removeItem(k)));
  await page.reload();
  await page.getByText('农业作业任务智能体').waitFor({ timeout: 30_000 });
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
  if (session.state !== 'done') throw new Error(`第一轮未完成：${session.state} ${JSON.stringify(session.error)}`);
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
  step('after_edit', { state: after.state, vehicle_count: fields.vehicle_count, task_count: fields.task_count,
    capacity: fields.vehicle_capacity, scenario: after.scenario_id, runs: after.run_ids.length,
    verified: Object.values(after.verifications).filter((v) => v.valid).length });
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
