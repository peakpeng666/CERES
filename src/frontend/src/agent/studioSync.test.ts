import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentSession } from './api';

const mocks = vi.hoisted(() => ({ open: vi.fn(), showRuns: vi.fn(), setState: vi.fn(), getState: vi.fn() }));
vi.mock('../studio/store', () => ({ showRuns: mocks.showRuns, useStudio: { getState: mocks.getState, setState: mocks.setState } }));
import { currentRuns, syncAgentStudio } from './studioSync';

const task = (overrides: Partial<AgentSession> = {}) => ({
  session_id: 'agent-a', scenario_id: 'map-a', contract: { revision: 3 }, built_revision: 3,
  run_ids: ['old-run'], active_run_ids: ['current-run'], tool_calls: [], ...overrides,
}) as AgentSession;

beforeEach(() => {
  vi.resetAllMocks();
  mocks.open.mockResolvedValue(true);
  mocks.getState.mockReturnValue({ open: mocks.open, scenario: { id: 'map-a' } });
});

describe('agent current task map and runs', () => {
  it('uses current runs instead of historical runs', () => {
    expect(currentRuns(task())).toEqual(['current-run']);
    expect(currentRuns(task({ active_run_ids: [] }))).toEqual([]);
  });

  it('hides runs immediately after contract edits, before the next map is built', () => {
    expect(currentRuns(task({ built_revision: 2 }))).toEqual([]);
  });

  it('keeps legacy sessions readable without an active run list', () => {
    expect(currentRuns(task({ active_run_ids: undefined }))).toEqual(['old-run']);
  });

  it('reloads the existing map before displaying runs for its updated task', async () => {
    expect(await syncAgentStudio(task(), () => true, true)).toBe(true);
    expect(mocks.open).toHaveBeenCalledWith('map-a', { reload: true });
    expect(mocks.showRuns).toHaveBeenCalledWith(['current-run']);
  });

  it('clears old comparison, selection and replay when the current build has no runs', async () => {
    await syncAgentStudio(task({ active_run_ids: [] }), () => true, false);
    expect(mocks.showRuns).not.toHaveBeenCalled();
    expect(mocks.setState).toHaveBeenCalledWith({ runIds: [], focusRun: null, manualFocus: false, playback: null });
  });

  it('does not display runs after the user changes sessions during map loading', async () => {
    let finish!: (value: boolean) => void;
    mocks.open.mockReturnValue(new Promise<boolean>((resolve) => { finish = resolve; }));
    let active = true;
    const loading = syncAgentStudio(task(), () => active, false);
    active = false;
    finish(true);
    expect(await loading).toBe(false);
    expect(mocks.showRuns).not.toHaveBeenCalled();
    expect(mocks.setState).not.toHaveBeenCalled();
  });

  it('does not change the comparison when map reloading was refused to preserve a draft', async () => {
    mocks.open.mockResolvedValue(false);
    expect(await syncAgentStudio(task(), () => true, true)).toBe(false);
    expect(mocks.showRuns).not.toHaveBeenCalled();
    expect(mocks.setState).not.toHaveBeenCalled();
  });

  it('does not attach task runs to a different map', async () => {
    mocks.getState.mockReturnValue({ open: mocks.open, scenario: { id: 'another-map' } });
    expect(await syncAgentStudio(task(), () => true, false)).toBe(false);
    expect(mocks.showRuns).not.toHaveBeenCalled();
  });
});
