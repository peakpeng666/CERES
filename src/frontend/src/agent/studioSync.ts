import { showRuns, useStudio } from '../studio/store';
import type { AgentSession } from './api';

/** History remains in the conversation; the shared map only shows the current task version. */
export function currentRuns(session: AgentSession): string[] {
  if (session.built_revision !== undefined && session.built_revision !== session.contract.revision) return [];
  if (session.active_run_ids !== undefined) return session.active_run_ids;
  const solves = session.tool_calls.filter((c) => c.name === 'solve' && c.status === 'ok');
  const last = solves[solves.length - 1]?.result as { runs?: { run_id: string }[] } | undefined;
  return last?.runs?.map((r) => r.run_id) ?? session.run_ids.slice(-6);
}

export async function syncAgentStudio(session: AgentSession, isCurrent: () => boolean, reload: boolean): Promise<boolean> {
  const scenarioId = session.scenario_id;
  if (!scenarioId) return false;
  const ready = await useStudio.getState().open(scenarioId, { reload });
  if (!ready || !isCurrent() || useStudio.getState().scenario?.id !== scenarioId) return false;
  const batch = currentRuns(session);
  if (batch.length) showRuns(batch);
  else useStudio.setState({ runIds: [], focusRun: null, manualFocus: false, playback: null });
  return true;
}
