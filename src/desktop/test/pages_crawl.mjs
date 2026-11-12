// Opens primary routes of a running backend and records render failures, script errors and 5xx
// answers. Run it against a backend on a machine without ROS, where a page that assumes ROS shows up here.
// Optional --cdp http://127.0.0.1:42873 checks the actual open Electron page; --screenshots failed saves only failures.
// Usage: PLAYWRIGHT_CORE=<playwright-core> [CERES_CHROMIUM=<chrome>] node pages_crawl.mjs --url http://127.0.0.1:4251 --out /new/absolute/dir
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const out = arg('--out');
const base = arg('--url', 'http://127.0.0.1:4100');
const cdp = arg('--cdp');
const screenshots = arg('--screenshots', 'all');
if (!out || !out.startsWith('/') || existsSync(out)) throw new Error('--out 须为尚不存在的绝对路径');
mkdirSync(out, { recursive: true });

const scenario = arg('--scenario');
const PAGES = ['#/agent', '#/field', '#/field/machines', '#/field/coverage', '#/maps', scenario ? `#/plan/${encodeURIComponent(scenario)}` : '#/plan', '#/assets', '#/experiments', '#/runs', '#/jobs', '#/sim', '#/algorithms', '#/library/cvrplib', '#/library/fields',
  '#/library/references', '#/archive', '#/bpp', '#/samples', '#/replay', '#/risk', '#/evidence', '#/system', '#/system/models', '#/system/capabilities', '#/system/data', '#/system/tools', '#/system/evals',
  '#/system/services', '#/system/logs', '#/system/desktop'];

const browser = cdp ? await chromium.connectOverCDP(cdp) : await chromium.launch({ headless: true, executablePath: process.env.CERES_CHROMIUM || undefined });
const page = cdp ? browser.contexts()[0].pages().find((p) => p.url().startsWith(base))
  : await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
if (!page) throw new Error('CDP 中没有指定 Lab 页面');
const originalURL = page.url();
let current = '';
const rows = new Map(PAGES.map((p) => [p, { page: p, script: [], server: [], unavailable: [], boundary: null, chars: 0 }]));
const note = (kind, text) => rows.get(current)?.[kind].push(text.slice(0, 300));
page.on('pageerror', (e) => note('script', String(e.message || e)));
page.on('console', (m) => { if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) note('script', m.text()); });
page.on('response', (r) => { if (r.status() >= 500) note('server', `${r.status()} ${new URL(r.url()).pathname}`); });

await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' });
for (const hash of PAGES) {
  current = hash;
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.waitForTimeout(1800);
  const row = rows.get(hash);
  const text = await page.evaluate(() => document.body.innerText);
  row.chars = text.length;
  row.boundary = text.includes('此视图渲染失败') ? text.slice(text.indexOf('此视图渲染失败'), text.indexOf('此视图渲染失败') + 200) : null;
  // Without the history pack the archive answers 503; that is fine only when the page says what to do about it.
  if (text.includes('下载历史档案')) {
    row.unavailable = row.server.filter((x) => x.startsWith('503 /api/'));
    row.server = row.server.filter((x) => !x.startsWith('503 /api/'));
  }
  row.actualURL = page.url();
  const bad = row.script.length || row.server.length || row.boundary || row.chars < 20;
  if (screenshots === 'all' || bad) await page.screenshot({ path: `${out}/${hash.replace(/[#/]+/g, '-').replace(/^-/, '')}.png` });
  console.log(bad ? '✗' : '✓', hash, bad ? JSON.stringify({ script: row.script, server: row.server, boundary: row.boundary, chars: row.chars }) : '');
}
if (cdp) await page.goto(originalURL, { waitUntil: 'domcontentloaded' });
else await browser.close();

const list = [...rows.values()];
const failed = list.filter((r) => r.script.length || r.server.length || r.boundary || r.chars < 20);
writeFileSync(`${out}/pages.json`, JSON.stringify({ base, scope: 'Route rendering; expected missing-pack 503 remains recorded, not functional acceptance', pages: list, failed: failed.length, unavailable: list.filter((r) => r.unavailable.length).length }, null, 1));
console.log(`${list.length - failed.length}/${list.length} 页正常`);
process.exit(failed.length ? 1 : 0);
