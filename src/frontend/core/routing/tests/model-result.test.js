'use strict';
// Explicit synthetic fixtures only. These tests do not invoke a solver/model or
// present fixture costs/timings as results of a real experiment.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const M = require('../cvrp-model-result.js');
const R = require('../cvrp-result.js');
const C = require('../cvrp-checker.js');
const clone = value => JSON.parse(JSON.stringify(value));
const INSTANCE = {
  coordinates: [[0, 0], [1, 0], [2, 0], [3, 0]], demands: [0, 1, 1, 1], capacity: 3,
  depot: 0, fleet: 'unlimited_identical_vehicles', objective: 'sum_unrounded_euclidean_distance'
};
const DIRECT = [[0, 1, 2, 3, 0]], SEPARATE = [[0, 1, 0], [0, 2, 0], [0, 3, 0]];
function distance(instance, routes) {
  return routes.reduce((sum, route) => sum + route.slice(1).reduce((cost, node, index) => {
    const a = instance.coordinates[route[index]], b = instance.coordinates[node];
    return cost + Math.hypot(a[0] - b[0], a[1] - b[1]);
  }, 0), 0);
}
function reference(instance = INSTANCE, routes = SEPARATE) {
  return {
    schema_version: 'ceres-cvrp-solver-result/1', input_profile: 'cvrp-workbench-input-v1',
    input: {...clone(instance), units: 'abstract'}, algorithm: 'pyvrp_ils_savings_warm',
    solver_version: '0.14.0', seed: 11,
    config: {cpu_budget_seconds: 1, wall_budget_seconds: 3, pyvrp_iterations: 1000000,
      pyvrp_cpu_return_margin_seconds: 0.02, common_cpu_return_margin_seconds: 0.02},
    status: 'accepted', budget_conformant: true, source_unchanged: true,
    output: {routes: clone(routes), reported_cost: distance(instance, routes)},
    independent_check: {accepted: true, fixture_only: true},
    runtime: {status: 'returned', child_exit_code: 0, solver_cpu_seconds: 0.1,
      child_total_cpu_seconds: 0.15, process_wall_seconds: 0.2},
    sources: [{path: 'synthetic-fixture-only', sha256: '0'.repeat(64)}]
  };
}
function fixture(options = {}) {
  const instance = options.instance || INSTANCE, ref = reference(instance, options.referenceRoutes || SEPARATE);
  const routes = options.candidateRoutes || DIRECT;
  const content = Object.hasOwn(options, 'content') ? options.content : JSON.stringify({routes});
  const transport = options.transport !== false, feasible = options.feasible !== false && transport;
  const cost = feasible ? distance(instance, routes) : null, referenceCost = ref.output.reported_cost;
  const tolerance = 1e-9 * Math.max(1, referenceCost, cost === null ? 0 : cost);
  const better = feasible && cost < referenceCost - tolerance;
  return {
    schema_version: M.SCHEMA, input_profile: 'cvrp-workbench-input-v1',
    input: {...clone(instance), units: 'abstract'}, reference: ref,
    candidate: {content_text: content, transport_valid: transport, route_feasible: feasible,
      recomputed_cost: cost, errors: feasible ? [] : ['Synthetic failure fixture']},
    selection: {selected: better ? 'model' : 'pyvrp_reference',
      reason: !feasible ? 'candidate_invalid' : better ? 'candidate_strictly_better' : 'candidate_not_better',
      distance_tolerance: tolerance, delivered_routes: clone(better ? routes : ref.output.routes),
      delivered_cost: better ? cost : referenceCost},
    model: {...M.MODEL}, runtime: {fixture_only: true}, scope: {fixture_only: true}
  };
}

test('strict improvement selects independently checked candidate with unsigned provenance and timing', () => {
  const raw = fixture(), checked = M.inspect(raw, INSTANCE, 'abstract');
  assert.equal(checked.reference.check.recomputed_cost, 12);
  assert.equal(checked.candidate_check.recomputed_cost, 6);
  assert.equal(checked.selected, 'model');
  assert.equal(checked.reason, 'candidate_strictly_better');
  assert.equal(checked.check.accepted, true);
  assert.deepEqual(checked.output, {routes: DIRECT, reported_cost: 6});
  assert.deepEqual(checked.candidate_routes, DIRECT);
  assert.equal(checked.candidate_check.cost_source, 'independent_geometry_checker');
  assert.equal(checked.provenance_authenticated, false);
  assert.equal(checked.timing_authenticated, false);
  assert.equal(checked.transport_authenticated, false);
  assert.equal(checked.reference.timing_authenticated, false);
  assert.equal(checked.record, raw);
});

test('ties and worse feasible candidates always deliver the reference routes', () => {
  for (const routes of [DIRECT, [[0, 3, 2, 1, 0]], SEPARATE]) {
    const checked = M.inspect(fixture({referenceRoutes: DIRECT, candidateRoutes: routes}), INSTANCE, 'abstract');
    assert.equal(checked.candidate_check.accepted, true);
    assert.equal(checked.selected, 'pyvrp_reference');
    assert.equal(checked.reason, 'candidate_not_better');
    assert.deepEqual(checked.output.routes, DIRECT);
    assert.equal(checked.output.reported_cost, 6);
  }
});

test('an improvement within the specified tolerance falls back; larger improvement selects model', () => {
  for (const [height, selected] of [[1e-10, 'pyvrp_reference'], [1e-7, 'model']]) {
    const instance = {...INSTANCE, coordinates: [[0, 0], [1, 0], [0, height]], demands: [0, 1, 1]};
    const checked = M.inspect(fixture({instance, referenceRoutes: [[0, 1, 0], [0, 2, 0]],
      candidateRoutes: [[0, 1, 2, 0]]}), instance, 'abstract');
    assert.equal(checked.selected, selected);
    assert.equal(checked.tolerance, 1e-9 * Math.max(1, checked.reference.check.recomputed_cost,
      checked.candidate_check.recomputed_cost));
  }
});

test('duplicate, missing, internal-depot, unknown, malformed, and overloaded routes fall back', () => {
  for (const routes of [
    [[0, 1, 1, 2, 3, 0]], [[0, 1, 2, 0]], [[0, 1, 0, 2, 3, 0]],
    [[0, 1, 2, 99, 0]], [[1, 2, 3, 0]], [[0, 1]], [], [[0, -1, 1, 2, 3, 0]],
    [[0, 1, 0], [0, 2, 0], [0, 3, 0], [0, 0, 0]]
  ]) {
    const checked = M.inspect(fixture({candidateRoutes: routes, feasible: false}), INSTANCE, 'abstract');
    assert.equal(checked.reason, 'candidate_invalid', JSON.stringify(routes));
    assert.equal(checked.candidate_check.accepted, false);
    assert.equal(checked.candidate_check.recomputed_cost, null);
    assert.equal(checked.candidate_routes, null);
    assert.deepEqual(checked.output.routes, SEPARATE);
    assert.equal(checked.check.accepted, true);
    assert.ok(checked.candidate_check.errors.length);
  }
  const instance = {...INSTANCE, capacity: 2};
  const checked = M.inspect(fixture({instance, feasible: false}), instance, 'abstract');
  assert.match(checked.candidate_check.errors.join(' '), /超载/);
  assert.equal(checked.selected, 'pyvrp_reference');
});

test('exact content JSON rejects duplicate keys, including escaped duplicate keys and nested duplicates', () => {
  for (const content of [
    '{"routes":[],"routes":[[0,1,2,3,0]]}',
    '{"routes":[],"rout\\u0065s":[[0,1,2,3,0]]}',
    '{"routes":[{"x":0,"x":1}]}',
    '{"routes":[[0,1,2,3,0]],"__proto__":{},"__proto__":{}}'
  ]) {
    assert.throws(() => M.parseCandidate(content), /重复键/);
    const checked = M.inspect(fixture({content, feasible: false}), INSTANCE, 'abstract');
    assert.equal(checked.reason, 'candidate_invalid');
  }
  assert.equal(Object.prototype.polluted, undefined);
});

test('node grammar rejects bool, float, exponent, string, null, unsafe and nonfinite node tokens', () => {
  for (const node of ['true', 'false', '1.0', '1e0', '1E+0', '-0.0', '1.00000000000000001',
    '9007199254740993', '"1"', 'null', 'NaN', 'Infinity', '-Infinity', '1e400']) {
    const content = '{"routes":[[0,' + node + ',2,3,0]]}';
    assert.throws(() => M.parseCandidate(content), undefined, node);
    const checked = M.inspect(fixture({content, feasible: false}), INSTANCE, 'abstract');
    assert.equal(checked.reason, 'candidate_invalid', node);
  }
  assert.deepEqual(M.parseCandidate('{"routes":[[-0,1,2,3,0]]}').routes[0].slice(1), [1, 2, 3, 0]);
});

test('only an object with exactly routes is accepted; no extraction, repair or code execution', () => {
  globalThis.__modelBridgeExecuted = false;
  try {
    for (const content of [
      '```json\n{"routes":[[0,1,2,3,0]]}\n```',
      'Result: {"routes":[[0,1,2,3,0]]}', '{"routes":[[0,1,2,3,0]]} trailing',
      '{"routes":[[0,1,2,3,0]],"cost":6}', '{"routes":[[0,1,2,3,0]],"reported_cost":6}',
      '{"routes":[[0,1,2,3,0]],"__proto__":{"polluted":true}}',
      '[[0,1,2,3,0]]', 'null', '{"routes":"0,1,2,3,0"}', '{"routes":[null]}',
      '{"routes":[[0,1,2,3,0],]}', '{"routes":[[0,01,2,3,0]]}',
      '{"routes": [[0,+1,2,3,0]]}', '{"routes":[[0,1,2,3,0]]}{}',
      '\uFEFF{"routes":[[0,1,2,3,0]]}', '\u00a0{"routes":[[0,1,2,3,0]]}',
      'globalThis.__modelBridgeExecuted=true;({"routes":[[0,1,2,3,0]]})'
    ]) {
      const checked = M.inspect(fixture({content, feasible: false}), INSTANCE, 'abstract');
      assert.equal(checked.reason, 'candidate_invalid', content);
    }
    assert.equal(globalThis.__modelBridgeExecuted, false);
    assert.equal(Object.prototype.polluted, undefined);
  } finally { delete globalThis.__modelBridgeExecuted; }
});

test('legal JSON whitespace is parsed without changing the original candidate text', () => {
  const content = '\r\n \t{ "rout\\u0065s" : [ [ 0, 1, 2, 3, 0 ] ] } \n\t';
  const raw = fixture({content}), serialized = JSON.stringify(raw);
  const checked = M.inspect(serialized, INSTANCE, 'abstract');
  assert.equal(checked.record.candidate.content_text, content);
  assert.equal(checked.selected, 'model');
  assert.equal(JSON.stringify(checked.record), serialized);
});

test('failed transport with valid-looking better routes remains invalid and falls back', () => {
  const raw = fixture({transport: false}), checked = M.inspect(raw, INSTANCE, 'abstract');
  assert.equal(checked.candidate_check.geometry_accepted, true);
  assert.equal(checked.candidate_check.accepted, false);
  assert.equal(checked.candidate_check.recomputed_cost, null);
  assert.equal(checked.selected, 'pyvrp_reference');
  assert.equal(checked.reason, 'candidate_invalid');
  raw.candidate.route_feasible = true;
  raw.candidate.recomputed_cost = 6;
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /候选可行性或距离/);
});

test('null response content is a checked fallback, even if imported transport says successful', () => {
  for (const transport of [true, false]) {
    const checked = M.inspect(fixture({content: null, feasible: false, transport}), INSTANCE, 'abstract');
    assert.equal(checked.candidate_check.accepted, false);
    assert.equal(checked.selected, 'pyvrp_reference');
    assert.equal(checked.check.accepted, true);
  }
});

test('input binding checks both outer record and embedded reference against the current task', () => {
  for (const target of ['input', 'reference']) {
    for (const change of [bound => bound.coordinates[1][0] += 0.01,
      bound => bound.demands[1]++, bound => bound.capacity++, bound => bound.units = 'm',
      bound => bound.depot = 1, bound => bound.fleet = 'finite', bound => bound.objective = 'rounded']) {
      const raw = fixture(), bound = target === 'input' ? raw.input : raw.reference.input;
      change(bound);
      assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'));
    }
  }
  assert.throws(() => M.inspect(fixture(), INSTANCE, 'm'), /不一致/);
});

test('numerically equivalent bindings accept signed zero and decimal/exponent coordinates and capacity', () => {
  const raw = fixture();
  raw.input.coordinates[0][0] = -0;
  raw.reference.input.coordinates[0][1] = -0;
  let serialized = JSON.stringify(raw);
  serialized = serialized.replaceAll('"capacity":3', '"capacity":3.0');
  serialized = serialized.replaceAll('"coordinates":[[0,0],[1,0]', '"coordinates":[[-0,0.0],[1e0,0]');
  assert.equal(M.inspect(serialized, INSTANCE, 'abstract').selected, 'model');
});

test('reference is required and must pass independent route, status and fixed-budget checks', () => {
  for (const change of [raw => delete raw.reference, raw => raw.reference = null,
    raw => raw.reference.status = 'rejected', raw => raw.reference.budget_conformant = false,
    raw => raw.reference.source_unchanged = false, raw => raw.reference.runtime.status = 'exception',
    raw => raw.reference.runtime.child_exit_code = 1, raw => raw.reference.runtime.solver_cpu_seconds = 1.01,
    raw => raw.reference.runtime.process_wall_seconds = 3.01, raw => raw.reference.runtime = {},
    raw => raw.reference.runtime.solver_cpu_seconds = '0.1', raw => raw.reference.runtime.process_wall_seconds = -1,
    raw => raw.reference.seed = 12, raw => raw.reference.solver_version = '0.13.0',
    raw => raw.reference.config.cpu_budget_seconds = 2, raw => raw.reference.output.reported_cost = 0,
    raw => raw.reference.output.routes[0][1] = 999]) {
    const raw = fixture(); change(raw);
    assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'));
  }
});

test('declared checker fields cannot rescue failed routes or invalidate independently valid routes', () => {
  const raw = fixture();
  raw.reference.independent_check = {accepted: false, recomputed_cost: -999};
  assert.equal(M.inspect(raw, INSTANCE, 'abstract').selected, 'model');
  raw.candidate.content_text = '{"routes":[[0,1,1,2,3,0]]}';
  raw.candidate.errors = [];
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /候选可行性或距离/);
  raw.reference.independent_check = {accepted: true, recomputed_cost: 0};
  raw.reference.output.routes = [];
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /独立路线检查失败/);
});

test('model repository revision filename and hash declarations must match the pinned profile', () => {
  for (const key of ['repository', 'revision', 'filename', 'sha256']) {
    const raw = fixture(); raw.model[key] += '-changed';
    assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /固定配置/);
  }
  for (const value of [null, [], 'model']) {
    const raw = fixture(); raw.model = value;
    assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /固定配置/);
  }
});

test('a matching model hash and plausible changed timing remain unauthenticated claims', () => {
  const raw = fixture();
  raw.runtime = {total_wall_seconds: 0.0001, producer_authenticated: true, timing_authenticated: true};
  raw.reference.runtime.solver_cpu_seconds = 0.0001;
  raw.reference.sources[0].sha256 = 'f'.repeat(64);
  const checked = M.inspect(raw, INSTANCE, 'abstract');
  assert.equal(checked.provenance_authenticated, false);
  assert.equal(checked.timing_authenticated, false);
  assert.equal(checked.reference.provenance_authenticated, false);
  assert.equal(checked.record.runtime.total_wall_seconds, 0.0001);
});

test('missing or malformed runtime scope candidate and schema metadata reject', () => {
  for (const change of [raw => delete raw.runtime, raw => raw.runtime = null, raw => raw.runtime = [],
    raw => raw.runtime = '1 second', raw => delete raw.scope, raw => raw.scope = [],
    raw => raw.input_profile = 'other', raw => raw.schema_version = R.SCHEMA,
    raw => raw.candidate.transport_valid = 1, raw => raw.candidate.route_feasible = 'true',
    raw => raw.candidate.content_text = {}, raw => delete raw.candidate.content_text,
    raw => raw.candidate.errors = 'failed', raw => raw.candidate.errors = [7]]) {
    const raw = fixture(); change(raw);
    assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'));
  }
});

test('forged selection routes reason tolerance and costs are rejected', () => {
  for (const change of [raw => raw.selection.selected = 'pyvrp_reference',
    raw => raw.selection.reason = 'candidate_not_better', raw => raw.selection.distance_tolerance = 0,
    raw => raw.selection.distance_tolerance *= 2, raw => raw.selection.delivered_routes = SEPARATE,
    raw => raw.selection.delivered_cost = 0, raw => raw.selection.delivered_cost = '6',
    raw => raw.candidate.recomputed_cost = 0, raw => raw.candidate.recomputed_cost = null,
    raw => raw.candidate.route_feasible = false]) {
    const raw = fixture(); change(raw);
    assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'));
  }
  const raw = fixture({feasible: false, content: '{}'});
  raw.candidate.recomputed_cost = 0;
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /候选可行性或距离/);
});

test('tolerance declaration permits cross-language rounding but the selection uses its own tolerance', () => {
  const raw = fixture(), derived = raw.selection.distance_tolerance;
  raw.selection.distance_tolerance *= 1 + 5e-13;
  const checked = M.inspect(raw, INSTANCE, 'abstract');
  assert.equal(checked.tolerance, derived);
  assert.notEqual(checked.tolerance, raw.selection.distance_tolerance);
  raw.selection.distance_tolerance = derived * (1 + 2e-12);
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /容差/);
});

test('full-record JSON duplicate keys, nonfinite and non-JSON object values reject', () => {
  const raw = fixture(), serialized = JSON.stringify(raw);
  assert.throws(() => M.inspect(serialized.replace('"runtime":', '"runtime":{},"runtime":'), INSTANCE, 'abstract'), /重复键/);
  assert.throws(() => M.inspect(serialized.replace('"fixture_only":true', '"fixture_only":NaN'), INSTANCE, 'abstract'));
  assert.throws(() => M.inspect(serialized.replace('"fixture_only":true', '"fixture_only":1e400'), INSTANCE, 'abstract'), /有限/);
  for (const value of [Infinity, NaN, undefined, () => 6]) {
    const changed = fixture(); changed.runtime.test_value = value;
    assert.throws(() => M.inspect(changed, INSTANCE, 'abstract'));
  }
  raw.runtime.circular = raw;
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /循环/);
});

test('1 to 20 customers supported; larger and empty tasks reject before model selection', () => {
  for (const count of [1, 20]) {
    const instance = {...INSTANCE, coordinates: Array.from({length: count + 1}, (_, n) => [n, 0]),
      demands: [0, ...Array(count).fill(1)], capacity: count};
    const routes = [[0, ...Array.from({length: count}, (_, n) => n + 1), 0]];
    const raw = fixture({instance, referenceRoutes: routes, candidateRoutes: routes});
    assert.equal(M.inspect(raw, instance, 'abstract').check.accepted, true);
  }
  const instance = {...INSTANCE, coordinates: Array.from({length: 22}, (_, n) => [n, 0]),
    demands: [0, ...Array(21).fill(1)], capacity: 21};
  const routes = [[0, ...Array.from({length: 21}, (_, n) => n + 1), 0]];
  assert.throws(() => M.inspect(fixture({instance, referenceRoutes: routes, candidateRoutes: routes}),
    instance, 'abstract'), /1–20/);
  const empty = {...INSTANCE, coordinates: [[0, 0]], demands: [0]};
  assert.throws(() => M.inspect(fixture(), empty, 'abstract'));
});

test('coincident coordinates preserve zero distance and ties select reference', () => {
  const instance = {...INSTANCE, coordinates: [[0, 0], [0, 0], [0, 0], [0, 0]]};
  const checked = M.inspect(fixture({instance}), instance, 'abstract');
  assert.equal(checked.selected, 'pyvrp_reference');
  assert.equal(checked.tolerance, 1e-9);
  assert.equal(checked.output.reported_cost, 0);
  assert.equal(C.check(instance, checked.output).accepted, true);
});

test('256 KiB applies to UTF-8 input bytes, preserving exactly-at-limit inputs', () => {
  const serialized = JSON.stringify(fixture()), padding = M.MAX_BYTES - Buffer.byteLength(serialized);
  assert.equal(M.inspect(serialized + ' '.repeat(padding), INSTANCE, 'abstract').check.accepted, true);
  assert.throws(() => M.inspect(serialized + ' '.repeat(padding + 1), INSTANCE, 'abstract'), /256 KiB/);
  const raw = fixture(); raw.runtime.message = '界'.repeat(Math.ceil(M.MAX_BYTES / 3));
  assert.ok(JSON.stringify(raw).length < M.MAX_BYTES);
  assert.throws(() => M.inspect(raw, INSTANCE, 'abstract'), /256 KiB/);
  assert.throws(() => M.parseCandidate(' '.repeat(M.MAX_BYTES + 1)), /256 KiB/);
});

test('bounded parser rejects excessive nesting and malformed strings without running arbitrary content', () => {
  for (const content of ['['.repeat(1000) + ']'.repeat(1000), '{"routes":"unterminated}',
    '{"routes":"\\x00"}', '{"routes":"line\nfeed"}', '{"routes":', '']) {
    assert.throws(() => M.parseCandidate(content));
  }
});

test('browser UMD export works without Node or TextEncoder and checks full record text', () => {
  const context = vm.createContext({});
  for (const name of ['cvrp-problem.js', 'cvrp-checker.js', 'cvrp-result.js', 'cvrp-model-result.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', name), 'utf8'), context, {filename: name});
  }
  assert.equal(context.CVRPModelResult.SCHEMA, M.SCHEMA);
  assert.equal(context.CVRPModelResult.MAX_CUSTOMERS, 20);
  assert.equal(context.CVRPModelResult.inspect(JSON.stringify(fixture()), INSTANCE, 'abstract').selected, 'model');
});

test('routeCost calculates geometry only and independent checker remains the feasibility authority', () => {
  assert.equal(M.routeCost(INSTANCE, DIRECT), 6);
  assert.equal(M.routeCost(INSTANCE, SEPARATE), 12);
  const duplicate = [[0, 1, 1, 2, 3, 0]], cost = M.routeCost(INSTANCE, duplicate);
  assert.equal(cost, 6);
  assert.equal(C.check(INSTANCE, {routes: duplicate, reported_cost: cost}).accepted, false);
  assert.throws(() => M.routeCost(INSTANCE, [[0, true, 0]]));
  assert.throws(() => M.routeCost(INSTANCE, [[0, 99, 0]]));
});
