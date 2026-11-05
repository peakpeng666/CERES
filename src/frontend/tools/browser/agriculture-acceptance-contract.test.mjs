import assert from 'node:assert/strict';
import test from 'node:test';
import { readJsonResponse, submittedRunId, waitForSuccessfulRun } from './agriculture-acceptance-contract.mjs';

const job = { job_id: 'job_20261022T123400_abc123', runs: ['run_20261022T123400_def456'] };
const scenarioId = 'agri-sampling-acceptance';
const algorithmId = 'panel.cluster_first';
const run = (state = 'done', overrides = {}) => ({ run_id: job.runs[0], job_id: job.job_id,
  scenario_id: scenarioId, algorithm: { id: algorithmId }, state, status: 'feasible', check: { valid: true }, ...overrides });
function polling(responses, options = {}) {
  const paths = [], waits = [], observed = [];
  return { paths, waits, observed, result: waitForSuccessfulRun({ job, scenarioId, algorithmId,
    maxAttempts: responses.length, get: async (path) => { paths.push(path); return responses[paths.length - 1]; },
    wait: async (ms) => { waits.push(ms); }, onRun: (value) => { observed.push(value); }, ...options }) };
}

test('actual POST /solve payload selects runs[0], not legacy run_ids', () => {
  assert.equal(submittedRunId({ ...job, run_ids: ['wrong-run'] }), job.runs[0]);
});
for (const [name, value] of Object.entries({ null: null, array: [], missingJob: { runs: job.runs },
  emptyJob: { ...job, job_id: '' }, malformedJob: { ...job, job_id: {} }, unsafeJob: { ...job, job_id: '../job' },
  legacyOnly: { job_id: job.job_id, run_ids: job.runs }, absentRuns: { job_id: job.job_id },
  nullRuns: { ...job, runs: null }, stringRuns: { ...job, runs: job.runs[0] }, emptyRuns: { ...job, runs: [] },
  summaryObjects: { ...job, runs: [{ run_id: job.runs[0], state: 'queued' }] },
  emptyId: { ...job, runs: [''] }, whitespaceId: { ...job, runs: [' '] }, numberId: { ...job, runs: [1] },
  unsafeId: { ...job, runs: ['../record?x=1'] }, extraRun: { ...job, runs: [...job.runs, 'another-run'] },
  duplicateRun: { ...job, runs: [...job.runs, ...job.runs] } })) {
  test(`reject malformed solve payload: ${name}`, async () => {
    assert.throws(() => submittedRunId(value));
    const p = polling([run()], { job: value });
    await assert.rejects(p.result);
    assert.deepEqual(p.paths, []);
  });
}

test('queued -> running -> done polls the submitted identity and succeeds only after validation', async () => {
  const responses = [run('queued'), run('running'), run()];
  const p = polling(responses);
  assert.deepEqual(await p.result, responses[2]);
  assert.deepEqual(p.paths, Array(3).fill('/lab/api/runs/' + job.runs[0]));
  assert.deepEqual(p.waits, [500, 500]);
  assert.deepEqual(p.observed, responses);
});
for (const state of ['error', 'canceled', 'timeout']) {
  test(`${state} stops immediately and never passes even with feasible/valid fields`, async () => {
    const p = polling([run(state), run()]);
    await assert.rejects(p.result, /terminated without completing/);
    assert.equal(p.paths.length, 1);
    assert.deepEqual(p.waits, []);
    assert.deepEqual(p.observed, [run(state)]);
  });
}
for (const state of ['cancelled', 'failed', 'succeeded', '', undefined]) {
  test(`unknown state ${String(state)} fails closed`, async () => {
    const p = polling([run('done', { state })]);
    await assert.rejects(p.result, /unknown or missing state/);
    assert.deepEqual(p.waits, []);
  });
}
for (const [name, value] of Object.entries({ null: null, array: [], empty: {},
  runId: run('done', { run_id: 'foreign-run' }), jobId: run('done', { job_id: 'foreign-job' }),
  scenario: run('done', { scenario_id: 'foreign-scenario' }), algorithm: run('done', { algorithm: { id: 'foreign-algorithm' } }),
  absentAlgorithm: run('done', { algorithm: undefined }), infeasible: run('done', { status: 'infeasible' }),
  noSolution: run('done', { status: 'no_solution' }), absentStatus: run('done', { status: undefined }),
  invalidCheck: run('done', { check: { valid: false } }), absentCheck: run('done', { check: null }),
  truthyCheck: run('done', { check: { valid: 'true' } }) })) {
  test(`reject malformed, unrelated or unsuccessful run: ${name}`, async () => {
    const p = polling([value]);
    await assert.rejects(p.result);
    assert.deepEqual(p.observed, [value]);
    assert.deepEqual(p.waits, []);
  });
}
test('identity is validated while queued, not only at completion', async () => {
  const p = polling([run('queued', { job_id: 'foreign-job' }), run()]);
  await assert.rejects(p.result, /submitted job ID/);
  assert.equal(p.paths.length, 1);
});
test('poll exhaustion is a local failure, not a fabricated gateway terminal state', async () => {
  const p = polling([run('queued'), run('running'), run('running')]);
  await assert.rejects(p.result, /after 3 polls \(last state: running\)/);
  assert.equal(p.paths.length, 3);
  assert.equal(p.waits.length, 2);
  assert.equal(p.observed.at(-1).state, 'running');
});
test('immediate success does not sleep', async () => {
  const p = polling([run()]);
  assert.equal((await p.result).state, 'done');
  assert.deepEqual(p.waits, []);
});
test('transport errors propagate without retries or success', async () => {
  const error = new Error('connection refused');
  const p = polling([run()], { get: async () => { throw error; } });
  await assert.rejects(p.result, (actual) => actual === error);
  assert.deepEqual(p.observed, []);
  assert.deepEqual(p.waits, []);
});
for (const status of [200, 202]) {
  test(`HTTP ${status} JSON response is parsed`, async () => {
    const value = status === 202 ? job : run();
    assert.equal(await readJsonResponse({ status: () => status, json: async () => value }, status, 'test'), value);
  });
}
for (const status of [404, 409, 500, 503]) {
  test(`HTTP ${status} response fails before parsing any apparent success payload`, async () => {
    let parsed = false;
    await assert.rejects(readJsonResponse({ status: () => status,
      json: async () => { parsed = true; return run(); } }, 200, 'GET /lab/api/runs/test'), /unexpected HTTP status/);
    assert.equal(parsed, false);
  });
}
test('solve requires 202, not a 200 response containing a valid job', async () => {
  await assert.rejects(readJsonResponse({ status: () => 200, json: async () => job }, 202, 'solve'), /unexpected HTTP status/);
});
test('invalid JSON fails without leaking response contents', async () => {
  await assert.rejects(readJsonResponse({ status: () => 200, json: async () => { throw new Error('private payload'); } },
    200, 'run'), (error) => error.message === 'run: invalid JSON response' && !error.stack.includes('private payload'));
});
test('polling rejects an HTTP failure instead of retrying or accepting it', async () => {
  const p = polling([run()], { get: async () => readJsonResponse({ status: () => 500, json: async () => run() }, 200, 'run') });
  await assert.rejects(p.result, /unexpected HTTP status/);
  assert.deepEqual(p.observed, []);
  assert.deepEqual(p.waits, []);
});
