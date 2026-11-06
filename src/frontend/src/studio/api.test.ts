import { afterEach, describe, expect, it, vi } from 'vitest';
import { LabApiError } from '../lab/api';
import { studio } from './api';
import { downloadServerRecord } from './experiment/export';

const envelope = {
  record: {
    schema: 'agri-workbench.experiment-record/2',
    run_id: 'run_test',
    planning: { params: { seed: 7 }, raw_result: { routes: [1, 2] } },
    simulations: { field: [{ session_id: 'field_test', session_report: { cleanup_status: 'PASS' } }], nav2: [{ session_id: 'sim_test' }] },
    assets: [{ id: 'vehicle', version: 4 }],
    decision: { recommended: 'pyvrp.fleet' },
    warnings: ['B5'],
  },
  integrity: { algorithm: 'sha256', sha256: 'abc123', canonicalization: 'server canonical JSON' },
  assembled_utc: '2026-10-17T08:00:00+00:00',
};

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('server record download', () => {
  it('downloads the exact server envelope without reassembly', async () => {
    const raw = JSON.stringify(envelope).replace('"seed":7', '"seed":7,"float":1.0,"large":9007199254740993,"tiny":1e-09');
    const fetchMock = vi.fn().mockResolvedValue(new Response(raw, { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const onDownload = vi.fn();
    const result = await downloadServerRecord('run_test', { onDownload });
    expect(fetchMock).toHaveBeenCalledWith('/lab/api/runs/run_test/record', expect.objectContaining({ method: 'GET' }));
    expect(result.serialized).toBe(raw);
    expect(await onDownload.mock.calls[0]?.[0]?.text?.()).toBe(raw);
    expect(result.envelope.integrity).toEqual(envelope.integrity);
    expect(onDownload).toHaveBeenCalledOnce();
    expect(onDownload.mock.calls[0][1]).toBe('experiment-run_test.json');
  });

  it('does not fall back to the legacy client record on failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: 'record_unavailable', message: '记录暂不可用' }, 500)));
    const onDownload = vi.fn();
    await expect(downloadServerRecord('run_test', { onDownload })).rejects.toMatchObject({ code: 'record_unavailable', status: 500 });
    expect(onDownload).not.toHaveBeenCalled();
  });

  it('rejects a malformed HTTP 200 envelope instead of downloading it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ record: { schema: '/1', run_id: 'other' }, integrity: {}, assembled_utc: '' })));
    const onDownload = vi.fn();
    await expect(downloadServerRecord('run_test', { onDownload })).rejects.toMatchObject({ code: 'bad_response', status: 200 });
    expect(onDownload).not.toHaveBeenCalled();
  });
});

describe('API failure boundaries', () => {
  it('sends the explicitly selected Gazebo traffic policy with the original run', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ phase: 'starting', traffic_mode: 'coordinated' }));
    vi.stubGlobal('fetch', fetchMock);
    await studio.startFieldsim('run_original', { traffic: 'coordinated', real_time_factor: 4 });
    expect(fetchMock).toHaveBeenCalledWith('/lab/api/fieldsim', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ run_id: 'run_original', traffic: 'coordinated', real_time_factor: 4 }),
    }));
  });

  it('preserves 422 execution admission errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: 'heterogeneous_fleet_execution', message: '异构车队不能执行' }, 422)));
    await expect(studio.startFieldsim('run_hetero')).rejects.toMatchObject({ code: 'heterogeneous_fleet_execution', status: 422, message: '异构车队不能执行' });
  });

  it('preserves 409 asset conflicts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: 'asset_version_conflict', message: '资产哈希已变化' }, 409)));
    await expect(studio.startFieldsim('run_conflict')).rejects.toMatchObject({ code: 'asset_version_conflict', status: 409 });
  });

  it('reports non-JSON responses and disconnects explicitly', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>bad gateway</html>', { status: 502 })));
    await expect(studio.runRecord('run_bad')).rejects.toMatchObject({ code: 'bad_response', status: 502 });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')));
    await expect(studio.runRecord('run_offline')).rejects.toMatchObject({ code: 'network', status: 0 });
  });

  it('supports cancellation and timeout without converting them into success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    })));
    const controller = new AbortController();
    const pending = studio.runRecord('run_cancel', { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await expect(studio.runRecord('run_timeout', { timeoutMs: 5 })).rejects.toMatchObject({ code: 'timeout', status: 0 });
  });

  it('uses LabApiError for HTTP details instead of treating HTTP 200 as experiment success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: 'curvature_constrained_execution', message: '曲率车型不能执行' }, 422)));
    const promise = studio.startFieldsim('run_curvature');
    await expect(promise).rejects.toBeInstanceOf(LabApiError);
    await expect(promise).rejects.toMatchObject({ code: 'curvature_constrained_execution' });
  });

  it('posts solver cancellation and adopts the returned terminal state', async () => {
    const { lab } = await import('../lab/api');
    const canceled = { run_id: 'run_cancel', state: 'canceled', status: null };
    const fetchMock = vi.fn().mockResolvedValue(json(canceled));
    vi.stubGlobal('fetch', fetchMock);

    await expect(lab.cancelRun('run_cancel')).resolves.toEqual(canceled);
    expect(fetchMock).toHaveBeenCalledWith('/lab/api/runs/run_cancel/cancel', expect.objectContaining({ method: 'POST', body: '{}' }));
  });
});
