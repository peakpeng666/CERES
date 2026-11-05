/** Lab solve/run contract checks, kept browser-independent for deterministic tests. */
import assert from 'node:assert/strict';

const terminalStates = new Set(['done', 'error', 'timeout', 'canceled']);
const states = new Set(['queued', 'running', ...terminalStates]);
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const identifier = (value) => typeof value === 'string' && /^[A-Za-z0-9_-]+$/.test(value);

export async function readJsonResponse(response, expectedStatus, operation) {
  assert.equal(response.status(), expectedStatus, `${operation}: unexpected HTTP status`);
  try { return await response.json(); }
  catch { throw new Error(`${operation}: invalid JSON response`); }
}

export function submittedRunId(job) {
  assert.ok(object(job) && identifier(job.job_id), 'Solve response must contain a valid job_id');
  // POST /solve returns IDs; GET /jobs/{id} instead returns run summary objects.
  assert.ok(Array.isArray(job.runs) && job.runs.length === 1 && identifier(job.runs[0]),
    'Single-algorithm solve response must contain exactly one run ID in runs');
  return job.runs[0];
}

export async function waitForSuccessfulRun({ get, wait, job, scenarioId, algorithmId,
  maxAttempts = 120, intervalMs = 500, onRun = () => {} }) {
  const runId = submittedRunId(job);
  assert.ok(Number.isInteger(maxAttempts) && maxAttempts > 0, 'maxAttempts must be positive');
  assert.ok(Number.isFinite(intervalMs) && intervalMs >= 0, 'intervalMs must be nonnegative');
  assert.ok(typeof scenarioId === 'string' && scenarioId.length > 0, 'Expected scenario ID is required');
  assert.ok(typeof algorithmId === 'string' && algorithmId.length > 0, 'Expected algorithm ID is required');
  let run;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    run = await get('/lab/api/runs/' + encodeURIComponent(runId));
    await onRun(run); // Preserve the last response as evidence even if it fails validation.
    assert.ok(object(run), 'Run response must be an object');
    assert.equal(run.run_id, runId, 'Run response does not match submitted run ID');
    assert.equal(run.job_id, job.job_id, 'Run response does not match submitted job ID');
    assert.equal(run.scenario_id, scenarioId, 'Run response does not match imported scenario');
    assert.equal(run.algorithm?.id, algorithmId, 'Run response does not match submitted algorithm');
    assert.ok(states.has(run.state), 'Run response contains an unknown or missing state');
    if (terminalStates.has(run.state)) {
      assert.equal(run.state, 'done', 'Solver terminated without completing successfully');
      assert.equal(run.status, 'feasible', 'Completed solver did not return a feasible result');
      assert.equal(run.check?.valid, true, 'Completed solver did not pass independent validation');
      return run;
    }
    if (attempt + 1 < maxAttempts) await wait(intervalMs);
  }
  throw new Error(`Run did not reach a terminal state after ${maxAttempts} polls (last state: ${run.state})`);
}
