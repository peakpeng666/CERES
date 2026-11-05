/** Real loopback gateway smoke. Only minimal acceptance summaries are persisted; raw API records stay in memory. */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const base = process.env.URP_BASE_URL ?? 'http://127.0.0.1:4100';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const [input, output] = process.argv.slice(2); assert.ok(input && output);
await fs.mkdir(output, { recursive: false });
const cases = JSON.parse(await fs.readFile(input, 'utf8'));
const results = [];
async function request(path, body, expected = 200) {
  const response = await fetch(base + '/lab/api' + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: base }, body: JSON.stringify(body) });
  const raw = await response.text(); assert.equal(response.status, expected, `${path}: unexpected HTTP status`); return JSON.parse(raw);
}
try {
  for (const id of ['field-sampling', 'orchard-harvest-transport', 'coverage-tillage']) {
    const scenario = structuredClone(cases.find((x) => x.pattern === id && x.seed === 42).scenario);
    scenario.id = `smoke-${id}-${Date.now().toString(36)}`;
    scenario.provenance.params = { ...scenario.editable.values, pattern_id: id, builder_version: 'urp.agri.builder/3' };
    await request('/scenarios', scenario, 201);
    const restored = await request('/scenarios/' + scenario.id);
    assert.equal(restored.id, scenario.id); assert.equal(restored.provenance.seed, 42);
    assert.deepEqual(restored.provenance.params, scenario.provenance.params);
    assert.equal(restored.sim.supported, false);
    await request('/scenarios', { ...scenario, title: 'must not overwrite' }, 409);
    assert.deepEqual(await request('/scenarios/' + scenario.id), restored);
    await request('/recommend', { scenario_id: scenario.id });
    const algorithm = id === 'coverage-tillage' ? 'panel.boustrophedon' : 'panel.cluster_first';
    const job = await request('/solve', { scenario_id: scenario.id, algorithms: [{ id: algorithm, params: {} }], label: 'agri compatibility acceptance' }, 202);
    let run;
    for (let n = 0; n < 240; n++) { run = await request('/runs/' + job.runs[0]); if (['done', 'error', 'cancelled', 'canceled', 'timeout'].includes(run.state)) break; await new Promise((r) => setTimeout(r, 250)); }
    assert.equal(run.state, 'done', JSON.stringify(run)); assert.equal(run.status, 'feasible', JSON.stringify(run));
    assert.equal(run.check?.valid, true, JSON.stringify(run.check));
    const record = await request('/runs/' + run.run_id + '/record');
    assert.deepEqual(record.record.scenario.provenance.params, scenario.provenance.params);
    assert.equal(record.record.scenario.provenance.seed, 42);
    const runs = await request('/runs?scenario_id=' + scenario.id); assert.ok(runs.runs.some((r) => r.run_id === run.run_id));
    results.push({ id, duplicate_rejected_409: true, original_preserved: true, provenance_roundtrip: true, scenario_id: scenario.id, run_id: run.run_id, state: run.state, status: run.status, check_valid: run.check.valid });
  }
} catch {
  // Do not leak raw API payloads through assertion diagnostics or CI logs.
  throw new Error('Agriculture HTTP acceptance failed; no raw response data was persisted.');
} finally { await fs.writeFile(output + '/summary.json', JSON.stringify(results, null, 2)); }
