// Browser acceptance of the desk shell against a running gateway: conversation, layers, contract edit, approval, maps, settings.
// Usage: NODE_PATH=<dir containing playwright-core> [URP_CHROMIUM=<chrome>] node browser_e2e.mjs --out /new/absolute/dir [--url http://127.0.0.1:4100] [--mode rule|local]
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const out = arg('--out');
const base = arg('--url', 'http://127.0.0.1:4100');
const mode = arg('--mode', 'rule');
if (!out || !out.startsWith('/') || existsSync(out)) throw new Error('--out 须为尚不存在的绝对路径');
mkdirSync(out, { recursive: true });

const log = { base, mode, started: new Date().toISOString(), steps: [], errors: [] };
const step = (name, extra = {}) => { log.steps.push({ name, t: new Date().toISOString(), ...extra }); console.log('✓', name, Object.keys(extra).length ? JSON.stringify(extra) : ''); };
const browser = await chromium.launch({ headless: true, executablePath: process.env.URP_CHROMIUM || undefined });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(40_000);
page.on('pageerror', (e) => log.errors.push(String(e.stack || e).slice(0, 400)));
const shot = (name) => page.screenshot({ path: `${out}/${name}.png` });
const api = async (path) => page.evaluate(async (p) => (await fetch(`/lab/api${p}`)).json(), path);
const sessionId = () => decodeURIComponent(new URL(page.url()).hash.split('/')[2]?.split('?')[0] ?? '');
async function until(id, states, ms = 300_000) {
  const end = Date.now() + ms;
  for (;;) {
    const s = await api(`/agent/sessions/${id}`);
    if (states.includes(s.state)) return s;
    if (Date.now() > end) throw new Error(`会话 ${id} 停在 ${s.state}`);
    await page.waitForTimeout(400);
  }
}
const composer = () => page.getByLabel('给智能体的消息');
const paneRow = (name) => page.locator('aside[aria-label="详情"] button').filter({ hasText: name }).first();
const tab = (name) => page.locator('aside[aria-label="详情"] [role="tab"]').filter({ hasText: name }).first();
const crumbs = () => page.locator('aside[aria-label="详情"] b').last();

try {
  await page.goto(`${base}/#/agent`, { waitUntil: 'load' });
  await page.getByRole('heading', { name: '今天要安排哪块地？' }).waitFor();
  await shot('01-new-task');
  step('新任务页');
  if (mode === 'rule') {
    await page.getByRole('button', { name: /qwen|glm|读取模型/ }).click();
    await page.getByRole('menuitem', { name: /规则流程/ }).click();
  }
  await composer().fill('60×40米果园，2台车，8个采样点，每车容量4，1个池塘');
  await composer().press('Enter');
  await page.getByRole('button', { name: '确认并执行' }).waitFor({ timeout: 300_000 });
  await shot('02-contract');
  await page.waitForURL(/#\/agent\/agt_/);
  const id = sessionId();
  step('合同待确认', { id });
  await page.getByRole('button', { name: '确认并执行' }).click();
  let s = await until(id, ['done', 'failed', 'awaiting_approval']);
  assert.equal(s.state, 'done', JSON.stringify(s.error));
  assert.ok(s.report.evidence.best_verified, '没有复核通过的方案');
  await page.getByText('个方案通过复核').waitFor();
  await shot('03-report');
  step('报告与下一步', { best: s.report.evidence.best_verified.algorithm, m: s.report.evidence.best_verified.total_distance_m, short: s.short_title });
  assert.match(s.short_title, /果园/);

  // Folded activity: one click per layer.
  await page.locator('section button[aria-expanded="false"]').filter({ hasText: /调用/ }).last().click();
  await page.locator('section button[aria-expanded="false"]').filter({ hasText: '求解对比' }).first().click();
  await page.waitForTimeout(350);
  await shot('04-folded-layers');
  step('折叠行逐层展开');

  // Pane layers follow the address.
  await tab('方案').click();
  const best = s.report.evidence.best_verified.algorithm;
  await paneRow(best).click();
  await page.getByText('依据', { exact: true }).waitFor();
  await paneRow('① 号车').click();
  await page.getByText('累计里程').first().waitFor();
  await page.locator('aside[aria-label="详情"] .num, aside[aria-label="详情"] button').filter({ hasText: /m$/ }).first().waitFor();
  const stopRow = page.locator('aside[aria-label="详情"] button').filter({ hasText: / m$/ }).first();
  await stopRow.click();
  await page.getByText('恒速模型到达').waitFor();
  await page.waitForTimeout(600);
  await shot('05-layer-stop');
  assert.match(page.url(), /[?&]p=/);
  await page.reload({ waitUntil: 'load' });
  await page.getByText('恒速模型到达').waitFor();
  step('面板层进入地址，刷新后回到同一层', { url: page.url().split('#')[1] });
  await page.locator('body').click({ position: { x: 700, y: 30 } });
  await page.keyboard.press('Escape');
  await page.getByText('累计里程').first().waitFor();
  step('Esc 退回上一层');

  // Contract edit returns to draft; the old plan stays as history.
  await tab('合同').click();
  await paneRow('车辆数').click();
  await page.getByRole('button', { name: '修改这一项' }).click();
  await page.locator('aside[aria-label="详情"] input').fill('3');
  await page.getByRole('button', { name: '保存' }).click();
  s = await until(id, ['awaiting_confirmation', 'failed']);
  await page.getByRole('button', { name: '确认并执行' }).last().click();
  s = await until(id, ['done', 'failed', 'awaiting_approval']);
  assert.equal(s.state, 'done', JSON.stringify(s.error));
  await shot('06-after-edit');
  step('修改合同后重新执行', { revision: s.contract.revision, vehicles: s.report.evidence.contract['车辆数'] });

  // Approval gate: a simulation request stops at the box; rejecting never starts Gazebo.
  await tab('合同').click();
  await page.getByLabel('添加字段').selectOption('simulate');
  await page.locator('aside[aria-label="详情"] select').last().selectOption('true');
  await page.getByRole('button', { name: '保存' }).click();
  s = await until(id, ['awaiting_confirmation', 'failed']);
  await page.getByRole('button', { name: '确认并执行' }).last().click();
  s = await until(id, ['awaiting_approval', 'done', 'failed']);
  assert.equal(s.state, 'awaiting_approval', `状态 ${s.state}`);
  await page.getByRole('button', { name: '批准一次' }).waitFor();
  await shot('07-approval');
  await page.getByRole('button', { name: '拒绝', exact: true }).click();
  s = await until(id, ['done', 'failed']);
  assert.equal(s.report.evidence.simulation_started, false);
  step('批准框拒绝后不启动仿真');

  // Slash command and session management.
  await composer().fill('/记');
  await page.getByRole('option', { name: /记录/ }).waitFor();
  await composer().press('Enter');
  await page.getByText('时间线').waitFor();
  await page.waitForTimeout(300);
  await shot('08-trace');
  step('斜杠命令打开运行记录');
  await page.getByRole('button', { name: '更多操作' }).click();
  await page.getByRole('menuitem', { name: '置顶' }).click();
  await page.waitForTimeout(500);
  const list = await api('/agent/sessions');
  assert.equal(list.sessions.find((x) => x.session_id === id).pinned, true);
  step('置顶');

  // A sentence makes a map; the map goes to a new task.
  await page.goto(`${base}/#/maps`);
  await page.getByLabel('任务描述').fill('在 120×80 米的菜地里，2 台小车配送 12 个点，每车容量 8');
  await page.getByRole('button', { name: '生成地图' }).click();
  await page.waitForURL(/#\/plan\//, { timeout: 30_000 });
  const mapId = decodeURIComponent(page.url().split('/plan/')[1].split('?')[0]);
  await page.getByPlaceholder('让智能体在这张地图上求解、对比或修改').waitFor();
  await page.waitForTimeout(800);
  await shot('09-plan');
  await composer().fill('比较推荐的算法');
  await composer().press('Enter');
  await page.waitForURL(/#\/agent\/agt_/, { timeout: 30_000 });
  const bound = sessionId();
  s = await until(bound, ['awaiting_confirmation', 'awaiting_user', 'failed', 'done']);
  assert.equal(s.scenario_id, mapId);
  step('规划页把地图交给新任务', { bound, map: mapId });
  await page.getByRole('button', { name: '确认并执行' }).last().click();
  s = await until(bound, ['done', 'failed', 'awaiting_approval']);
  assert.equal(s.state, 'done', JSON.stringify(s.error));
  assert.equal(s.scenario_id, mapId);
  step('绑定地图的任务完成', { best: s.report.evidence.best_verified?.algorithm });

  // Tools open beside the conversation.
  await tab('地图').click();
  await paneRow('编辑地图').click();
  await page.getByRole('radiogroup', { name: '编辑工具' }).or(page.getByRole('group', { name: '编辑工具' })).first().waitFor();
  await page.waitForTimeout(600);
  await shot('10-editor-beside-chat');
  assert.ok(await page.getByLabel('给智能体的消息').isVisible(), '编辑地图时对话应仍在');
  step('地图编辑器在对话旁打开');

  // Search, settings, free layout.
  await page.keyboard.press('Control+k');
  await page.getByPlaceholder('搜索任务、地图、面板或操作…').fill('工具与闸门');
  await page.keyboard.press('Enter');
  await page.waitForURL(/#\/system\/tools/);
  await page.getByText('每轮模型调用上限').waitFor();
  step('命令面板跳到设置');
  await page.goto(`${base}/#/system/general`);
  await page.getByRole('button', { name: '深色' }).click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
  await page.waitForTimeout(300);
  await shot('11-dark-settings');
  await page.getByRole('button', { name: '浅色' }).click();
  await page.getByRole('button', { name: '自由布局' }).click();
  await page.goto(`${base}/#/plan/${encodeURIComponent(mapId)}`);
  await page.locator('.dv-tab').first().waitFor();
  await page.waitForTimeout(800);
  await shot('12-free-layout');
  await page.getByRole('button', { name: '标准布局' }).click();
  await page.getByPlaceholder('让智能体在这张地图上求解、对比或修改').waitFor();
  step('自由布局往返');

  // Every room opens.
  const rooms = ['#/maps', '#/assets', '#/experiments', '#/runs', '#/jobs', '#/sim', '#/algorithms', '#/library/algorithms', '#/library/cvrplib', '#/library/fields', '#/library/references', '#/bpp', '#/samples', '#/risk', '#/evidence', '#/system/models', '#/system/capabilities', '#/system/tools', '#/system/evals', '#/system/services', '#/system/logs'];
  for (const hash of rooms) {
    await page.goto(`${base}/${hash}`);
    await page.waitForTimeout(700);
    assert.equal(await page.locator('text=此视图渲染失败').count(), 0, `${hash} 渲染失败`);
  }
  step('全部页面可打开', { rooms: rooms.length });

  // The algorithm library: a class, an algorithm, its record, a trial on a map against a rival.
  await page.goto(`${base}/#/algorithms`);
  await page.getByRole('button', { name: /PyVRP 固定车队/ }).first().click();
  await page.waitForFunction(() => location.hash.startsWith('#/algorithms/pyvrp.fleet'));
  await page.getByRole('button', { name: /实现与版本/ }).click();
  await page.getByText('同台对比').click();
  await page.getByRole('checkbox', { name: 'OR-Tools 车队调度' }).check();
  await page.getByRole('button', { name: '运行 2 个算法' }).click();
  await page.getByText('通过检查 ·').nth(1).waitFor({ timeout: 40_000 });
  await shot('20-algorithm-trial');
  step('算法库同台试跑');
  await page.goto(`${base}/#/system/capabilities`);
  await page.getByText('MuJoCo 物理预演').waitFor();
  step('能力清单');
  await page.goto(`${base}/#/sim`);
  await page.getByRole('tab', { name: '物理预演' }).click();
  await page.getByText('MuJoCo 3.').first().waitFor();
  await shot('21-physics-panel');
  step('物理预演面板');
  await page.goto(`${base}/#/plan/${encodeURIComponent(mapId)}`);
  await page.waitForTimeout(700);
  for (const name of ['求解', '方案', '验算', '检查器']) {
    await tab(name).click();
    await page.waitForTimeout(350);
  }
  for (const name of ['实验结果', '仿真回放', '决策记录', '科研图表', '场景库', '日志', '经典视图']) {
    await page.locator('aside[aria-label="详情"] button[aria-haspopup="menu"]').first().click();
    await page.getByRole('menuitem', { name }).first().click();
    await page.waitForTimeout(450);
    assert.equal(await page.locator('text=此视图渲染失败').count(), 0, `${name} 渲染失败`);
  }
  step('规划页全部面板可打开');
  assert.deepEqual(log.errors, []);
  log.passed = true;
} catch (e) {
  log.passed = false;
  log.failure = String(e.stack || e);
  await shot('99-failure').catch(() => undefined);
  console.error(e);
  process.exitCode = 1;
} finally {
  log.finished = new Date().toISOString();
  writeFileSync(`${out}/log.json`, JSON.stringify(log, null, 2));
  await browser.close();
}
