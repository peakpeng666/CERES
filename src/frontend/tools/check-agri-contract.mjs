/** Build real TS templates and pass them to the actual Python backend validator.
 * Run from anywhere: AGRI_PYTHON=src/lab/.venv/bin/python node src/frontend/tools/check-agri-contract.mjs [output.json]
 * Requires installed frontend TypeScript and lab dependencies; absence is an error, never a silent pass.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import ts from 'typescript';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const source = path.join(root, 'src/frontend/src/studio/agri');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'urp-agri-contract-'));
try {
  fs.writeFileSync(path.join(temp, 'package.json'), '{"type":"module"}');
  for (const rel of ['geometry.ts', 'patterns/runnable.ts', 'patterns/approx.ts', 'patterns/proposals.ts', 'patterns/rowWeeding.ts', 'patterns/index.ts']) {
    const code = ts.transpileModule(fs.readFileSync(path.join(source, rel), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText.replace(/from ['"]([.][^'"]+)['"]/g, (_, p) => `from '${p}.js'`);
    const target = path.join(temp, rel.replace(/\.ts$/, '.js'));
    fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, code);
  }
  const { AGRI_PATTERNS } = await import(pathToFileURL(path.join(temp, 'patterns/index.js')));
  const buildable = AGRI_PATTERNS.filter(p => p.runnable);
  if (buildable.length !== 23) throw new Error(`Expected 23 builders; got ${buildable.length}`);
  const records = [];
  for (const pattern of buildable) for (const seed of [42, 7, 2026]) {
    const values = Object.fromEntries(pattern.runnable.params.map(p => [p.key, p.default]));
    records.push({ group: 'default', pattern: pattern.id, seed, scenario: pattern.runnable.build(values, seed) });
  }
  const rejected = [];
  for (const pattern of buildable) {
    const defaults = Object.fromEntries(pattern.runnable.params.map(p => [p.key, p.default]));
    const fleet = pattern.runnable.problem_class === 'fleet_routing';
    // Wide, unobstructed reference combinations isolate individual numeric bounds.
    // Row weeding derives swath from row spacing × rows per pass and enforces implement ≤ swath and
    // headland ≥ turn radius, so its reference combination and joint case are its own.
    const rows = pattern.id === 'row-weeding-coverage';
    const safe = rows
      ? { ...defaults, field_w: 200, field_h: 100, row_spacing: 0.6, rows_per_pass: 10, headland_width: 12, turn_radius: 5, implement_width: 2 }
      : fleet
      ? { ...defaults, field_w: 600, field_h: 600, capacity: 5000, demand_per_task: 1, obstacle_count: 0, obstacle_r: 5 }
      : { ...defaults, field_w: 200, field_h: 100, pond_r: 5, headland_width: 5, swath_width: 8, robot_width: 2 };
    const cases = [{ name: 'joint-safe', values: safe },
      { name: 'joint-angle-footprint', values: rows
        ? { ...safe, field_w: 40, field_h: 40, row_angle_deg: 89, row_spacing: 1.2, rows_per_pass: 10, implement_width: 12, headland_width: 12, turn_radius: 12 }
        : fleet
        ? { ...safe, row_angle: 179, robot_radius: 2, vehicles: 3, capacity: 500, demand_per_task: 100, task_count: 4 }
        : { ...safe, field_w: 40, field_h: 40, headland_width: 0, robot_width: 8, turn_radius: 12, swath_width: 12 } }];
    for (const param of pattern.runnable.params) {
      if (param.type !== 'number' || ['field_w', 'field_h'].includes(param.key)) continue;
      for (const [side, bound] of [['min', param.min], ['max', param.max]]) {
        if (bound !== undefined) cases.push({ name: `${param.key}-${side}`, values: { ...safe, [param.key]: bound } });
      }
    }
    for (const c of cases) {
      const seed = c.name === 'joint-angle-footprint' ? 0xffffffff : 0;
      // Any unexpected builder rejection fails the gate; only the explicit negative cases below may reject.
      try { records.push({ group: 'boundary', case: c.name, pattern: pattern.id, seed, scenario: pattern.runnable.build(c.values, seed) }); }
      catch (error) {
        // A single bound that breaks a declared joint constraint must be rejected with that constraint's message, never silently adjusted.
        if (rows && /^(row_spacing|rows_per_pass|turn_radius|implement_width|headland_width)-/.test(c.name) && /压行|转弯半径|0\.5–12/.test(error.message)) { rejected.push({ pattern: pattern.id, case: c.name, message: error.message }); continue; }
        throw new Error(`${pattern.id}/${c.name}: ${error.message}`); }
    }
    const negative = rows
      ? [{ name: 'implement-wider-than-swath', values: { ...defaults, implement_width: 5 }, expected: /压行/ },
        { name: 'headland-below-turn-radius', values: { ...defaults, headland_width: 3 }, expected: /转弯半径/ },
        { name: 'empty-interior', values: { ...defaults, field_w: 40, field_h: 40, headland_width: 20, turn_radius: 5 }, expected: /内缩作业区为空/ }]
      : fleet
      ? [{ name: 'insufficient-capacity', values: { ...defaults, capacity: 1, demand_per_task: 500 }, expected: /单车容量不能小于单点需求/ }]
      : [{ name: 'empty-interior', values: { ...defaults, field_w: 40, field_h: 40, headland_width: 20 }, expected: /内缩作业区为空/ },
        { name: 'excessive-grid', values: { ...defaults, field_w: 600, field_h: 600, swath_width: 0.5 }, expected: /田块相对作业幅宽过大/ }];
    negative.push({ name: 'invalid-seed', values: defaults, seed: -1, expected: /随机种子/ });
    for (const c of negative) {
      let message;
      try { pattern.runnable.build(c.values, c.seed ?? 42); }
      catch (error) { message = String(error.message); }
      if (!message || !c.expected.test(message)) throw new Error(`${pattern.id}/${c.name}: expected ${c.expected}, got ${message ?? 'accepted'}`);
      rejected.push({ pattern: pattern.id, case: c.name, seed: c.seed ?? 42, values: c.values, expected_builder_rejection: message });
    }
  }
  if (process.argv[3]) fs.writeFileSync(process.argv[3], JSON.stringify(records, null, 2));
  const input = path.join(temp, 'instances.json');fs.writeFileSync(input, JSON.stringify(records));
  const python = process.env.AGRI_PYTHON || path.join(root, 'src/lab/.venv/bin/python');
  const result = spawnSync(python, [path.join(root, 'src/lab/tests/agri_contract_bridge.py'), input], {
    encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, maxBuffer: 10 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  const report = JSON.parse(result.stdout);
  report.builder_rejections = rejected;
  report.groups = Object.fromEntries(['default', 'boundary'].map(group => {
    const selected = report.results.filter(r => r.group === group);
    return [group, { checked: selected.length, passed: selected.filter(r => r.valid).length }];
  }));
  const output = JSON.stringify(report, null, 2) + '\n';
  if (process.argv[2]) fs.writeFileSync(process.argv[2], output);
  process.stdout.write(output); process.stderr.write(result.stderr);
  if (result.status !== 0) process.exitCode = result.status ?? 1;
} finally { fs.rmSync(temp, { recursive: true, force: true }); }
