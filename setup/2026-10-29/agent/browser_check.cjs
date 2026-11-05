// Browser check of the research agent panel; started by browser_check.sh, which runs the gateways and the stub.
// Writes OUT/summary.json and screenshots; exits 1 when any check fails.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2];
const AGENT = 'http://127.0.0.1:4199/';
const BARE = 'http://127.0.0.1:4198/';
const STUB = 'http://127.0.0.1:4291/';
const checks = [];
const record = (name, pass, detail) => checks.push({ name, pass: Boolean(pass), detail });

(async () => {
  const browser = await chromium.launch({
    ...(process.env.URP_CHROMIUM_EXECUTABLE ? { executablePath: process.env.URP_CHROMIUM_EXECUTABLE } : {}),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = [];
  page.on('console', (m) => { if (m.type() === 'error') problems.push('console: ' + m.text()); });
  page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
  page.on('response', (r) => { if (r.url().includes('/lab/api/agent') && r.status() >= 400) problems.push(`http ${r.status()} ${r.url()}`); });
  const api = async (url) => (await page.request.get(url)).json();
  const latest = async () => (await api(AGENT + 'lab/api/agent/sessions')).sessions[0];
  const session = async (id) => api(`${AGENT}lab/api/agent/sessions/${id}`);
  const shot = (p, name) => p.screenshot({ path: path.join(OUT, name + '.png') });
  const agentButton = (p) => p.getByRole('button', { name: '智能体', exact: true });
  const input = page.locator('textarea[aria-label="给智能体的消息"]');

  // 1. A research turn: compose, recommend + catalogue, solve, wait, compare, answer.
  await page.goto(AGENT, { waitUntil: 'networkidle' });
  await agentButton(page).click();
  await input.waitFor({ timeout: 15000 });
  await page.locator('button[class*="_example_"]').first().click();
  await page.getByRole('button', { name: '发送' }).click();
  await page.getByText(/^结论：场景/).waitFor({ timeout: 180000 });
  const research = await session((await latest()).session_id);
  const tools = research.steps.filter((s) => s.kind === 'tool');
  const waited = tools.find((s) => s.name === 'wait_for_runs')?.result?.runs ?? [];
  const g = research.grounding ?? {};
  record('research_turn', research.state === 'idle' && tools.every((s) => s.ok),
    tools.map((s) => s.name).join(' → '));
  record('runs_pass_independent_check', waited.length > 0 && waited.every((r) => r.state === 'done' && r.check?.valid === true),
    waited.map((r) => `${r.algorithm.id}: ${r.state}, check.valid=${r.check?.valid}, ${r.objective.value} ${r.objective.unit}`));
  record('answer_grounded', g.checked > 0 && g.grounded === g.checked && g.unknown_ids.length === 0, g);
  const badge = `${g.grounded}/${g.checked} 个数字可在工具结果中找到`;
  record('grounding_badge_shown', await page.getByText(badge).isVisible(), badge);
  await page.waitForTimeout(400);
  await shot(page, 'research-final');

  // 2. A run id in the answer opens the plan workspace on that scenario.
  const scenario = tools.find((s) => s.name === 'compose_scenario').result.scenario_id;
  await page.locator('button[title="在地图和方案对比中查看"]').first().click();
  await page.waitForTimeout(2500);
  const hash = await page.evaluate(() => location.hash);
  record('run_link_opens_plan', hash === `#/plan/${encodeURIComponent(scenario)}`, hash);

  // 3. ⌘/Ctrl+Enter sends the message without also triggering the workbench's global solve shortcut.
  const workbenchRuns = async () => (await api(AGENT + 'lab/api/runs?limit=100')).runs.filter((r) => r.label === '工作台对比').length;
  const before = await workbenchRuns();
  await agentButton(page).click();
  await input.fill('用最好的方案启动田间仿真');
  await input.press('Control+Enter');

  // 4. Actuation stops for approval; denying it is recorded and answered.
  await page.getByRole('button', { name: '批准执行' }).waitFor({ timeout: 30000 });
  const waiting = await session(research.session_id);
  record('approval_requested', waiting.state === 'awaiting_approval' && waiting.pending?.tool === 'start_field_simulation', waiting.pending);
  await shot(page, 'approval');
  await page.waitForTimeout(1500);
  record('shortcut_does_not_solve', (await workbenchRuns()) === before, `工作台对比 runs before/after: ${before}/${await workbenchRuns()}`);
  await page.getByRole('button', { name: '拒绝' }).click();
  await page.getByText('结论：你没有批准').waitFor({ timeout: 30000 });
  const denied = await session(research.session_id);
  const deniedStep = denied.steps.filter((s) => s.kind === 'tool').pop();
  record('denial_recorded', denied.state === 'idle' && deniedStep.name === 'start_field_simulation' && deniedStep.error === '用户拒绝执行',
    { state: denied.state, step: deniedStep.error });

  // 5. After a reload, and after re-opening the shown tab, the timeline still shows the latest step.
  await page.reload({ waitUntil: 'networkidle' });
  await agentButton(page).click();
  await page.getByText('结论：你没有批准').waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  const scroll = await page.evaluate(() => { const t = document.querySelector('[class*="_timeline_"]'); return [t.scrollTop, t.scrollHeight, t.clientHeight]; });
  record('timeline_follows_latest', scroll[0] + scroll[2] >= scroll[1] - 2, { scrollTop: scroll[0], scrollHeight: scroll[1], clientHeight: scroll[2] });

  // 6. The compose bar hands its text to a new session; stopping ends the turn while runs are being awaited.
  const text = '30 亩菜地，两台小车从东北角出发运输，每台载重 20，十二个投放点聚集分布';
  await page.locator('input[aria-label="任务描述"]').fill(text);
  await page.getByRole('button', { name: '交给智能体' }).click();
  await page.getByText('提交求解').first().waitFor({ timeout: 60000 });
  const handed = await latest();
  const first = (await session(handed.session_id)).steps[0];
  record('compose_bar_handoff', handed.session_id !== research.session_id && first.kind === 'user' && first.text === text, handed.title);
  await page.getByRole('button', { name: '停止' }).click();
  await page.getByText('已取消这一轮').waitFor({ timeout: 30000 });
  const stopped = await session(handed.session_id);
  record('stop_cancels_turn', stopped.state === 'canceled' && stopped.steps.at(-1).kind === 'notice',
    stopped.steps.map((s) => s.kind + (s.name ? ':' + s.name : '')).join(' '));
  await shot(page, 'stopped');

  // 7. Without a configured model the panel says how to configure it and cannot send.
  const bare = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  bare.on('pageerror', (e) => problems.push('pageerror (unconfigured): ' + e.message));
  await bare.goto(BARE, { waitUntil: 'networkidle' });
  await agentButton(bare).click();
  await bare.getByText('未配置模型').waitFor({ timeout: 15000 });
  const status = await (await bare.request.get(BARE + 'lab/api/agent')).json();
  record('unconfigured_notice', status.configured === false && (await bare.getByRole('button', { name: '发送' }).isDisabled()), status.reason);
  await shot(bare, 'unconfigured');

  // 8. The key goes to the model endpoint only: not into the API, the session records, the database or the logs.
  const key = process.env.AGENT_CHECK_KEY;
  const leaks = [];
  if (JSON.stringify(await api(AGENT + 'lab/api/agent')).includes(key)) leaks.push('GET /lab/api/agent');
  for (const { session_id: id } of (await api(AGENT + 'lab/api/agent/sessions')).sessions) {
    if (JSON.stringify(await api(`${AGENT}lab/api/agent/sessions/${id}?messages=true`)).includes(key)) leaks.push(id);
  }
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true })
    .flatMap((e) => (e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]));
  for (const file of [...walk(path.join(OUT, 'runtime')), path.join(OUT, 'gateway.log')]) {
    if (fs.readFileSync(file).includes(key)) leaks.push(path.relative(OUT, file));
  }
  record('key_not_recorded', Boolean(key) && leaks.length === 0, leaks.length ? leaks : 'API, session records with messages, runtime files and gateway log scanned');

  const stub = await (await page.request.get(STUB + 'log')).json();
  record('model_protocol', stub.length > 0 && stub.every((r) => r.errors.length === 0), `${stub.length} model requests, ${stub.reduce((n, r) => n + r.errors.length, 0)} protocol errors`);
  record('no_page_errors', problems.length === 0, problems);

  const summary = {
    check: 'urp.agent.browser/1', utc: new Date().toISOString(), browser: `chromium ${browser.version()}`,
    model: 'stub_llm.py: loopback stand-in that plays a fixed script; not a language model',
    passed: checks.filter((c) => c.pass).length, total: checks.length, checks,
  };
  fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1) + '\n');
  console.log(`${summary.passed}/${summary.total} checks passed`);
  for (const c of checks) console.log(`${c.pass ? 'PASS' : 'FAIL'} ${c.name}`);
  await browser.close();
  process.exit(summary.passed === summary.total ? 0 : 1);
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
