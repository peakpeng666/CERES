// Connecting a model service through the real interface, against an in-process stand-in that speaks the OpenAI shape.
// The key is a throwaway test value. The gateway must run with its own URP_LLM_CONFIG, URP_LLM_CREDENTIALS and URP_CONFIG_DIR.
// Usage: PLAYWRIGHT_CORE=<playwright-core> URP_CHROMIUM=<chrome> node models_e2e.mjs --out /new/absolute/dir [--url http://127.0.0.1:4100]
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const out = arg('--out');
const base = arg('--url', 'http://127.0.0.1:4100');
if (!out || !out.startsWith('/') || existsSync(out)) throw new Error('--out 须为尚不存在的绝对路径');
mkdirSync(out, { recursive: true });

const KEY = 'stand-in-test-key-0123456789';
const chats = [];
const standIn = createServer((req, res) => {
  const send = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  const authorized = req.headers.authorization === `Bearer ${KEY}`;
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    if (!authorized) return send(401, { error: { message: 'invalid key' } });
    if (req.method === 'GET') return send(200, { data: [{ id: 'stand-in-large' }, { id: 'stand-in-small' }] });
    const body = JSON.parse(raw);
    chats.push({ model: body.model, tools: (body.tools ?? []).length });
    send(200, { model: body.model, usage: { prompt_tokens: 100, completion_tokens: 20 }, choices: [{ finish_reason: 'stop', message: { content: '已记录这项任务。' } }] });
  });
});
await new Promise((ok) => standIn.listen(0, '127.0.0.1', ok));
const endpoint = `http://127.0.0.1:${standIn.address().port}/v1`;

const log = { base, started: new Date().toISOString(), steps: [], errors: [] };
const step = (name, extra = {}) => { log.steps.push({ name, ...extra }); console.log('✓', name, Object.keys(extra).length ? JSON.stringify(extra) : ''); };
const browser = await chromium.launch({ headless: true, executablePath: process.env.URP_CHROMIUM || undefined });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
page.on('pageerror', (e) => log.errors.push(String(e.stack || e).slice(0, 400)));
const leaks = [];
page.on('response', async (r) => {
  if (!r.url().includes('/lab/api/')) return;
  try { if ((await r.text()).includes(KEY)) leaks.push(r.url()); } catch { /* streams have no body */ }
});
const shot = (name) => page.screenshot({ path: `${out}/${name}.png` });
const api = (path) => page.evaluate(async (p) => (await fetch(`/lab/api${p}`)).json(), path);
async function until(id, states, ms = 120_000) {
  const end = Date.now() + ms;
  for (;;) {
    const s = await api(`/agent/sessions/${id}`);
    if (states.includes(s.state)) return s;
    if (Date.now() > end) throw new Error(`仍为 ${s.state}`);
    await page.waitForTimeout(300);
  }
}

try {
  await page.goto(`${base}/#/agent`);
  await page.getByText('连接一个模型服务商').waitFor();
  await page.getByRole('button', { name: /连接模型/ }).waitFor();
  await shot('01-first-run');
  step('首次打开时提示连接，并说明规则流程');

  await page.getByRole('button', { name: /连接模型/ }).click();
  await page.waitForFunction(() => location.hash.startsWith('#/system/models'));
  for (const name of ['DeepSeek', '智谱 GLM', 'Kimi', '通义千问', 'OpenRouter', 'OpenAI', 'Anthropic']) await page.getByRole('button', { name: new RegExp(`^${name}`) }).first().waitFor();
  await shot('02-services');
  await page.getByLabel('搜索服务商').fill('moonshot');
  await page.getByRole('button', { name: /^Kimi/ }).first().waitFor();
  await page.getByLabel('搜索服务商').fill('');
  step('服务商列表与搜索');

  await page.getByRole('button', { name: /^DeepSeek/ }).first().click();
  await page.getByLabel('DeepSeek 的 API Key').waitFor();
  assert.equal(await page.getByRole('link', { name: '获取 API Key' }).getAttribute('href'), 'https://platform.deepseek.com/api_keys');
  await page.getByText(/deepseek/i).first().waitFor();
  await shot('03-service-layer');
  await page.getByRole('button', { name: '模型', exact: true }).click();
  step('服务商一层：填 Key 的入口和模型清单');

  await page.getByRole('button', { name: /^自定义接口/ }).click();
  const fill = async (key) => {
    await page.getByLabel('名称').fill('Stand In');
    await page.getByLabel('接口地址').fill(endpoint);
    await page.getByLabel('API Key').fill(key);
    await page.getByRole('button', { name: '添加', exact: true }).click();
  };
  await fill('wrong-test-key-0000000');
  await page.getByText('服务商拒绝了这个 API Key').waitFor();
  assert.equal((await api('/agent/providers')).connected.length, 0);
  step('错误的 Key 被拒绝，不保存');

  await fill(KEY);
  await page.getByText('已验证').first().waitFor();
  const info = await api('/agent/providers');
  assert.equal(info.selected, 'custom-stand-in/stand-in-large');
  assert.deepEqual(info.connected[0].models.map((m) => m.id), ['stand-in-large', 'stand-in-small']);
  await shot('04-connected');
  step('正确的 Key 连上后自动选中模型', { selected: info.selected });

  await page.getByRole('button', { name: /stand-in-small/ }).click();
  await page.waitForTimeout(400);
  assert.equal((await api('/agent/providers')).selected, 'custom-stand-in/stand-in-small');
  step('切换模型');

  await page.goto(`${base}/#/agent`);
  await page.getByRole('button', { name: 'stand-in-small' }).waitFor();
  assert.equal(await page.getByText('连接一个模型服务商').count(), 0);
  await page.getByRole('textbox').first().fill('60×40米果园，2台车，8个采样点，每车容量4');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => location.hash.startsWith('#/agent/agt_'));
  const id = decodeURIComponent(new URL(page.url()).hash.split('/')[2].split('?')[0]);
  let s = await until(id, ['awaiting_confirmation', 'failed']);
  assert.equal(s.provider.id, 'custom-stand-in/stand-in-small');
  await page.getByRole('button', { name: '确认并执行' }).last().click();
  s = await until(id, ['done', 'failed']);
  assert.equal(s.state, 'done', JSON.stringify(s.error));
  assert.ok(chats.length >= 3 && chats.every((c) => c.model === 'stand-in-small'));
  assert.equal(s.report.source, 'llm');
  await page.getByText(/MuJoCo · \d+\/\d+ 通过/).first().waitFor();
  await shot('05-session');
  step('任务走所选模型，调用记录和报告都在', { calls: chats.length, usage: s.usage.calls });

  await page.goto(`${base}/#/system/models`);
  await page.getByRole('button', { name: /^Stand In/ }).first().click();
  await page.getByRole('button', { name: '断开', exact: true }).click();
  await page.getByRole('button', { name: /^自定义接口/ }).waitFor();
  const after = await api('/agent/providers');
  assert.deepEqual([after.selected, after.connected.length], [null, 0]);
  step('断开后回到规则流程');

  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  assert.ok(!stored.includes(KEY), '浏览器存储里出现了 Key');
  assert.deepEqual(leaks, []);
  assert.deepEqual(log.errors, []);
  step('Key 没有出现在任何接口响应和浏览器存储里');
  log.passed = true;
} catch (e) {
  log.passed = false;
  log.failure = String(e.stack || e);
  await shot('99-failure').catch(() => undefined);
  console.error(e);
  process.exitCode = 1;
} finally {
  writeFileSync(`${out}/log.json`, JSON.stringify(log, null, 2));
  await browser.close();
  standIn.close();
}
