/** Live browser acceptance. Run only against a disposable local test database.
 * Uses a six-node engineering example; never starts the formal CVRPLIB baseline.
 * CERES_ROUTE_FIXTURE_ID may name an imported backend/tests/route_fixture.py fixture.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.CERES_BASE_URL ?? 'http://127.0.0.1:4173';
const url = new URL(base);
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname), 'Use a local test service');
assert.equal(url.protocol, 'http:');
const output = path.resolve(process.env.CERES_BROWSER_OUTPUT ?? `artifacts/workspace-${Date.now()}`);
await fs.mkdir(output, { recursive: false });
const checks = [], errors = [], jobs = [];
let browser, page;
const check = async (name, action) => {
  const started = Date.now();
  try { await action(); checks.push({ name, status: 'passed', elapsed_ms: Date.now() - started }); }
  catch (error) { checks.push({ name, status: 'failed', error: error.message }); throw error; }
};
const get = async (endpoint) => {
  const response = await page.request.get(`${base}${endpoint}`);
  assert.equal(response.status(), 200, endpoint);
  return response.json();
};
const resultPanel = () => page.locator('section').filter({ has: page.getByRole('heading', { name: '运行结果', exact: true }) });
const run = async () => {
  const oldHash = new URL(page.url()).hash;
  await page.getByRole('button', { name: '运行并独立验算', exact: true }).click();
  await page.waitForURL((u) => u.hash.startsWith('#/jobs/') && u.hash !== oldHash);
  const id = decodeURIComponent(new URL(page.url()).hash.slice('#/jobs/'.length));
  await resultPanel().getByRole('status').filter({ hasText: '执行完成' }).waitFor({ timeout: 20000 });
  const job = await get(`/api/jobs/${encodeURIComponent(id)}`);
  assert.equal(job.status, 'completed'); assert.equal(job.result.feasible, true);
  assert.ok(Number.isFinite(job.result.cost)); assert.ok(job.result.routes.length > 0);
  assert.equal(await resultPanel().locator('svg polyline').count(), job.result.routes.length);
  jobs.push(job);
  return job;
};

try {
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.CERES_BROWSER_EXECUTABLE || undefined,
    args: ['--no-sandbox'] });
  page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, acceptDownloads: true });
  page.on('pageerror', (error) => errors.push(error.message));
  await check('production_page_and_api', async () => {
    await page.goto(`${base}/#/jobs`);
    await page.getByText('本地后端已连接', { exact: false }).waitFor();
    const health = await get('/api/health');
    assert.equal(health.executes_experiments, true);
    assert.equal(await page.getByRole('link', { name: '本项目数据核验通过', exact: true }).count(), 0);
  });
  await check('proxy_rejects_foreign_origin', async () => {
    const response = await page.request.post(`${base}/api/jobs`, {
      headers: { Origin: 'https://untrusted.example' }, data: {} });
    assert.equal(response.status(), 403);
  });
  await check('invalid_input_does_not_enqueue', async () => {
    const before = (await get('/api/jobs')).jobs.length;
    await page.getByRole('textbox', { name: '任务 JSON', exact: true }).fill('{"coordinates":null}');
    await page.getByRole('button', { name: '运行并独立验算', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '四个字段' }).waitFor();
    assert.equal((await get('/api/jobs')).jobs.length, before);
    await page.getByRole('button', { name: '载入示例', exact: true }).click();
  });
  await check('savings_real_solution_and_checker', async () => { await run(); });
  await check('refresh_preserves_job_identity', async () => {
    const id = jobs[0].id;
    await page.reload();
    await resultPanel().getByRole('status').filter({ hasText: '执行完成' }).waitFor();
    assert.ok((await resultPanel().innerText()).includes(id));
    assert.equal((await get(`/api/jobs/${id}`)).input_sha256, jobs[0].input_sha256);
  });
  await check('download_preserves_result_and_identity', async () => {
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出记录', exact: true }).click();
    const download = await pending, file = path.join(output, 'downloaded-job.json');
    await download.saveAs(file);
    assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), jobs[0]);
  });
  await check('edited_input_pyvrp_has_new_identity', async () => {
    const problem = JSON.parse(await page.getByRole('textbox', { name: '任务 JSON', exact: true }).inputValue());
    problem.coordinates[1][0] += 3;
    await page.getByRole('textbox', { name: '任务 JSON', exact: true }).fill(JSON.stringify(problem, null, 2));
    await page.getByLabel('算法', { exact: true }).selectOption('pyvrp');
    const job = await run();
    assert.notEqual(job.id, jobs[0].id); assert.notEqual(job.input_sha256, jobs[0].input_sha256);
    assert.equal(job.request.algorithm, 'pyvrp');
  });
  await check('desktop_and_mobile_geometry', async () => {
    await page.getByRole('textbox', { name: '任务 JSON', exact: true }).evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert.ok(width.content <= width.viewport + 1, JSON.stringify(width));
    await resultPanel().getByText('任务身份与验算记录', { exact: true }).click();
    const expanded = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert.ok(expanded.content <= expanded.viewport + 1, `Expanded evidence: ${JSON.stringify(expanded)}`);
    await resultPanel().evaluate((element) => element.scrollIntoView({ block: 'start' }));
    await page.evaluate(() => scrollBy(0, -64));
    await page.screenshot({ path: path.join(output, 'mobile.png') });
    await page.setViewportSize({ width: 1440, height: 1080 });
  });
  await check('archive_list_reads_real_backend', async () => {
    await page.getByRole('link', { name: '实验档案', exact: true }).click();
    const experiments = (await get('/api/experiments')).experiments;
    assert.ok(experiments.length > 0, 'Bootstrap the test database before running acceptance');
    await page.getByText(experiments[0].id, { exact: true }).waitFor();
  });
  const fixture = process.env.CERES_ROUTE_FIXTURE_ID;
  if (fixture) {
    await check('archived_routes_keep_partial_invalid_and_no_solution', async () => {
      const row = page.getByRole('row').filter({ has: page.getByText(fixture, { exact: true }) });
      await row.getByRole('button', { name: '查看路线', exact: true }).click();
      const select = page.getByLabel('归档运行', { exact: true });
      for (const [runId, status, polylines] of [
        ['fixture_possible.distance_first', '完整服务', 1],
        ['fixture_possible.time_window_first', '部分服务', 1],
        ['fixture_possible.capacity_only_diagnostic', '无效候选', 1],
        ['fixture_impossible.oracle_full', '不可行', 0],
      ]) {
        await select.selectOption(runId);
        const panel = page.locator('section').filter({ has: page.getByRole('heading', { name: '归档路线', exact: true }) });
        await panel.getByText(status, { exact: true }).waitFor();
        assert.ok((await panel.innerText()).includes(runId));
        assert.equal(await panel.locator('svg polyline').count(), polylines);
        const scene = await get(`/api/v1/experiments/${fixture}/runs/${runId}/route-scene?revision=1`);
        assert.equal(scene.simulation_ready, false); assert.equal(scene.revision, 1);
      }
    });
  }
  await check('pagination_and_terminal_labels_with_explicit_response_fixtures', async () => {
    // Only this check substitutes list responses. No fabricated jobs are saved.
    await page.route('**/api/jobs?*', async (route) => {
      const query = new URL(route.request().url()).searchParams;
      const offset = Number(query.get('offset')), limit = Number(query.get('limit'));
      assert.equal(limit, 50);
      const rows = Array.from({ length: Math.max(0, Math.min(limit, 101 - offset)) }, (_, i) => ({
        id: `browser-response-fixture-${offset + i}`, status: offset + i === 100 ? 'interrupted' : 'failed',
        scene_id: 'explicit-browser-test-fixture', scene_revision: 1, created_at: '2026-01-01T00:00:00Z',
        feasible: null, cost: null, result_status: null,
      }));
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ jobs: rows,
        total: 101, limit, offset, counts: { queued: 0, running: 0, completed: 0, failed: 100, interrupted: 1 } }) });
    });
    await page.goto(`${base}/#/jobs`);
    await page.getByRole('link', { name: 'browser-response-fixture-0', exact: true }).waitFor();
    assert.equal(await page.getByText('待验算', { exact: true }).count(), 0);
    assert.equal(await page.getByText('未产生验算结果', { exact: true }).count(), 50);
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await page.getByRole('link', { name: 'browser-response-fixture-50', exact: true }).waitFor();
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await page.getByRole('link', { name: 'browser-response-fixture-100', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '下一页', exact: true }).isDisabled(), true);
    await page.reload();
    await page.getByRole('link', { name: 'browser-response-fixture-100', exact: true }).waitFor();
    assert.equal(await page.getByText('未产生验算结果', { exact: true }).count(), 1);
    await page.unroute('**/api/jobs?*');
  });
  await check('no_uncaught_browser_errors', async () => assert.deepEqual(errors, []));
} catch (error) {
  if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  console.error(error.stack); process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  const report = { schema: 'ceres-workspace-browser-acceptance-v1',
    status: process.exitCode ? 'failed' : 'passed', generated_utc: new Date().toISOString(),
    scope: 'Real local API, two six-node engineering runs, optional synthetic archived route fixtures, and one explicitly intercepted pagination/terminal-state fixture; no formal baseline or physics.',
    fixture_id: process.env.CERES_ROUTE_FIXTURE_ID ?? null,
    omitted: process.env.CERES_ROUTE_FIXTURE_ID ? [] : ['archived_routes_keep_partial_invalid_and_no_solution'],
    checks, browser_errors: errors, jobs: jobs.map(({ id, request, input_sha256, source_sha256, result }) =>
      ({ id, algorithm: request.algorithm, input_sha256, source_sha256, status: result.status, feasible: result.feasible, cost: result.cost })) };
  await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ status: report.status, checks: checks.length, output }));
}
