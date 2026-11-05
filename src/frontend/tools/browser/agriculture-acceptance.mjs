/** Production UI + actual gateway smoke. Run with disposable URP_LAB_RUNTIME, never a live database. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';
import { readJsonResponse, submittedRunId, waitForSuccessfulRun } from './agriculture-acceptance-contract.mjs';
const base = process.env.URP_BASE_URL ?? 'http://127.0.0.1:4100';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.URP_BROWSER_OUTPUT;
assert.ok(output, 'Set a new output directory'); await fs.mkdir(output, { recursive: false });
const browser = await chromium.launch({ headless: true, executablePath: process.env.URP_BROWSER_EXECUTABLE || undefined, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 1100 }, acceptDownloads: true });
const checks = [], errors = [];
page.on('pageerror', (e) => errors.push(e.message));
const check = async (name, fn) => { await fn(); checks.push(name); };
const get = async (path) => readJsonResponse(await page.request.get(base + path), 200, path);
try {
  await page.goto(base + '/#/');
  await page.getByRole('button', { name: '打开农业任务目录' }).click();
  await check('production catalogue and filters', async () => {
    await page.getByLabel('生产环境', { exact: true }).selectOption('orchard');
    await page.getByLabel('农业作业', { exact: true }).selectOption('pruning');
    await page.getByRole('status').filter({ hasText: '此组合暂无模式' }).waitFor();
    await page.getByLabel('农业作业', { exact: true }).selectOption('');
    await page.getByLabel('生产环境', { exact: true }).selectOption('');
  });
  await check('proposal cannot submit', async () => {
    await page.getByLabel('模式状态', { exact: true }).selectOption('proposal');
    assert.equal(await page.getByRole('button', { name: '校验并导入任务规划', exact: true }).count(), 0);
    await page.getByLabel('模式状态', { exact: true }).selectOption('');
  });
  await check('real matrix downloads', async () => {
    for (const format of ['JSON', 'CSV']) {
      const wait = page.waitForEvent('download');
      await page.getByRole('button', { name: `下载完整矩阵 ${format}` }).click();
      const d = await wait; const path = output + '/' + d.suggestedFilename(); await d.saveAs(path);
      const raw = await fs.readFile(path, 'utf8');
      if (format === 'JSON') { const doc = JSON.parse(raw); assert.equal(doc.environment_problem.length, 14); assert.equal(Object.keys(doc.environment_problem[0].cells).length, 13); }
      else assert.equal(raw.split('\r\n').length, 363);
    }
  });
  await page.getByLabel('农业作业', { exact: true }).selectOption('sampling');
  const card = page.locator('details').filter({ hasText: '大田土壤采样' });
  // Match by stable label if the human-facing title changes.
  const actualCard = await card.count() ? card : page.locator('details').filter({ has: page.locator('input[aria-label$=" 任务点数"]') }).first();
  await actualCard.locator('summary').click();
  await check('invalid values stop import and show errors', async () => {
    await actualCard.locator('input[aria-label$=" 田块宽度"]').fill('0');
    await actualCard.getByRole('button', { name: '校验并导入任务规划', exact: true }).click();
    await actualCard.getByRole('alert').waitFor();
    await actualCard.locator('input[aria-label$=" 田块宽度"]').fill('100');
    await actualCard.locator('input[aria-label$=" 任务点数"]').fill('6');
    await actualCard.locator('input[aria-label$=" 随机种子"]').fill('7');
  });
  await check('raw scenario retains parameters and seed', async () => {
    const wait = page.waitForEvent('download'); await actualCard.getByRole('button', { name: '下载原始场景 JSON' }).click();
    const d = await wait; const path = output + '/original-scenario.json'; await d.saveAs(path);
    const raw = JSON.parse(await fs.readFile(path, 'utf8')); assert.equal(raw.provenance.seed, 7); assert.equal(raw.editable.values.task_count, 6);
  });
  await actualCard.getByRole('button', { name: '校验并导入任务规划', exact: true }).click();
  await page.waitForURL((url) => url.hash.startsWith('#/plan/agri-'));
  const id = decodeURIComponent(new URL(page.url()).hash.slice('#/plan/'.length));
  const scenario = await get('/lab/api/scenarios/' + id);
  await fs.writeFile(output + '/persisted-scenario.json', JSON.stringify(scenario, null, 2));
  assert.equal(scenario.instance.tasks.length, 6); checks.push('real backend validated and persisted import');
  await check('refresh preserves imported scene', async () => { await page.reload(); await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {}); assert.equal((await get('/lab/api/scenarios/' + id)).id, id); });
  const algorithmId = 'panel.cluster_first';
  const response = await page.request.post(base + '/lab/api/solve', { headers: { Origin: base }, data: { scenario_id: id, algorithms: [{ id: algorithmId, params: {} }], label: 'agri acceptance' } });
  const job = await readJsonResponse(response, 202, 'POST /lab/api/solve');
  await fs.writeFile(output + '/job.json', JSON.stringify(job, null, 2));
  const runId = submittedRunId(job); let run;
  try {
    await waitForSuccessfulRun({ get, wait: (ms) => page.waitForTimeout(ms), job, scenarioId: id, algorithmId,
      onRun: (value) => { run = value; } });
  } finally {
    if (run !== undefined) await fs.writeFile(output + '/run.json', JSON.stringify(run, null, 2));
  }
  checks.push('actual solver completed feasible');
  const record = await get('/lab/api/runs/' + runId + '/record'); await fs.writeFile(output + '/record.json', JSON.stringify(record, null, 2));
  checks.push('server experiment export');
  await page.reload(); await page.waitForTimeout(1200); await page.screenshot({ path: output + '/after-run.png', fullPage: true });
  assert.deepEqual(errors, []);
} catch (error) { await page.screenshot({ path: output + '/failure.png', fullPage: true }).catch(() => {}); await fs.writeFile(output + '/failure.txt', error.stack + '\n' + await page.locator('body').innerText()); throw error; }
finally { await fs.writeFile(output + '/summary.json', JSON.stringify({ checks, errors }, null, 2)); await browser.close(); }
